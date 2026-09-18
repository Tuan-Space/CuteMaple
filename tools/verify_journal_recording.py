"""Explicit 2-second microphone check in a fresh disposable library."""
import sys,json,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer,Qt,QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtMultimedia import QMediaRecorder
from journal_store import JournalStore
from journal_ui import JournalWindow

p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
if a.output.exists():raise ValueError('Use a fresh output directory')
a.output.mkdir(parents=True);app=QApplication([]);s=JournalStore(a.output/'library',create=True);w=JournalWindow(s,None);w.tabs.setCurrentIndex(2);w.note_editor.title.setText('隔离录音验收');w.show();report={}
def begin():
    e=w.note_editor;e.show_recording();e.media.begin();QTimer.singleShot(600,dismiss)
def dismiss():
    e=w.note_editor;event=QMouseEvent(QEvent.MouseButtonPress,e.title.rect().center(),Qt.LeftButton,Qt.LeftButton,Qt.NoModifier);app.sendEvent(e.title,event)
    report['panelDismissed']=not e.media.record_panel.isVisible();report['recordingContinued']=e.media.recorder.recorderState()==QMediaRecorder.RecordingState;report['stopVisible']=e.media.compact_stop.isVisible();QTimer.singleShot(1600,stop)
def stop():
    w.note_editor.media.compact_stop.click();QTimer.singleShot(1500,finish)
def finish():
    import wave
    files=s.attachments(w.note_editor.identity);report['files']=len(files);report['status']=w.note_editor.status.text()
    try:
        with wave.open(str(s.attachment_path(files[0]['relative'])),'rb') as wav:report['seconds']=wav.getnframes()/wav.getframerate()
        report['savedToOriginalNote']='attachments/' in s.notes()[0]['body'];report['passed']=all(report.get(k) for k in ('panelDismissed','recordingContinued','stopVisible','savedToOriginalNote')) and report['seconds']>=1
    except Exception as error:report['error']=str(error);report['passed']=False
    (a.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');w.close();s.close();app.quit()
QTimer.singleShot(300,begin);QTimer.singleShot(12000,finish);app.exec();sys.exit(0 if report.get('passed') else 1)
