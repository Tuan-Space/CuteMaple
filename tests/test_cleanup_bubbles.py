"""Visible cleanup lifecycle with controlled backend replies, without UAC/API calls."""
from concurrent.futures import Future

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QApplication

import pet_app
import monitor_ui
from test_live2d_integration import pet


@pytest.fixture(autouse=True)
def no_windows_notifications(monkeypatch):
    monkeypatch.setattr(pet_app.QSystemTrayIcon, 'showMessage',
                        lambda *a, **k: pytest.fail('Windows notification was emitted'))


def test_cleanup_progress_keeps_one_bubble_and_never_changes_animation(pet, monkeypatch):
    operation = 'a'*32
    pet.start_state('sleep_loop')
    before = pet.state, pet._renderer_token
    pet._begin_cleanup_operation(operation)
    bubble = pet.bubble
    for step in monitor_ui.CLEANUP_STEP_NAMES:
        if step == 'process_working_sets_fallback':
            continue
        result = {'operation_id': operation, 'status': 'running', 'current_step': step}
        monkeypatch.setattr(pet_app, 'read_cleanup', lambda op: result)
        pet._poll_cleanup()
        assert bubble.isVisible() and not bubble.hide_timer.isActive()
        assert monitor_ui.CLEANUP_STEP_NAMES[step] in bubble.label.text()
        bubble.show_message('ordinary dialogue must not replace cleanup', pet.geometry(), pet._screen_area())
        assert monitor_ui.CLEANUP_STEP_NAMES[step] in bubble.label.text()
        monkeypatch.setattr(pet_app, 'begin_cleanup', lambda: pytest.fail('duplicate cleanup'))
        pet.start_memory_cleanup()
        assert pet.bubble is bubble and pet.cleanup_operation == operation
    assert (pet.state, pet._renderer_token) == before
    result = {'operation_id': operation, 'status': 'succeeded', 'terminal': True,
              'processExitVerified': False}
    pet._poll_cleanup()
    assert '正在核验后台退出' in bubble.label.text()
    assert not bubble.hide_timer.isActive() and pet.cleanup_operation == operation
    result['processExitVerified'] = True
    pet._poll_cleanup()
    assert pet.cleanup_operation is None and bubble.hide_timer.isActive()
    assert 5900 <= bubble.hide_timer.remainingTime() <= 6000


@pytest.mark.parametrize('status', ['cancelled', 'failed', 'partial', 'timed_out', 'helper_missing', 'security_blocked'])
def test_terminal_feedback_has_eight_seconds_and_no_toast(pet, monkeypatch, status):
    now = [100.]
    monkeypatch.setattr(pet_app.time, 'monotonic', lambda: now[0])
    result = {'status': status, 'message': '<b>plain error</b>'}
    pet._set_cleanup_progress(result, active=False)
    assert pet.bubble._cleanup_expires == 108.
    assert pet.bubble.label.textFormat() == Qt.PlainText
    now[0] += 2
    pet._show_cleanup_feedback(result)
    assert pet.bubble._cleanup_expires == 108.  # Repeated delivery cannot extend it.
    assert 5900 <= pet.bubble.hide_timer.remainingTime() <= 6000


def test_hidden_cleanup_restores_only_current_or_unexpired_feedback(pet, monkeypatch):
    now = [100.]
    monkeypatch.setattr(pet_app.time, 'monotonic', lambda: now[0])
    pet._set_cleanup_progress({'status': 'running', 'current_step': 'registry_cache'})
    pet.hide_pet()
    pet._set_cleanup_progress({'status': 'running', 'current_step': 'combine_memory'})
    assert not pet.bubble.isVisible()
    now[0] += 60
    pet.show_pet()
    assert pet.bubble.isVisible() and '内存合并' in pet.bubble.label.text()
    pet.hide_pet()
    pet._set_cleanup_progress({'status': 'failed', 'message': 'hidden result'}, active=False)
    now[0] += 4
    pet.show_pet()
    assert pet.bubble.isVisible() and 'hidden result' in pet.bubble.label.text()
    pet.hide_pet()
    now[0] += 5
    pet.show_pet()
    assert not pet.bubble.isVisible()


@pytest.mark.parametrize('accepted', [True, False])
def test_first_authorization_feedback_and_result_use_bubble(pet, monkeypatch, accepted):
    future = Future()
    monkeypatch.setattr(pet._install_executor, 'submit', lambda *_: future)
    started = []
    monkeypatch.setattr(pet, 'start_memory_cleanup', lambda: started.append(True))
    pet._begin_clean_task_install(True)
    assert '等待管理员授权' in pet.bubble.label.text()
    assert not pet.bubble.hide_timer.isActive()
    future.set_result({'status': 'authorized' if accepted else 'cancelled', 'ok': accepted, 'valid': accepted})
    pet._poll_clean_task_install()
    assert bool(started) == accepted
    assert ('本次运行已授权' if accepted else '本次清理已取消') in pet.bubble.label.text()


def test_auto_cleanup_waits_for_authorization_without_uac(pet, monkeypatch):
    monkeypatch.setattr(pet_app, 'session_is_valid', lambda: False)
    monkeypatch.setattr(pet._install_executor, 'submit', lambda *_: pytest.fail('automatic UAC'))
    pet.start_memory_cleanup(manual=False)
    assert '本次运行尚未授权' in pet.bubble.label.text()
    deadline = pet.bubble._cleanup_expires
    pet.start_memory_cleanup(manual=False)
    assert pet.bubble._cleanup_expires == deadline


def test_bubble_theme_anchor_and_bounds_without_recreation(pet, monkeypatch):
    pet._set_cleanup_progress({'status': 'running', 'current_step': 'empty_working_sets'})
    bubble = pet.bubble
    for theme in (monitor_ui.DARK, monitor_ui.LIGHT):
        monkeypatch.setattr(monitor_ui, 'system_theme', lambda: theme)
        QApplication.instance().styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Unknown)
        assert theme['card'] in bubble.styleSheet() and theme['text'] in bubble.styleSheet()
        assert bubble._cleanup_active and pet.bubble is bubble
    area = QRect(-1920, 0, 1920, 1080)
    for rect in (QRect(-1920, 0, 220, 220), QRect(-220, 860, 220, 220)):
        bubble.follow_pet(rect, area)
        assert area.contains(bubble.geometry())
