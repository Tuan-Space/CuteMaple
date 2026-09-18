from __future__ import annotations
import time
from PySide6.QtCore import QObject,QTimer
from journal_ui import JournalWindow
from journal_reminders import ReminderBubble
from monitor_ui import cleanup_summary


class JournalService(QObject):
    def __init__(self,store,pet):
        super().__init__(pet);self.store,self.pet=store,pet
        self.window=JournalWindow(store,pet)
        self.bubble=ReminderBubble(store,pet);self.bubble.openRequested.connect(self.open)
        self.bubble.changed.connect(self.window.refresh_current)
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.tick);self.timer.start()
        self.follow_timer=QTimer(self);self.follow_timer.setInterval(100);self.follow_timer.timeout.connect(self.follow);self.follow_timer.start()
        QTimer.singleShot(1500,self.window.updates.check)
        self.last_backup=0
        from journal_queries import QueryRunner
        self.scheduler=QueryRunner(store.path,self)
        self.store.maintenance_request=self.maintain

    def open(self):
        self.window.show();self.window.raise_();self.window.activateWindow()

    def tick(self):
        if 'schedule' in self.scheduler.busy or getattr(self,'applying_schedule',False):return
        from journal_queries import schedule_data
        now=self.store.clock()
        def done(result,error):
            if self.store.closed:return
            if error:self.window.notice('提醒检查未完成：'+error);return
            self._apply_tick(now,result)
        self.scheduler.submit('schedule',now,lambda reader,cancel:schedule_data(reader,now,cancel),done)

    def _apply_tick(self,now,prepared):
        if self.store.clock()<now:return
        self.applying_schedule=True;identities=list(prepared);first=[True]
        def apply():
            if self.store.closed or self.store.clock()<now:self.applying_schedule=False;return
            start=time.monotonic()
            try:
                while identities and time.monotonic()-start<.006:
                    identity=identities.pop(0);self.store.tick(now,prepared=prepared,only_ids=(identity,),process_habits=first[0]);first[0]=False
                if first[0]:self.store.tick(now,prepared={},process_habits=True);first[0]=False
                self.bubble.refresh()
                if identities:QTimer.singleShot(0,apply)
                else:self.applying_schedule=False;self._after_tick()
            except Exception as error:self.applying_schedule=False;self.window.notice('提醒处理未完成：'+str(error))
        apply()

    def _after_tick(self):
        try:
            revision=self.store.revision('events','statistics')
            if getattr(self,'_visible_revision',None)!=revision:
                self._visible_revision=revision
                if self.window.isVisible() and self.window.tabs.currentIndex() in (0,1,3):self.window.refresh_current()
            if self.bubble.isVisible():
                self.pet.bubble.hide()
                if self.pet.bubble.cleanup_owns_bubble:
                    self.bubble.cleanup.setText(cleanup_summary(self.pet._cleanup_feedback));self.bubble.cleanup.show()
                else:self.bubble.cleanup.hide()
            elif self.pet.bubble.cleanup_owns_bubble:
                self.pet.bubble.restore_cleanup(self.pet._bubble_anchor_rect(),self.pet._screen_area(),self.pet.isVisible() and not self.pet._pet_hidden,placement=self.pet.base_mode)
            if time.monotonic()-self.last_backup>3600:
                self.maintain();self.last_backup=time.monotonic()
        except Exception as error:
            self.store._event_wake.clear()
            self.window.status.setText('资料暂时无法保存，请检查文件夹：'+str(error))
            self.window.status.show()
            if self.bubble.isVisible():self.bubble.meta.setText('保存异常，提醒仍保留：'+str(error));self.bubble.meta.show()

    def follow(self):
        if self.bubble.isVisible():self.bubble.follow()

    def maintain(self):
        if getattr(self,'maintenance_job',None) or getattr(self.store,'background_backup',False):return
        from journal_jobs import FileJob
        self.maintenance_job=FileJob(self);self.store.background_backup=True;revision=self.store.revision('notes','files')
        def done(value,error):
            self.maintenance_job.deleteLater();self.maintenance_job=None;self.store.background_backup=False
            if error:self.window.notice('备份或附件清理未完成：'+error);return
            candidates,fingerprint=value
            # Deletions are applied below with a fresh revision for each chunk.
            def apply_chunk():
                nonlocal revision
                if self.store.closed or revision!=self.store.revision('notes','files') or getattr(self.store,'background_backup',False):return
                current=tuple(sorted((p.name,p.stat().st_mtime_ns,p.stat().st_size) for p in (self.store.root/'backups').glob('*.sqlite3')))
                if current!=fingerprint:return
                started=time.monotonic()
                try:
                    while candidates and time.monotonic()-started<.008:
                        identity,relative=candidates.pop();self.store.attachment_path(relative).unlink(missing_ok=True)
                        with self.store.db:self.store.db.execute('DELETE FROM files WHERE id=?',(identity,))
                    revision=self.store.revision('notes','files')
                    if candidates:QTimer.singleShot(0,apply_chunk)
                except Exception as e:self.window.notice('记录已删除，附件清理未完成：'+str(e))
            apply_chunk()
        self.maintenance_job.finished.connect(done);self.maintenance_job.start(self.store.backup_snapshot)

    def close(self):
        if getattr(self,'maintenance_job',None):self.window.notice('正在完成备份，请稍候');return False
        if getattr(self.window,'backup_job',None):self.window.notice('正在完成备份，请稍候');return False
        if not self.window.note_editor.finish():return False
        self.timer.stop();self.follow_timer.stop();self.scheduler.close();self.window.queries.close();self.bubble.hide();self.window.hide();self.store.close();return True
