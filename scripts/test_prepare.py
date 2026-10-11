"""Small-file regressions for installation, interrupted downloads and settings preservation."""
import hashlib
import errno
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile

import prepare


def spec(data):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class Response(io.BytesIO):
    def __init__(self, data, status=200, content_range=None):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Range": content_range}


class Interrupted(Response):
    def read(self, size=-1):
        data = super().read(size)
        if not data:
            raise ConnectionResetError("connection interrupted")
        return data


class SetupTests(unittest.TestCase):
    def setUp(self):
        base = prepare.ROOT / ".gui" / "verification"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="setup-test-", dir=base)
        self.root = Path(self.temp.name).resolve()
        assert self.root.is_relative_to(base.resolve())
        self.addCleanup(self.temp.cleanup)
        self.cache = self.root / ".setup"
        self.cache.mkdir()
        self.target = self.root / "model.bin"
        self.data = b"a complete model file"
        self.info = spec(self.data)
        self.addCleanup(patch.stopall)
        patch("builtins.print").start()
        patch.object(prepare.time, "sleep").start()

    def test_complete_file_is_reused_without_network(self):
        self.target.write_bytes(self.data)
        with patch.object(prepare, "urlopen") as network:
            prepare.download("https://example.test/file", self.target, self.info)
        network.assert_not_called()

    def test_resume_requests_only_remaining_bytes(self):
        self.target.with_suffix(".bin.part").write_bytes(self.data[:5])
        def open_request(request, **kwargs):
            self.assertEqual(request.get_header("Range"), "bytes=5-")
            return Response(self.data[5:], 206, f"bytes 5-{len(self.data)-1}/{len(self.data)}")
        with patch.object(prepare, "urlopen", side_effect=open_request):
            prepare.download("https://example.test/file", self.target, self.info)
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertFalse(self.target.with_suffix(".bin.part").exists())

    def test_ignored_range_restarts_instead_of_appending(self):
        self.target.with_suffix(".bin.part").write_bytes(self.data[:5])
        with patch.object(prepare, "urlopen", return_value=Response(self.data)):
            prepare.download("https://example.test/file", self.target, self.info)
        self.assertEqual(self.target.read_bytes(), self.data)

    def test_rejected_range_can_restart_download(self):
        self.target.with_suffix(".bin.part").write_bytes(self.data[:5])
        responses = [HTTPError("url", 501, "unsupported range", {}, None), Response(self.data)]
        with patch.object(prepare, "urlopen", side_effect=responses) as network:
            prepare.download("https://example.test/file", self.target, self.info)
        self.assertIsNone(network.call_args_list[1].args[0].get_header("Range"))
        self.assertEqual(self.target.read_bytes(), self.data)

    def test_interrupted_download_keeps_bytes_for_next_run(self):
        def fail(request, **kwargs):
            offset = int(request.get_header("Range", "bytes=0-").split("=")[1][:-1])
            return Interrupted(self.data[offset:offset+2], 206,
                               f"bytes {offset}-{len(self.data)-1}/{len(self.data)}")
        with patch.object(prepare, "urlopen", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "下载未完成"):
                prepare.download("https://example.test/file", self.target, self.info)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.target.with_suffix(".bin.part").read_bytes(), self.data[:6])
        with patch.object(prepare, "urlopen", return_value=Response(
                self.data[6:], 206, f"bytes 6-{len(self.data)-1}/{len(self.data)}")):
            prepare.download("https://example.test/file", self.target, self.info)
        self.assertEqual(self.target.read_bytes(), self.data)

    def test_corrupt_download_never_replaces_existing_file(self):
        self.target.write_bytes(b"previous file")
        with patch.object(prepare, "urlopen", side_effect=lambda *a, **k: Response(b"x" * len(self.data))):
            with self.assertRaises(RuntimeError):
                prepare.download("https://example.test/file", self.target, self.info)
        self.assertEqual(self.target.read_bytes(), b"previous file")

    def test_incorrect_range_does_not_change_partial_file(self):
        partial = self.target.with_suffix(".bin.part")
        partial.write_bytes(self.data[:5])
        with patch.object(prepare, "urlopen", side_effect=lambda *a, **k: Response(
                self.data, 206, f"bytes 0-{len(self.data)-1}/{len(self.data)}")):
            with self.assertRaises(RuntimeError):
                prepare.download("https://example.test/file", self.target, self.info)
        self.assertEqual(partial.read_bytes(), self.data[:5])

    def test_git_blob_digest_checks_small_model_files(self):
        self.target.write_bytes(self.data)
        digest = hashlib.sha1(f"blob {len(self.data)}\0".encode() + self.data).hexdigest()
        self.assertTrue(prepare.matches(self.target, {"size": len(self.data), "git_sha1": digest}))

    def test_full_disk_stops_without_repeated_network_retries(self):
        with patch.object(prepare, "urlopen", return_value=Response(self.data)) as network, \
                patch.object(Path, "open", side_effect=OSError(errno.ENOSPC, "disk full")):
            with self.assertRaisesRegex(RuntimeError, "磁盘空间不足"):
                prepare.download("https://example.test/file", self.target, self.info)
        self.assertEqual(network.call_count, 1)

    def engine_fixture(self, extra=None):
        files = {"infer.exe": b"executable", "_internal/python310.dll": b"runtime",
                 "models/whisper_vad.onnx": b"vad", "models/whisper-base/preprocessor_config.json": b"{}"}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as package:
            for name, content in files.items():
                package.writestr("package/" + name, content)
            if extra:
                package.writestr(extra, b"bad")
        data = stream.getvalue()
        pieces = (data[:len(data)//2], data[len(data)//2:])
        resources = {"engine_url": "https://example.test/", "engines": {
            "test": {**spec(data), "parts": [spec(p) for p in pieces]}}}
        return data, pieces, resources

    def test_split_engine_download_is_joined_verified_and_installed(self):
        data, pieces, resources = self.engine_fixture()
        old = self.root / "engine"
        old.mkdir()
        (old / "user-notes.txt").write_text("keep")
        with patch.object(prepare, "RESOURCES", resources), patch.object(prepare, "urlopen",
                side_effect=[Response(p) for p in pieces]):
            prepare.install_engine("test", self.root, self.cache)
        self.assertTrue(prepare.engine_ready(old))
        backups = list(self.cache.glob("engine-backup-*"))
        self.assertEqual((backups[0] / "user-notes.txt").read_text(), "keep")
        self.assertFalse(list(self.cache.glob("*.zip*")))

    def test_complete_engine_is_not_replaced(self):
        data, pieces, resources = self.engine_fixture()
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            package.extractall(self.cache)
        (self.cache / "package").rename(self.root / "engine")
        with patch.object(prepare, "urlopen") as network:
            prepare.install_engine("test", self.root, self.cache)
        network.assert_not_called()

    def test_legacy_engine_is_renamed_without_download(self):
        data, pieces, resources = self.engine_fixture()
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            package.extractall(self.cache)
        legacy = self.root / "extracted"
        (self.cache / "package").rename(legacy)
        (legacy / "user-notes.txt").write_text("keep")
        with patch.object(prepare, "urlopen") as network:
            prepare.install_engine("test", self.root, self.cache)
        network.assert_not_called()
        self.assertFalse(legacy.exists())
        self.assertTrue(prepare.engine_ready(self.root / "engine"))
        self.assertEqual((self.root / "engine/user-notes.txt").read_text(), "keep")

    def test_switch_to_amd_installs_matching_engine_and_keeps_the_old_one(self):
        data, pieces, resources = self.engine_fixture()
        resources["engines"]["gfx110x_all"] = resources["engines"].pop("test")
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            package.extractall(self.cache)
        target = self.root / "engine"
        (self.cache / "package").rename(target)
        (target / "user-notes.txt").write_text("keep")
        with patch.object(prepare, "RESOURCES", resources), patch.object(prepare, "urlopen",
                side_effect=[Response(p) for p in pieces]) as network:
            prepare.install_engine("gfx110x_all", self.root, self.cache)
        self.assertEqual(network.call_count, 2)
        self.assertEqual((target / "engine-variant.txt").read_text(), "gfx110x_all")
        backup = next(self.cache.glob("engine-backup-*"))
        self.assertEqual((backup / "user-notes.txt").read_text(), "keep")
        with patch.object(prepare, "urlopen") as network:
            prepare.install_engine("gfx110x_all", self.root, self.cache)
            prepare.install_engine("cu118", self.root, self.cache, device="cpu")
        network.assert_not_called()

    def test_different_amd_architecture_requires_a_matching_package(self):
        data, pieces, resources = self.engine_fixture()
        resources["engines"]["gfx120x_all"] = resources["engines"].pop("test")
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            package.extractall(self.cache)
        target = self.root / "engine"
        (self.cache / "package").rename(target)
        (target / "engine-variant.txt").write_text("gfx101x_dgpu")
        with patch.object(prepare, "RESOURCES", resources), patch.object(prepare, "urlopen",
                side_effect=[Response(p) for p in pieces]):
            prepare.install_engine("gfx120x_all", self.root, self.cache)
        self.assertEqual((target / "engine-variant.txt").read_text(), "gfx120x_all")

    def test_archive_path_cannot_escape_staging(self):
        data, pieces, resources = self.engine_fixture("../escaped.txt")
        with patch.object(prepare, "RESOURCES", resources), patch.object(prepare, "urlopen",
                side_effect=[Response(p) for p in pieces]):
            with self.assertRaisesRegex(RuntimeError, "文件路径"):
                prepare.install_engine("test", self.root, self.cache)
        self.assertFalse((self.cache / "escaped.txt").exists())
        self.assertFalse((self.root / "engine").exists())

    def model_fixture(self):
        files = {"model.bin": b"weights", "config.json": b"{}", "tokenizer.json": b"{}",
                 "preprocessor_config.json": b"{}", "vocabulary.json": b"{}", "README.md": b"card"}
        model = {"title": "测试模型", "repo": "author/model", "revision": "fixed",
                 "files": {name: spec(body) for name, body in files.items()}}
        return files, {"models": {"translate": model}}

    def test_existing_model_outside_default_folder_is_reused(self):
        files, resources = self.model_fixture()
        existing = self.root / "old-model-location"
        existing.mkdir()
        for name, body in files.items():
            (existing / name).write_bytes(body)
        settings = {"translate_model": str(existing)}
        with patch.object(prepare, "RESOURCES", resources), patch.object(prepare, "urlopen") as network:
            self.assertEqual(prepare.prepare_model("translate", settings, self.root), existing)
        network.assert_not_called()
        self.assertFalse((self.root / "models").exists())

    def test_different_user_model_is_not_overwritten_or_reselected(self):
        files, resources = self.model_fixture()
        existing = self.root / "custom"
        existing.mkdir()
        (existing / "model.bin").write_bytes(b"custom model")
        with patch.object(prepare, "RESOURCES", resources), patch.object(prepare, "urlopen") as network:
            with self.assertRaisesRegex(RuntimeError, "原文件和设置已保留"):
                prepare.prepare_model("translate", {"translate_model": str(existing)}, self.root)
        self.assertEqual((existing / "model.bin").read_bytes(), b"custom model")
        network.assert_not_called()

    def test_fresh_setup_configures_downloaded_model_and_cpu(self):
        files, resources = self.model_fixture()
        with patch.object(prepare, "RESOURCES", resources), patch.object(prepare, "detect_engine", return_value=("cu118", "cpu")), \
                patch.object(prepare, "prepare_gui"), patch.object(prepare, "install_engine"), \
                patch.object(prepare, "urlopen", side_effect=lambda request, **kw: Response(files[request.full_url.rsplit('/', 1)[1].split('?')[0]])):
            prepare.prepare(self.root)
        saved = json.loads((self.root / ".gui/settings.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["device"], saved["compute"]), ("cpu", "int8"))
        self.assertEqual(Path(saved["translate_model"]), self.root / "models/model")
        self.assertNotIn("transcribe_model", saved)
        self.assertEqual((self.root / "models/model/model.bin").read_bytes(), b"weights")

    def test_fresh_amd_setup_downloads_and_reuses_engine_and_model(self):
        data, _, resources = self.engine_fixture()
        resources["engines"] = {"gfx110x_all": {**spec(data), "parts": [spec(data)]}}
        files, models = self.model_fixture()
        resources.update(models)
        def response(request, **kwargs):
            name = request.full_url.rsplit("/", 1)[1].split("?")[0]
            return Response(data if name.endswith(".zip.0000") else files[name])
        with patch.object(prepare, "RESOURCES", resources), patch.object(prepare, "prepare_gui"), \
                patch.object(prepare.shutil, "which", return_value=None), \
                patch.object(prepare.subprocess, "check_output", return_value=b"AMD Radeon RX 7800 XT"), \
                patch.object(prepare, "urlopen", side_effect=response):
            prepare.prepare(self.root)
            with patch.object(prepare, "urlopen") as network:
                prepare.prepare(self.root)
                network.assert_not_called()
        saved = json.loads((self.root / ".gui/settings.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["device"], saved["compute"]), ("amd", "auto"))
        self.assertTrue(prepare.engine_ready(self.root / "engine"))
        self.assertEqual((self.root / "engine/engine-variant.txt").read_text(), "gfx110x_all")
        self.assertEqual((Path(saved["translate_model"]) / "model.bin").read_bytes(), b"weights")

    def test_gui_changes_during_download_are_preserved(self):
        path = self.root / ".gui/settings.json"
        path.parent.mkdir()
        original = {"translate_model": "old", "output": "original", "threshold": 0.5,
                    "device": "cpu", "compute": "float32"}
        path.write_text(json.dumps(original))
        changed = dict(original, output="changed by user", threshold=0.35, translate_model="new user choice")
        def complete(*args):
            path.write_text(json.dumps(changed))
            return self.root / "models/downloaded"
        with patch.object(prepare, "detect_engine", return_value=("cu122", "cuda")) as detect, \
                patch.object(prepare, "prepare_gui"), patch.object(prepare, "install_engine"), \
                patch.object(prepare, "prepare_model", side_effect=complete):
            prepare.prepare(self.root)
        detect.assert_not_called()
        self.assertEqual(json.loads(path.read_text()), changed)

    def test_amd_setup_updates_stale_gpu_choice_without_changing_other_settings(self):
        path = self.root / ".gui/settings.json"
        path.parent.mkdir()
        for original in ({}, {"device": "cuda", "compute": "int8_float16",
                              "output": "keep", "threshold": 0.35}):
            with self.subTest(original=original):
                path.write_text(json.dumps(original))
                with patch.object(prepare, "detect_engine", return_value=("gfx110x_all", "amd")), \
                        patch.object(prepare, "prepare_gui"), patch.object(prepare, "install_engine"), \
                        patch.object(prepare, "prepare_model", return_value=self.root / "models/zh"):
                    prepare.prepare(self.root)
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual((saved["device"], saved["compute"]), ("amd", "auto"))
                for key in ("output", "threshold"):
                    self.assertEqual(saved.get(key), original.get(key))

    def test_user_device_change_during_amd_setup_is_preserved(self):
        path = self.root / ".gui/settings.json"
        path.parent.mkdir()
        path.write_text('{"device": "cuda", "compute": "float16"}')
        def complete(*args):
            path.write_text('{"device": "cpu", "compute": "int8"}')
            return self.root / "models/zh"
        with patch.object(prepare, "detect_engine", return_value=("gfx110x_all", "amd")), \
                patch.object(prepare, "prepare_gui"), patch.object(prepare, "install_engine"), \
                patch.object(prepare, "prepare_model", side_effect=complete):
            prepare.prepare(self.root)
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((saved["device"], saved["compute"]), ("cpu", "int8"))

    def test_failed_download_does_not_save_half_prepared_settings(self):
        path = self.root / ".gui/settings.json"
        path.parent.mkdir()
        path.write_text('{"output": "keep", "device": "cuda", "compute": "float16"}')
        before = path.read_bytes()
        with patch.object(prepare, "detect_engine", return_value=("cu122", "cuda")), \
                patch.object(prepare, "prepare_gui"), patch.object(prepare, "install_engine"), \
                patch.object(prepare, "prepare_model", side_effect=[self.root / 'models/zh', RuntimeError("interrupted")]):
            with self.assertRaises(RuntimeError):
                prepare.prepare(self.root, both=True)
        self.assertEqual(path.read_bytes(), before)

    def test_device_selection_and_driver_requirement(self):
        for gpu, version, expected in (("", (0, 0), ("cu118", "cpu")),
                ("NVIDIA GTX 1080", (13, 2), ("cu118", "cuda")),
                ("NVIDIA GTX 1650", (12, 2), ("cu118", "cuda")),
                ("NVIDIA RTX 2060", (11, 8), ("cu118", "cuda")),
                ("NVIDIA RTX 3060", (12, 2), ("cu122", "cuda")),
                ("NVIDIA RTX 4060 Laptop GPU", (13, 2), ("cu122", "cuda")),
                ("NVIDIA RTX 5090", (13, 2), ("cu128", "cuda"))):
            with self.subTest(gpu=gpu):
                self.assertEqual(prepare.select_engine(gpu, version), expected)
        with self.assertRaisesRegex(RuntimeError, "更新"):
            prepare.select_engine("NVIDIA RTX 5090", (12, 2))

    def test_amd_models_select_the_matching_upstream_package(self):
        for gpu, expected in (
                ("AMD Radeon RX 5700 XT", "gfx101x_dgpu"),
                ("AMD Radeon RX 5500M", "gfx101x_dgpu"),
                ("AMD Radeon(TM) RX 6600 XT", "gfx103x_dgpu"),
                ("AMD Radeon RX 6800 XT", "gfx103x_dgpu"),
                ("AMD Radeon RX 6850M XT", "gfx103x_dgpu"),
                ("AMD Radeon RX 7900 XTX", "gfx110x_all"),
                ("AMD Radeon RX 7650 GRE", "gfx110x_all"),
                ("AMD Radeon RX 9070 XT", "gfx120x_all"),
                ("AMD Radeon RX 580", None),
                ("AMD Radeon 890M", None),
                ("AMD Radeon RX 9999", None),
                ("Intel UHD Graphics", None)):
            with self.subTest(gpu=gpu):
                self.assertEqual(prepare.select_amd_engine(gpu), expected)

    def test_detection_uses_supported_amd_and_falls_back_for_unsupported_adapters(self):
        for names, expected in (
                (b"AMD Radeon 890M\nAMD Radeon RX 7800 XT\n", ("gfx110x_all", "amd")),
                (b"AMD Radeon RX 580\n", ("cu118", "cpu")),
                (b"Intel UHD Graphics\n", ("cu118", "cpu"))):
            with self.subTest(names=names), patch.object(prepare.shutil, "which", return_value=None), \
                    patch.object(prepare.subprocess, "check_output", return_value=names):
                self.assertEqual(prepare.detect_engine(), expected)

    def test_nvidia_keeps_priority_unless_amd_is_selected(self):
        with patch.object(prepare.shutil, "which", return_value="nvidia-smi"), \
                patch.object(prepare.subprocess, "check_output", side_effect=[
                    b"NVIDIA GeForce RTX 5090\n", b"CUDA UMD Version: 13.4"]):
            self.assertEqual(prepare.detect_engine(), ("cu128", "cuda"))
        with patch.object(prepare.shutil, "which", return_value="nvidia-smi"), \
                patch.object(prepare.subprocess, "check_output", return_value=b"AMD Radeon RX 9070 XT") as command:
            self.assertEqual(prepare.detect_engine("amd"), ("gfx120x_all", "amd"))
        self.assertIn("powershell", command.call_args.args[0][0].lower())

    def test_stale_nvidia_driver_can_fall_back_to_amd(self):
        for adapters, expected in ((b"AMD Radeon RX 6600", ("gfx103x_dgpu", "amd")),
                                   (b"Intel UHD Graphics", None)):
            with self.subTest(adapters=adapters), patch.object(prepare.shutil, "which", return_value="nvidia-smi"), \
                    patch.object(prepare.subprocess, "check_output", side_effect=[OSError("driver unavailable"), adapters]):
                if expected:
                    self.assertEqual(prepare.detect_engine(), expected)
                else:
                    with self.assertRaisesRegex(RuntimeError, "NVIDIA"):
                        prepare.detect_engine()


if __name__ == "__main__":
    unittest.main(verbosity=2)
