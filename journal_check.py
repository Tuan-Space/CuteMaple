"""Explicit isolated UI/media verification; never opens a personal library."""
from __future__ import annotations
import argparse,json,os,sys,time
from datetime import datetime,timedelta
from pathlib import Path


def run(args):
    p=argparse.ArgumentParser();p.add_argument('--profile',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--theme',choices=['light','dark'],default='light');p.add_argument('--hardware',action='store_true');a=p.parse_args(args)
    root=a.profile.resolve();personal=(Path(os.environ.get('APPDATA',Path.home()))/'美腻枫').resolve()
    if root==personal or root.is_relative_to(personal) or personal.is_relative_to(root):raise ValueError('Use an isolated verification profile')
    if (root/'library'/'journal.sqlite3').exists():raise ValueError('Use a fresh profile')
    os.environ['MEINIFENG_PROFILE_DIRECTORY']=str(root)
    output=a.output.resolve();output.mkdir(parents=True,exist_ok=True)
    from PySide6.QtCore import QTimer,Qt
    from journal_store import JournalStore
    from journal_recurrence import Rule,MilestoneRule
    from journal_service import JournalService
    from journal_editors import EventEditor
    report={'passed':False,'theme':a.theme,'hardwareRequested':a.hardware,'screens':[],'errors':[]}
    store=JournalStore(root/'library',create=True)
    now=datetime.now()
    for title,kind,delta in [('给未来的自己写封信','todo',1),('约朋友喝茶','schedule',2),('值得纪念的那一天','anniversary',3)]:
        store.save_event(title,kind,'这是隔离验收资料，不会进入个人资料库。',Rule((now+timedelta(days=delta)).isoformat(timespec='seconds'),period='weekly' if kind=='schedule' else 'once'))
    note_id=store.save_note('九月的一页','# 今天的小事\n\n慢一点，也很好。\n\n- [x] 整理桌面\n- [ ] 看一看远处\n\n**给自己留一点时间。**')
    store.save_event('相识的日子','anniversary','',MilestoneRule((now-timedelta(days=99)).replace(hour=9,minute=0,second=0).isoformat(timespec='seconds'),hundreds=True,days=(520,1314)))
    def observer(app,pet):
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark if a.theme=='dark' else Qt.ColorScheme.Light)
        pet.journal=JournalService(store,pet)
        pet.journal.window.updates.check=lambda *_:None
        window=pet.journal.window
        tasks=[]
        def capture(name):
            app.processEvents();path=output/(name+'.png');window.grab().save(str(path));report['screens'].append(name)
        def begin():
            try:
                window.show();window.note_editor.load(store.rows('SELECT * FROM notes WHERE id=?',(note_id,))[0])
                for index,name in enumerate(['reminders','notes','calendar','statistics','settings']):
                    def action(i=index,n=name):window.tabs.setCurrentIndex(i);capture(n)
                    tasks.append(action)
                tasks.append(event_dialog)
                if a.hardware:tasks.append(record)
                else:tasks.append(finish)
                next_task()
            except Exception as error:fail(error)
        def next_task():
            if not tasks:return
            try:tasks.pop(0)()
            except Exception as error:fail(error);return
            if tasks:QTimer.singleShot(400,next_task)
        def event_dialog():
            dialog=EventEditor(store,parent=window,initial_kind='anniversary');dialog.title.setText('相识的日子');dialog.hundreds.setChecked(True);dialog.day520.setChecked(True);dialog.show();app.processEvents();dialog.grab().save(str(output/'event-editor.png'));report['screens'].append('event-editor');dialog.close()
        def record():
            window.tabs.setCurrentIndex(1);media=window.note_editor.media
            media.begin();QTimer.singleShot(2200,lambda:(media.finish(),QTimer.singleShot(1200,check_record)))
        def check_record():
            files=store.attachments(note_id);recordings=[r for r in files if r['mime'].startswith('audio/')]
            report['recordingSaved']=bool(recordings)
            report['recordingStatus']=window.note_editor.media.status.text()
            if recordings:
                import wave
                path=store.attachment_path(recordings[-1]['relative'])
                with wave.open(str(path),'rb') as wav:report['recordingSeconds']=wav.getnframes()/wav.getframerate()
                report['recordingSaved']=report['recordingSeconds']>=.5
                # Do not replay captured private speech; a generated tone checks playback.
            if a.hardware:
                import wave,math,struct
                tone=root/'test-output.wav'
                with wave.open(str(tone),'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(48000)
                    wav.writeframes(b''.join(struct.pack('<h',int(3200*math.sin(2*math.pi*330*i/48000))) for i in range(48000*3)))
                pet.settings.audio_enabled=False
                pet._configure_activity()
                window.note_editor.media.output.setVolume(.5);window.note_editor.media.load(tone)
                QTimer.singleShot(1700,check_playback)
            else:finish()
        def check_playback():
            report['audibleOutputDetected']=pet._audio_prevents_sleep
            report['audioDetectionStatus']=dict(pet.desktop_activity._audio_status) if pet.desktop_activity else {}
            report['playerPositionMs']=window.note_editor.media.player.position()
            report['playerError']=window.note_editor.media.player.errorString()
            report['audioRepliesDisabled']=not pet.settings.audio_enabled
            window.note_editor.media.player.stop()
            QTimer.singleShot(1500,check_silence)
        def check_silence():
            report['silenceReleasedSleepLatch']=not pet._audio_prevents_sleep
            finish()
        def fail(error):
            report['errors'].append(str(error));finish()
        def finish():
            report['passed']=not report['errors'] and (not a.hardware or (report.get('recordingSaved',False) and report.get('audibleOutputDetected',False) and report.get('silenceReleasedSleepLatch',False)))
            report['rendererReady']=pet._presentation_ready
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            pet.quit_app()
        QTimer.singleShot(4000,begin)
        QTimer.singleShot(30000,lambda:fail('Verification deadline exceeded') if not pet._quitting else None)
    from pet_app import run as app_run
    app_run(observer=observer)
    return 0 if report['passed'] else 1
