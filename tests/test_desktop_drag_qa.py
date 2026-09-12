"""Physical drag acceptance requires pointer travel, contact, and native playback."""
from copy import deepcopy
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

from desktop_check import PhysicalDragEvidence, install_physical_drag_observer


def snapshot(**changes):
    value = {'window': [300, 400, 200, 200], 'area': [0, 0, 999, 799],
             'anchors': {'ground': [.5, .9], 'left': [.2, .3], 'right': [.8, .3], 'hang': [.5, .1]},
             'state': 'idle', 'base': 'ground', 'motion': None, 'renderName': 'idle',
             'token': 2, 'generation': 1, 'live': True, 'paused': False,
             'visible': True, 'dragging': False, 'screenValid': True, 'geometryAvailable': True}
    value.update(changes)
    return value


def release_at(tracker, xy, **changes):
    tracker.press((350, 450), snapshot(), 0)
    pointer = (350 + xy[0] - 300, 450 + xy[1] - 400)
    # The observer sees mouse move before the application's handler moves the window.
    tracker.move(pointer, snapshot(), .1)
    released = snapshot(window=[*xy, 200, 200], dragging=True)
    released.update(changes)
    tracker.release(pointer, released, .2)
    return released


def native_frames(tracker, settled, at=.3, **changes):
    event = {'type': 'geometry', 'token': settled['token'], 'generation': settled['generation'],
             'name': settled['renderName'], 'bounds': [.1, .1, .8, .8]}
    event.update(changes)
    for i in range(3):
        tracker.sample(settled, at + i * .06, event)


@pytest.mark.parametrize('release,settled,contact', [
    ((400, 610), snapshot(window=[400, 620, 200, 200]), 'ground'),
    ((300, 10), snapshot(window=[300, -20, 200, 200], base='top_swing', state='swing_cycle', renderName='swing_cycle'), 'top'),
    ((10, 300), snapshot(window=[-40, 300, 200, 200], base='climb_left', state='climb_left', renderName='climb_left', motion='climb'), 'left'),
    ((790, 300), snapshot(window=[839, 300, 200, 200], base='climb_right', state='climb_right', renderName='climb_right', motion='climb'), 'right'),
])
def test_drag_requires_valid_contact_and_later_native_activity(release, settled, contact):
    tracker = PhysicalDragEvidence()
    release_at(tracker, release)
    tracker.sample(settled, .25)
    assert not tracker.passed
    native_frames(tracker, settled)
    assert tracker.passed
    record = tracker.records[-1]
    assert record['startPointer'] != record['endPointer']
    assert record['startWindow'] != record['releaseWindow']
    assert record['finalSettlement']['contact'] == contact
    assert len(record['nativeActivity']) == 3


def test_fall_is_not_complete_until_valid_landing_and_native_activity():
    tracker = PhysicalDragEvidence()
    release_at(tracker, (400, 300))
    falling = snapshot(window=[400, 310, 200, 200], state='fall_float', motion='fall', renderName='fall_float')
    native_frames(tracker, falling)
    assert not tracker.passed
    landed = snapshot(window=[400, 620, 200, 200], state='land', renderName='land', token=3)
    native_frames(tracker, landed, .6)
    assert tracker.passed
    assert [row['contact'] for row in tracker.records[-1]['settleStates']] == ['fall', 'land']


@pytest.mark.parametrize('state,base,xy,settled_xy', [
    ('swing_cycle', 'top_swing', (300, 10), (300, -20)),
    ('clean_top', 'top_swing', (300, 10), (300, -20)),
    ('climb_left', 'climb_left', (10, 300), (-40, 300)),
    ('clean_climb_left', 'climb_left', (10, 300), (-40, 300)),
    ('clean_climb_right', 'climb_right', (790, 300), (839, 300)),
    ('idle', 'ground', (400, 610), (400, 620)),
    ('clean_ground', 'ground', (400, 610), (400, 620)),
])
def test_release_waits_for_current_motion_geometry(state, base, xy, settled_xy):
    tracker = PhysicalDragEvidence()
    release_at(tracker, xy)
    transient = snapshot(window=[*settled_xy, 200, 200], state=state, renderName=state,
                         base=base, anchors={}, geometryAvailable=False)
    tracker.sample(transient, .21)
    tracker.sample(transient, .4)
    assert tracker.current is not None
    assert not tracker.passed
    settled = snapshot(window=[*settled_xy, 200, 200], state=state, renderName=state, base=base)
    native_frames(tracker, settled, .5)
    assert tracker.passed


def test_missing_geometry_stays_pending_then_times_out_without_pass():
    tracker = PhysicalDragEvidence()
    release_at(tracker, (300, 10))
    transient = snapshot(anchors={}, geometryAvailable=False)
    tracker.sample(transient, .21)
    tracker.sample(transient, 3.5)
    assert not tracker.passed
    assert tracker.records[-1]['status'] == 'rejected'


@pytest.mark.parametrize('case', ['jitter', 'no_window_move', 'released_before_drag', 'synthetic', 'no_screen'])
def test_click_or_invalid_release_never_counts(case):
    tracker = PhysicalDragEvidence()
    tracker.press((350, 450), snapshot(), 0)
    end = (352, 452) if case == 'jitter' else (450, 450)
    tracker.move(end, snapshot(), .1)
    released = snapshot(window=[end[0]-50, end[1]-50, 200, 200], dragging=True)
    if case == 'no_window_move':
        released['window'] = [300, 400, 200, 200]
    if case == 'released_before_drag':
        released['dragging'] = False
    if case == 'no_screen':
        released['screenValid'] = False
    tracker.release(end, released, .2, native=case != 'synthetic')
    native_frames(tracker, snapshot(window=[400, 620, 200, 200]))
    assert not tracker.passed
    assert tracker.current is None
    assert tracker.records[-1]['status'] == 'rejected'


@pytest.mark.parametrize('event_change', [{'token': 99}, {'generation': 99}, {'name': 'other'},
                                         {'type': 'ready'}, {'bounds': [0, 0, 0, 0]}])
def test_stale_or_invalid_renderer_messages_cannot_validate(event_change):
    tracker = PhysicalDragEvidence()
    release_at(tracker, (400, 610))
    native_frames(tracker, snapshot(window=[400, 620, 200, 200]), **event_change)
    assert not tracker.passed


@pytest.mark.parametrize('settled', [
    snapshot(window=[400, 620, 200, 200]),  # Falling sequence silently skipped.
    snapshot(window=[400, 500, 200, 200], state='land', renderName='land'),
    snapshot(window=[400, 300, 200, 200], state='fall_float', motion='fall', paused=True),
    snapshot(window=[400, 300, 200, 200], state='fall_float', motion='fall', generation=2),
])
def test_invalid_or_interrupted_settlement_rejects_gesture(settled):
    tracker = PhysicalDragEvidence()
    release_at(tracker, (400, 300))
    native_frames(tracker, deepcopy(settled))
    assert not tracker.passed
    assert tracker.records[-1]['status'] == 'rejected'


def test_observer_does_not_consume_or_accept_synthetic_qt_input():
    from PySide6.QtCore import QObject, QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    pet = QObject()
    tracker = PhysicalDragEvidence()
    observer = install_physical_drag_observer(app, pet, tracker, lambda: None)
    observer.snapshot = lambda *args: snapshot()
    event = QMouseEvent(QEvent.MouseButtonPress, QPointF(20, 20), QPointF(350, 450),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    assert not event.spontaneous()
    assert observer.eventFilter(pet, event) is False
    assert tracker.current is None
    assert observer.parent() is app
    observer.stop()
    assert not observer.timer.isActive()
