"""Local-only adapter for the packaged ChickenRice CLI. No shell commands for inference."""
from __future__ import annotations

import json
import math
import os
import queue
import re
import subprocess
import threading
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "extracted" / "infer.exe"
DATA = ROOT / ".gui"
MODEL_ROOT = ROOT / "models"
SUFFIXES = "wav,flac,mp3,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv"
SUPPORTED = {"." + s for s in SUFFIXES.split(",")}
PRESETS = {
    "标准": (0.5, 300, 100, 200),
    "轻声 / 耳语": (0.35, 100, 250, 400),
}


@dataclass
class Settings:
    task: str = "translate"
    translate_model: str = str(MODEL_ROOT / "whisper-large-v2-translate-zh-v0.2-st-ct2")
    transcribe_model: str = str(MODEL_ROOT / "whisper-ja-1.5B-ct2")
    device: str = "cuda"
    compute: str = "int8_float16"
    formats: str = "srt"
    output: str = ""
    overwrite: bool = False
    threshold: float = 0.5
    min_speech: int = 300
    min_silence: int = 100
    padding: int = 200
    smart_split: bool = True
    chunk_seconds: float = 30
    merge: bool = True
    merge_gap: int = 2000
    merge_duration: int = 20000

    @property
    def model(self):
        return Path(self.translate_model if self.task == "translate" else self.transcribe_model)

    @property
    def language_dir(self):
        return "中文" if self.task == "translate" else "日文"

    def generation_config(self):
        return {
            "language": "ja", "task": self.task, "vad_filter": True,
            "vad_parameters": {
                "threshold": self.threshold, "min_speech_duration_ms": self.min_speech,
                "min_silence_duration_ms": self.min_silence, "speech_pad_ms": self.padding,
            },
            "max_initial_timestamp": 30, "repetition_penalty": 1.1,
            "smart_split_with_vad": self.smart_split,
            "target_chunk_duration_s": self.chunk_seconds,
            "segment_merge": {
                "enabled": self.merge, "max_gap_ms": self.merge_gap,
                "max_duration_ms": self.merge_duration,
            },
        }


def load_settings(path=DATA / "settings.json"):
    if not path.exists():
        return Settings()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("设置文件应为 JSON 对象。")
    allowed = {f.name for f in fields(Settings)}
    defaults = Settings()
    for key, value in raw.items():
        if key not in allowed:
            continue
        expected = type(getattr(defaults, key))
        if expected in (int, float):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"设置 {key} 必须是有限数值。")
        elif type(value) is not expected:
            raise ValueError(f"设置 {key} 类型错误。")
    return Settings(**{k: v for k, v in raw.items() if k in allowed})


def save_settings(settings, path=DATA / "settings.json"):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def discover_files(paths):
    found, ignored = {}, 0
    for value in paths:
        path = Path(value).resolve()
        if not path.exists():
            raise ValueError(f"文件不存在：{path}")
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        for item in candidates:
            if not item.is_file():
                continue
            if item.suffix.lower() in SUPPORTED:
                found[str(item.resolve()).casefold()] = item.resolve()
            else:
                ignored += 1
    return list(found.values()), ignored


def output_dir(source, settings):
    base = Path(settings.output) if settings.output else source.parent / "ChickenRice字幕"
    return base / settings.language_dir


def expected_outputs(source, settings):
    return [output_dir(source, settings) / (source.stem + "." + fmt)
            for fmt in settings.formats.split(",")]


def validate(settings, sources, engine=ENGINE):
    if not engine.is_file():
        raise ValueError(f"找不到推理程序：{engine}")
    if not sources:
        raise ValueError("请先添加音频或视频文件。")
    if settings.task not in {"translate", "transcribe"}:
        raise ValueError("请选择翻译或转录任务。")
    if settings.device not in {"cuda", "cpu"}:
        raise ValueError("请选择 NVIDIA GPU 或 CPU。")
    choices = {"cuda": {"int8_float16", "float16", "float32"}, "cpu": {"int8", "float32"}}
    if settings.compute not in choices[settings.device]:
        raise ValueError("计算精度与运行设备不匹配。")
    for name in ("model.bin", "config.json", "tokenizer.json"):
        if not (settings.model / name).is_file() or (settings.model / name).stat().st_size == 0:
            raise ValueError(f"模型文件缺失：{settings.model / name}\n请在“参数设置”中选择完整的本地模型目录。")
    for relative in ("models/whisper_vad.onnx", "models/whisper-base/preprocessor_config.json"):
        if not (engine.parent / relative).is_file():
            raise ValueError(f"本地语音检测资源缺失：{relative}")
    if not settings.formats or not set(settings.formats.split(",")) <= {"srt", "vtt", "lrc"}:
        raise ValueError("至少选择一种字幕格式。")
    ranges = {"threshold": (0.1, 0.9), "min_speech": (0, 3000), "min_silence": (0, 5000),
              "padding": (0, 2000), "chunk_seconds": (5, 30),
              "merge_gap": (0, 5000), "merge_duration": (1000, 60000)}
    for name, (low, high) in ranges.items():
        if not low <= getattr(settings, name) <= high:
            raise ValueError(f"参数 {name} 应在 {low} 到 {high} 之间。")
    if settings.output and not Path(settings.output).is_absolute():
        raise ValueError("输出文件夹必须是完整路径，请点击“浏览”选择。")
    targets = {}
    for source in sources:
        if not source.is_file() or source.suffix.lower() not in SUPPORTED:
            raise ValueError(f"输入文件不可用：{source}")
        for target in expected_outputs(source, settings):
            key = str(target.resolve()).casefold()
            if key in targets and targets[key] != source:
                raise ValueError(f"两个输入会生成同名字幕：\n{targets[key]}\n{source}\n"
                                 "请分批处理、重命名音频，或选择“源文件旁”。")
            targets[key] = source


def build_args(settings, sources, destination, config):
    # argparse.REMAINDER in the engine requires every option before the input paths.
    args = ["--model_name_or_path", str(settings.model.resolve()), "--device", settings.device,
            "--compute_type", settings.compute, "--task", settings.task,
            "--sub_formats", settings.formats, "--audio_suffixes", SUFFIXES,
            "--output_dir", str(destination.resolve()), "--generation_config", str(config.resolve()),
            "--log_level", "DEBUG"]
    if settings.overwrite:
        args.append("--overwrite")
    return args + [str(p.resolve()) for p in sources]


def read_cues(path):
    """Return SRT/VTT timing and text for preview; malformed timed subtitles fail explicitly."""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".lrc":
        cues = [(stamp, "", body.strip()) for stamp, body in
                re.findall(r"\[(\d+:\d+(?:\.\d+)?)\](.*)", text) if body.strip()]
        if text.strip() and not cues:
            raise ValueError(f"无法解析歌词字幕：{path.name}")
        return cues
    pattern = r"(?m)^(\d{2,}:\d{2}:\d{2}[,.]\d{3}) --> (\d{2,}:\d{2}:\d{2}[,.]\d{3})[^\n]*\n([^\n]+(?:\n(?!\s*$)[^\n]+)*)"
    cues = [(a, b, c.strip()) for a, b, c in re.findall(pattern, text)]
    if text.strip().upper() not in {"", "WEBVTT"} and not cues:
        raise ValueError(f"无法解析字幕时间轴：{path.name}")
    return cues


def parse_progress(line):
    chunk = re.search(r"Smart VAD chunk (\d+)/(\d+)", line)
    if chunk:
        return "chunk", (int(chunk[1]), int(chunk[2]))
    current = re.search(r"Processing \((?:translate|transcribe)\) \((\d+)/(\d+)\): (.+)", line)
    if not current:
        current = re.search(r"正在处理（[^，]+，(\d+)/(\d+)）：(.+)", line)
    if current:
        return "file", current[3].strip()
    if "Loading Whisper model" in line or "正在加载Whisper" in line or "加载 Whisper" in line:
        return "stage", "正在加载模型，首次加载需要一些时间…"
    return None


class Runner:
    """One inference process per output folder; model stays loaded across tracks in a group."""
    def __init__(self, emit, runs_dir=DATA / "runs", engine=ENGINE, command=None):
        self.emit = emit
        self.runs_dir = runs_dir
        self.engine = engine
        self.command = command or [str(engine)]
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def _stop(self, process):
        if process.poll() is not None:
            return
        if os.name == "nt":
            # Only the process tree created by this runner is stopped.
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        else:
            process.kill()
        if process.poll() is None:
            process.kill()

    def run(self, sources, settings):
        validate(settings, sources, self.engine)
        run_dir = self.runs_dir / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
        run_dir.mkdir(parents=True)
        config = run_dir / "generation.json"
        config.write_text(json.dumps(settings.generation_config(), ensure_ascii=False, indent=2), encoding="utf-8")
        manifest = {"settings": asdict(settings), "inputs": [str(p) for p in sources], "results": [], "engine_errors": []}
        self.emit("run_dir", str(run_dir))
        results, groups = {}, defaultdict(list)

        def result(source, status, message, outputs=()):
            item = {"source": str(source), "status": status, "message": message,
                    "outputs": [str(p) for p in outputs]}
            results[str(source)] = item
            self.emit("result", item)

        for source in sources:
            outputs = expected_outputs(source, settings)
            if not settings.overwrite and all(p.exists() for p in outputs):
                result(source, "skipped", "已有字幕，已跳过", outputs)
            else:
                groups[output_dir(source, settings)].append(source)

        with (run_dir / "engine.log").open("w", encoding="utf-8") as log:
            for destination, group in groups.items():
                # Stay below Windows' command-line length limit even with long Unicode paths.
                batches, batch = [], []
                for source in group:
                    candidate = batch + [source]
                    if batch and len(subprocess.list2cmdline(self.command + build_args(settings, candidate, destination, config))) > 28000:
                        batches.append(batch)
                        batch = []
                    batch.append(source)
                if batch:
                    batches.append(batch)
                for batch in batches:
                    if self.cancelled.is_set():
                        break
                    destination.mkdir(parents=True, exist_ok=True)
                    before = {str(p): (p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None
                              for source in batch for p in expected_outputs(source, settings)}

                    def collect_output(source):
                        outputs = expected_outputs(source, settings)
                        written = all(p.exists() and (not settings.overwrite and before[str(p)] is not None
                                      or (p.stat().st_mtime_ns, p.stat().st_size) != before[str(p)]) for p in outputs)
                        if not written:
                            return False
                        try:
                            cues = read_cues(outputs[0])
                            result(source, "done" if cues else "empty",
                                   f"已生成 · {len(cues)} 条字幕" if cues else "未检测到对白", outputs)
                        except (ValueError, OSError, UnicodeError) as error:
                            result(source, "failed", str(error))
                        return True

                    command = self.command + build_args(settings, batch, destination, config)
                    log.write("COMMAND " + subprocess.list2cmdline(command) + "\n")
                    log.flush()
                    self.emit("stage", "正在加载模型…")
                    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
                    process = subprocess.Popen(command, cwd=str(self.engine.parent), env=env,
                                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                               stderr=subprocess.STDOUT,
                                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    lines = queue.Queue()

                    def read_stdout():
                        try:
                            for raw in iter(process.stdout.readline, b""):
                                lines.put(raw.decode("utf-8", errors="replace").rstrip())
                        finally:
                            lines.put(None)

                    reader = threading.Thread(target=read_stdout, daemon=True)
                    reader.start()
                    current_source = None
                    try:
                        while True:
                            if self.cancelled.is_set():
                                self._stop(process)
                            try:
                                line = lines.get(timeout=0.1)
                            except queue.Empty:
                                continue
                            if line is None:
                                break
                            log.write(line + "\n")
                            log.flush()
                            self.emit("log", line)
                            event = parse_progress(line)
                            if event:
                                if event[0] == "file":
                                    if current_source in batch:
                                        collect_output(current_source)
                                    current_source = Path(event[1])
                                self.emit(*event)
                        exit_code = process.wait()
                    finally:
                        if process.poll() is None:
                            self._stop(process)
                        reader.join(timeout=3)
                        process.stdout.close()
                    if exit_code and not self.cancelled.is_set():
                        manifest["engine_errors"].append(exit_code)
                        self.emit("log", f"推理程序退出码：{exit_code}。请查看上方错误信息。")
                    for source in batch:
                        if str(source) in results or collect_output(source):
                            continue
                        if self.cancelled.is_set():
                            result(source, "cancelled", "已停止，当前文件未完成")
                        else:
                            result(source, "failed", f"未生成完整字幕（退出码 {exit_code}），详情见日志")
            for source in sources:
                if str(source) not in results:
                    result(source, "cancelled", "已停止，未处理")
        manifest["results"] = list(results.values())
        manifest["cancelled"] = self.cancelled.is_set()
        (run_dir / "result.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest
