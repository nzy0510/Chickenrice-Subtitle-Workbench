"""Prepare the Windows workbench from pinned upstream releases and model revisions."""
from __future__ import annotations

import argparse
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parent.parent
RESOURCES = json.loads(Path(__file__).with_name("resources.json").read_text(encoding="utf-8"))
BLOCK = 1024 * 1024


def matches(path, spec):
    if not path.is_file() or path.stat().st_size != spec["size"]:
        return False
    if "sha256" in spec:
        digest, expected = hashlib.sha256(), spec["sha256"]
    else:
        digest, expected = hashlib.sha1(), spec["git_sha1"]
        digest.update(f"blob {spec['size']}\0".encode())
    with path.open("rb") as source:
        for block in iter(lambda: source.read(BLOCK), b""):
            digest.update(block)
    return digest.hexdigest() == expected


def download(url, target, spec, label="所需文件"):
    """Only promote a partial file after both its size and digest match."""
    if matches(target, spec):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    for attempt in range(3):
        if matches(partial, spec):
            partial.replace(target)
            return
        offset = partial.stat().st_size if partial.exists() else 0
        if offset >= spec["size"]:
            partial.unlink()
            offset = 0
        headers = {"User-Agent": "ChickenRice-Setup/1.0", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            try:
                response = urlopen(Request(url, headers=headers), timeout=30)
            except HTTPError as error:
                if not offset or error.code not in (400, 416, 501):
                    raise
                headers.pop("Range")
                response = urlopen(Request(url, headers=headers), timeout=30)
            with response:
                if response.status == 206:
                    content_range = response.headers.get("Content-Range", "")
                    if content_range != f"bytes {offset}-{spec['size'] - 1}/{spec['size']}":
                        raise OSError("下载站返回的续传位置不正确。")
                elif response.status == 200:
                    offset = 0  # A server may ignore Range; never append a full response.
                else:
                    raise OSError("下载站没有返回文件。")
                done, last = offset, 0.0
                with partial.open("ab" if offset else "wb") as output:
                    while block := response.read(BLOCK):
                        output.write(block)
                        done += len(block)
                        if done > spec["size"]:
                            raise OSError("下载文件大小不正确。")
                        if spec["size"] >= 10 * BLOCK and time.monotonic() - last >= 1:
                            print(f"\r  {label}  {done / spec['size']:.0%}", end="", flush=True)
                            last = time.monotonic()
            if last:
                print()
            if not matches(partial, spec):
                raise OSError("下载的文件还不完整。")
            partial.replace(target)
            return
        except (OSError, URLError, http.client.HTTPException) as error:
            if getattr(error, "errno", None) == errno.ENOSPC:
                raise RuntimeError("磁盘空间不足。请腾出空间后，再双击下载脚本继续。") from error
            if attempt == 2:
                raise RuntimeError(f"{label}下载未完成。重新双击下载脚本即可继续。") from error
            print("\n下载尚未完成，正在重试…", flush=True)
            time.sleep(1)


def select_engine(gpu_name, cuda_version):
    if not gpu_name:
        return "cu118", "cpu"
    if cuda_version < (11, 8):
        raise RuntimeError("请先更新 NVIDIA 显卡驱动，再运行下载脚本。")
    if re.search(r"RTX\s*50\d{2}", gpu_name, re.I):
        if cuda_version < (12, 8):
            raise RuntimeError("请先更新 NVIDIA 显卡驱动，再运行下载脚本。")
        return "cu128", "cuda"
    if re.search(r"GTX\s*(10|16)\d{2}", gpu_name, re.I):
        return "cu118", "cuda"
    return ("cu122" if cuda_version >= (12, 2) else "cu118"), "cuda"


def detect_engine():
    program = shutil.which("nvidia-smi")
    if not program:
        return select_engine("", (0, 0))
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        names = subprocess.check_output([program, "--query-gpu=name", "--format=csv,noheader"],
                                        timeout=10, creationflags=flags, stderr=subprocess.DEVNULL).decode()
        status = subprocess.check_output([program], timeout=10, creationflags=flags,
                                         stderr=subprocess.DEVNULL).decode(errors="replace")
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("暂时无法读取 NVIDIA 显卡信息，请检查显卡驱动后重试。") from error
    version = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", status)
    if not version or not names.strip():
        raise RuntimeError("请先更新 NVIDIA 显卡驱动，再运行下载脚本。")
    return select_engine(names.splitlines()[0], tuple(map(int, version.groups())))


def engine_ready(directory):
    required = ("infer.exe", "models/whisper_vad.onnx", "models/whisper-base/preprocessor_config.json")
    return (all((directory / name).is_file() and (directory / name).stat().st_size for name in required)
            and any((directory / "_internal").glob("python3*.dll")))


def install_engine(variant, root, cache):
    target = root / "engine"
    legacy = root / "extracted"
    if not target.exists() and legacy.resolve().is_relative_to(root.resolve()) and engine_ready(legacy):
        legacy.rename(target)
    if engine_ready(target):
        print("字幕处理程序已准备好。")
        return
    spec = RESOURCES["engines"][variant]
    name = f"faster_whisper_transwithai_windows_{variant}-nomodel.zip"
    archive = cache / name
    parts = [cache / f"{name}.{i:04d}" for i in range(len(spec["parts"]))]
    if not matches(archive, spec):
        print("正在下载字幕处理程序…", flush=True)
        for index, (part, info) in enumerate(zip(parts, spec["parts"]), 1):
            download(RESOURCES["engine_url"] + part.name, part, info,
                     f"字幕处理程序（{index}/{len(parts)}）")
        combined = archive.with_name(archive.name + ".part")
        with combined.open("wb") as output:
            for part in parts:
                with part.open("rb") as source:
                    shutil.copyfileobj(source, output, BLOCK)
        if not matches(combined, spec):
            raise RuntimeError("字幕处理程序校验失败，请重新运行下载脚本。")
        combined.replace(archive)
    for part in parts:
        part.unlink(missing_ok=True)
    print("正在安装字幕处理程序…", flush=True)
    with tempfile.TemporaryDirectory(prefix="unpack-", dir=cache) as temp:
        staging = Path(temp).resolve()
        assert staging.is_relative_to(root.resolve())
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                member = item.filename.replace("\\", "/")
                if ":" in member or not (staging / member).resolve().is_relative_to(staging):
                    raise RuntimeError("下载包中包含不正确的文件路径。")
            needed = sum(item.file_size for item in package.infolist())
            if shutil.disk_usage(staging).free < needed + 32 * BLOCK:
                raise RuntimeError("磁盘剩余空间不足，请腾出空间后重新运行。下载的文件已保留。")
            package.extractall(staging)
        candidates = [p.parent for p in staging.rglob("infer.exe") if engine_ready(p.parent)]
        if len(candidates) != 1:
            raise RuntimeError("下载包中缺少字幕处理程序，请检查下载版本。")
        # Keep any pre-existing incomplete installation intact in the local setup cache.
        assert target.resolve().is_relative_to(root.resolve())
        backup = None
        if target.exists():
            backup = cache / f"engine-backup-{time.time_ns()}"
            assert backup.resolve().is_relative_to(root.resolve())
            target.rename(backup)
        try:
            candidates[0].rename(target)
        except OSError:
            if backup is not None and not target.exists():
                backup.rename(target)
            raise
    archive.unlink()


def prepare_model(task, settings, root, check=False):
    spec = RESOURCES["models"][task]
    default = root / "models" / spec["repo"].split("/")[-1]
    selected = Path(settings.get(task + "_model") or default)
    print(f"正在检查{spec['title']}…", flush=True)
    # Local model cards are sometimes edited; only inference files determine reuse.
    files = {name: info for name, info in spec["files"].items() if name != "README.md"}
    if all(matches(selected / name, info) for name, info in files.items()):
        print(f"{spec['title']}已准备好。")
        return selected
    if selected != default and (selected / "model.bin").is_file():
        raise RuntimeError(f"已配置的{spec['title']}与下载版本不同，原文件和设置已保留。请先在工作台确认模型路径。")
    if check:
        print(f"{spec['title']}尚需下载。")
        return None
    if not default.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("模型下载目录指向了项目外部，请在参数设置中选择已有模型。")
    base = f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/"
    print(f"正在下载{spec['title']}…", flush=True)
    for name, info in spec["files"].items():
        download(base + name + "?download=true", default / name, info, spec["title"])
    return default


def prepare_gui(root, cache):
    python = root / ".venv" / "Scripts" / "python.exe"
    with (cache / "install.log").open("a", encoding="utf-8") as log:
        if not python.is_file():
            print("正在准备工作台…", flush=True)
            subprocess.run([sys.executable, "-m", "venv", str(root / ".venv")],
                           stdout=log, stderr=log, check=True)
        result = subprocess.run([str(python), "-c", "from PyQt5.QtWidgets import QApplication"],
                                stdout=log, stderr=log)
        if result.returncode:
            print("正在安装界面…", flush=True)
            subprocess.run([str(python), "-m", "pip", "--isolated", "--disable-pip-version-check",
                            "install", "--no-input",
                            "-r", str(root / "gui" / "requirements.txt")],
                           stdout=log, stderr=log, check=True)
            subprocess.run([str(python), "-c", "from PyQt5.QtWidgets import QApplication"],
                           stdout=log, stderr=log, check=True)
    print("工作台界面已准备好。")


def prepare(root, both=False, check=False):
    cache = root / ".setup"
    cache.mkdir(exist_ok=True)
    for folder in (cache, root / "models", root / "engine", root / ".venv"):
        if not folder.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("安装目录指向了项目外部，请将工作台放在独立文件夹后重试。")
    path = root / ".gui" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(settings, dict):
        raise RuntimeError("工作台设置无法读取，请先通过诊断启动检查。")
    variant, device = ("cu118", "cpu") if settings.get("device") == "cpu" else detect_engine()
    selected_device = settings.get("device", device)
    print("将使用 NVIDIA 显卡处理字幕。" if selected_device == "cuda" else "将使用 CPU 处理字幕。")
    if check:
        print("字幕处理程序已准备好。" if engine_ready(root / "engine") else "字幕处理程序尚需下载。")
    else:
        prepare_gui(root, cache)
        install_engine(variant, root, cache)
    model_paths = {}
    for task in (("translate", "transcribe") if both else ("translate",)):
        model = prepare_model(task, settings, root, check)
        if model:
            model_paths[task + "_model"] = str(model)
    if check:
        return
    # Preserve changes made in an open workbench while downloads were running.
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(current, dict):
        raise RuntimeError("工作台设置无法读取，下载的文件已保留。")
    updated = dict(current)
    for key, value in model_paths.items():
        if current.get(key) == settings.get(key):
            updated[key] = value
    updated.setdefault("device", device)
    updated.setdefault("compute", "int8" if updated["device"] == "cpu" else "int8_float16")
    if updated != current:
        path.parent.mkdir(exist_ok=True)
        temporary = path.with_suffix(".prepare.tmp")
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    print("\n准备完成！双击“启动字幕工作台.bat”，即可开始生成字幕。")


def main():
    parser = argparse.ArgumentParser(description="下载并准备字幕工作台")
    parser.add_argument("--check", action="store_true", help="只检查本地文件，不下载")
    args = parser.parse_args()
    if sys.version_info < (3, 10) or sys.maxsize < 2**32:
        raise RuntimeError("请安装 64 位 Python 3.12 后重试。")
    if os.name != "nt":
        raise RuntimeError("请在 Windows 电脑上运行下载脚本。")
    cache = ROOT / ".setup"
    if not cache.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError("请将工作台放在独立文件夹后重试。")
    cache.mkdir(exist_ok=True)
    import msvcrt
    with (cache / "download.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("已有下载窗口正在运行，请到原窗口查看进度。") from error
        print("ChickenRice 字幕工作台\n")
        both = True
        if not args.check:
            print("1. 中文翻译模型（生成中文字幕，直接按回车）\n2. 中文翻译和日文转录模型（生成中文字幕和日文字幕）\n")
            choice = input("请选择：").strip()
            while choice not in ("", "1", "2"):
                choice = input("请输入 1 或 2：").strip()
            both = choice == "2"
        prepare(ROOT, both, args.check)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n下载已暂停。下次双击下载脚本即可继续。")
        sys.exit(1)
    except Exception as error:
        cache = ROOT / ".setup"
        if cache.resolve().is_relative_to(ROOT.resolve()):
            try:
                cache.mkdir(exist_ok=True)
                (cache / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
            except OSError:
                pass
        if isinstance(error, subprocess.CalledProcessError):
            print("\n界面准备未完成，请查看 .setup/install.log 后重试。")
        elif isinstance(error, RuntimeError):
            print(f"\n{error}")
        else:
            print(f"\n准备未完成：{error}\n详情已保存到 .setup/error.log。")
        sys.exit(1)
