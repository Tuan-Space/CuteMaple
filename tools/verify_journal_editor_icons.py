"""Recapture isolated date/editor views after an appearance or wording correction."""
import argparse,json,os,sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QDateTime,QEventLoop,QTimer
from PySide6.QtWidgets import QApplication
from journal_design import journal_font
from journal_editors import EventEditor
from journal_store import JournalStore


def run():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path);parser.add_argument('--theme',required=True,choices=['light','dark']);args=parser.parse_args()
    report=json.loads((args.output/'report.json').read_text(encoding='utf-8'))
    if report.get('version')!=(Path(__file__).resolve().parents[1]/'VERSION').read_text().strip():raise ValueError('Expected a screenshot fixture for the current version')
    app=QApplication([]);app.setFont(journal_font());app.styleHints().setColorScheme(Qt.ColorScheme.Dark if args.theme=='dark' else Qt.ColorScheme.Light)
    import monitor_ui
    monitor_ui.system_theme=lambda:monitor_ui.DARK if args.theme=='dark' else monitor_ui.LIGHT
    fixed=datetime(2026,10,17,12);store=JournalStore(args.output/'profile',clock=lambda:fixed.timestamp());captures=[]
    def settle():
        app.processEvents();loop=QEventLoop();QTimer.singleShot(90,loop.quit);loop.exec();app.processEvents()
    def capture(name,dialog):
        settle();assert dialog.scroll.horizontalScrollBar().maximum()==0
        if hasattr(dialog,'start') and dialog.start.lunar:assert all(not dialog.start.day.itemText(i).endswith('日') for i in range(dialog.start.day.count()))
        assert dialog.grab().save(str(args.output/(name+'.png')));captures.append(name)
    for small in (False,True):
        prefix='small-' if small else ''
        for name,kind in [('event','schedule'),('anniversary','anniversary')]:
            dialog=EventEditor(store,initial_kind=kind);dialog.resize(620,420) if small else dialog.resize(560,620)
            dialog.title.setText('给重要的日子留个提醒');dialog.start.setDateTime(QDateTime(fixed))
            if kind=='schedule':dialog.period.setCurrentIndex(5);dialog.calendar.setCurrentIndex(1)
            else:dialog.hundreds.setChecked(True);dialog.day520.setChecked(True)
            dialog.show();capture(prefix+name,dialog)
            if small:dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum());capture(prefix+name+'-bottom',dialog)
            dialog.close()
        dialog=EventEditor(store);dialog.resize(620,420) if small else dialog.resize(560,620);dialog.show();dialog.save();capture(prefix+'event-save-error',dialog);dialog.close()
    if (args.output/'event-advanced.png').exists():
        dialog=EventEditor(store);dialog.resize(560,620);dialog.period.setCurrentIndex(4);dialog.more.setChecked(True);dialog.show();settle();dialog.scroll.ensureWidgetVisible(dialog.advanced);capture('event-advanced',dialog)
        dialog.calendar.setCurrentIndex(1);dialog.start.setDateTime(QDateTime(datetime(2025,7,25,9)));dialog.scroll.ensureWidgetVisible(dialog.time_card);capture('event-leap-month',dialog)
        dialog.title.setText('纪念日校验');dialog.custom_days.setText('不合法');dialog.kind.setCurrentIndex(2);dialog.save();capture('event-validation-error',dialog);dialog.close()
    (args.output/'icon-recapture.json').write_text(json.dumps({'passed':True,'theme':args.theme,'scale':os.environ.get('QT_SCALE_FACTOR','1'),'captures':captures},ensure_ascii=False,indent=2),encoding='utf-8');store.close()


if __name__=='__main__':run()
