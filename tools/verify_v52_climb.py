"""Record the production pet's v5.2 wall cadence, without UAC or system cleanup."""
from __future__ import annotations
import argparse, base64, io, json, os, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"src"))

def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,required=True)
    output=parser.parse_args().output.resolve(); output.mkdir(parents=True,exist_ok=False)
    os.environ.update(MEINIFENG_PROFILE_DIRECTORY=str(output/'profile'),MEINIFENG_DISABLE_AUTOSTART='1',
        MEINIFENG_DISABLE_CLEAN_TASK='1',CUTEMAPLE_DISABLE_ACTIVITY='1')
    os.environ.pop('QT_QPA_PLATFORM',None)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from PIL import Image
    import live2d_host, pet_app
    from pet_core import PetSettings
    from tools.record_maple_preview import resource_snapshot, review_background
    os.environ.pop('QT_QPA_PLATFORM',None)  # Preview utility defaults to offscreen; this test needs PetWindow runtime.
    live2d_host.register_live2d_scheme(); live2d_host.APP_URL+='?diagnostics=1'
    app=QApplication([]); pet=pet_app.PetWindow(app,PetSettings(autostart=False,roaming_enabled=False))
    pet.show(); pet.idle_timer.stop(); pet.behavior_timer.stop()
    frames=[]; rows=[]; events=[]; errors=[]; phase=None; started=0.; done_at=None; pending=False; panel_used=False; panel_close=None; stopped=False
    hashes=resource_snapshot(ROOT/'assets/live2d/Maple',ROOT/'web/dist')
    def begin(side):
        nonlocal phase,started,done_at,panel_used,panel_close
        phase=side;started=time.monotonic();done_at=None;panel_used=False;panel_close=None
        pet.close_details_panel(); pet.settings.paused=False
        area=pet._screen_area(); pet.move(area.center().x(),area.bottom()-pet.height())
        pet._attach_side(side);events.append({'type':'attach','side':side,'time':started})
    def receive_event(value):
        nonlocal done_at
        if value.get('type') in ('climb-rest','error'):
            events.append(dict(value,wallTime=time.monotonic(),side=phase))
            if value['type']=='error': errors.append(value.get('message'));stop()
            elif phase: done_at=time.monotonic()
    def stop():
        nonlocal stopped
        stopped=True
        timer.stop();pet._finalize_runtime();pet.tray.hide()
        for widget in (pet.bubble,pet.monitor_button,pet.monitor_capsule,pet.details_panel):widget.close()
        pet.close();app.quit()
    def sample():
        nonlocal pending
        if pending or phase is None:return
        pending=True; side=phase;elapsed=time.monotonic()-started
        def captured(raw):
            nonlocal pending
            pending=False
            if stopped:return  # Qt may cancel a queued capture while destroying its page.
            try:
                value=json.loads(raw)
                if value['visiblePixels']<100 or value['glError']:raise ValueError('Empty native frame or GL error')
                image=Image.open(io.BytesIO(base64.b64decode(value['png'].split(',',1)[1]))).convert('RGBA')
                framepath=output/'frames'/f'{len(rows):04d}.png';framepath.parent.mkdir(exist_ok=True);image.save(framepath)
                bg=review_background(image.size,{'left':'black','right':'checkerboard'}[side]);bg.alpha_composite(image);frames.append(bg.convert('RGB'))
                rows.append({'side':side,'elapsed':elapsed,'state':pet.state,'position':[pet.x(),pet.y()],
                    'waiting':pet._climb_cadence.waiting,'remaining':pet._climb_cadence.remaining,'panel':pet.panel_pause_active,
                    'playback':value['playback'],'parameters':value['parameters'],'geometry':value['geometry'],
                    'frame':str(framepath.relative_to(output))})
            except Exception as exc:errors.append(str(exc));stop()
        pet.live2d_host.page.runJavaScript('JSON.stringify(window.__cutemapleCapture())',captured)
    def tick():
        nonlocal panel_used,panel_close
        if phase is None:
            if pet._presentation_ready:
                pet.live2d_host.event.connect(receive_event);begin('left')
            return
        now=time.monotonic();elapsed=now-started
        if not panel_used and ((phase=='left' and elapsed>5) or (phase=='right' and not pet._climb_cadence.waiting and pet._climb_progress%1>.25)):
            panel_used=True;panel_close=now+2;pet.open_details_panel()
            events.append({'type':'panel-open','side':phase,'time':now})
        if panel_close and now>=panel_close:
            panel_close=None;pet.close_details_panel();events.append({'type':'panel-close','side':phase,'time':now})
        # Explicit renderer preview gaze; does not move the user's cursor or read input.
        pet._send_renderer('gaze',x=1. if int(elapsed/4)%2 else -1.,y=1. if int(elapsed/6)%2 else -1.,enabled=True)
        sample()
        if done_at and now-done_at>2:
            if phase=='left':begin('right')
            else:stop()
    timer=QTimer();timer.setInterval(100);timer.timeout.connect(tick);timer.start()
    QTimer.singleShot(110000,lambda:(errors.append('Native wall sequence timed out'),stop()))
    app.exec()
    checks=[]
    for side in ('left','right'):
        selected=[r for r in rows if r['side']==side]
        bursts=[r for r in selected if not r['waiting'] and not r['panel']]
        checks.append({'name':side+'-wait30-and-native-two-cycles','passed':bool(bursts) and bursts[0]['elapsed']>=29.8 and any(e.get('type')=='climb-rest' and e.get('side')==side and e.get('cycles')==2 for e in events)})
        stable=[r for r in selected if r['waiting'] and r['elapsed']>1 and (not bursts or r['elapsed']<bursts[0]['elapsed'])]
        moving={'ParamAngleX','ParamAngleY','ParamAngleZ','ParamHeadTurn','ParamEyeBallX','ParamEyeBallY','ParamEyeLOpen','ParamEyeROpen'}
        variation={k:max(r['parameters'][k] for r in stable)-min(r['parameters'][k] for r in stable) for k in stable[0]['parameters']} if stable else {}
        body={k:v for k,v in variation.items() if k not in moving and v>1e-5}
        checks.append({'name':side+'-rest-body-and-window-fixed','passed':bool(stable) and not body and len({tuple(r['position']) for r in stable})==1,'unexpectedParameterChanges':body})
        checks.append({'name':side+'-head-and-eyes-alive','passed':variation.get('ParamAngleX',0)>1 and variation.get('ParamEyeLOpen',0)>.2})
        held=[r for r in selected if r['panel']]
        checks.append({'name':side+'-panel-freezes-phase-and-window','passed':len(held)>4 and len({tuple(r['position']) for r in held})==1 and max(r['geometry'].get('climbPhase',0) for r in held)-min(r['geometry'].get('climbPhase',0) for r in held)<1e-5})
    if resource_snapshot(ROOT/'assets/live2d/Maple',ROOT/'web/dist')!=hashes:errors.append('Runtime resources changed during recording')
    for side in ('left','right'):
        indexes=[i for i,r in enumerate(rows) if r['side']==side]
        if indexes:
            chosen=[frames[i] for i in indexes]
            durations=[max(30,round((rows[b]['elapsed']-rows[a]['elapsed'])*1000)) for a,b in zip(indexes,indexes[1:])]+[100]
            chosen[0].save(output/f'{side}.gif',save_all=True,append_images=chosen[1:],duration=durations,loop=0)
    report={'passed':not errors and all(c['passed'] for c in checks),'checks':checks,'errors':errors,'events':events,'snapshots':rows,'resourceHashes':hashes,
        'scope':'Production PetWindow and native Cubism; scripted attachment, panel and gaze. No hardware input/UAC/cleanup. Visual acceptance requires frame review.'}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'checks':checks,'errors':errors},ensure_ascii=False));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
