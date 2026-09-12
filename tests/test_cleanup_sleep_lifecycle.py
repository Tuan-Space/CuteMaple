"""Controlled renderer/backend replies only; no privileged operation is run."""
import concurrent.futures
import time

import pytest

from test_live2d_integration import pet, activate
import pet_app




def test_rejected_launch_does_not_wake_an_existing_sleep_pose(pet, monkeypatch):
    pet.sleep_phase, pet.sleep_started_at = 'enter', None
    pet.start_state('sleep_enter')
    complete = concurrent.futures.Future()
    complete.set_result({'ok': False, 'status': 'failed', 'message': 'controlled prelaunch rejection'})
    monkeypatch.setattr(pet._install_executor, 'submit', lambda *_: complete)
    pet.start_memory_cleanup()
    pet._poll_cleanup()
    assert pet.cleanup_operation is None
    assert (pet.state, pet.sleep_phase, pet.sleep_started_at) == ('sleep_enter', 'enter', None)
