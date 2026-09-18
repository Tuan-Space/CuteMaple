"""Exercise real WebGL context loss and a fresh production-host page generation."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu-compositing --enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader --ignore-gpu-blocklist')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QT_OPENGL', 'software')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT));sys.path.insert(0,str(ROOT/"src"))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
import live2d_host


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    options = parser.parse_args()
    live2d_host.APP_URL += '?diagnostics=1'
    live2d_host.register_live2d_scheme()
    app = QApplication([])
    window = QWidget()
    window.resize(384, 384)
    host = live2d_host.Live2DHost(window, ROOT)
    host.view.resize(window.size())
    result = {'readyGenerations': [], 'expectedContextLoss': 0, 'pixelsAfterRecovery': 0, 'errors': []}
    done = False

    def finish(error=None):
        nonlocal done
        if done:
            return
        done = True
        if error:
            result['errors'].append(str(error))
        host.stop()
        window.close()
        app.quit()

    def captured(raw):
        try:
            value = json.loads(raw)
            result['pixelsAfterRecovery'] = value.get('visiblePixels', 0)
            if not result['pixelsAfterRecovery'] or value.get('glError'):
                return finish('Recovered renderer did not produce a valid native frame')
            finish()
        except (ValueError, TypeError, AttributeError) as error:
            finish(error)

    def lose_context():
        host.page.runJavaScript("(() => { const gl=document.querySelector('canvas').getContext('webgl'); const ext=gl.getExtension('WEBGL_lose_context'); if(!ext)return false; ext.loseContext(); return true; })()",
            lambda supported: None if supported else finish('WEBGL_lose_context is unavailable'))

    def event(value):
        if done:
            return
        if value['type'] == 'ready':
            result['readyGenerations'].append(value['generation'])
            host.view.show()
            host.send('play', name='idle', token=value['generation'], playback='loop')
            if len(result['readyGenerations']) == 1:
                QTimer.singleShot(250, lose_context)
            else:
                QTimer.singleShot(350, lambda: host.page.runJavaScript('JSON.stringify(window.__cutemapleCapture())', captured))
        elif value['type'] == 'error':
            if 'context was lost' in value.get('message', '') and result['expectedContextLoss'] == 0:
                result['expectedContextLoss'] += 1
                assert not host.ready and host._state == 'failed'
                QTimer.singleShot(150, host.load)
            else:
                finish(value.get('message', 'Unknown renderer failure'))

    host.event.connect(event)
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(lambda: finish('Recovery timed out'))
    deadline.start(25000)
    window.show()
    host.load()
    app.exec()
    deadline.stop()
    result['passed'] = (result['readyGenerations'] == [1, 2] and result['expectedContextLoss'] == 1
                        and result['pixelsAfterRecovery'] > 0 and not result['errors'])
    result['platform'] = os.environ['QT_QPA_PLATFORM']
    result['chromiumFlags'] = os.environ['QTWEBENGINE_CHROMIUM_FLAGS']
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
