"""Explicit headless verification of the resources inside this application build.

This path never creates a PetWindow, loads user settings, starts sensors, or
installs startup/cleanup tasks. It is invoked only with --verify-live2d.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path


def run(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=40)
    options = parser.parse_args(arguments)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu-compositing --enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader --ignore-gpu-blocklist")
    os.environ.setdefault("QT_QUICK_BACKEND", "software")
    os.environ.setdefault("QT_OPENGL", "software")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QWidget
    import live2d_host
    from pet_core import ANIMATIONS, resource_root

    live2d_host.APP_URL += "?diagnostics=1"
    live2d_host.register_live2d_scheme()
    app = QApplication([])
    root = resource_root()
    result = {"ready": False, "nativeIdleCycles": 0, "nativeLandingFinished": False,
              "geometry": False, "visiblePixels": 0, "errors": [], "states": [],
              "resourceRoot": str(root), "settingsAndSensorsLoaded": False}
    model = root / "assets/live2d/Maple/Maple.model3.json"
    result["modelSha256"] = hashlib.sha256(model.read_bytes()).hexdigest() if model.exists() else None
    parent = QWidget()
    parent.resize(512, 512)
    host = live2d_host.Live2DHost(parent, root)
    host.view.resize(512, 512)

    def fail(message):
        result["errors"].append(str(message))
        app.quit()

    def captured(raw):
        try:
            capture = json.loads(raw) if isinstance(raw, str) else raw
            result["visiblePixels"] = capture.get("visiblePixels", 0)
            result["geometryCapture"] = capture.get("geometry")
            if capture.get("glError"):
                result["errors"].append(f"WebGL error {capture['glError']}")
            png = capture.get("png", "")
            if png:
                options.report.parent.mkdir(parents=True, exist_ok=True)
                screenshot = options.report.with_suffix(".png")
                screenshot.write_bytes(base64.b64decode(png.split(",", 1)[1]))
                result["screenshot"] = str(screenshot.resolve())
        except (ValueError, TypeError, AttributeError, IndexError) as error:
            result["errors"].append(f"Invalid renderer capture: {error}")
        app.quit()

    def event(value):
        kind = value.get("type")
        if kind == "ready":
            result["ready"], result["sdk"] = True, value.get("sdk")
            result["states"] = value.get("states", [])
            missing = {name for name in ANIMATIONS if not name.startswith('clean_')} - set(result["states"])
            if missing:
                return fail(f"Missing packaged motion states: {sorted(missing)}")
            host.view.show()
            host.send("gaze", x=0, y=0, enabled=False)
            host.send("play", name="idle", token=901, playback="loop", fade=.15)
        elif kind == "geometry":
            bounds = value.get("bounds", [])
            result["geometry"] = bool(len(bounds) == 4 and bounds[2] > 0 and bounds[3] > 0)
        elif kind == "cycle" and value.get("token") == 901:
            result["nativeIdleCycles"] += 1
            host.send("play", name="land", token=902, playback="one_shot", fade=.15)
        elif kind == "finished" and value.get("token") == 902:
            result["nativeLandingFinished"] = True
            host.page.runJavaScript("JSON.stringify(window.__cutemapleCapture())", captured)
        elif kind == "error":
            fail(value.get("message", "Unknown renderer error"))

    host.event.connect(event)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: fail("Packaged Live2D verification timed out"))
    timer.start(max(5, min(options.timeout, 180)) * 1000)
    parent.show()
    host.load()
    app.exec()
    timer.stop()
    host.stop()
    parent.close()
    app.processEvents()
    result["passed"] = all((result["ready"], result["nativeIdleCycles"], result["nativeLandingFinished"],
                             result["geometry"], result["visiblePixels"])) and not result["errors"]
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return 0 if result["passed"] else 1
