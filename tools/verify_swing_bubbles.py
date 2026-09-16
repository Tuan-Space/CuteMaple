"""Capture the actual Qt/WebEngine swing and bubble UI in an isolated profile.

Cleanup replies are explicitly simulated; no authorization or system cleanup.
"""
from pathlib import Path
import argparse
import base64
import io
import json
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=True)
os.environ['MEINIFENG_PROFILE_DIRECTORY'] = str(out/'profile')
os.environ['MEINIFENG_DISABLE_AUTOSTART'] = '1'
os.environ['MEINIFENG_DISABLE_CLEAN_TASK'] = '1'
os.environ['CUTEMAPLE_DISABLE_ACTIVITY'] = '1'
import live2d_host
live2d_host.register_live2d_scheme()
live2d_host.APP_URL += '?diagnostics=1'
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PIL import Image, ImageDraw
from pet_core import PetSettings
from pet_app import PetWindow
import monitor_ui
from diagnostics import initialize
initialize()
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
pet = PetWindow(app, PetSettings(autostart=False))
pet.move(650, 100)
pet.show()
report = {'errors': [], 'frames': [], 'cleanupBackendSimulated': True}
images = []
started = time.monotonic()
phase = 'load'
due = 0.
pending = False
captured_themes = False


def stop():
    global phase
    if phase == 'done':
        return
    phase = 'done'
    timer.stop()
    report['passed'] = not report['errors'] and len(report['frames']) > 100
    if images:
        images[0].save(out/'swing-review.gif', save_all=True, append_images=images[1:], duration=160, loop=0)
        chosen = [images[min(len(images)-1, i*len(images)//8)] for i in range(8)]
        strip = Image.new('RGB', (chosen[0].width*4, chosen[0].height*2), 'white')
        for i, im in enumerate(chosen):
            strip.paste(im, (i%4*im.width, i//4*im.height))
        strip.save(out/'swing-review.jpg')
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    pet._finalize_runtime()
    pet.tray.hide()
    pet._install_executor.shutdown(wait=False, cancel_futures=True)
    for w in (pet, pet.bubble, pet.monitor_button, pet.monitor_capsule, pet.details_panel):
        w.close()
    QTimer.singleShot(300, app.quit)


def capture():
    global pending
    if pending:
        return
    pending = True
    def received(raw):
        global pending
        pending = False
        if phase == 'done':
            return
        try:
            value = json.loads(raw)
            assert value['visiblePixels'] > 100 and value['glError'] == 0
            drawables = {d['id']: d['bounds'] for d in value['drawables']
                         if d['id'] in {'seat', 'rope_l', 'rope_r', 'hand_l_grip_palm', 'hand_r_grip_palm'}}
            report['frames'].append({'elapsed': time.monotonic()-started, 'stage': phase, 'state': pet.state,
                                     'position': [pet.x(), pet.y()], 'playback': value['playback'],
                                     'swing': value['parameters']['ParamSwing'], 'drawables': drawables})
            if len(report['frames']) % 2 == 0:
                im = Image.open(io.BytesIO(base64.b64decode(value['png'].split(',', 1)[1]))).convert('RGBA')
                backgrounds = ['white', '#181818', '#b3b3b3']
                bg = Image.new('RGBA', im.size, backgrounds[len(images)//20 % 3])
                if len(images)//20 % 3 == 2:
                    painter = ImageDraw.Draw(bg)
                    for y in range(0, im.height, 16):
                        for x in range(0, im.width, 16):
                            if (x//16+y//16) % 2:
                                painter.rectangle((x,y,x+15,y+15),fill='#eeeeee')
                bg.alpha_composite(im)
                images.append(bg.convert('RGB'))
        except Exception as exc:
            report['errors'].append(repr(exc)); stop()
    pet.live2d_host.page.runJavaScript('JSON.stringify(window.__cutemapleCapture())', received)


def tick():
    global phase, due, captured_themes
    now = time.monotonic()
    try:
        if now-started > 65:
            raise AssertionError('Native UI verification timed out: '+phase)
        if phase == 'load':
            if not pet._presentation_ready:
                return
            pet._attach_top(True)
            phase = 'intro'; due = now+7
        if phase == 'intro' and pet.state == 'swing_idle':
            phase = 'idle'; due = now+10
        if phase == 'idle' and now >= due:
            phase = 'petting'; due = now+4
            pet._start_temporary('petting')
        if phase == 'petting' and now >= due:
            phase = 'paused'; due = now+1
            pet.settings.paused = True; pet._sync_runtime_pause()
        if phase == 'paused' and now >= due:
            phase = 'resumed'; due = now+2
            pet.settings.paused = False; pet._sync_runtime_pause(); pet._resume_activity()
        if phase == 'resumed' and now >= due:
            phase = 'hidden'; due = now+1; pet.hide_pet()
        if phase == 'hidden' and now >= due:
            phase = 'restored'; due = now+2; pet.show_pet()
        if phase == 'restored' and now >= due:
            phase = 'bubbles'; due = now+1
        if phase == 'bubbles' and not captured_themes:
            captured_themes = True
            original = monitor_ui.system_theme
            try:
                for name, colors in [('light', monitor_ui.LIGHT), ('dark', monitor_ui.DARK)]:
                    monitor_ui.system_theme = lambda: colors
                    pet.bubble.theme.apply()
                    for status, extra in [('authorizing', {}), ('running', {'current_step': 'empty_working_sets'}),
                                          ('succeeded', {'terminal': True, 'processExitVerified': True}),
                                          ('security_blocked', {'message': '系统防护阻止本次清理，请在详情中查看原因。'})]:
                        pet._set_cleanup_progress({'status': status, **extra}, active=status in {'authorizing','running'})
                        pet.bubble.grab().save(str(out/f'bubble-{name}-{status}.png'))
            finally:
                monitor_ui.system_theme = original
                pet.bubble.theme.apply()
        if phase == 'bubbles' and now >= due:
            stop(); return
        capture()
    except Exception as exc:
        report['errors'].append(repr(exc)); stop()


timer = QTimer()
timer.setInterval(80)
timer.timeout.connect(tick)
timer.start()
app.exec()
print(json.dumps({k:report[k] for k in ('passed','errors')}, ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
