"""Explicit, ordinary-user GUI acceptance of real cleanup in an isolated profile.

No fault injection or privileged API lives here. Every operation goes through
the same PetWindow action, UAC, pipe and result polling as a user's click.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import time


def run(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--allow-real-cleanup', action='store_true', required=True)
    args = parser.parse_args(argv)
    args.profile.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if (args.profile / 'config.json').exists() or args.report.exists():
        raise ValueError('Acceptance needs a new isolated profile and report')
    os.environ['MEINIFENG_PROFILE_DIRECTORY'] = str(args.profile.resolve())
    os.environ['MEINIFENG_DISABLE_AUTOSTART'] = '1'
    import memory_cleaner as client
    import cleanup_session as session
    from cleanup_protocol import STEPS, digest, operation_path
    from cleanup_process import identity_alive, current_identity
    from pet_core import PetSettings, save_settings, stable_application_path
    from PySide6.QtCore import QTimer
    import pet_app
    if client.is_process_elevated():
        raise PermissionError('GUI acceptance must run as the ordinary user')
    save_settings(PetSettings(autostart=False, roaming_enabled=False,
                             auto_clean_interval_enabled=False, auto_clean_memory_enabled=False))
    helper = client.helper_path()
    report = {'version': '2.1.0', 'passed': False, 'realCleanupPerformed': False,
              'method': 'ordinary PetWindow.start_memory_cleanup with real UAC',
              'observerElevated': False, 'client': current_identity(),
              'helper': str(helper), 'helperSha256': digest(helper),
              'mainSha256': digest(stable_application_path()), 'startedAt': time.time(),
              'operations': [], 'progress': [], 'errors': [], 'sessionIdentities': []}
    pet_refs, timers = [], []
    phase = 'loading'
    deadline = time.monotonic() + 180
    next_at = 0.
    session_process = None
    received = set()

    def persist():
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def observer(app, pet):
        nonlocal phase, next_at, session_process
        pet_refs.append(pet)

        def stop(error=None):
            nonlocal phase
            if error:
                report['errors'].append(error)
            phase = 'done'
            timer.stop()
            persist()
            pet.quit_app()

        def tick():
            nonlocal phase, next_at, session_process
            try:
                if time.monotonic() > deadline:
                    return stop('Acceptance deadline exceeded; no extra cleanup requested')
                feedback = dict(pet._cleanup_feedback or {})
                stamp = (feedback.get('operation_id'), feedback.get('status'), feedback.get('step'))
                if not report['progress'] or report['progress'][-1]['stamp'] != list(stamp):
                    report['progress'].append({'at': time.time(), 'stamp': list(stamp),
                                              'bubble': pet.bubble.label.text(), 'feedback': feedback})
                if session._client is not None:
                    session_process = session._client.process.identity
                    if session_process not in report['sessionIdentities']:
                        report['sessionIdentities'].append(session_process)
                if phase == 'loading' and pet._live2d_active:
                    phase = 'running'; pet.start_memory_cleanup()
                    # Duplicate user actions must refresh progress, not schedule more work.
                    for _ in range(4):
                        pet.start_memory_cleanup()
                elif phase == 'waiting' and time.monotonic() >= next_at:
                    phase = 'running'; pet.start_memory_cleanup()
                    for _ in range(4):
                        pet.start_memory_cleanup()
                value = pet.cleanup_result
                if phase == 'running' and value and value.get('terminal') and value.get('operation_id') not in received:
                    op = value['operation_id']; received.add(op)
                    report['realCleanupPerformed'] = True
                    report['operations'].append(value)
                    assert value['status'] == 'succeeded' and value['ok'], value.get('message')
                    assert value['processExitVerified'] and value['exitCodeVerified']
                    assert not value.get('guardTriggered')
                    assert set(value['steps']) == set(STEPS)
                    for step in STEPS:
                        row = value['steps'][step]
                        assert row['ok'] and row['exit_verified'] and row['exit_code'] == 0
                        assert row['processElevated'] and row['implementation'] == 'cpp-msvc'
                        assert row['nativeCalls'] and all(call['api'] != 'TEST_ONLY' for call in row['nativeCalls'])
                    assert not any(identity_alive(row['worker']) for row in value['workers'])
                    assert not identity_alive(value['supervisor'])
                    source = operation_path(args.profile, op)
                    report.setdefault('operationFiles', {}).update({str(p): digest(p) for p in source.glob('*.json')})
                    if len(received) == 3:
                        return stop()
                    phase = 'waiting'; next_at = time.monotonic() + 2
                elif phase == 'running' and feedback.get('status') in {'cancelled', 'helper_missing', 'security_blocked', 'failed'} and not pet.cleanup_operation and pet._install_future is None and pet._cleanup_future is None:
                    return stop(feedback.get('message', 'Cleanup did not start'))
                persist()
            except Exception as exc:
                stop(f'{type(exc).__name__}: {exc}')

        timer = QTimer(app); timer.setInterval(100); timer.timeout.connect(tick); timers.append(timer); timer.start()
        persist()

    code = pet_app.run(observer=observer)
    session.close_session()
    stop_deadline = time.monotonic() + 6
    while session_process and identity_alive(session_process) and time.monotonic() < stop_deadline:
        time.sleep(.05)
    report['sessionExited'] = not session_process or not identity_alive(session_process)
    report['helperSha256After'] = digest(helper)
    report['finishedAt'] = time.time()
    report['exitCode'] = code
    report['passed'] = (code == 0 and len(received) == 3 and len(report['sessionIdentities']) == 1
                        and report['sessionExited'] and not report['errors']
                        and report['helperSha256After'] == report['helperSha256'])
    persist()
    return 0 if report['passed'] else 1
