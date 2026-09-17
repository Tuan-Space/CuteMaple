from __future__ import annotations
import time
from PySide6.QtCore import QObject,QTimer
from journal_ui import JournalWindow
from journal_reminders import ReminderBubble
from monitor_ui import cleanup_summary


class JournalService(QObject):
    def __init__(self,store,pet):
        super().__init__(pet);self.store,self.pet=store,pet
        self.window=JournalWindow(store,pet);self.window.visibilityChanged.connect(pet._journal_visibility)
        self.bubble=ReminderBubble(store,pet);self.bubble.openRequested.connect(self.open)
        self.bubble.changed.connect(self.window.refresh_current)
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.tick);self.timer.start()
        self.follow_timer=QTimer(self);self.follow_timer.setInterval(100);self.follow_timer.timeout.connect(self.follow);self.follow_timer.start()
        QTimer.singleShot(1500,self.window.updates.check)
        self.last_backup=0

    def open(self):
        self.window.show();self.window.raise_();self.window.activateWindow()

    def tick(self):
        try:
            self.store.tick();self.bubble.refresh()
            if self.bubble.isVisible():
                self.pet.bubble.hide()
                if self.pet.bubble.cleanup_owns_bubble:
                    self.bubble.cleanup.setText(cleanup_summary(self.pet._cleanup_feedback));self.bubble.cleanup.show()
                else:self.bubble.cleanup.hide()
            elif self.pet.bubble.cleanup_owns_bubble:
                self.pet.bubble.restore_cleanup(self.pet._bubble_anchor_rect(),self.pet._screen_area(),self.pet.isVisible() and not self.pet._pet_hidden,placement=self.pet.base_mode)
            if time.monotonic()-self.last_backup>3600:
                self.store.backup_daily();self.last_backup=time.monotonic()
        except Exception as error:
            self.store._event_wake.clear()
            self.window.status.setText('资料暂时无法保存，请检查文件夹：'+str(error))
            self.window.status.show()
            if self.bubble.isVisible():self.bubble.meta.setText('保存异常，提醒仍保留：'+str(error));self.bubble.meta.show()

    def follow(self):
        if self.bubble.isVisible():self.bubble.follow()

    def close(self):
        if not self.window.note_editor.finish():return False
        self.timer.stop();self.follow_timer.stop();self.bubble.hide();self.window.hide();self.store.close();return True
