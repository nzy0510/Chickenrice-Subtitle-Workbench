"""Behavior checks: safe CLI routing, result verification, cancellation, and GUI controls."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The bundled Windows Qt runtime hangs while initializing its offscreen plugin.
# Use its normal platform with hidden widgets instead.

import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from PyQt5.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt5.QtGui import QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import QApplication

import app as desktop
from backend import (DATA, PRESETS, ROOT, Runner, Settings, build_args, discover_files,
                     expected_outputs, load_settings, output_dir, parse_progress, read_cues,
                     save_settings, validate)

FAKE_ENGINE = r'''
import sys, pathlib, time
args = sys.argv[1:]
def option(name):
    return args[args.index(name) + 1]
inputs = args[args.index('--log_level') + 2:]
overwrite = '--overwrite' in inputs
inputs = [i for i in inputs if i != '--overwrite']
for i, value in enumerate(inputs):
    p = pathlib.Path(value)
    print(f'Processing ({option("--task")}) ({i+1}/{len(inputs)}): {p}', flush=True)
    print('Smart VAD chunk 1/2: 00:00:00,000 --> 00:00:01,000', flush=True)
    if p.stem == 'slow':
        time.sleep(60)
    if p.stem == 'failure':
        print('ERROR: requested test failure', flush=True)
        sys.exit(7)
    if p.stem == 'missing':
        continue
    for fmt in option('--sub_formats').split(','):
        output = pathlib.Path(option('--output_dir')) / (p.stem + '.' + fmt)
        if output.exists() and not overwrite:
            continue
        text = '' if p.stem == 'silence' else '1\n00:00:00,000 --> 00:00:01,000\n' + '\u4f60\u597d' + '\n\n'
        if p.stem == 'malformed':
            text = 'no timestamps'
        output.write_text(text.replace('\\n', '\n').replace('\\u4f60\\u597d', '\u4f60\u597d'), encoding='utf-8')
'''


class Fixture(unittest.TestCase):
    def setUp(self):
        DATA.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="test-", dir=DATA)
        self.root = Path(self.temp.name)
        # All temporary test cleanup is confined to the workspace's dedicated GUI directory.
        assert self.root.resolve().is_relative_to(DATA.resolve())
        self.addCleanup(self.temp.cleanup)
        self.engine = self.root / "engine" / "infer.exe"
        self.engine.parent.mkdir()
        self.engine.write_bytes(b"fixture")
        for name in ("models/whisper_vad.onnx", "models/whisper-base/preprocessor_config.json"):
            p = self.engine.parent / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}")
        self.model = self.root / "model"
        self.model.mkdir()
        for name in ("model.bin", "config.json", "tokenizer.json"):
            (self.model / name).write_text("{}")
        self.settings = Settings(translate_model=str(self.model), transcribe_model=str(self.model), output=str(self.root / "out"))
        self.fake = self.root / "fake.py"
        self.fake.write_text(FAKE_ENGINE, encoding="utf-8")

    def source(self, name="音声 & test", folder="input"):
        p = self.root / folder / (name + ".wav")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fixture")
        return p

    def runner(self, emit=lambda *_: None):
        return Runner(emit, self.root / "runs", self.engine, [sys.executable, str(self.fake)])


class BackendTests(Fixture):
    def test_recursive_import_deduplicates_supported_paths(self):
        source = self.source()
        (source.parent / "note.txt").write_text("x")
        files, ignored = discover_files([source.parent, source])
        self.assertEqual(files, [source])
        self.assertEqual(ignored, 1)

    def test_model_validation_blocks_missing_local_weights(self):
        (self.model / "model.bin").unlink()
        with self.assertRaisesRegex(ValueError, "模型文件缺失"):
            validate(self.settings, [self.source()], self.engine)

    def test_task_pairs_correct_model_and_separate_language_output(self):
        source = self.source()
        ja = replace(self.settings, task="transcribe", transcribe_model=str(self.root / "ja"))
        args = build_args(ja, [source], output_dir(source, ja), self.root / "generation.json")
        self.assertEqual(args[1], str(self.root / "ja"))
        self.assertEqual(args[args.index("--task") + 1], "transcribe")
        self.assertEqual(args[-1], str(source))
        self.assertNotEqual(expected_outputs(source, ja), expected_outputs(source, self.settings))

    def test_output_collision_stops_before_inference(self):
        with self.assertRaisesRegex(ValueError, "同名字幕"):
            validate(self.settings, [self.source("track", "one"), self.source("track", "two")], self.engine)

    def test_cpu_rejects_float16(self):
        with self.assertRaisesRegex(ValueError, "精度"):
            validate(replace(self.settings, device="cpu"), [self.source()], self.engine)

    def test_settings_roundtrip_and_snapshot(self):
        path = self.root / "settings.json"
        save_settings(self.settings, path)
        self.assertEqual(load_settings(path), self.settings)
        self.assertEqual(self.settings.generation_config()["language"], "ja")

    def test_malformed_saved_settings_have_actionable_error(self):
        path = self.root / "settings.json"
        path.write_text('{"threshold": "wrong"}')
        with self.assertRaisesRegex(ValueError, "数值"):
            load_settings(path)

    def test_actual_subprocess_handles_unicode_spaces_and_shell_metacharacters(self):
        source = self.source("音声 ' & test")
        events = []
        result = self.runner(lambda *e: events.append(e)).run([source], self.settings)
        self.assertEqual(result["results"][0]["status"], "done")
        self.assertEqual(len(read_cues(expected_outputs(source, self.settings)[0])), 1)
        self.assertTrue(any(e[0] == "chunk" for e in events))

    def test_two_tracks_share_process_and_first_result_arrives_before_second(self):
        sources = [self.source("one"), self.source("two")]
        events = []
        runner = self.runner(lambda *e: events.append(e))
        result = runner.run(sources, self.settings)
        self.assertEqual([r["status"] for r in result["results"]], ["done", "done"])
        done_one = next(i for i, e in enumerate(events) if e[0] == "result" and e[1]["source"] == str(sources[0]))
        start_two = next(i for i, e in enumerate(events) if e == ("file", str(sources[1])))
        self.assertLess(done_one, start_two)
        log = next((self.root / "runs").glob("*/engine.log")).read_text(encoding="utf-8")
        self.assertEqual(log.count("COMMAND "), 1)

    def test_existing_manual_subtitle_is_not_changed(self):
        source = self.source()
        target = expected_outputs(source, self.settings)[0]
        target.parent.mkdir(parents=True)
        target.write_text("手工字幕", encoding="utf-8")
        result = self.runner().run([source], self.settings)
        self.assertEqual(result["results"][0]["status"], "skipped")
        self.assertEqual(target.read_text(encoding="utf-8"), "手工字幕")

    def test_overwrite_does_not_treat_stale_output_as_success(self):
        source = self.source("missing")
        target = expected_outputs(source, self.settings)[0]
        target.parent.mkdir(parents=True)
        target.write_text("1\n00:00:00,000 --> 00:00:01,000\nold\n")
        result = self.runner().run([source], replace(self.settings, overwrite=True))
        self.assertEqual(result["results"][0]["status"], "failed")

    def test_explicit_overwrite_updates_subtitle(self):
        source = self.source()
        target = expected_outputs(source, self.settings)[0]
        target.parent.mkdir(parents=True)
        target.write_text("old")
        result = self.runner().run([source], replace(self.settings, overwrite=True))
        self.assertEqual(result["results"][0]["status"], "done")
        self.assertNotEqual(target.read_text(encoding="utf-8"), "old")

    def test_zero_exit_without_output_is_failure(self):
        result = self.runner().run([self.source("missing")], self.settings)
        self.assertEqual(result["results"][0]["status"], "failed")

    def test_engine_error_and_malformed_output_are_not_success(self):
        result = self.runner().run([self.source("failure")], self.settings)
        self.assertEqual(result["engine_errors"], [7])
        self.assertEqual(result["results"][0]["status"], "failed")
        result = self.runner().run([self.source("malformed")], self.settings)
        self.assertEqual(result["results"][0]["status"], "failed")

    def test_silence_is_distinct_from_failure(self):
        result = self.runner().run([self.source("silence")], self.settings)
        self.assertEqual(result["results"][0]["status"], "empty")

    def test_cancel_terminates_running_process_and_does_not_start_next(self):
        began = threading.Event()
        runner = self.runner(lambda kind, data: began.set() if kind == "file" else None)
        results = []
        thread = threading.Thread(target=lambda: results.append(runner.run([self.source("slow"), self.source("next")], self.settings)))
        thread.start()
        self.assertTrue(began.wait(10))
        runner.cancel()
        thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertTrue(results[0]["cancelled"])
        self.assertEqual([r["status"] for r in results[0]["results"]], ["cancelled", "cancelled"])

    def test_chinese_progress_and_srt_vtt_lrc_preview(self):
        self.assertEqual(parse_progress("正在处理（translate，1/2）：D:\\音声.wav"), ("file", "D:\\音声.wav"))
        for suffix, body in (("srt", "1\n00:00:00,000 --> 00:00:01,000\n你好\n第二行\n\n"),
                             ("vtt", "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n你好\n\n"),
                             ("lrc", "[00:01.20]你好\n")):
            path = self.root / ("subtitle." + suffix)
            path.write_text(body, encoding="utf-8")
            self.assertEqual(len(read_cues(path)), 1)
        path = self.root / "silence.vtt"
        path.write_text("WebVTT\n\n")
        self.assertEqual(read_cues(path), [])
        path = self.root / "subtitle.lrc"
        path.write_text("[00:01.20]hello\n[00:02.00]\n")
        self.assertEqual(len(read_cues(path)), 1)


class GuiTests(Fixture):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle("Fusion")

    def setUp(self):
        super().setUp()
        self.window = desktop.Window(self.settings, self.root / "gui-settings.json")
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.window.show()
        self.qt.processEvents()
        self.addCleanup(self.window.close)

    def test_task_device_presets_and_settings_persist(self):
        w = self.window
        w.task.setCurrentIndex(1)
        w.device.setCurrentIndex(1)
        w.set_preset("轻声 / 耳语")
        w.save_and_return()
        saved = load_settings(self.root / "gui-settings.json")
        self.assertEqual((saved.task, saved.device, saved.compute), ("transcribe", "cpu", "int8"))
        self.assertEqual((saved.threshold, saved.min_speech, saved.min_silence, saved.padding), PRESETS["轻声 / 耳语"])
        w.threshold.setValue(0.4)
        self.assertEqual(w.preset.currentText(), "自定义")

    def test_small_window_keeps_start_and_settings_actions_on_correct_pages(self):
        w = self.window
        w.resize(1020, 720)
        self.qt.processEvents()
        self.assertTrue(w.start_button.isVisible())
        self.assertFalse(w.settings_actions.isVisible())
        w.navigate(1)
        self.qt.processEvents()
        self.assertTrue(w.settings_actions.isVisible())
        self.assertFalse(w.start_button.isVisible())

    def test_native_dialog_import_queue_remove_and_drag_drop(self):
        source = self.source()
        w = self.window
        with patch.object(desktop.QFileDialog, "getOpenFileNames", return_value=([str(source)], "")):
            w.add_button.click()
        self.assertEqual(w.sources, [source])
        w.table.selectRow(0)
        w.remove_button.click()
        self.assertEqual(w.sources, [])
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(source))])
        enter = QDragEnterEvent(QPoint(250, 180), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(w, enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(QPointF(250, 180), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(w, drop)
        self.assertEqual(w.sources, [source])

    def test_background_worker_updates_gui_and_restores_controls(self):
        w = self.window
        w.add_paths([self.source()])
        worker_class = desktop.Worker
        def fake_worker(sources, settings, parent):
            return worker_class(sources, settings, parent, runner_factory=self.runner)
        with patch.object(desktop, "Worker", side_effect=fake_worker):
            w.start_button.click()
        self.assertTrue(w.busy)
        self.assertFalse(w.add_button.isEnabled())
        self.assertTrue(w.stop_button.isEnabled())
        deadline = time.monotonic() + 15
        while w.busy and time.monotonic() < deadline:
            self.qt.processEvents()
            time.sleep(0.01)
        self.assertFalse(w.busy)
        self.assertTrue(w.start_button.isEnabled())
        self.assertEqual(w.cues.rowCount(), 1)
        self.assertEqual(w.records[str(w.sources[0])]["status"], "done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
