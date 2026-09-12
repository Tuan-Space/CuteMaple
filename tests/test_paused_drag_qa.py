"""Synthetic fixtures test rejection/acceptance logic, never desktop acceptance."""
from copy import deepcopy

import pytest

from desktop_check import PausedDragEvidence


def state(**changes):
    value = {'window': [300, 400, 200, 200], 'area': [0, 0, 999, 799],
             'anchors': {'ground': [.5, .9], 'left': [.2, .3], 'right': [.8, .3], 'hang': [.5, .1]},
             'state': 'idle', 'base': 'ground', 'motion': None, 'renderName': 'idle',
             'token': 2, 'generation': 1, 'live': True, 'paused': True, 'pausedSetting': True,
             'visible': True, 'dragging': False, 'screenValid': True, 'geometryAvailable': True,
             'motionY': 300., 'climbSpec': {'risePerCycle': .2, 'cycleDuration': 2., 'phaseTravel': [[0, 0], [1, 1]]}}
    value.update(changes)
    return value


def release(tracker, xy, **changes):
    tracker.press((350, 450), state(), 0)
    pointer = (350+xy[0]-300, 450+xy[1]-400)
    tracker.move(pointer, state(), .1)
    value = state(window=[*xy, 200, 200], dragging=True)
    value.update(changes)
    tracker.release(pointer, value, .2)


def capture(snapshot, **changes):
    value = {'visiblePixels': 500, 'glError': 0,
             'playback': {'paused': True, 'token': snapshot['token'], 'name': snapshot['renderName'],
                          'evaluated': True, 'motionTimeSeconds': 1., 'nativeUpdateCount': 10,
                          'staticSampleCount': 1, 'cycle': 0},
             'nativeVertexSignature': {'floatCount': 240, 'hash32': 'deadbeef'}}
    for key, change in changes.items():
        if isinstance(change, dict): value[key].update(change)
        else: value[key] = change
    return value


def deliver(tracker, snapshot, now, **changes):
    tracker.capture(snapshot, now, capture(snapshot, **changes),
                    [snapshot['generation'], snapshot['token'], snapshot['renderName']])


def stable(tracker, snapshot):
    deliver(tracker, snapshot, .3)
    assert not tracker.checks['staticGeometry']
    deliver(tracker, snapshot, 1.)
    assert tracker.checks['staticGeometry'] and not tracker.checks['resumeWithoutCatchUp']


@pytest.mark.parametrize('xy,settled,edge,corner', [
    ((10, 300), state(window=[-40, 300, 200, 200], base='climb_left', state='climb_left', renderName='climb_left', motion='climb'), 'left', False),
    ((790, 300), state(window=[839, 300, 200, 200], base='climb_right', state='climb_right', renderName='climb_right', motion='climb'), 'right', False),
    ((300, 10), state(window=[300, -20, 200, 200], base='top_swing', state='swing_idle', renderName='swing_idle'), 'top', False),
    ((10, 10), state(window=[0, -20, 200, 200], base='top_swing', state='swing_idle', renderName='swing_idle'), 'top', True),
    ((790, 10), state(window=[790, -20, 200, 200], base='top_swing', state='swing_idle', renderName='swing_idle'), 'top', True),
])
def test_pause_preserved_and_real_static_capture_required_before_resume(xy, settled, edge, corner):
    tracker = PausedDragEvidence()
    release(tracker, xy)
    stable(tracker, settled)
    assert tracker.checks[edge] and tracker.checks['cornerTop'] is corner
    tracker.sample(settled, 5.)  # Time remains frozen during a long actual pause.
    resumed = {**settled, 'pausedSetting': False, 'paused': False, 'motionY': 299.}
    deliver(tracker, resumed, 5.05, playback={'paused': False, 'motionTimeSeconds': 1.04, 'nativeUpdateCount': 11})
    assert not tracker.passed
    deliver(tracker, resumed, 5.4, playback={'paused': False, 'motionTimeSeconds': 1.38, 'nativeUpdateCount': 14})
    assert tracker.passed and tracker.checks['resumeWithoutCatchUp']
    assert tracker.records[-1]['pausedSettingAtPress'] is True


def left_case():
    tracker = PausedDragEvidence()
    release(tracker, (10, 300))
    settled = state(window=[-40, 300, 200, 200], base='climb_left', state='climb_left', renderName='climb_left', motion='climb')
    return tracker, settled


def test_hover_and_unrelated_release_after_resume_do_not_reject_static_drag():
    tracker, settled = left_case()
    stable(tracker, settled)
    resumed = {**settled, 'pausedSetting': False, 'paused': False, 'motionY': 299.}
    deliver(tracker, resumed, 1.05, playback={'paused': False, 'motionTimeSeconds': 1.04, 'nativeUpdateCount': 11})
    tracker.move((350, 450), resumed, 1.1, left_down=False)
    tracker.release((350, 450), resumed, 1.2)
    assert tracker.current['staticVerified']
    deliver(tracker, resumed, 1.4, playback={'paused': False, 'motionTimeSeconds': 1.38, 'nativeUpdateCount': 14})
    assert tracker.checks['resumeWithoutCatchUp']


@pytest.mark.parametrize('change', [
    {'playback': {'motionTimeSeconds': 1.2}}, {'playback': {'nativeUpdateCount': 11}},
    {'nativeVertexSignature': {'hash32': 'changed'}}, {'playback': {'paused': False}},
    {'playback': {'staticSampleCount': 0}},
])
def test_native_motion_drift_while_paused_rejects(change):
    tracker, settled = left_case()
    deliver(tracker, settled, .3)
    deliver(tracker, settled, 1., **change)
    assert tracker.current is None and tracker.records[-1]['status'] == 'rejected'
    assert not any(tracker.checks.values())


def test_clock_catchup_after_pause_is_rejected():
    tracker, settled = left_case()
    stable(tracker, settled)
    tracker.sample(settled, 10.)
    resumed = {**settled, 'pausedSetting': False, 'paused': False}
    deliver(tracker, resumed, 10.1, playback={'paused': False, 'motionTimeSeconds': 10., 'nativeUpdateCount': 11})
    assert not tracker.passed and tracker.records[-1]['status'] == 'rejected'
    assert not tracker.checks['left']


def test_host_position_catchup_is_rejected_even_if_native_clock_does_not_jump():
    tracker, settled = left_case()
    stable(tracker, settled)
    tracker.sample(settled, 10.)
    resumed = {**settled, 'pausedSetting': False, 'paused': False, 'motionY': 100., 'window': [-40, 100, 200, 200]}
    deliver(tracker, resumed, 10.1, playback={'paused': False, 'motionTimeSeconds': 1.08, 'nativeUpdateCount': 11})
    assert not tracker.passed and tracker.records[-1]['status'] == 'rejected'


def test_hidden_movement_accumulator_cannot_advance_behind_a_fixed_window():
    tracker, settled = left_case()
    stable(tracker, settled)
    tracker.sample({**settled, 'motionY': 320.}, 1.1)
    assert tracker.records[-1]['status'] == 'rejected' and not tracker.checks['left']


def test_corner_does_not_accept_side_contact():
    tracker = PausedDragEvidence()
    release(tracker, (10, 10))
    tracker.sample(state(window=[-40, 10, 200, 200], base='climb_left', state='climb_left'), .3)
    assert tracker.records[-1]['status'] == 'rejected'


def test_real_release_waits_for_current_static_geometry():
    tracker, settled = left_case()
    missing = {**settled, 'anchors': {}, 'geometryAvailable': False}
    deliver(tracker, missing, .3)
    assert tracker.current is not None and not tracker.checks['staticGeometry']
    stable(tracker, settled)


def test_stale_capture_and_missing_diagnostics_cannot_pass():
    tracker, settled = left_case()
    tracker.capture(settled, .3, capture(settled), [1, 99, 'climb_left'])
    value = capture(settled)
    value.pop('playback')
    tracker.capture(settled, 1., value, [1, 2, 'climb_left'])
    assert not any(tracker.checks.values()) and tracker.current is not None


def test_next_real_drag_can_preserve_static_only_evidence_without_claiming_resume():
    tracker, settled = left_case()
    stable(tracker, settled)
    tracker.press((0, 350), settled, 2., native=True)
    assert tracker.records[-1]['status'] == 'static-verified'
    assert tracker.checks['left'] and not tracker.checks['resumeWithoutCatchUp']


@pytest.mark.parametrize('phase', ['press', 'move', 'release'])
def test_synthetic_input_never_establishes_paused_drag(phase):
    tracker = PausedDragEvidence()
    tracker.press((350, 450), state(), 0, native=phase != 'press')
    tracker.move((60, 350), state(), .1, native=phase != 'move')
    tracker.release((60, 350), state(window=[10, 300, 200, 200], dragging=True), .2, native=phase != 'release')
    assert tracker.current is None and not any(tracker.checks.values())


def test_event_filter_never_consumes_synthetic_qt_paused_input():
    from PySide6.QtCore import QObject, QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from desktop_check import PhysicalDragEvidence, install_physical_drag_observer
    app = QApplication.instance() or QApplication([])
    pet, paused = QObject(), PausedDragEvidence()
    observer = install_physical_drag_observer(app, pet, PhysicalDragEvidence(), lambda: None, paused)
    observer.snapshot = lambda *_: state()
    event = QMouseEvent(QEvent.MouseButtonPress, QPointF(20, 20), QPointF(350, 450),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    assert not event.spontaneous() and observer.eventFilter(pet, event) is False
    assert paused.current is None and observer.parent() is app
    observer.stop()


def test_reaction_evidence_copies_actual_effects_and_current_native_binding():
    from desktop_check import reaction_capture_evidence
    value = {'generation': 4, 'playback': {'token': 7, 'name': 'idle', 'paused': False, 'evaluated': True},
             'effects': {'visiblePixels': 83, 'drawnCount': 2, 'kinds': ['leaf', 'note'],
                         'particles': [{'kind': 'leaf', 'x': .5, 'y': .4, 'radius': .1}], 'pixelBounds': [.1, .2, .3, .4]}}
    result = reaction_capture_evidence(value, [4, 7, 'idle'])
    assert result['captureBinding'] == [4, 7, 'idle']
    assert result['actualVisibleEffects'] == value['effects']
    value['effects']['visiblePixels'] = 0
    assert reaction_capture_evidence(value, [4, 7, 'idle'])['actualVisibleEffects']['visiblePixels'] == 0
    assert reaction_capture_evidence(value, [4, 8, 'idle']) == {}
    value['playback']['paused'] = True
    assert reaction_capture_evidence(value, [4, 7, 'idle']) == {}
