"""Independent monitor windows must never outlive the pet's visibility."""
import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_live2d_integration import pet


def assert_hidden(pet):
    assert not any(w.isVisible() for w in
                   (pet, pet.monitor_button, pet.monitor_capsule, pet.details_panel, pet.bubble))


@pytest.mark.parametrize('always', [False, True])
@pytest.mark.parametrize('details', [False, True])
@pytest.mark.parametrize('direct', [False, True])
def test_hidden_windows_reject_late_callbacks(pet, always, details, direct, monkeypatch):
    monkeypatch.setattr(pet, '_check_auto_cleanup', lambda: None)
    pet.settings.monitor_always_visible = always
    pet._set_monitor_visibility(button=True, capsule=True)
    if details:
        pet.open_details_panel()
        assert pet.details_panel.isVisible()
    pet.monitor_hide_timer.start(10)
    pet.monitor_button._click_timer.start(10)
    (pet.hide if direct else pet.hide_pet)()
    assert not pet.monitor_hide_timer.isActive()
    assert not pet.monitor_button._click_timer.isActive()
    assert_hidden(pet)
    for _ in range(3):
        pet._hide_monitor_if_allowed()
        for widget in (pet.monitor_button, pet.monitor_capsule):
            pet.eventFilter(widget, QEvent(QEvent.Enter))
            pet.eventFilter(widget, QEvent(QEvent.Leave))
        pet.monitor_button.singleClicked.emit()
        pet.close_details_panel()
        pet._refresh_monitor()
        pet._position_monitor_overlays()
        pet._show_monitor_button()
        pet.toggle_monitor_always(always)
        QApplication.processEvents()
        assert_hidden(pet)
    assert pet.settings.monitor_always_visible == always


@pytest.mark.parametrize('always', [False, True])
def test_real_timers_and_pending_click_cannot_reopen_hidden_pet(pet, always, monkeypatch):
    monkeypatch.setattr(pet, '_check_auto_cleanup', lambda: None)
    pet.settings.monitor_always_visible = always
    pet._set_monitor_visibility(button=True, capsule=True)
    QTest.mouseClick(pet.monitor_button, Qt.LeftButton)
    assert pet.monitor_button._click_timer.isActive()
    pet.monitor_hide_timer.start(800)
    # Multiple real sampler timeouts; no test-only replacement for the callback.
    pet.monitor_timer.setInterval(100)
    pet.hide_pet()
    QTest.qWait(950)
    assert_hidden(pet)


@pytest.mark.parametrize('always', [False, True])
@pytest.mark.parametrize('near', [False, True])
def test_show_restores_preferences_without_reopening_details(pet, always, near, monkeypatch):
    monkeypatch.setattr(pet, '_pointer_near_model', lambda point: near)
    # Keep native cursor events from affecting these explicit restore cases.
    QCursor.setPos(QPoint(-10000, -10000))
    pet.settings.monitor_always_visible = always
    pet.settings.paused = True
    pet.open_details_panel()
    for _ in range(4):
        pet.hide_pet()
        assert_hidden(pet)
        pet.show_pet()
        assert not pet.details_panel.isVisible()
        assert pet.monitor_button.isVisible() == near
        assert pet.monitor_capsule.isVisible() == (near or always)
        assert pet.settings.paused


def test_hidden_refresh_still_samples_and_runs_cleanup_policy(pet, monkeypatch):
    calls = []
    network = pet.latest_network
    monkeypatch.setattr(pet.network_sampler, 'sample', lambda: calls.append('sample') or network)
    monkeypatch.setattr(pet, '_check_auto_cleanup', lambda: calls.append('policy'))
    pet.hide_pet()
    pet._refresh_monitor()
    assert calls == ['sample', 'policy']
    assert_hidden(pet)


def test_hidden_preference_change_applies_on_restore(pet, monkeypatch):
    monkeypatch.setattr(pet, '_pointer_near_model', lambda point: False)
    pet.hide_pet()
    pet.toggle_monitor_always(True)
    assert_hidden(pet)
    pet.show_pet()
    assert pet.monitor_capsule.isVisible()
    assert not pet.monitor_button.isVisible()
