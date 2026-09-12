import io
import os
import subprocess
import sys
import time
import threading

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from audio_process import AudioProcess


class Process:
    def __init__(self):
        self.stdin, self.stdout = io.StringIO(), io.StringIO()
        self.returncode = None
        self.pid, self.kills = 999, 0
    def poll(self):
        return self.returncode
    def kill(self):
        self.kills += 1
        self.returncode = -9


def test_hung_child_shutdown_has_two_second_grace_and_only_kills_owned_process():
    app = QApplication.instance() or QApplication([])
    clock, child = [10.0], Process()
    worker = AudioProcess(process_factory=lambda *a, **kw: child, clock=lambda: clock[0])
    done = []
    worker.finished.connect(lambda: done.append(True))
    worker.start()
    worker.set_enabled(True)
    worker._messages.put({'active': True, 'generation': worker._generation})
    worker._poll()
    assert worker.activity_active
    worker.stop()
    assert not worker.activity_active and child.kills == 0 and not done
    clock[0] = 11.99
    worker._poll()
    assert child.kills == 0
    clock[0] = 12.01
    worker._poll()
    assert child.kills == 1
    worker._poll()
    assert done == [True] and not worker.running


def test_late_audio_revision_cannot_reenable_after_pause():
    app = QApplication.instance() or QApplication([])
    child = Process()
    worker = AudioProcess(process_factory=lambda *a, **kw: child)
    worker.start()
    worker.set_enabled(True)
    old = worker._generation
    worker.set_enabled(False)
    worker.set_enabled(True)
    worker._messages.put({'active': True, 'generation': old})
    worker._poll()
    assert not worker.activity_active
    worker._messages.put({'active': True, 'generation': worker._generation})
    worker._poll()
    assert worker.activity_active
    worker.force_stop()
    worker._poll()


def test_requested_graceful_child_exit_is_stopped_without_a_failure_or_restart():
    app = QApplication.instance() or QApplication([])
    child = Process()
    worker = AudioProcess(process_factory=lambda *a, **kw: child)
    worker.start()
    worker.stop()
    child.returncode = 0
    worker._poll()
    assert worker.status['state'] == 'stopped' and worker.status['error'] == ''
    assert worker._restart_at is None and worker._restarts == 0


def test_actual_unresponsive_child_does_not_block_qt_and_is_reaped():
    app = QApplication.instance() or QApplication([])
    clock = [10.0]
    def spawn(*args, **kwargs):
        return subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
    worker = AudioProcess(process_factory=spawn, clock=lambda: clock[0])
    worker.start()
    started = time.monotonic()
    worker.stop()
    assert time.monotonic() - started < .2
    clock[0] += 2.1
    worker._poll()
    deadline = time.monotonic() + 2
    while worker.running and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    worker._poll()
    assert not worker.running


def test_native_child_crashes_have_finite_restarts_and_stale_generations_are_rejected():
    app = QApplication.instance() or QApplication([])
    now, children = [10.0], []
    def factory(*args, **kwargs):
        child = Process(); children.append(child); return child
    worker = AudioProcess(process_factory=factory, clock=lambda: now[0])
    worker.start(); worker.set_enabled(True)
    obsolete = worker._generation
    for index, delay in enumerate((1.0, 3.0, None)):
        children[-1].returncode = 1
        worker._poll()
        if delay is not None:
            now[0] += delay
            worker._poll()
            worker._messages.put({'active': True, 'initialized': True, 'generation': obsolete, 'peak': .8})
            worker._poll()
            assert not worker.activity_active
    assert len(children) == 3 and worker.status['state'] == 'error'
    now[0] += 100; worker._poll()
    assert len(children) == 3
    worker.stop(); worker._poll()


def test_blocked_control_pipe_never_blocks_gui_shutdown():
    app = QApplication.instance() or QApplication([])
    entered, release = threading.Event(), threading.Event()
    class Blocked:
        def write(self, text): entered.set(); release.wait(2)
        def flush(self): pass
        def close(self): pass
    child = Process(); child.stdin = Blocked()
    worker = AudioProcess(process_factory=lambda *a, **kw: child)
    try:
        worker.start()
        assert entered.wait(1)
        started = time.monotonic()
        worker.set_enabled(True); worker.set_endpoint('headphones'); worker.stop()
        assert time.monotonic() - started < .2
        worker.force_stop(); worker._poll()
        assert child.kills == 1 and not worker.running
    finally:
        release.set()
