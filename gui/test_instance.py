"""Regression: a second real launch must restore the existing minimized window."""
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt5.QtCore import QLockFile
from PyQt5.QtWidgets import QApplication

from app import Window
from backend import DATA, Settings
from desktop_instance import activate_window, desktop_identity, instance_lock_path


class InstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def test_second_launch_restores_existing_window(self):
        # Use the production lock namespace and launch the actual application entry point.
        path = instance_lock_path(DATA)
        lock = QLockFile(str(path))
        lock.setStaleLockTime(0)
        self.assertTrue(lock.tryLock(), "Close only this desktop's test GUI before running the regression.")
        window = Window(Settings(), DATA / "verification" / "instance-test-settings.json")
        process = None
        try:
            window.showMinimized()
            self.qt.processEvents()
            self.assertTrue(window.isMinimized())
            process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("app.py"))],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                self.qt.processEvents()
                if not window.isMinimized() and process.poll() is not None:
                    break
                time.sleep(0.02)
            self.assertFalse(window.isMinimized(), "Second launch left the existing main window minimized.")
            self.assertEqual(process.poll(), 0, "Second launch displayed a blocking dialog instead of restoring the window.")
        finally:
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            window.close()
            lock.unlock()

    def test_desktop_user_and_session_have_separate_locks(self):
        normal = instance_lock_path(DATA, ("user", 1, "Default"))
        sandbox = instance_lock_path(DATA, ("sandbox", 1, "CodexSandboxDesktop-test"))
        other_desktop = instance_lock_path(DATA, ("user", 1, "other"))
        other_session = instance_lock_path(DATA, ("user", 2, "Default"))
        self.assertEqual(len({normal, sandbox, other_desktop, other_session}), 4)
        self.assertNotEqual(instance_lock_path(DATA), DATA / "desktop.lock")

    def test_live_legacy_lock_does_not_block_current_desktop(self):
        old = QLockFile(str(DATA / "desktop.lock"))
        current = QLockFile(str(instance_lock_path(DATA)))
        self.assertTrue(old.tryLock())
        try:
            self.assertTrue(current.tryLock())
            self.assertTrue(desktop_identity())
        finally:
            current.unlock()
            old.unlock()

    def test_window_outside_monitors_is_brought_back(self):
        window = Window(Settings(), DATA / "verification" / "instance-test-settings.json")
        try:
            window.move(50000, 50000)
            window.show()
            self.qt.processEvents()
            self.assertTrue(activate_window(os.getpid()))
            for _ in range(10):
                self.qt.processEvents()
                time.sleep(0.01)
            self.assertTrue(any(screen.availableGeometry().intersects(window.frameGeometry())
                                for screen in self.qt.screens()))
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
