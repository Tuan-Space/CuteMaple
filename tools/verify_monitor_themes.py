"""Render both monitor themes at a requested Qt scale; no OS theme/settings writes."""
import argparse,os,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--scale',default='1');a=p.parse_args()
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['QT_SCALE_FACTOR']=a.scale
from PySide6.QtWidgets import QApplication,QLabel,QPushButton
from PySide6.QtGui import QPalette,QFontDatabase,QFont
from PySide6.QtCore import Qt,QEvent
from monitor_ui import DetailsPanel,MonitorCapsule,MonitorButton,AutoCleanDialog
from resource_monitor import NetworkSnapshot,MemorySnapshot
from pet_core import PetSettings
app=QApplication([]);a.output.mkdir(parents=True,exist_ok=False)
font_id=QFontDatabase.addApplicationFont(str(ROOT/'assets/fonts/雅痞-简+常规体.otf'));families=QFontDatabase.applicationFontFamilies(font_id)
assert families,'Bundled font unavailable'
app.setFont(QFont(families[0],11))
import monitor_ui

widgets=[DetailsPanel(),MonitorCapsule(),MonitorButton(),AutoCleanDialog(PetSettings(autostart=False))]
net=NetworkSnapshot(12.6*1024**2,935*1024,987*1024**2,103*1024**2,{'Ethernet / Realtek PCIe 2.5GbE Family Controller':(1024,512)})
mem=MemorySnapshot(load_percent=67,physical_total=32*1024**3,physical_available=10*1024**3)
widgets[0].update_stats(net,mem);widgets[1].update_stats(net,mem);records=[]
for name,scheme in [('light',Qt.ColorScheme.Light),('dark',Qt.ColorScheme.Dark)]:
 # Inject only the theme source, then use the real Qt theme notification/bindings.
 monitor_ui.system_theme=lambda: monitor_ui.DARK if name=='dark' else monitor_ui.LIGHT
 app.styleHints().colorSchemeChanged.emit(scheme);app.processEvents()
 for widget in widgets:
  widget.show();app.processEvents();widget.grab().save(str(a.output/f'{name}-{type(widget).__name__}.png'))
  records.append({'theme':name,'widget':type(widget).__name__,'size':[widget.width(),widget.height()],
   'minimum':[widget.minimumSizeHint().width(),widget.minimumSizeHint().height()],
   'colors':widget._theme,'labels':[{'text':v.text(),'rect':v.geometry().getRect(),'color':v.palette().color(QPalette.WindowText).name()} for v in widget.findChildren(QLabel)]})
 widgets[3].interval_enabled.setChecked(True);widgets[3].memory_enabled.setChecked(True)
 app.processEvents();widgets[3].grab().save(str(a.output/f'{name}-checked-settings.png'))
 widgets[3].interval.showPopup();app.processEvents();widgets[3].interval.view().grab().save(str(a.output/f'{name}-dropdown.png'));widgets[3].interval.hidePopup()
 # A theme change must retain progress and an existing widget instance.
 widgets[0].set_cleaning(True);widgets[0].show_result({'status':'running','current_step':'registry_cache'})
 widgets[2].set_cleaning(True);app.processEvents()
 widgets[0].grab().save(str(a.output/f'{name}-progress.png'));widgets[2].grab().save(str(a.output/f'{name}-progress-button.png'))
 button=widgets[0].clean_button
 for state in ('hover','pressed','focus','disabled'):
  if state=='hover':app.sendEvent(button,QEvent(QEvent.Enter))
  if state=='pressed':button.setDown(True)
  if state=='focus':button.setDown(False);button.setFocus()
  if state=='disabled':button.setEnabled(False)
  app.processEvents();button.grab().save(str(a.output/f'{name}-button-{state}.png'))
 button.setEnabled(True);button.clearFocus();app.sendEvent(button,QEvent(QEvent.Leave))
 widgets[0].scroll.verticalScrollBar().setValue(widgets[0].scroll.verticalScrollBar().maximum())
 app.processEvents();widgets[0].grab().save(str(a.output/f'{name}-scrolled-bottom.png'))
 widgets[0].scroll.verticalScrollBar().setValue(0)
 widgets[0].set_cleaning(False)
for widget in widgets:widget.close()
(a.output/'report.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
print([(r['theme'],r['widget'],r['size'],r['minimum']) for r in records])
