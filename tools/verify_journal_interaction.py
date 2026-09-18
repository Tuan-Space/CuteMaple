"""Journal interaction and visual audit in an isolated library; no microphone use."""
from __future__ import annotations
import argparse,json,os,sys,wave
from datetime import datetime,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QDate,QDateTime,QRect,Qt,QTimer,QEventLoop
from PySide6.QtGui import QImage,QColor
from PySide6.QtMultimedia import QMediaPlayer,QMediaRecorder
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QMessageBox,QScrollArea
from journal_design import journal_font,load_journal_fonts
from journal_editors import EventEditor
from journal_recurrence import Rule,MilestoneRule
from journal_store import JournalStore
from journal_ui import JournalWindow

def run():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--theme',choices=['light','dark'],required=True)
    p.add_argument('--extended',action='store_true');a=p.parse_args()
    if (a.output/'profile').exists():raise ValueError('Use a fresh fixture directory')
    a.output.mkdir(parents=True,exist_ok=True)
    app=QApplication([]);app.setFont(journal_font())
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark if a.theme=='dark' else Qt.ColorScheme.Light)
    import monitor_ui,journal_ui
    monitor_ui.system_theme=lambda:monitor_ui.DARK if a.theme=='dark' else monitor_ui.LIGHT
    journal_ui.system_theme=monitor_ui.system_theme
    fixed=datetime(2026,10,17,12);s=JournalStore(a.output/'profile',create=True,clock=lambda:fixed.timestamp())
    titles=['和朋友一起喝茶','整理本周照片','给未来的自己写一封信','下午一起整理旅行照片和准备周末的小计划，记得带上相机与充电器','读完手边的一本书','给阳台的植物浇水','散步时记下的灵感','准备下周的小目标']
    notes=[]
    for n,title in enumerate(titles):
        s.save_event(title,'schedule' if n%2 else 'todo','留一点时间，慢慢完成。',Rule(f'2026-10-17T{13+n:02}:30:00',period='weekly'))
        notes.append(s.save_note(title,'# 十月的一页\n\n慢慢来，也很好。\n\n## 值得记录\n\n**今天的重点**，还有 *一个小小的灵感*。\n\n- 一杯温水\n- 一段散步\n  - 看见落叶\n\n### 明天\n\n1. 继续阅读\n2. 整理照片\n\n> 给自己一点时间。\n'))
    s.save_event('相识的日子','anniversary','',MilestoneRule((fixed-timedelta(days=99)).replace(hour=9).isoformat(timespec='seconds'),hundreds=True,days=(520,1314)))
    completed=s.save_event('已经完成的一件小事','todo','',Rule('2026-10-17T11:00:00'));s.tick()
    for r in s.pending():
        if r.get('event_id')==completed:s.respond(r['id'],'done')
    s.tick();trash=s.save_event('暂时放下的旅行计划','schedule','',Rule('2026-10-18T09:00:00'));s.archive_event(trash)
    trash=s.save_note('可以找回的一页','这篇笔记保留在回收站。');s.trash_note(trash)
    for n in range(110):
        identity=s.save_note(f'归档手记 {n+1:03} · '+titles[n%len(titles)],'保留这一页的文字与回忆。\n\n- 旅途中的小事\n- 还想继续的计划')
        s.trash_note(identity)
    pending_event=s.save_event('需要重新安排时间的旧计划','todo','仍留在回收站，处理后再提醒。',Rule('2026-10-16T09:00:00'));s.archive_event(pending_event)
    repeat_event=s.save_event('每周整理相册','schedule','同一计划的不同日期独立选择。',Rule('2026-10-17T18:00:00',period='weekly'))
    for due in (datetime(2026,10,17,18),datetime(2026,10,24,18),datetime(2026,10,31,18)):s.trash_occurrence(repeat_event,due.timestamp())
    for n in range(9):
        due=fixed.timestamp()-n*1800
        s.db.execute('INSERT INTO habit_log VALUES(?,?,?,?,?,?)',(f'visual-{n}',['water','walk','eyes'][n%3],due,'2026-10-17',None if n%3==2 else due+60,None if n%3==2 else int(n%3==0)))
    s.db.commit();audio=a.output/'短音频验收.wav'
    with wave.open(str(audio),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000);wav.writeframes(b'\0\0'*4000)
    s.attach(notes[0],audio)
    doc=a.output/'这是一份名称很长的旅行准备清单与行程安排.txt';doc.write_text('隔离验收附件',encoding='utf-8');s.attach(notes[0],doc)
    readonly_note=s.notes(trash=True,limit=1)[0]['id'];s.attach(readonly_note,audio);s.attach(readonly_note,doc)
    w=JournalWindow(s,None);w.updates.check=lambda *_:None;w.resize(1000,700);w.show()
    w.calendar.setSelectedDate(QDate(2026,10,17));w.stat_date.setDate(QDate(2026,10,17))
    e=w.note_editor
    def note(identity=notes[0]):e.load(s.rows('SELECT * FROM notes WHERE id=?',(identity,))[0])
    note()
    report={'version':(Path(__file__).resolve().parents[1]/'VERSION').read_text().strip(),'noteFixtureCount':len(s.notes(limit=None))+len(s.notes(trash=True,limit=None)),'theme':a.theme,'scale':os.environ.get('QT_SCALE_FACTOR','1'),'fonts':load_journal_fonts(),'fixedTime':fixed.isoformat(),'recordingStatesSimulated':True,'captures':[],'horizontalOverflow':{}}
    def wait(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def settle():app.processEvents();w.wait_for_queries();wait(90);app.processEvents()
    def capture(name,target=None):
        target=target or w;settle()
        if not target.grab().save(str(a.output/(name+'.png'))):raise OSError(name)
        report['captures'].append({'name':name,'width':target.width(),'height':target.height()})
    def page(index,name,bottom=False):
        w.tabs.setCurrentIndex(index);settle();scroll=w.tabs.widget(index)
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum() if bottom else 0)
        settle();report['horizontalOverflow'][name]=scroll.horizontalScrollBar().maximum();capture(name)
    def modal(name,action):
        def inspect():
            dialog=app.activeModalWidget()
            if dialog is None:raise RuntimeError('No modal: '+name)
            capture(name,dialog);dialog.reject()
        QTimer.singleShot(180,inspect);action()
    for small in (False,True):
        w.resize(620,420) if small else w.resize(1000,700);prefix='small-' if small else ''
        for i,name in enumerate(['overview','reminders','notes','statistics','settings']):
            page(i,prefix+name)
            if small:page(i,prefix+name+'-bottom',True)
        pinned_event=s.save_event('周末去看一场展览','schedule','',Rule('2026-10-19T10:00:00'));s.set_pinned('event',pinned_event,True);page(1,prefix+'reminders-pinned-countdown')
        s.set_pinned('note',notes[0],True);page(2,prefix+'notes-pinned');note()
        def popup(name,action):
            def inspect():
                menu=app.activePopupWidget()
                if menu:capture(name,menu);menu.close()
            QTimer.singleShot(180,inspect);action()
        popup(prefix+'note-pin-menu',lambda:w.note_menu(w.notes_list.visualItemRect(w.notes_list.item(0)).center()))
        page(1,prefix+'reminder-pin-page');popup(prefix+'reminder-pin-menu',lambda:w.event_menu(w.events_list.visualItemRect(w.events_list.item(0)).center()))
        page(2,prefix+'toolbar-single-row')
        popup(prefix+'editor-mode-menu',e.mode.showMenu)
        if e.more_tools.isVisible():popup(prefix+'toolbar-more-menu',e.more_tools.showMenu)
        s.set_pinned('note',notes[0],False);s.archive_event(pinned_event);s.purge_reminders([('event',pinned_event)])
        for month in (10,11,12):
            w.calendar.setCurrentPage(2026,month);page(0,prefix+f'month-{month}')
        w.calendar.setSelectedDate(QDate(2026,10,17));w.calendar.setCurrentPage(2026,10)
        for name,kind in [('event','schedule'),('anniversary','anniversary')]:
            d=EventEditor(s,parent=w,initial_kind=kind);d.resize(560,620);d.title.setText('给重要的日子留个提醒');d.start.setDateTime(QDateTime(fixed))
            if kind=='schedule':d.period.setCurrentIndex(5);d.calendar.setCurrentIndex(1)
            else:d.hundreds.setChecked(True);d.day520.setChecked(True)
            if small:d.resize(620,420)
            d.show();capture(prefix+name,d)
            if small:
                for scroll in d.findChildren(QScrollArea):scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                capture(prefix+name+'-bottom',d)
            d.close()
        w.trash.setChecked(True);w.reset_notes();page(2,prefix+'notes-trash-none')
        w.notes_list.toggle_item(w.notes_list.item(1));w.notes_list.toggle_item(w.notes_list.item(2));page(2,prefix+'notes-trash-multiple')
        w.select_all_notes(True);page(2,prefix+'notes-trash-all');modal(prefix+'notes-bulk-confirm',w.purge_checked_notes)
        if small:page(2,prefix+'notes-trash-preview-bottom',True)
        w.trash.setChecked(False);w.reset_notes();note();e.mode.setCurrentIndex(1);page(2,prefix+'notes-source');e.mode.setCurrentIndex(0)
        if small:page(2,prefix+'notes-toolbar-bottom',True)
        w.event_status.setCurrentIndex(2);page(1,prefix+'reminders-trash-none');w.select_all_events(True);page(1,prefix+'reminders-trash-all')
        modal(prefix+'reminders-bulk-confirm',w.purge_checked_events)
        restore_fixture=s.reminder_trash(limit=None);w.restore_checked_events();page(1,prefix+'reminders-trash-pending')
        modal(prefix+'reminders-pending',w.show_pending_reminders)
        readonly=EventEditor(s,s.rows('SELECT * FROM events WHERE id=?',(pending_event,))[0],w);readonly.resize(620,420) if small else readonly.resize(560,620);readonly.show();capture(prefix+'reminder-readonly',readonly);readonly.close()
        w.event_status.setCurrentIndex(0)
        for restored in restore_fixture:
            if restored['id']==pending_event:continue
            if restored.get('_occurrence'):s.trash_occurrence(restored['id'],restored['due'])
            else:s.archive_event(restored['id'])
        d=EventEditor(s,parent=w);d.resize(620,420) if small else d.resize(560,620);d.show();d.save();capture(prefix+'event-save-error',d);d.close()
    w.resize(1000,700)
    if a.extended:
        for i,name in enumerate(['active','completed','trash']):w.event_status.setCurrentIndex(i);page(1,'reminders-'+name)
        w.event_search.setText('不存在的关键词');page(1,'reminders-search-empty');w.event_search.clear();w.event_status.setCurrentIndex(0)
        w.trash.setChecked(True);w.reset_notes();page(2,'notes-trash');w.trash.setChecked(False);w.reset_notes()
        w.note_search.setText('不存在的关键词');page(2,'notes-search-empty');w.note_search.clear();note()
        e.mode.setCurrentIndex(1);page(2,'notes-source');e.mode.setCurrentIndex(0)
        modal('insert-link',e.insert_link)
        e.heading.showPopup();settle();capture('heading-menu',e.heading.view().window());e.heading.hidePopup()
        def plus_menu_capture():
            menu=app.activePopupWidget()
            if menu:capture('note-add-menu',menu);menu.close()
        QTimer.singleShot(180,plus_menu_capture);e.add_menu()
        complex_id=s.save_note('复杂原文保护','# 保留原文\n\n<div>扩展内容</div>\n\n[^a]: 脚注')
        note(complex_id);page(2,'notes-protected');note();page(2,'notes-attachments')
        e.show_recording();capture('recording-idle');e.media.state_changed(QMediaRecorder.RecordingState)
        e.media.status.setText('● 正在使用麦克风 · 00:12');capture('recording-active-simulated')
        e.media.state_changed(QMediaRecorder.PausedState);e.media.status.setText('录音已暂停 · 00:12');capture('recording-paused-simulated')
        e.media.record_panel.hide();capture('recording-collapsed-simulated');e.media.state_changed(QMediaRecorder.StoppedState)
        e.media.status.setText('录音尚未完整写入，原文件已保留');capture('recording-error-simulated');e.media.hide()
        e.media.output.setVolume(0);e.media.load(audio);capture('playback-active');wait(900);capture('playback-completed')
        report['playbackEnded']=e.media.player.mediaStatus()==QMediaPlayer.EndOfMedia
        QTest.mouseClick(e.title,Qt.LeftButton);capture('playback-dismissed');report['playbackDismissed']=not e.media.play_panel.isVisible()
        w.resize(620,420);e.show_recording();page(2,'small-recording');page(2,'small-recording-bottom',True)
        e.media.record_panel.hide();e.media.update_visibility();e.mode.setCurrentIndex(1);page(2,'small-notes-source-bottom',True);e.mode.setCurrentIndex(0);w.resize(1000,700)
        picture=a.output/'image-fixture.png';img=QImage(160,90,QImage.Format_RGB32);img.fill(QColor('#b8d2f2'));img.save(str(picture))
        image_id=s.save_note('一张图片的记录','');attached=s.attach(image_id,picture);s.save_note('一张图片的记录','## 沿途\n\n![图片]('+attached['relative']+')',image_id);note(image_id);page(2,'notes-image');note()
        for i,name in enumerate(['day','week','month','year']):w.stat_period.setCurrentIndex(i);page(3,'statistics-'+name)
        w.stat_date.setDate(QDate(2027,1,1));page(3,'statistics-empty');w.stat_date.setDate(QDate(2026,10,17))
        for name,result in [('latest',{'new':False}),('available',{'new':True,'version':'9.9.9','body':'有一个可以下载的新版本。'}),('error',{'error':'离线验收'})]:w.update_result(result);page(4,'settings-update-'+name)
        w.notice('备份已保存');page(4,'settings-backup-feedback');w.tabs.setCurrentIndex(1)
        modal('health-times',w.edit_habit_times)
        modal('event-ledger',lambda:w.event_ledger(s.rows('SELECT * FROM events WHERE id=?',(completed,))[0]))
        from journal_library import select_library
        modal('library-selector',lambda:select_library(w))
        from journal_reminders import snooze_seconds,ReminderBubble
        modal('snooze',lambda:snooze_seconds(w))
        modal('error-dialog',lambda:QMessageBox.warning(w,'尚未保存','资料库暂时无法写入，内容仍保留在编辑器中。'))
        modal('delete-confirmation',lambda:QMessageBox.question(w,'永久删除','永久删除这篇笔记和不再使用的附件？此操作不能撤销。'))
        d=EventEditor(s,parent=w);d.resize(560,620);d.period.setCurrentIndex(4);d.more.setChecked(True);d.show();settle();d.scroll.ensureWidgetVisible(d.advanced);capture('event-advanced',d)
        d.calendar.setCurrentIndex(1);d.start.setDateTime(QDateTime(datetime(2025,7,25,9)));d.scroll.ensureWidgetVisible(d.time_card);capture('event-leap-month',d);d.title.setText('纪念日校验');d.custom_days.setText('不合法');d.kind.setCurrentIndex(2);d.save();capture('event-validation-error',d);d.close()
        class Pet:
            _pet_hidden=True
            def isVisible(self):return False
            def _screen_area(self):return QRect(0,0,1000,700)
        s.save_event('出门前记得带上相机','todo','拍下秋天的颜色。',Rule('2026-10-17T12:00:00'));s.set_habit('water',True,1)
        s.db.execute("UPDATE habits SET next_due=? WHERE kind='water'",(s.clock(),));s.db.commit();s.tick()
        b=ReminderBubble(s,Pet());b.refresh()
        for kind,name in [('water','health-bubble'),(None,'event-bubble')]:
            r=next((r for r in s.pending() if r.get('habit')==kind),None)
            if r:b.current=r['id'];b.refresh();capture(name,b)
        long_id=s.save_event('一条很长的提醒','todo','重要的安排需要完整阅读。'*45,Rule('2026-10-17T12:00:00'));s.tick()
        r=next((r for r in s.pending() if r.get('event_id')==long_id),None)
        if r:
            b.pet._screen_area=lambda:QRect(0,0,800,420);b.current=r['id'];b.refresh();capture('event-bubble-long',b)
            for scroll in b.findChildren(QScrollArea):scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            capture('event-bubble-long-bottom',b)
        b.close();w.calendar_filter.setCurrentIndex(4);page(0,'overview-note-filter');w.calendar_filter.setCurrentIndex(0)
        w.calendar.setSelectedDate(QDate(2027,2,2));page(0,'overview-empty')
    report['passed']=not any(report['horizontalOverflow'].values())
    if a.extended:report['passed'] &= report['playbackEnded'] and report['playbackDismissed']
    (a.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    w.close();s.close();return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(run())
