"""Real executor scheduling with controlled results; no task, UAC, or cleaner is executed."""
import threading

import pytest

from test_live2d_integration import pet, activate, event
import pet_app


def delayed_begin(pet, monkeypatch, result):
    entered, release = threading.Event(), threading.Event()
    def begin():
        entered.set()
        assert release.wait(3), 'controlled begin was not released'
        return result
    monkeypatch.setattr(pet_app, 'begin_cleanup', begin)
    pet.start_memory_cleanup()
    assert entered.wait(1) and pet._cleanup_future.running()
    return release


@pytest.mark.parametrize('initial', [
    {'ok': True, 'operation_id': 'a'*32, 'status': 'queued'},
    {'ok': False, 'operation_id': 'a'*32, 'status': 'busy'},
])
def test_cancel_survives_real_delayed_begin_and_precedes_first_observer_handshake(pet, monkeypatch, initial):
    activate(pet)
    sequence = []
    release = delayed_begin(pet, monkeypatch, initial)
    monkeypatch.setattr(pet, '_begin_clean_task_install', lambda *_: pytest.fail('cancelled request must not request UAC'))
    monkeypatch.setattr(pet_app, 'cancel_cleanup', lambda op: sequence.append(('cancel', op)) or {'ok': True, 'status': 'cancelling'})
    result = {'operation_id': 'a'*32, 'status': 'cancelling', 'terminal': False, 'processExitVerified': False}
    monkeypatch.setattr(pet_app, 'read_cleanup', lambda op: sequence.append(('read', op)) or result)
    try:
        assert pet.details_panel.cancel_button.isEnabled()
        pet.cancel_memory_cleanup()
        assert not sequence and pet._cleanup_future is not None
        assert pet._cleanup_cancel_requested and not pet.details_panel.cancel_button.isEnabled()
    finally:
        release.set()
    pet._cleanup_future.result(timeout=1)
    pet._poll_cleanup()
    assert sequence[:2] == [('cancel', 'a'*32), ('read', 'a'*32)]
    assert pet.cleanup_operation == 'a'*32 and not pet.cleanup_finish_pending
    assert pet.settings.last_clean_timestamp == 0 and not pet.details_panel.cancel_button.isEnabled()
    event(pet, 'cycle', cycle=20)
    assert pet.cleanup_operation == 'a'*32  # Visual progress never proves backend exit.
    result.update(status='cancelled', terminal=True, processExitVerified=True, steps={})
    pet._poll_cleanup()
    event(pet, 'cycle', cycle=21)
    assert pet.cleanup_operation is None and not pet._cleanup_cancel_requested


def test_cancelled_delayed_authorization_result_cannot_launch_uac(pet, monkeypatch):
    feedback = []
    pet.sleep_phase, pet.sleep_started_at = 'loop', 123.
    pet.start_state('sleep_loop')
    release = delayed_begin(pet, monkeypatch, {'ok': False, 'status': 'authorization_required'})
    monkeypatch.setattr(pet, '_begin_clean_task_install', lambda *_: pytest.fail('late authorization requirement after cancel'))
    monkeypatch.setattr(pet, '_show_cleanup_feedback', lambda value: feedback.append(value))
    try:
        pet.cancel_memory_cleanup()
    finally:
        release.set()
    pet._cleanup_future.result(timeout=1)
    pet._poll_cleanup()
    assert pet.cleanup_operation is None and pet._cleanup_future is None
    assert not pet.cleanup_poll_timer.isActive() and pet.details_panel.clean_button.isEnabled()
    assert feedback[-1]['status'] == 'cancelled' and feedback[-1]['operationStarted'] is False
    assert 'processExitVerified' not in feedback[-1] and pet.settings.last_clean_timestamp == 0
    assert (pet.sleep_phase, pet.sleep_started_at, pet.state) == ('loop', 123., 'sleep_loop')


def test_queued_future_is_cancelled_before_begin_runs(pet, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    blocker = pet._install_executor.submit(lambda: (entered.set(), release.wait(3)))
    assert entered.wait(1)
    called, feedback = [], []
    monkeypatch.setattr(pet_app, 'begin_cleanup', lambda: called.append(True) or pytest.fail('cancelled queue must not run'))
    monkeypatch.setattr(pet, '_show_cleanup_feedback', lambda value: feedback.append(value))
    try:
        pet.start_memory_cleanup()
        pending = pet._cleanup_future
        assert not pending.running()
        pet.cancel_memory_cleanup()
        assert pending.cancelled() and pet._cleanup_future is None
        assert pet.cleanup_operation is None and pet.details_panel.clean_button.isEnabled()
        assert feedback[-1]['operationStarted'] is False and 'processExitVerified' not in feedback[-1]
    finally:
        release.set(); blocker.result(timeout=1)
    assert not called


def test_cancel_write_failure_remains_a_failure_and_allows_retry(pet, monkeypatch):
    activate(pet)
    pet._begin_cleanup_operation('b'*32)
    feedback = []
    monkeypatch.setattr(pet, '_show_cleanup_feedback', lambda value: feedback.append(value))
    monkeypatch.setattr(pet_app, 'cancel_cleanup', lambda op: {'ok': False, 'message': 'controlled write failure'})
    pet.cancel_memory_cleanup()
    assert pet.cleanup_operation == 'b'*32 and not pet.cleanup_finish_pending
    assert not pet._cleanup_cancel_requested and pet.details_panel.cancel_button.isEnabled()
    assert feedback[-1]['ok'] is False and 'processExitVerified' not in feedback[-1]
