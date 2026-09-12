"""Headless check of real Cubism loading/animation in the production Qt host.

Pass a model3.json path. Model files are copied to an isolated temporary bundle;
no sample data is added to the application assets. This check creates no desktop
window because Qt uses the offscreen platform.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu-compositing --enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader --ignore-gpu-blocklist")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_OPENGL", "software")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
import live2d_host
from live2d_host import Live2DHost, register_live2d_scheme


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("--timeout", type=int, default=35)
    parser.add_argument("--screenshot", type=Path)
    options = parser.parse_args()
    source = options.model.resolve(strict=True)
    live2d_host.APP_URL += "?diagnostics=1"
    register_live2d_scheme()
    app = QApplication([])
    outcome = {"ready": False, "cycles": 0, "finished": False, "geometry": False, "visiblePixels": 0, "errors": []}
    with tempfile.TemporaryDirectory(prefix="cutemaple-web-check-") as work:
        bundle = Path(work)
        shutil.copytree(ROOT / "web" / "dist", bundle / "web" / "dist")
        destination = bundle / "assets" / "live2d" / "Maple"
        shutil.copytree(source.parent, destination)
        if source.name != "Maple.model3.json":
            shutil.copyfile(source, destination / "Maple.model3.json")
        parent = QWidget()
        parent.resize(512, 512)
        host = Live2DHost(parent, bundle)
        host.view.resize(512, 512)
        states: list[str] = []

        def one_shot() -> None:
            host.send("pause", paused=False)
            host.send("play", name=states[-1], token=92, playback="one_shot", fade=0.15)

        def captured(value) -> None:
            if isinstance(value, str) and value:
                value = json.loads(value)
            if isinstance(value, dict):
                outcome["visiblePixels"] = value.get("visiblePixels", 0)
                outcome["finalGeometry"] = value.get("geometry")
                if value.get("glError"):
                    outcome["errors"].append(f"WebGL error {value['glError']}")
                if options.screenshot and value.get("png"):
                    options.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    options.screenshot.write_bytes(base64.b64decode(value["png"].split(",", 1)[1]))
            else:
                outcome["errors"].append(f"No diagnostic result: {value!r}")
            app.quit()

        def event(value: dict) -> None:
            nonlocal states
            kind = value.get("type")
            if kind != "geometry":
                print(json.dumps(value, ensure_ascii=False), flush=True)
            if kind == "ready":
                outcome["ready"] = True
                states = value.get("states", [])
                if not states:
                    outcome["errors"].append("No native motions")
                    app.quit()
                    return
                host.view.show()
                host.send("play", name="idle" if "idle" in states else states[0], token=91,
                          playback="loop", fade=0.15)
            elif kind == "geometry":
                bounds = value.get("bounds", [])
                outcome["geometry"] = bool(len(bounds) == 4 and bounds[2] > 0 and bounds[3] > 0)
            elif kind == "cycle" and value.get("token") == 91:
                outcome["cycles"] += 1
                host.send("pause", paused=True)
                QTimer.singleShot(200, one_shot)
            elif kind == "finished" and value.get("token") == 92:
                outcome["finished"] = True
                # Force draw and read in one JavaScript call, before the compositor
                # clears the non-preserved drawing buffer.
                host.page.runJavaScript("JSON.stringify(window.__cutemapleCapture())", captured)
            elif kind == "error":
                outcome["errors"].append(value.get("message", "unknown"))
                app.quit()

        host.event.connect(event)
        parent.show()
        host.load()
        QTimer.singleShot(options.timeout * 1000, app.quit)
        app.exec()
        host.stop()
        parent.close()
        app.processEvents()
    print(json.dumps(outcome, ensure_ascii=False), flush=True)
    return 0 if all((outcome["ready"], outcome["cycles"], outcome["finished"], outcome["geometry"], outcome["visiblePixels"])) and not outcome["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
