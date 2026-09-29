"""Explicit local GPU smoke: python smoke.py <short-audio-file>. Outputs stay in .gui."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import json
import time
from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from app import Window
from backend import DATA, Settings


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        raise SystemExit("Usage: smoke.py <existing-short-audio-file>")
    destination = DATA / "verification" / datetime.now().strftime("smoke-%Y%m%d-%H%M%S")
    destination.mkdir(parents=True)
    settings = Settings(output=str(destination / "subtitles"))
    app = QApplication([])
    app.setStyle("Fusion")
    window = Window(settings, destination / "settings.json")
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.show()
    window.add_paths([sys.argv[1]])
    window.start_button.click()
    app.processEvents()
    window.grab().save(str(destination / "running.png"))
    deadline = time.monotonic() + 180
    events = 0
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        events += 1
        time.sleep(0.02)
    if window.busy:
        window.stop_run()
        stop_deadline = time.monotonic() + 20
        while window.busy and time.monotonic() < stop_deadline:
            app.processEvents()
            time.sleep(0.02)
        raise RuntimeError("GPU smoke exceeded 180 seconds; stop requested")
    app.processEvents()
    window.grab().save(str(destination / "completed.png"))
    window.navigate(1)
    app.processEvents()
    window.grab().save(str(destination / "parameters.png"))
    statuses = [record.get("status") for record in window.records.values()]
    report = {"statuses": statuses, "preview_cues": window.cues.rowCount(),
              "ui_event_loop_iterations": events, "engine_log_dir": str(window.run_dir),
              "output": str(destination), "controls_restored": window.start_button.isEnabled()}
    (destination / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True), flush=True)
    window.close()
    if statuses != ["done"] or not report["preview_cues"] or not report["controls_restored"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
