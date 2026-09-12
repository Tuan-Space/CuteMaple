"""Explicit QA of the complete visible pet; never an offscreen substitute.

This opt-in entry calls the ordinary application entry and supplies an observer.
Real input providers stay enabled. Scripted app actions and externally observed
keyboard/device/display events are reported separately, without inventing passes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reaction_capture_evidence(value, binding):
    """Copy actual same-capture diagnostics; absent/stale evidence stays absent."""
    playback = value.get('playback', {})
    capture_binding = [value.get('generation'), playback.get('token'), playback.get('name')]
    if capture_binding != binding or playback.get('paused') is not False or playback.get('evaluated') is not True:
        return {}
    effects = value.get('effects', {})
    return {'captureBinding': capture_binding, 'actualVisibleEffects': {
        key: effects.get(key) for key in ('visiblePixels', 'drawnCount', 'kinds', 'particles', 'pixelBounds')}}


class PhysicalDragEvidence:
    """Read-only gesture/settlement evidence; synthetic test sequences cannot drive Qt."""
    def __init__(self, threshold=6, snap=28):
        self.threshold, self.snap = threshold, snap
        self.current = None
        self.records = []
        self.passed = False

    def cancel(self, reason):
        if self.current is not None:
            self.current.update(status='rejected', reason=reason)
            self.records.append(self.current)
            self.records[:] = self.records[-30:]
            self.current = None

    def press(self, point, snapshot, now, native=True):
        self.cancel('another press interrupted the previous gesture')
        if not native or not snapshot['live'] or snapshot['paused'] or not snapshot['visible']:
            return
        self.current = {'status': 'pressed', 'pressedAt': now, 'startPointer': list(point),
                        'startWindow': snapshot['window'][:], 'lastPointer': list(point),
                        'pointerTravel': 0, 'windowTravel': 0, 'moveEvents': 0,
                        'generation': snapshot['generation'], 'settleStates': [], 'nativeActivity': []}

    def move(self, point, snapshot, now, left_down=True, native=True):
        item = self.current
        if item is None or item['status'] != 'pressed':
            return
        if not native or not left_down:
            return self.cancel('mouse sequence lost its native left-button drag')
        item['moveEvents'] += 1
        item['lastPointer'] = list(point)
        item['pointerTravel'] = max(item['pointerTravel'], sum(abs(a-b) for a, b in zip(point, item['startPointer'])))
        item['windowTravel'] = max(item['windowTravel'], sum(abs(a-b) for a, b in zip(snapshot['window'][:2], item['startWindow'][:2])))

    def release(self, point, snapshot, now, native=True):
        item = self.current
        if item is None or item['status'] != 'pressed':
            return
        item['windowTravel'] = max(item['windowTravel'], sum(abs(a-b) for a, b in zip(snapshot['window'][:2], item['startWindow'][:2])))
        item.update(releasedAt=now, endPointer=list(point), releaseWindow=snapshot['window'][:])
        expected_xy = [a+b-c for a, b, c in zip(item['startWindow'][:2], item['lastPointer'], item['startPointer'])]
        if (not native or not snapshot['dragging'] or not snapshot['screenValid'] or not item['moveEvents'] or
                item['pointerTravel'] < self.threshold or item['windowTravel'] < self.threshold or
                max(abs(a-b) for a, b in zip(expected_xy, snapshot['window'][:2])) > 2):
            return self.cancel('click, invalid release, or window did not follow the physical pointer')
        x, y, w, h = snapshot['window']
        left, top, right, bottom = snapshot['area']
        item['expectedSettle'] = ('top' if y <= top + self.snap else 'left' if x <= left + self.snap
                                  else 'right' if x+w >= right-self.snap else 'fall' if y+h < bottom-3 else 'ground')
        item['releaseArea'] = snapshot['area'][:]
        item['status'] = 'settling'

    @staticmethod
    def contact(snapshot):
        x, y, w, h = snapshot['window']
        left, top, right, bottom = snapshot['area']
        anchors = snapshot['anchors']
        state, base, motion = snapshot['state'], snapshot['base'], snapshot['motion']
        floor = bottom + 1 - round(anchors.get('ground', (0, 1))[1] * h)
        if base == 'top_swing' and state in {'swing_cycle', 'swing_idle', 'clean_top'}:
            anchor = anchors.get('hang') or anchors.get('top')
            if anchor and abs(y + anchor[1]*h - top) <= 3 and left-3 <= x <= right-w+4:
                return 'top'
        for side in ('left', 'right'):
            anchor = anchors.get(side)
            edge = left if side == 'left' else right
            if (base == 'climb_' + side and state in {base, 'clean_' + base} and anchor and
                    abs(x + anchor[0]*w - edge) <= 3 and top-anchor[1]*h-3 <= y <= floor+3):
                return side
        if base == 'ground' and left-3 <= x <= right-w+4:
            if motion == 'fall' and state == 'fall_float' and top-3 <= y < floor+3:
                return 'fall'
            if motion is None and state in {'idle', 'land', 'clean_ground'} and abs(y-floor) <= 3:
                return 'land' if state == 'land' else 'ground'
        return None

    def sample(self, snapshot, now, native_event=None):
        item = self.current
        if item is None or item['status'] != 'settling':
            return
        if (now - item['releasedAt'] > 30 or snapshot['dragging'] or not snapshot['live'] or
                snapshot['paused'] or not snapshot['visible'] or not snapshot['screenValid'] or
                snapshot['generation'] != item['generation']):
            return self.cancel('settlement interrupted, unavailable, or timed out')
        # start_state clears the preceding motion's anchors synchronously. Qt
        # release handling completes before the next native geometry message;
        # that gap is neither invalid contact nor evidence of settled playback.
        if not snapshot['geometryAvailable']:
            item.setdefault('awaitingGeometrySince', now)
            if now - item['awaitingGeometrySince'] > 3:
                self.cancel('current motion did not supply native geometry within three seconds')
            return
        item.pop('awaitingGeometrySince', None)
        contact = self.contact(snapshot)
        wanted = item['expectedSettle']
        allowed = {'fall', 'land', 'ground'} if wanted == 'fall' else {wanted}
        if contact not in allowed:
            return self.cancel('settlement state/contact does not match the release location')
        if wanted == 'fall' and contact == 'ground' and not any(row['contact'] in {'fall', 'land'} for row in item['settleStates']):
            return self.cancel('falling release reached idle without an observed fall or landing')
        sample = {'at': now, 'state': snapshot['state'], 'contact': contact,
                  'window': snapshot['window'][:], 'area': snapshot['area'][:], 'anchors': snapshot['anchors']}
        if not item['settleStates'] or item['settleStates'][-1]['contact'] != contact:
            item['settleStates'].append(sample)
        if native_event is not None:
            if (native_event.get('generation') != snapshot['generation'] or
                    native_event.get('token') != snapshot['token'] or
                    native_event.get('name') != snapshot['renderName']):
                return
            kind = native_event.get('type')
            if kind not in {'geometry', 'cycle', 'finished'}:
                return
            bounds = native_event.get('bounds', [])
            if kind == 'geometry' and (len(bounds) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bounds)
                                       or bounds[2] <= 0 or bounds[3] <= 0):
                return
            item['nativeActivity'].append({'at': now, 'kind': kind, 'name': native_event['name'],
                                           'token': native_event['token'], 'contact': contact})
            item['nativeActivity'][:] = item['nativeActivity'][-30:]
        activity = [row for row in item['nativeActivity'] if row['contact'] != 'fall']
        if (contact != 'fall' and len(activity) >= 3 and activity[-1]['at']-activity[0]['at'] >= .1):
            item.update(status='passed', settledAt=now, finalSettlement=sample)
            self.records.append(item)
            self.records[:] = self.records[-30:]
            self.current = None
            self.passed = True


class PausedDragEvidence(PhysicalDragEvidence):
    """Physical paused attachment and native clock/vertex evidence, kept separate."""
    def __init__(self, threshold=6, snap=28):
        super().__init__(threshold, snap)
        self.checks = dict.fromkeys(('left', 'right', 'top', 'cornerTop', 'pausedStatePreserved',
                                    'staticGeometry', 'noMotionWhilePaused', 'resumeWithoutCatchUp'), False)

    def _refresh_checks(self):
        self.checks = dict.fromkeys(self.checks, False)
        for row in self.records + ([self.current] if self.current else []):
            if row.get('staticVerified'):
                self.checks[row['expectedSettle']] = True
                self.checks['cornerTop'] |= row.get('cornerTop', False)
                for key in ('pausedStatePreserved', 'staticGeometry', 'noMotionWhilePaused'):
                    self.checks[key] = True
            self.checks['resumeWithoutCatchUp'] |= row.get('resumeVerified', False)

    def cancel(self, reason):
        if self.current and self.current.get('staticVerified'):
            self.current.update(status='static-verified', reason=reason)
            self.records.append(self.current)
            self.records[:] = self.records[-30:]
            self.current = None
        else:
            super().cancel(reason)
        self._refresh_checks()

    def reject(self, reason):
        # A detected violation is different from ending a valid static-only
        # observation before the user resumes. Never retain a passing label.
        if self.current:
            self.current['staticVerified'] = False
        super().cancel(reason)
        self._refresh_checks()

    def press(self, point, snapshot, now, native=True):
        self.cancel('next pointer press ended the previous observation')
        if (not native or not snapshot.get('pausedSetting') or not snapshot['paused']
                or not snapshot['live'] or not snapshot['visible'] or not snapshot['screenValid']):
            return
        # Reuse only the established pointer-travel bookkeeping. Eligibility is
        # the inverse of the ordinary tracker and remains independently gated.
        super().press(point, {**snapshot, 'paused': False}, now, native)
        self.current.update(staticSamples=[], resumeSamples=[], pausedSettingAtPress=True,
                            staticVerified=False, resumeVerified=False)

    def move(self, point, snapshot, now, left_down=True, native=True):
        if self.current and self.current['status'] == 'pressed' and (not snapshot.get('pausedSetting') or not snapshot['paused']):
            return self.reject('pause changed during physical dragging')
        super().move(point, snapshot, now, left_down, native)

    def release(self, point, snapshot, now, native=True):
        if self.current and self.current['status'] == 'pressed' and (not snapshot.get('pausedSetting') or not snapshot['paused']):
            return self.reject('pause changed before release')
        super().release(point, snapshot, now, native)
        item = self.current
        if not item or item['status'] != 'settling':
            return
        if item['expectedSettle'] not in {'left', 'right', 'top'}:
            return self.reject('paused attachment check requires an edge release')
        x, y, w, _ = snapshot['window']
        left, top, right, _ = snapshot['area']
        item['cornerTop'] = y <= top+self.snap and (x <= left+self.snap or x+w >= right-self.snap)

    @staticmethod
    def _binding(snapshot):
        return [snapshot['generation'], snapshot['token'], snapshot['renderName']]

    def sample(self, snapshot, now, native_event=None):
        item = self.current
        if not item or item['status'] == 'pressed':
            return
        if (now-item['releasedAt'] > 180 or snapshot['dragging'] or not snapshot['live']
                or not snapshot['visible'] or not snapshot['screenValid']
                or snapshot['generation'] != item['generation']):
            return self.cancel('paused observation interrupted or expired')
        if not snapshot['geometryAvailable']:
            item.setdefault('awaitingGeometrySince', now)
            if now-item['awaitingGeometrySince'] > 3:
                self.reject('paused play did not produce current native geometry')
            return
        item.pop('awaitingGeometrySince', None)
        contact = self.contact(snapshot)
        if contact != item['expectedSettle']:
            return self.reject('paused contact does not match release; corners must prefer top')
        binding = self._binding(snapshot)
        if 'settledBinding' not in item:
            item.update(settledBinding=binding, settledWindow=snapshot['window'][:],
                        settledMotionY=snapshot.get('motionY'), settledAt=now,
                        settlement={'state': snapshot['state'], 'base': snapshot['base'],
                                    'window': snapshot['window'][:], 'anchors': snapshot['anchors'],
                                    'area': snapshot['area'][:], 'contact': contact})
        elif binding != item['settledBinding']:
            return self.cancel('motion/token changed before observation completed')
        if snapshot.get('pausedSetting'):
            if not snapshot['paused'] or 'resumedAt' in item:
                return self.reject('pause state changed during static verification')
            if max(abs(a-b) for a, b in zip(snapshot['window'], item['settledWindow'])) > 1:
                return self.reject('window moved while paused')
            before, after = item.get('settledMotionY'), snapshot.get('motionY')
            if (isinstance(before, (int, float)) and isinstance(after, (int, float))
                    and (not math.isfinite(after) or abs(after-before) > 1)):
                return self.reject('host movement accumulator advanced while paused')
            item['lastPausedAt'] = now
            if native_event and native_event.get('type') in {'cycle', 'finished'} and [
                    native_event.get('generation'), native_event.get('token'), native_event.get('name')] == binding:
                return self.reject('native motion completed a cycle while paused')
        else:
            if not item.get('staticVerified'):
                return self.reject('resumed before static native evidence was complete')
            item.setdefault('resumedAt', now)
            # The bound uses only time since the last actual paused sample, not
            # the whole pause. It detects a resumed host consuming paused time.
            spec = snapshot.get('climbSpec')
            if spec and contact in {'left', 'right'}:
                rows = spec['phaseTravel']
                slope = max((b[1]-a[1])/(b[0]-a[0]) for a, b in zip(rows, rows[1:]))
                speed = slope/spec['cycleDuration']*spec['risePerCycle']*snapshot['window'][3]
                before, after = item.get('settledMotionY'), snapshot.get('motionY')
                if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                    limit = speed*(now-item['lastPausedAt']+.1)+2
                    if abs(after-before) > limit:
                        return self.reject('resumed host movement consumed more than elapsed unpaused time')

    def capture(self, snapshot, now, capture, request_binding):
        self.sample(snapshot, now)
        item = self.current
        if (not item or item['status'] == 'pressed' or not snapshot['geometryAvailable']
                or 'settledBinding' not in item or self._binding(snapshot) != request_binding):
            return
        playback, signature = capture.get('playback', {}), capture.get('nativeVertexSignature', {})
        seconds = playback.get('motionTimeSeconds')
        if (playback.get('token') != snapshot['token'] or playback.get('name') != snapshot['renderName']
                or playback.get('evaluated') is not True or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or not isinstance(playback.get('nativeUpdateCount'), int)
                or not isinstance(signature.get('floatCount'), int) or signature['floatCount'] <= 0
                or not isinstance(signature.get('hash32'), (str, int)) or capture.get('glError')
                or capture.get('visiblePixels', 0) < 50):
            return  # Missing diagnostics are pending evidence, never inferred success.
        record = {'at': now, 'binding': request_binding[:], 'window': snapshot['window'][:],
                  'playback': playback.copy(), 'nativeVertexSignature': signature.copy(),
                  'visiblePixels': capture['visiblePixels']}
        if snapshot.get('pausedSetting'):
            if playback.get('paused') is not True or playback.get('staticSampleCount', 0) < 1:
                return self.reject('paused attachment was not a native static sample')
            samples = item['staticSamples']
            if samples:
                old = samples[0]
                if (abs(seconds-old['playback']['motionTimeSeconds']) > 1e-6
                        or playback['nativeUpdateCount'] != old['playback']['nativeUpdateCount']
                        or signature != old['nativeVertexSignature']):
                    return self.reject('native motion clock or vertices advanced while paused')
            samples.append(record)
            item['staticSamples'] = samples[-20:]
            if len(samples) >= 2 and now-samples[0]['at'] >= .5:
                item['staticVerified'] = True
                item['status'] = 'holding'
                self.checks[item['expectedSettle']] = True
                self.checks['cornerTop'] |= item['cornerTop']
                for key in ('pausedStatePreserved', 'staticGeometry', 'noMotionWhilePaused'):
                    self.checks[key] = True
        else:
            if playback.get('paused') is not False:
                return
            baseline = item['staticSamples'][-1]['playback']
            delta, elapsed = seconds-baseline['motionTimeSeconds'], now-item['lastPausedAt']
            if delta < -1e-6 or delta > elapsed+.1:
                return self.reject('native motion clock jumped across paused time on resume')
            item['resumeSamples'].append(record)
            item['resumeSamples'] = item['resumeSamples'][-20:]
            if (len(item['resumeSamples']) >= 2 and now-item['resumedAt'] >= .2 and delta > 0
                    and playback['nativeUpdateCount'] > baseline['nativeUpdateCount']):
                item.update(status='passed', resumeVerified=True, finishedAt=now,
                            resumedMotionSeconds=delta, observedUnpausedSeconds=elapsed)
                self.records.append(item)
                self.records[:] = self.records[-30:]
                self.current = None
                self.passed = True
                self.checks['resumeWithoutCatchUp'] = True


def install_physical_drag_observer(app, pet, tracker, on_change, paused_tracker=None):
    """Observe native Qt delivery only; every event continues to its receiver."""
    from PySide6.QtCore import QObject, QEvent, QTimer, Qt
    from PySide6.QtGui import QGuiApplication

    class Observer(QObject):
        def __init__(self):
            super().__init__(app)
            self.closed = False
            self.trackers = [tracker] + ([paused_tracker] if paused_tracker else [])
            self.timer = QTimer(self)
            self.timer.setInterval(100)
            self.timer.timeout.connect(self.sample)
            self.timer.start()
            app.installEventFilter(self)
            app.aboutToQuit.connect(self.stop)

        def snapshot(self, release_point=None):
            screen = QGuiApplication.screenAt(release_point) if release_point is not None else pet.current_screen
            valid = screen in QGuiApplication.screens()
            area = screen.availableGeometry() if valid else pet._screen_area()
            host = pet.live2d_host
            geometry = pet._renderer_geometry
            return {'window': [pet.x(), pet.y(), pet.width(), pet.height()],
                    'area': [area.left(), area.top(), area.right(), area.bottom()],
                    'anchors': {name: point for name in ('left', 'right', 'hang', 'top', 'ground')
                                if (point := pet._model_anchor(name)) is not None},
                    'state': pet.state, 'base': pet.base_mode, 'motion': pet.motion_mode,
                    'renderName': pet._render_state_name(), 'token': pet._renderer_token,
                    'generation': host._generation if host else None,
                    'geometryAvailable': bool(geometry and geometry.get('token') == pet._renderer_token and
                        geometry.get('name') == pet._render_state_name() and host and
                        geometry.get('generation') == host._generation),
                    'live': bool(pet._live2d_active and host and host.ready), 'paused': pet._animation_paused(),
                    'pausedSetting': bool(pet.settings.paused), 'motionY': pet._motion_y,
                    'climbSpec': pet._climb_spec,
                    'visible': pet.isVisible(), 'dragging': pet.dragging, 'screenValid': valid}

        def sample(self, native_event=None):
            if self.closed or not any(item.current for item in self.trackers):
                return
            try:
                snapshot = self.snapshot()
                for item in self.trackers:
                    item.sample(snapshot, time.monotonic(), native_event)
            except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
                for item in self.trackers:
                    item.cancel('observer could not read settlement: ' + str(exc))
            on_change()

        def capture(self, value, binding):
            if not self.closed and paused_tracker and paused_tracker.current:
                try:
                    paused_tracker.capture(self.snapshot(), time.monotonic(), value, binding)
                except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
                    paused_tracker.reject('observer could not read static capture: ' + str(exc))
                on_change()

        def eventFilter(self, watched, event):
            if self.closed or watched is not pet:
                return False
            kind = event.type()
            mouse = {QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.MouseButtonRelease}
            if kind in mouse:
                native = event.spontaneous() and event.source() == Qt.MouseEventNotSynthesized
                point = event.globalPosition().toPoint()
                now = time.monotonic()
                try:
                    if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                        for item in self.trackers:
                            item.press((point.x(), point.y()), self.snapshot(), now, native)
                    elif kind == QEvent.MouseMove:
                        for item in self.trackers:
                            item.move((point.x(), point.y()), self.snapshot(), now,
                                      bool(event.buttons() & Qt.LeftButton), native)
                    elif kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                        for item in self.trackers:
                            item.release((point.x(), point.y()), self.snapshot(point), now, native)
                        QTimer.singleShot(0, self.sample)  # Let PetWindow handle release first.
                except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
                    for item in self.trackers:
                        item.cancel('observer could not read pointer event: ' + str(exc))
                on_change()
            elif kind == QEvent.Hide:
                for item in self.trackers:
                    item.cancel('pet hidden during the gesture')
                on_change()
            return False

        def stop(self):
            self.closed = True
            self.timer.stop()
            app.removeEventFilter(self)
            for item in self.trackers:
                item.cancel('application exited before gesture verification finished')
            on_change()

    observer = Observer()
    app._desktop_drag_observer = observer
    return observer


def run(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--duration', type=float, default=3600)
    parser.add_argument('--scenario', choices=('startup', 'full'), default='full')
    options = parser.parse_args(arguments)
    if options.duration < 5:
        parser.error('Duration must be at least five seconds')
    disabled = {name: os.environ[name] for name in (
        'MEINIFENG_DISABLE_RUNTIME', 'MEINIFENG_DISABLE_GLOBAL_ACTIVITY',
        'MEINIFENG_DISABLE_AUDIO', 'MEINIFENG_DISABLE_ACTIVITY', 'CUTEMAPLE_DISABLE_ACTIVITY', 'MEINIFENG_SMOKE_TEST',
        'QT_QPA_PLATFORM', 'QT_QUICK_BACKEND', 'QT_OPENGL', 'QTWEBENGINE_CHROMIUM_FLAGS'
    ) if os.environ.get(name)}
    if any(name.startswith(('MEINIFENG_', 'CUTEMAPLE_')) for name in disabled) or disabled.get('QT_QPA_PLATFORM') == 'offscreen':
        parser.error('Visible QA cannot run with runtime/provider bypass or offscreen flags')
    options.profile.mkdir(parents=True, exist_ok=True)
    options.report.parent.mkdir(parents=True, exist_ok=True)
    os.environ['MEINIFENG_PROFILE_DIRECTORY'] = str(options.profile.resolve())
    from diagnostics import event
    import psutil
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication, QCursor
    import live2d_host
    import pet_app
    from pet_core import ANIMATIONS, resource_root, stable_application_path

    # Capture diagnostics adds read-only pixel/parameter inspection. It does not
    # change rendering backend, normal startup, sensors, or the PetWindow path.
    live2d_host.APP_URL += ('&' if '?' in live2d_host.APP_URL else '?') + 'diagnostics=1'
    target = stable_application_path()
    report = {
        'schemaVersion': 1, 'startedAt': utc_now(), 'finishedAt': None,
        'pid': os.getpid(), 'scenario': options.scenario,
        'requestedDurationSeconds': options.duration, 'durationSeconds': 0,
        'executable': str(target), 'executableSha256': digest(target),
        'profile': str(options.profile.resolve()), 'offscreen': False,
        'graphicsOverrides': disabled, 'defaultGraphicsBackend': not disabled,
        'ordinaryPetWindow': True, 'runtimeActive': False, 'nativeProvidersActive': False, 'nativeProvidersObserved': False,
        'completed': False, 'passed': False, 'errors': [], 'observations': [],
        'keyboardEvents': 0, 'audioActiveEvents': 0, 'screenEvents': [], 'physicalDrags': [],
        'pausedDrags': [], 'pausedDragInProgress': None, 'pausedDragChecks': {},
        'providerStatus': {}, 'providerStatusHistory': [], 'reactionObservations': [],
        'simultaneousKeyboardAudioObserved': False,
        'statesObserved': [], 'nativeCycles': 0, 'nativeFinished': 0,
        'rendererGeneration': None, 'rendererPid': None,
        'mainRssPeakBytes': 0, 'childRssPeakBytes': 0, 'ownedChildPids': [],
        'checks': {'cleanExit': False, 'idleGaze': False, 'keyboardReaction': False,
                   'audioReaction': False, 'pauseResume': False, 'dragDrop': False,
                   'displayChange': False, 'repeatedLaunch': False},
        'manualOrExternalChecksPending': ['idleGazeMovement', 'keyboardReaction', 'audioReaction', 'physicalDragDrop', 'displayChange',
                                         'lockUnlock', 'sleepResume', 'audioDeviceChange',
                                         'manualAutostart', 'manualCleanupAuthorization'],
        'actionMethod': 'Public application methods under the normal GUI event loop; no OS input injection',
        'securityObservation': {'status': 'pending-external-read-only-event-review'},
        'assetSha256': {},
    }
    root = resource_root()
    for name in ('Maple.model3.json', 'Maple.moc3', 'Maple.pet.json'):
        path = root / 'assets/live2d/Maple' / name
        if path.exists():
            report['assetSha256'][f'assets/live2d/Maple/{name}'] = digest(path)
    started = time.monotonic()
    ready_at = None
    stop_requested = False
    observed = set()
    actions_done = set()
    owned = set()
    process = psutil.Process()
    timers = []
    pet_ref = []
    capture_pending = False
    gaze_samples = []
    drag_evidence = PhysicalDragEvidence(pet_app.DRAG_THRESHOLD, pet_app.EDGE_SNAP_PX)
    paused_drag_evidence = PausedDragEvidence(pet_app.DRAG_THRESHOLD, pet_app.EDGE_SNAP_PX)
    last_keyboard_at = last_audio_at = None
    audio_active = False

    def persist():
        temporary = options.report.with_suffix('.writing.json')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, options.report)

    def note(name, **fields):
        report['observations'].append({'at': utc_now(), 'event': name, **fields})
        event('desktop_qa_' + name, **fields)

    def sync_drag_evidence():
        report['physicalDrags'] = drag_evidence.records[:]
        report['pausedDrags'] = paused_drag_evidence.records[:]
        report['pausedDragInProgress'] = paused_drag_evidence.current
        report['pausedDragChecks'] = paused_drag_evidence.checks.copy()
        if drag_evidence.passed and not report['checks']['dragDrop']:
            report['checks']['dragDrop'] = True
            report['manualOrExternalChecksPending'].remove('physicalDragDrop')
            note('physical_drag_validated', evidence=next(
                row for row in reversed(drag_evidence.records) if row['status'] == 'passed'))
            persist()

    def stop(pet, error=None):
        nonlocal stop_requested
        if stop_requested:
            return
        stop_requested = True
        if error:
            report['errors'].append(str(error))
        note('exit_requested')
        persist()
        pet.quit_app()

    def observed_keyboard():
        nonlocal last_keyboard_at
        last_keyboard_at = time.monotonic()
        report['keyboardEvents'] += 1
        note('keyboard_activity', providerStatus=report['providerStatus'])

    def observed_audio(active):
        nonlocal last_audio_at, audio_active
        audio_active = bool(active)
        if active:
            last_audio_at = time.monotonic()
            report['audioActiveEvents'] += 1
            note('audio_activity', providerStatus=report['providerStatus'])

    def observed_provider_status(value):
        report['providerStatus'] = value
        report['providerStatusHistory'].append({'at': utc_now(), 'status': value})
        report['providerStatusHistory'][:] = report['providerStatusHistory'][-300:]

    def observed_screen(kind, screen=None):
        observation = {'at': utc_now(), 'kind': kind, 'repositionValidated': False}
        report['screenEvents'].append(observation)
        def validate():
            if not pet_ref or stop_requested:
                return
            pet = pet_ref[0]
            screens = QGuiApplication.screens()
            valid = pet.current_screen in screens and any(s.availableGeometry().intersects(pet.frameGeometry()) for s in screens)
            if valid and pet.base_mode.startswith('climb_') and not pet._top_transition:
                side = pet.base_mode.removeprefix('climb_')
                anchor = pet._model_anchor(side)
                if anchor:
                    expected = pet._screen_area().left() if side == 'left' else pet._screen_area().right()
                    valid = abs(pet.x() + anchor[0] * pet.width() - expected) <= 3
            observation['repositionValidated'] = bool(valid)
            observation['window'] = [pet.x(), pet.y(), pet.width(), pet.height()]
            if valid:
                report['checks']['displayChange'] = True
                if 'displayChange' in report['manualOrExternalChecksPending']:
                    report['manualOrExternalChecksPending'].remove('displayChange')
        QTimer.singleShot(300, validate)

    def observe(app, pet):
        nonlocal ready_at, capture_pending
        pet_ref.append(pet)
        drag_observer = install_physical_drag_observer(app, pet, drag_evidence, sync_drag_evidence, paused_drag_evidence)
        report['qtPlatform'] = QGuiApplication.platformName()
        report['offscreen'] = report['qtPlatform'] == 'offscreen'
        report['initialSettings'] = {
            name: getattr(pet.settings, name) for name in (
                'autostart', 'gaze_enabled', 'roaming_enabled', 'keyboard_enabled',
                'audio_enabled', 'particles_enabled', 'auto_clean_interval_enabled',
                'auto_clean_memory_enabled')
        }
        QGuiApplication.instance().screenAdded.connect(lambda screen: observed_screen('added', screen))
        QGuiApplication.instance().screenRemoved.connect(lambda screen: observed_screen('removed', screen))
        QGuiApplication.instance().primaryScreenChanged.connect(lambda screen: observed_screen('primary', screen))
        for screen in QGuiApplication.screens():
            screen.geometryChanged.connect(lambda _: observed_screen('geometry'))
            screen.availableGeometryChanged.connect(lambda _: observed_screen('availableGeometry'))

        def renderer_event(value):
            drag_observer.sample(value)
            kind = value.get('type')
            if kind in ('error', 'ready'):
                note('renderer_' + kind, payload=value)
            if kind == 'error':
                stop(pet, value.get('message', 'Renderer error'))
            elif kind == 'cycle':
                report['nativeCycles'] += 1
            elif kind == 'finished':
                report['nativeFinished'] += 1
            elif kind == 'geometry' and value.get('bounds'):
                report['lastGeometry'] = value

        def capture_result(raw, request_binding):
            nonlocal capture_pending
            capture_pending = False
            if not raw:
                return
            try:
                value = json.loads(raw)
                if not value or value.get('glError') or value.get('visiblePixels', 0) < 50:
                    stop(pet, 'Empty native canvas or WebGL error during visible QA')
                    return
                report['lastVisiblePixels'] = value['visiblePixels']
                drag_observer.capture(value, request_binding)
                params = value.get('parameters', {})
                now = time.monotonic()
                eligible = (request_binding == [pet.live2d_host._generation, pet._renderer_token, pet._render_state_name()]
                            and not pet._animation_paused() and pet.isVisible())
                keyboard_recent = eligible and last_keyboard_at is not None and now-last_keyboard_at <= 3.5
                audio_recent = (last_audio_at is not None and
                    eligible and (now-last_audio_at <= 1 or audio_active))
                typing, listening = params.get('ParamTyping', 0) > .2, params.get('ParamListening', 0) > .2
                if keyboard_recent and typing:
                    report['checks']['keyboardReaction'] = True
                if audio_recent and listening:
                    report['checks']['audioReaction'] = True
                if (keyboard_recent and typing) or (audio_recent and listening):
                    report['reactionObservations'].append({'at': utc_now(), 'keyboardRecent': keyboard_recent,
                        'audioRecent': audio_recent, 'typing': params.get('ParamTyping'),
                        'listening': params.get('ParamListening'), 'providerStatus': report['providerStatus'],
                        'binding': request_binding, 'visiblePixels': value['visiblePixels'],
                        **reaction_capture_evidence(value, request_binding)})
                    report['reactionObservations'][:] = report['reactionObservations'][-100:]
                report['simultaneousKeyboardAudioObserved'] |= keyboard_recent and audio_recent and typing and listening
                if pet.state == 'idle' and pet.settings.gaze_enabled and not pet._active_reaction and not pet._animation_paused():
                    actual = [params.get('ParamEyeBallX'), params.get('ParamEyeBallY')]
                    if all(isinstance(n, (int, float)) and math.isfinite(n) for n in actual):
                        cursor = QCursor.pos()
                        point = pet.mapFromGlobal(cursor)
                        head = pet._model_anchor('head') or (.5, .32)
                        expected = [max(-1, min(1, (point.x() - head[0] * pet.width()) / max(120, pet.width() * 1.5))),
                                    max(-1, min(1, (head[1] * pet.height() - point.y()) / max(120, pet.height())))]
                        sample = {'cursor': [cursor.x(), cursor.y()], 'expected': expected, 'actual': actual}
                        # Require an externally observed cursor change and a matching
                        # native parameter response; mere parameter presence is insufficient.
                        aligned = max(abs(a-b) for a, b in zip(expected, actual)) < .2
                        if aligned and any(math.dist(old['cursor'], sample['cursor']) >= 12 and
                            math.dist(old['expected'], expected) >= .05 and math.dist(old['actual'], actual) >= .03
                            for old in gaze_samples):
                            report['checks']['idleGaze'] = True
                        if aligned:
                            gaze_samples.append(sample)
                            gaze_samples[:] = gaze_samples[-100:]
                        report['lastGazeObservation'] = sample
                        report['gazeAlignedSamples'] = len(gaze_samples)
                for check, pending in [('idleGaze', 'idleGazeMovement'), ('keyboardReaction', 'keyboardReaction'), ('audioReaction', 'audioReaction')]:
                    if report['checks'][check] and pending in report['manualOrExternalChecksPending']:
                        report['manualOrExternalChecksPending'].remove(pending)
            except (ValueError, TypeError, KeyError) as error:
                stop(pet, error)

        def safe_action(key, function):
            if key in actions_done:
                return
            actions_done.add(key)
            drag_evidence.cancel('scripted QA action interrupted physical gesture verification')
            paused_drag_evidence.cancel('scripted QA action interrupted paused gesture verification')
            sync_drag_evidence()
            function()
            note('scripted_action', action=key)

        def tick():
            nonlocal ready_at, capture_pending
            if stop_requested:
                return
            now = time.monotonic()
            try:
                host, activity = pet.live2d_host, pet.desktop_activity
                if host is not None and 'host_connected' not in actions_done:
                    actions_done.add('host_connected')
                    host.event.connect(renderer_event)
                if activity is not None and 'activity_connected' not in actions_done:
                    actions_done.add('activity_connected')
                    activity.keyboardActivity.connect(observed_keyboard)
                    activity.audioActivityChanged.connect(observed_audio)
                    activity.statusChanged.connect(observed_provider_status)
                    if getattr(activity, '_last_status', None):
                        observed_provider_status(activity._last_status)
                if pet._live2d_active and ready_at is None:
                    ready_at = now
                    note('native_ready')
                report['runtimeActive'] = bool(pet._live2d_active and host and host.ready)
                worker = getattr(activity, '_worker', None)
                report['nativeProvidersActive'] = bool(activity and activity.hardware_available and
                    activity._started and ((activity._filter is not None and activity._filter.registered)
                    or (getattr(activity, '_hook', None) is not None and activity._hook.registered))
                    and worker and worker.running and worker.initialized)
                report['nativeProvidersObserved'] |= report['nativeProvidersActive']
                if host and host.page is not None:
                    report['rendererGeneration'] = host._generation
                    report['rendererPid'] = int(host.page.renderProcessPid())
                if now - started > 45 and ready_at is None:
                    stop(pet, 'Ordinary visible application did not reach native ready within 45 seconds')
                    return
                if ready_at is None:
                    return
                elapsed = now - ready_at
                report['durationSeconds'] = elapsed
                observed.add(pet.state)
                report['statesObserved'] = sorted(observed)
                children = process.children(recursive=True)
                owned.update(child.pid for child in children)
                report['ownedChildPids'] = sorted(owned)
                report['mainRssPeakBytes'] = max(report['mainRssPeakBytes'], process.memory_info().rss)
                child_rss = 0
                for child in children:
                    try:
                        child_rss += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                report['childRssPeakBytes'] = max(report['childRssPeakBytes'], child_rss)
                if (report['runtimeActive'] and pet.isVisible() and not capture_pending
                        and (not pet.activity_paused or paused_drag_evidence.current is not None)):
                    capture_pending = True
                    binding = [host._generation, pet._renderer_token, pet._render_state_name()]
                    host.page.runJavaScript('JSON.stringify(window.__cutemapleCapture())',
                                           lambda raw, binding=binding: capture_result(raw, binding))
                if options.scenario == 'full':
                    if elapsed >= 12:
                        safe_action('pause', pet.toggle_pause)
                    if elapsed >= 14:
                        safe_action('resume', pet.toggle_pause)
                        report['checks']['pauseResume'] = not pet.activity_paused and pet.runtime_timer.isActive()
                    if elapsed >= 20:
                        safe_action('hide', pet.hide_pet)
                    if elapsed >= 22:
                        safe_action('show', pet.show_pet)
                    if elapsed >= 30:
                        safe_action('enlarge', lambda: pet.set_scale(1.5))
                    if elapsed >= 35:
                        safe_action('restore_size', lambda: pet.set_scale(1.0))
                    # Exercise genuine native motion playback without invoking
                    # elevated cleanup; permission behavior has separate checks.
                    state_index = int((elapsed - 45) // 8)
                    states = list(ANIMATIONS)
                    if 0 <= state_index < len(states):
                        state = states[state_index]
                        safe_action('motion_' + state, lambda: pet.start_state(state))
                    if elapsed >= 45 + len(states) * 8:
                        safe_action('restore_ground', pet._set_ground_idle)
                    if elapsed >= 230:
                        safe_action('climb_left', lambda: pet._attach_side('left'))
                    if elapsed >= 265:
                        safe_action('climb_right', lambda: pet._attach_side('right'))
                    if elapsed >= 300:
                        safe_action('top_swing', pet._attach_top)
                    if elapsed >= 340:
                        safe_action('final_ground', pet._set_ground_idle)
                if elapsed >= options.duration:
                    stop(pet)
                elif int(elapsed) % 5 == 0:
                    persist()
            except Exception as error:
                stop(pet, f'{type(error).__name__}: {error}')

        timer = QTimer(app)
        timer.setInterval(1000)
        timer.timeout.connect(tick)
        timers.append(timer)
        timer.start()
        persist()

    exit_code = pet_app.run(observer=observe)
    report['finishedAt'] = utc_now()
    report['exitCode'] = exit_code
    report['completed'] = bool(stop_requested and pet_ref)
    activity = pet_ref[0].desktop_activity if pet_ref else None
    worker = getattr(activity, '_worker', None)
    deadline = time.monotonic() + 2
    while worker is not None and worker.running and time.monotonic() < deadline:
        time.sleep(.01)
    report['audioChildExited'] = worker is None or not worker.running
    report['checks']['cleanExit'] = bool(exit_code == 0 and report['completed'] and report['audioChildExited'])
    report['passed'] = bool(report['checks']['cleanExit'] and report['runtimeActive'] and report['nativeProvidersObserved'] and
        report['durationSeconds'] >= options.duration and not report['offscreen'] and not report['errors'])
    report['elapsedWallSeconds'] = time.monotonic() - started
    persist()
    print(json.dumps({key: report[key] for key in ('passed', 'durationSeconds', 'errors', 'checks')}, ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1
