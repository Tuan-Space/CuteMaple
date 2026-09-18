"""Wait for actual asynchronous UI work before legacy state assertions."""
import time
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication

def settle_queries(timeout=10):
    app=QApplication.instance()
    if not app:return
    until=time.monotonic()+timeout
    while True:
        busy=False
        for w in app.topLevelWidgets():
            if getattr(getattr(w,'store',None),'closed',False):continue
            busy |= bool(getattr(w,'_applying',{}))
            for name in ('queries','preview_queries'):
                runner=getattr(w,name,None)
                if runner and runner.busy:busy=True
            for timer in getattr(w,'search_timers',[]):
                if timer.isActive():busy=True
            if getattr(w,'preview_timer',None) and w.preview_timer.isActive():busy=True
        app.processEvents()
        if not busy:return
        if time.monotonic()>until:raise AssertionError('Asynchronous journal query did not finish')
        time.sleep(.001)
