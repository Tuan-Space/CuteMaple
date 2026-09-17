import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtWidgets import QApplication,QPushButton,QLabel
from PySide6.QtGui import QColor,QPalette,QFont,QFontDatabase
from PySide6.QtCore import Qt,QRect
from pathlib import Path
import monitor_ui as ui
from resource_monitor import NetworkSnapshot,MemorySnapshot
from pet_core import PetSettings
from test_interaction_ui import contrast

@pytest.mark.parametrize('colors',[ui.LIGHT,ui.DARK])
def test_theme_contrast_palette_and_all_monitor_controls(monkeypatch,colors):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(ui,'system_theme',lambda:colors)
    for fg in ('text','muted','accent'):
        for bg in ('background','card','button','disabled'):
            assert contrast(QColor(colors[fg]),QColor(colors[bg]))>=4.5,(fg,bg)
    for fg in ('border','focus'):
        for bg in ('background','card','button'):
            assert contrast(QColor(colors[fg]),QColor(colors[bg]))>=3,(fg,bg)
    widgets=[ui.DetailsPanel(),ui.MonitorCapsule(),ui.MonitorButton(),ui.AutoCleanDialog(PetSettings(autostart=False))]
    try:
        for widget in widgets:
            widget.show();app.processEvents();assert widget._theme==colors
            for label in widget.findChildren(QLabel):
                assert label.palette().color(QPalette.WindowText).name() in (colors['text'],colors['muted'],colors['accent'])
            for button in widget.findChildren(QPushButton):
                assert contrast(button.palette().color(QPalette.ButtonText),button.palette().color(QPalette.Button))>=4.5
        close=widgets[0].findChild(QPushButton,'close');called=[]
        widgets[0].closeRequested.connect(lambda:called.append(True));close.click();assert called
        assert close.contentsRect().width()>=30 and 'padding: 0' in widgets[0].styleSheet()
    finally:
        for widget in widgets:widget.close();widget.deleteLater()


def test_theme_signal_updates_existing_visible_and_hidden_panels_without_reset(monkeypatch):
    app=QApplication.instance() or QApplication([]);selected=[ui.LIGHT]
    monkeypatch.setattr(ui,'system_theme',lambda:selected[0])
    panel=ui.DetailsPanel();capsule=ui.MonitorCapsule();panel.show()
    panel.update_stats(NetworkSnapshot(123456,7890),MemorySnapshot());panel.set_cleaning(True)
    panel.show_result({'status':'running','current_step':'registry_cache'})
    before=(panel.net_down.text(),panel.result.text(),panel.clean_button.text(),id(panel))
    selected[0]=ui.DARK;app.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark);app.processEvents()
    assert panel._theme==capsule._theme==ui.DARK
    assert before==(panel.net_down.text(),panel.result.text(),panel.clean_button.text(),id(panel))
    capsule.show();assert capsule._theme==ui.DARK
    for widget in (panel,capsule):widget.close();widget.deleteLater()


def test_long_network_and_status_keep_close_and_cleanup_outside_scroll(monkeypatch):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(ui,'system_theme',lambda:ui.DARK)
    # Constrain the screen explicitly: real fonts can fit this sample on a tall
    # screen, so an empty offscreen font database must not define this scenario.
    class SmallScreen:
        def availableGeometry(self):return QRect(0,0,1280,600)
    monkeypatch.setattr(ui.DetailsPanel,'screen',lambda self:SmallScreen())
    panel=ui.DetailsPanel();panel.update_stats(NetworkSnapshot(1024,2048,0,0,{'Very long Ethernet adapter name '*8:(1024,2048)}),MemorySnapshot())
    panel.show_result({'status':'failed','message':'Detailed failure message '*8});panel.show();app.processEvents()
    assert '1.0 KB/s' in panel.net_down.text() and '2.0 KB/s' in panel.net_up.text()
    assert not panel.content.isAncestorOf(panel.clean_button)
    assert panel.clean_button.isVisible() and panel.findChild(QPushButton,'close').isVisible()
    assert panel.scroll.verticalScrollBar().maximum()>0
    panel.close();panel.deleteLater()
