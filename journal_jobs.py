"""Bounded background I/O with all UI and live database writes on the Qt thread."""
import threading
from PySide6.QtCore import QObject,Signal
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QSize,QUrl
from PySide6.QtGui import QImage,QImageReader,QTextDocument


class ImageLoader(QObject):
    ready=Signal(object,object)
    def __init__(self,editor):
        super().__init__(editor);self.editor=editor;self.cache=OrderedDict();self.pending=set();self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='journal-image');self.closed=False
        self.ready.connect(self.loaded);self.destroyed.connect(lambda *_:self.close())
    def close(self):self.closed=True;self.pool.shutdown(wait=False,cancel_futures=True)
    def get(self,relative,path,width):
        width=max(320,min(1600,width));key=(relative,str(path),width)
        if key in self.cache:self.cache.move_to_end(key);return self.cache[key]
        if not self.closed and key not in self.pending:
            self.pending.add(key)
            def read():
                reader=QImageReader(str(path));reader.setAutoTransform(True);size=reader.size()
                if size.isValid() and (size.width()>width or size.height()>2048):reader.setScaledSize(size.scaled(QSize(width,2048),__import__('PySide6.QtCore',fromlist=['Qt']).Qt.KeepAspectRatio))
                result=reader.read()
                if not self.closed:
                    try:self.ready.emit(key,result)
                    except RuntimeError:pass
            self.pool.submit(read)
        image=QImage(320,120,QImage.Format_ARGB32);image.fill(0);return image
    def loaded(self,key,image):
        if self.closed:return
        self.pending.discard(key);self.cache[key]=image
        while len(self.cache)>64 or sum(v.sizeInBytes() for v in self.cache.values())>32*1024*1024:self.cache.popitem(last=False)
        doc=self.editor.document();doc.addResource(QTextDocument.ImageResource,QUrl(key[0]),image);doc.markContentsDirty(0,doc.characterCount());self.editor.viewport().update()

class FileJob(QObject):
    finished=Signal(object,object)
    def start(self,work):
        def run():
            try:value=work();error=None
            except Exception as e:value=None;error=str(e)
            self.finished.emit(value,error)
        threading.Thread(target=run,name='journal-files',daemon=True).start()
