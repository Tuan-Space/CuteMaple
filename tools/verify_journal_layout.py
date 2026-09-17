"""Deterministic widget screenshots at independent Qt scale factors."""
import argparse,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);p.add_argument('--theme',choices=['light','dark'],required=True);a=p.parse_args()
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase,QFont
from PySide6.QtCore import Qt
from journal_store import JournalStore
from journal_ui import JournalWindow
from journal_editors import EventEditor
from journal_recurrence import Rule,MilestoneRule
from datetime import datetime,timedelta
a.output.mkdir(parents=True,exist_ok=True);app=QApplication([])
for font in ('msyh.ttc','msyhbd.ttc'):
    QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/font))
app.setFont(QFont('Microsoft YaHei UI',10))
app.styleHints().setColorScheme(Qt.ColorScheme.Dark if a.theme=='dark' else Qt.ColorScheme.Light)
import monitor_ui,journal_ui
colors=monitor_ui.DARK if a.theme=='dark' else monitor_ui.LIGHT
monitor_ui.system_theme=lambda:colors
journal_ui.system_theme=lambda:colors
s=JournalStore(a.output/'profile',create=True);s.save_event('和朋友一起喝茶','schedule','一个轻松的下午。',Rule((datetime.now()+timedelta(days=1)).isoformat(timespec='seconds'),period='weekly',advances=(86400,3600)))
note=s.save_note('小小的记录','# 留一点时间给自己\n\n- [x] 喝一杯水\n- [ ] 看远处的树\n\n**慢慢来，也很好。**')
s.save_event('相识的日子','anniversary','',MilestoneRule((datetime.now()-timedelta(days=99)).replace(hour=9,minute=0,second=0).isoformat(timespec='seconds'),hundreds=True,days=(520,1314)))
s.save_event('整理本周照片','todo','',Rule(datetime.now().replace(hour=20,minute=0,second=0).isoformat(timespec='seconds')))
s.save_event('下午一起整理旅行照片和准备周末的小计划','schedule','',Rule(datetime.now().replace(hour=18,minute=0,second=0).isoformat(timespec='seconds')))
s.set_habit('water',True,1);s.db.execute("UPDATE habits SET next_due=? WHERE kind='water'",(s.clock(),));s.db.commit();s.tick()
w=JournalWindow(s,None);w.resize(1000,700);w.note_editor.load(s.rows('SELECT * FROM notes WHERE id=?',(note,))[0]);w.show()
assert (w._theme['background']=='#1d2835') == (a.theme=='dark')
report={'scale':os.environ.get('QT_SCALE_FACTOR','1'),'theme':a.theme,'captures':[]}
for i,name in enumerate(['overview','reminders','notes','statistics','settings']):
    w.tabs.setCurrentIndex(i);w.nav[i].setFocus();app.processEvents();w.grab().save(str(a.output/(name+'.png')));report['captures'].append({'name':name,'logicalWidth':w.width(),'logicalHeight':w.height()})
w.tabs.setCurrentIndex(1);w.event_status.setCurrentIndex(2);QTest.qWait(50);assert w.page_title.text()=='提醒回收站';w.grab().save(str(a.output/'reminder-trash.png'));w.event_status.setCurrentIndex(0)
w.tabs.setCurrentIndex(2);w.trash.click();QTest.qWait(50);assert w.page_title.text()=='笔记回收站';w.grab().save(str(a.output/'note-trash.png'));w.trash.click()
d=EventEditor(s,parent=w);d.title.setText('给重要的日子留个提醒');d.period.setCurrentIndex(5);d.calendar.setCurrentIndex(1);d.show();app.processEvents();d.grab().save(str(a.output/'event.png'));d.close()
d=EventEditor(s,parent=w,initial_kind='anniversary');d.title.setText('相识的日子');d.hundreds.setChecked(True);d.day520.setChecked(True);d.show();app.processEvents();d.grab().save(str(a.output/'anniversary.png'));d.close()
w.resize(620,420)
for i,name in enumerate(['overview','reminders','notes','statistics','settings']):
    w.tabs.setCurrentIndex(i);app.processEvents();w.grab().save(str(a.output/('small-'+name+'.png')))
    scroll=w.tabs.widget(i);report.setdefault('smallHorizontalScroll',{})[name]=scroll.horizontalScrollBar().maximum()
report['smallWindow']=[w.width(),w.height()]
(a.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');w.close();s.close()
