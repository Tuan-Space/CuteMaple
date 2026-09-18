"""Exercise the real Qt/WebEngine startup and all retained native states in an isolated profile."""
from pathlib import Path
import os,sys,json,time,argparse
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));sys.path.insert(0,str(root/"src"))
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
os.environ['MEINIFENG_PROFILE_DIRECTORY']=str(out/'profile');os.environ['MEINIFENG_DISABLE_AUTOSTART']='1';os.environ['MEINIFENG_DISABLE_CLEAN_TASK']='1'
from live2d_host import register_live2d_scheme
register_live2d_scheme()
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from pet_core import PetSettings,ANIMATIONS
import pet_app
from diagnostics import initialize
initialize()
app=QApplication([]);app.setQuitOnLastWindowClosed(False)
pet=pet_app.PetWindow(app,PetSettings());pet.move(450,250)
report={'errors':[],'states':[],'events':[]};start=time.monotonic();phase='initial';index=0;due=0.;last_state=None
original=pet._on_renderer_event
def receive(e):
    report['events'].append({k:e.get(k) for k in ('type','generation','token','name')})
    try:original(e)
    except Exception as exc:report['errors'].append(repr(exc))
pet._on_renderer_event=receive
pet.show();pet.grab().save(str(out/'loading.png'))
report['loadingVisible']=pet.loading_panel.isVisible();report['legacyWidgetAbsent']=not hasattr(pet,'sprite')
names=list(ANIMATIONS)+sorted(pet_app.TOP_TRANSITIONS)
def stop():
    report['passed']=not report['errors'] and len(report['states'])==19 and report.get('retryPresented',False)
    pet._finalize_runtime();pet.tray.hide();pet._install_executor.shutdown(wait=False,cancel_futures=True)
    for w in (pet,pet.bubble,pet.monitor_button,pet.monitor_capsule,pet.details_panel):w.close()
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    QTimer.singleShot(300,app.quit)
def tick():
    global phase,index,due,last_state
    now=time.monotonic()
    try:
        if now-start>95:raise AssertionError('Timed out: '+phase)
        if phase=='initial':
            if not pet._presentation_ready:return
            assert not pet.loading_panel.isVisible() and pet.live2d_host.view.isVisible()
            report['firstPresentedSeconds']=now-start;pet.grab().save(str(out/'first-native.png'))
            phase='states';due=now
        if phase=='states' and now>=due:
            if last_state:
                pet.grab().save(str(out/(last_state+'.png')))
                assert pet._renderer_geometry.get('bounds'),last_state
                report['states'].append(last_state)
            if index==len(names):
                phase='crash';pet.live2d_host.page.runJavaScript("document.getElementById('pet').getContext('webgl').getExtension('WEBGL_lose_context').loseContext()")
                due=now+2;return
            name=names[index];index+=1;last_state=name
            if name.startswith('climb_to_top_'):
                side=name.removeprefix('climb_to_top_');pet._attach_side(side)
                pet._begin_top_transition(endpoint={})
            elif name.startswith('climb_'):pet._attach_side(name.removeprefix('climb_'));pet.open_details_panel()
            else:
                pet.close_details_panel();pet._set_base('top_swing' if name.startswith('swing') else 'ground');pet.motion_mode=None;pet.start_state(name)
            due=now+.65
        elif phase=='crash' and now>=due:
            assert not pet._presentation_ready and pet.loading_panel.isVisible() and pet.retry_button.isVisible()
            pet.grab().save(str(out/'failure.png'));pet.retry_live2d();phase='retry'
        elif phase=='retry' and pet._presentation_ready:
            pet.grab().save(str(out/'retry.png'));report['retryPresented']=True;phase='done';timer.stop();stop()
    except Exception as e:
        report['errors'].append(repr(e));phase='done';timer.stop();stop()
timer=QTimer();timer.timeout.connect(tick);timer.start(30)
app.exec();print(json.dumps({k:report[k] for k in ('passed','errors','states')},ensure_ascii=False));raise SystemExit(0 if report['passed'] else 1)
