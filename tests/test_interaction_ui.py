"""Panel remains legible under a dark system palette; no provider/UI input."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QPushButton

from interaction_ui import EFFECT_CHOICES, InteractionPanel


def luminance(color):
    channels = [color.redF(), color.greenF(), color.blueF()]
    channels = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(v * weight for v, weight in zip(channels, (.2126, .7152, .0722)))


def contrast(foreground, background):
    values = sorted((luminance(foreground), luminance(background)))
    return (values[1] + .05) / (values[0] + .05)


def test_panel_button_text_overrides_light_text_from_dark_system_palette():
    app = QApplication.instance() or QApplication([])
    previous = app.palette()
    palette = QPalette(previous)
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        palette.setColor(group, QPalette.ButtonText, QColor('#ffffff'))
        palette.setColor(group, QPalette.Text, QColor('#ffffff'))
        palette.setColor(group, QPalette.WindowText, QColor('#ffffff'))
        palette.setColor(group, QPalette.Window, QColor('#202020'))
    app.setPalette(palette)
    panel = InteractionPanel()
    try:
        panel.ensurePolished()
        buttons = panel.findChildren(QPushButton)
        assert len(buttons) == 2
        for button in buttons:
            button.ensurePolished()
            for group in (QPalette.Active, QPalette.Inactive):
                assert contrast(button.palette().color(group, QPalette.ButtonText),
                                button.palette().color(group, QPalette.Button)) >= 4.5
            button.setEnabled(False)
            button.ensurePolished()
            assert contrast(button.palette().color(QPalette.Disabled, QPalette.ButtonText),
                            button.palette().color(QPalette.Disabled, QPalette.Button)) >= 4.5
        assert contrast(panel.effects.palette().color(QPalette.Text), panel.effects.palette().color(QPalette.Base)) >= 4.5
    finally:
        panel.close()
        app.setPalette(previous)


def test_effect_menu_keeps_other_choices_but_removes_wind():
    choices = dict((value, title) for title, value in EFFECT_CHOICES)
    assert 'wind' not in choices
    assert set(choices) == {'leaf', 'note', 'glasses', 'star', 'heart', 'bubble', 'petal', 'keyboard', 'audio'}
    assert 'clean_dust' not in choices
