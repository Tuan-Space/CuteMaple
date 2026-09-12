from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget

import desktop_decorations as decorations


def _app():
    return QApplication.instance() or QApplication([])


def test_decoration_rate_count_lifetime_and_no_arbitrary_text(monkeypatch):
    app = _app()
    parent = QWidget()
    parent.resize(220, 220)
    layer = decorations.DesktopDecorations(parent)
    parent.show()
    layer.show()
    app.processEvents()
    now = [100.0]
    monkeypatch.setattr(decorations.time, "monotonic", lambda: now[0])
    try:
        assert layer.testAttribute(Qt.WA_TransparentForMouseEvents)
        layer.trigger("user typed this", 50, 60)
        assert layer._particles == []
        for _ in range(100):
            layer.trigger("keyboard", 50, 60)
        assert len(layer._particles) == 2
        for _ in range(60):
            now[0] += 0.4
            layer.trigger("happy", 50, 60)
        assert len(layer._particles) == layer.MAX_PARTICLES
        assert all(not p.text or p.text in layer.TEXTS for p in layer._particles)
        image = QImage(220, 220, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        layer.render(image)
        for _ in range(30):
            now[0] += 0.1
            layer._advance()
        assert layer._particles == []
        assert not layer._timer.isActive()
    finally:
        layer.stop()
        parent.close()


def test_hidden_or_suspended_overlay_does_no_work():
    app = _app()
    parent = QWidget()
    layer = decorations.DesktopDecorations(parent)
    parent.show()
    layer.show()
    app.processEvents()
    layer.trigger("audio", 30, 50)
    assert layer._particles
    layer.set_suspended(True)
    assert not layer._particles and not layer._timer.isActive()
    layer.trigger("audio", 30, 50)
    assert not layer._particles
    layer.set_suspended(False)
    layer.trigger("audio", 30, 50)
    assert layer._particles
    parent.hide()
    app.processEvents()
    assert not layer._particles and not layer._timer.isActive()
    layer.trigger("happy", 30, 50)
    assert not layer._particles
    parent.close()


def test_wind_removed_and_cleaning_emits_only_dust_near_the_supplied_fan():
    app = _app()
    parent = QWidget()
    parent.resize(220, 220)
    layer = decorations.DesktopDecorations(parent)
    parent.show()
    layer.show()
    app.processEvents()
    try:
        layer.trigger('wind', 80, 150)
        assert layer._particles == []
        layer.trigger('clean_dust', 80, 150)
        assert len(layer._particles) == 3
        assert all(p.kind == 'dust' and abs(p.x - 80) < .01 and abs(p.y - 150) < .01 for p in layer._particles)
        assert layer.testAttribute(Qt.WA_TransparentForMouseEvents)
    finally:
        layer.stop()
        parent.close()
