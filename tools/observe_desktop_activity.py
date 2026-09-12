"""Observe real input activity and output-meter status without injecting input or changing Windows devices.

This is provider evidence only. It does not establish a visible PetWindow reaction.
Run only when the user is ready to type normally and play their own audio.
"""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--duration', type=int, default=45, choices=range(10, 601), metavar='10..600')
    parser.add_argument('--endpoint', default='default', help='Provider selection only; never changes the system default')
    options = parser.parse_args()
    if os.name != 'nt' or ctypes.windll.shell32.IsUserAnAdmin():
        parser.error('Use an ordinary Windows user process, not an administrator process.')
    output = options.output.resolve()
    if output.exists():
        parser.error('Output directory already exists; observations are never overwritten.')
    output.mkdir(parents=True)
    os.environ['MEINIFENG_PROFILE_DIRECTORY'] = str(output / 'profile')
    sys.path.insert(0, str(ROOT))
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QWidget
    from desktop_activity import DesktopActivity, hardware_activity_allowed
    if not hardware_activity_allowed():
        parser.error('Native providers are disabled by the current platform/environment.')
    sources = [ROOT / name for name in ('desktop_activity.py', 'audio_probe.py', 'audio_process.py', 'pet_reactions.py', 'pet_core.py', 'main.py')]
    snapshot = lambda: {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    before = snapshot()
    app = QApplication([])
    message_window = QWidget()  # Hidden HWND receives Raw Input; it never steals focus.
    provider = DesktopActivity(message_window)
    provider.set_audio_endpoint(options.endpoint)
    report = {'reportKind': 'cutemaple-v5-activity-observation', 'scope': 'provider-only',
        'testInjection': False, 'systemDeviceChanged': False, 'pid': os.getpid(),
        'requestedDurationSeconds': options.duration, 'startedAtUnix': time.time(),
        'keyboardSignals': 0, 'audioActiveTransitions': 0, 'maximumPeak': 0.0,
        'sourceHashesAtStart': before, 'status': {}, 'samples': 0}
    started = time.monotonic()
    stream = (output / 'activity.jsonl').open('x', encoding='utf-8')
    stopping = [False]
    stop_started = [0.0]

    def record(kind, **values):
        stream.write(json.dumps({'elapsed': time.monotonic() - started, 'kind': kind, **values}, ensure_ascii=False) + '\n')
        stream.flush()

    def keyboard():
        report['keyboardSignals'] += 1
        record('keyboard-activity', count=report['keyboardSignals'])

    def audio(active):
        if active: report['audioActiveTransitions'] += 1
        record('audio-activity', active=active)

    def peak(value):
        report['maximumPeak'] = max(report['maximumPeak'], value)

    def status(value):
        report['status'] = value

    def begin_stop():
        if stopping[0]: return
        stopping[0] = True
        stop_started[0] = time.monotonic()
        provider.stop()

    def sample():
        report['samples'] += 1
        record('sample', status=report['status'], keyboardSignals=report['keyboardSignals'], maximumPeak=report['maximumPeak'])
        if not stopping[0] and time.monotonic() - started >= options.duration:
            begin_stop()
        if stopping[0]:
            if not provider.stopping:
                app.quit()
            elif time.monotonic() - stop_started[0] >= 2:
                provider.force_stop()
                if time.monotonic() - stop_started[0] >= 4:
                    report['shutdownTimedOut'] = True
                    app.quit()

    provider.keyboardActivity.connect(keyboard)
    provider.audioActivityChanged.connect(audio)
    provider.audioLevelChanged.connect(peak)
    provider.statusChanged.connect(status)
    provider.start(int(message_window.winId()))
    timer = QTimer(); timer.setInterval(100); timer.timeout.connect(sample); timer.start()
    try:
        app.exec()
    finally:
        timer.stop(); provider.stop()
        if provider.stopping: provider.force_stop()
        report.update(elapsedSeconds=time.monotonic() - started, finishedAtUnix=time.time(),
            audioChildExited=not bool(provider._worker and provider._worker.running),
            hookThreadExited=not bool(provider._hook and provider._hook.running), sourceHashesAtEnd=snapshot())
        report['inputsFrozen'] = report['sourceHashesAtStart'] == report['sourceHashesAtEnd']
        report['keyboardObserved'] = report['keyboardSignals'] > 0
        report['audioObserved'] = report['audioActiveTransitions'] > 0 and report['maximumPeak'] > 0
        report['result'] = 'recorded' if report['inputsFrozen'] and report['audioChildExited'] and report['hookThreadExited'] else 'failed'
        stream.close()
        report['activitySha256'] = hashlib.sha256((output / 'activity.jsonl').read_bytes()).hexdigest()
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        message_window.close()
    print(json.dumps({'result': report['result'], 'report': str(output / 'report.json'),
        'keyboardObserved': report['keyboardObserved'], 'audioObserved': report['audioObserved']}, ensure_ascii=False))
    return 0 if report['result'] == 'recorded' else 1


if __name__ == '__main__':
    raise SystemExit(main())
