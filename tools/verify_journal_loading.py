"""Loading, failure and retry states on an isolated reminder page."""
import os,sys,argparse,time,json
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from journal_ui import JournalWindow
from journal_design import journal_font
from journal_store import JournalStore
from journal_recurrence import Rule,check_cancel

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--theme',choices=['light','dark'],required=True);a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=True);app=QApplication([]);app.setFont(journal_font());app.styleHints().setColorScheme(Qt.ColorScheme.Dark if a.theme=='dark' else Qt.ColorScheme.Light)
import monitor_ui,journal_ui
monitor_ui.system_theme=lambda:monitor_ui.DARK if a.theme=='dark' else monitor_ui.LIGHT;journal_ui.system_theme=monitor_ui.system_theme
s=JournalStore(a.output/'profile',create=True);s.save_event('加载期间仍可操作窗口','todo','',Rule('2027-01-01T10:00:00'))
w=JournalWindow(s,None);w.resize(1000,700);w.show();w.tabs.setCurrentIndex(1);w.wait_for_queries();screens=[]
def capture(name):app.processEvents();w.grab().save(str(a.output/(name+'.png')));screens.append(name)
def slow(reader,cancel):
    for _ in range(80):check_cancel(cancel);time.sleep(.01)
    raise OSError('暂时无法读取资料库，请重试。')
for small in (False,True):
    prefix='small-' if small else '';w.resize(620,420) if small else w.resize(1000,700)
    for empty in (False,True):
        w.queries.invalidate('events')
        if empty:w.events_list.clear();w.empty(w.events_list,'正在加载提醒…')
        w.read_page('events',w._query_active['events'],('events',),slow,w._apply_events)
        QTest.qWait(220);capture(prefix+('first-' if empty else 'refresh-')+'loading')
        assert w.wait_for_queries();capture(prefix+('first-' if empty else 'refresh-')+'error');assert w.query_retry.isVisible()
        w.query_retry.click();assert w.wait_for_queries();assert w.events_list.item(0).data(Qt.UserRole);capture(prefix+'retry-restored')
(a.output/'report.json').write_text(json.dumps(dict(passed=True,theme=a.theme,screens=sorted(set(screens))),ensure_ascii=False,indent=2),encoding='utf-8');w.close();w.queries.close();s.close()
