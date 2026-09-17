"""Bounded background I/O with all UI and live database writes on the Qt thread."""
import threading
from PySide6.QtCore import QObject,Signal

class FileJob(QObject):
    finished=Signal(object,object)
    def start(self,work):
        def run():
            try:value=work();error=None
            except Exception as e:value=None;error=str(e)
            self.finished.emit(value,error)
        threading.Thread(target=run,name='journal-files',daemon=True).start()
