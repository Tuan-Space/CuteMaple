"""Deterministic widget screenshots at independent Qt scale factors."""
import argparse,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);p.add_argument('--theme',choices=['light','dark'],required=True);a=p.parse_args()
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase,QFont
from PySide6.QtCore import Qt
from journal_store import JournalStore
from journal_ui import JournalWindow
from journal_editors import EventEditor
from journal_recurrence import Rule
from datetime import datetime,timedelta
a.output.mkdir(parents=True,exist_ok=True);app=QApplication([])
font=QFontDatabase.addApplicationFont(str(next((Path(__file__).resolve().parents[1]/'assets/fonts').glob('*.otf'))));app.setFont(QFont(QFontDatabase.applicationFontFamilies(font)[0],11))
app.styleHints().setColorScheme(Qt.ColorScheme.Dark if a.theme=='dark' else Qt.ColorScheme.Light)
import monitor_ui,journal_ui
colors=monitor_ui.DARK if a.theme=='dark' else monitor_ui.LIGHT
monitor_ui.system_theme=lambda:colors
journal_ui.system_theme=lambda:colors
s=JournalStore(a.output/'profile',create=True);s.save_event('和朋友一起喝茶','schedule','一个轻松的下午。',Rule((datetime.now()+timedelta(days=1)).isoformat(timespec='seconds'),period='weekly',advances=(86400,3600)))
note=s.save_note('小小的记录','# 留一点时间给自己\n\n- [x] 喝一杯水\n- [ ] 看远处的树\n\n**慢慢来，也很好。**')
w=JournalWindow(s,None);w.note_editor.load(s.rows('SELECT * FROM notes WHERE id=?',(note,))[0]);w.show()
assert w._theme is colors
report={'scale':os.environ.get('QT_SCALE_FACTOR','1'),'theme':a.theme,'captures':[]}
for i,name in enumerate(['reminders','notes','calendar','statistics','settings']):
    w.tabs.setCurrentIndex(i);app.processEvents();w.grab().save(str(a.output/(name+'.png')));report['captures'].append({'name':name,'logicalWidth':w.width(),'logicalHeight':w.height()})
d=EventEditor(s,parent=w);d.title.setText('给重要的日子留个提醒');d.period.setCurrentIndex(5);d.calendar.setCurrentIndex(1);d.show();app.processEvents();d.grab().save(str(a.output/'event.png'));d.close()
w.resize(620,420);app.processEvents();w.grab().save(str(a.output/'small-window.png'));report['smallWindow']=[w.width(),w.height()]
(a.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');w.close();s.close()
