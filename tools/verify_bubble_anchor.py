"""Measure and capture vertical dialogue in the production Qt/WebEngine player.

Uses an isolated profile and synthetic Qt clicks. No UAC or system cleanup.
The wall wait is shortened only by this test driver to exercise native bursts.
"""
import argparse, base64, io, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args(); out = a.output.resolve(); out.mkdir(parents=True, exist_ok=True)
os.environ.update(MEINIFENG_PROFILE_DIRECTORY=str(out/'profile'), MEINIFENG_DISABLE_AUTOSTART='1',
                  MEINIFENG_DISABLE_CLEAN_TASK='1', CUTEMAPLE_DISABLE_ACTIVITY='1')
import live2d_host
live2d_host.register_live2d_scheme(); live2d_host.APP_URL += '?diagnostics=1'
from PySide6.QtCore import QTimer, Qt, QPoint, QBuffer, QIODevice
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PIL import Image
from pet_app import PetWindow
from pet_core import PetSettings
app = QApplication([]); app.setQuitOnLastWindowClosed(False)
pet = PetWindow(app, PetSettings(autostart=False)); pet.move(500,250); pet.show()
cases = [('left_wait',3),('left_climb',3),('right_wait',3),('right_climb',3),
         ('left_top',6),('right_top',6),('swing_big',4),('swing_idle',5),
         ('ground',3),('scale_small',3),('scale_large',3)]
report = {'frames':[], 'errors':[], 'qtScaleFactor':os.environ.get('QT_SCALE_FACTOR','1'),
          'syntheticClicks':True, 'wallWaitShortenedByTest':True}
index = -1; entered = 0.; started = time.monotonic(); click_at = 0.; clicks = 0; pending = False; done = False
screenshots = set()


def stop():
    global done
    if done: return
    done = True; timer.stop()
    report['passed'] = not report['errors'] and index >= len(cases)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    pet._finalize_runtime(); pet.tray.hide(); pet._install_executor.shutdown(wait=False,cancel_futures=True)
    for w in (pet,pet.bubble,pet.monitor_button,pet.monitor_capsule,pet.details_panel):w.close()
    QTimer.singleShot(200, app.quit)


def enter(name):
    area = pet._screen_area()
    if name.endswith('_wait'):
        pet.move(500,area.top()+350); pet._attach_side(name.split('_')[0])
    elif name.endswith('_climb'):
        pet._climb_cadence.remaining = 0
    elif name.endswith('_top'):
        pet._attach_side(name.split('_')[0]); anchor = pet._model_anchor(name.split('_')[0])
        pet.move(pet.x(),area.top()-round((anchor or [0,.4])[1]*pet.height())+15)
        pet._motion_y = float(pet.y()); pet._climb_cadence.remaining = 0
    elif name.startswith('swing'):
        pet.move(area.center().x()-pet.width()//2,area.top()); pet._attach_top(name=='swing_big')
    elif name == 'ground':
        pet._set_ground_idle(); pet.move(area.center().x(),pet.y())
    else:
        pet.set_scale(.6 if name=='scale_small' else 1.8); pet._attach_top(False)


def capture(name):
    global pending
    if pending:return
    pending = True
    def receive(raw):
        global pending
        pending = False
        if done:return
        try:
            v=json.loads(raw); assert v['visiblePixels']>100 and v['glError']==0
            anchor=pet._bubble_anchor_rect(); rect=pet.bubble.geometry(); area=pet._screen_area()
            assert area.contains(rect), (name,rect.getRect())
            above=anchor.top()-rect.bottom()-1; below=rect.top()-anchor.bottom()-1
            assert above==8 or below==8, (name,above,below)
            report['frames'].append({'case':name,'state':pet.state,'base':pet.base_mode,
                'aboveGap':above,'belowGap':below,'bubble':list(rect.getRect()),'anchor':list(anchor.getRect()),
                'window':list(pet.geometry().getRect()),'geometry':v['geometry'],'playback':v['playback']})
            if name not in screenshots:
                screenshots.add(name)
                # Preserve actual relative positions, showing the visible screen intersection.
                union=pet.geometry().united(rect).intersected(area).adjusted(-8,-8,8,8)
                canvas=Image.new('RGBA',(union.width(),union.height()),'#edf2f5')
                model=Image.open(io.BytesIO(base64.b64decode(v['png'].split(',',1)[1]))).convert('RGBA')
                model=model.resize((pet.width(),pet.height()))
                canvas.alpha_composite(model,(pet.x()-union.x(),pet.y()-union.y()))
                buffer=QBuffer();buffer.open(QIODevice.WriteOnly);pet.bubble.grab().save(buffer,'PNG')
                bubble=Image.open(io.BytesIO(bytes(buffer.data()))).convert('RGBA').resize((rect.width(),rect.height()))
                canvas.alpha_composite(bubble,(rect.x()-union.x(),rect.y()-union.y()))
                canvas.convert('RGB').save(out/(name+'.png'))
        except Exception as e:
            report['errors'].append(repr(e));stop()
    pet.live2d_host.page.runJavaScript('JSON.stringify(window.__cutemapleCapture())',receive)


def tick():
    global index,entered,click_at,clicks
    try:
        now=time.monotonic()
        if now-started>90:raise AssertionError('Timed out')
        if not pet._presentation_ready:return
        if index<0 or now-entered>=cases[index][1]:
            if pending:return
            index+=1
            if index==len(cases):stop();return
            entered=now;click_at=now+.6;enter(cases[index][0]);return
        if now>=click_at:
            head=pet._renderer_geometry.get('headBounds',[.4,.2,.2,.2])
            point=QPoint(round((head[0]+head[2]/2)*pet.width()),round((head[1]+head[3]/2)*pet.height()))
            if clicks%2:QTest.mouseDClick(pet,Qt.LeftButton,pos=point)
            else:QTest.mouseClick(pet,Qt.LeftButton,pos=point)
            clicks+=1;click_at=now+2
        if now-entered>1 and pet.bubble.isVisible():capture(cases[index][0])
    except Exception as e:
        report['errors'].append(repr(e));stop()

timer=QTimer();timer.setInterval(120);timer.timeout.connect(tick);timer.start()
app.exec();print(json.dumps({'passed':report['passed'],'errors':report['errors'],'frames':len(report['frames'])}))
raise SystemExit(0 if report['passed'] else 1)
