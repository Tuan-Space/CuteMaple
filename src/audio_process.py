"""GUI ownership of one unprivileged meter child, with bounded recovery and shutdown."""
from __future__ import annotations
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from PySide6.QtCore import QObject, QTimer, Signal


class AudioProcess(QObject):
    activityChanged = Signal(bool)
    levelChanged = Signal(float)
    statusChanged = Signal(dict)
    finished = Signal()
    MAX_RESTARTS = 2

    def __init__(self, parent=None, *, process_factory=subprocess.Popen, clock=time.monotonic):
        super().__init__(parent)
        self._factory, self._clock = process_factory, clock
        self._process = None
        self._messages = queue.Queue(maxsize=64)
        self._writes = None
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)
        self._enabled = False
        self._endpoint = 'default'
        self._generation = 0
        self._closing = False
        self._finished_emitted = False
        self._deadline = None
        self._last_heartbeat = 0.0
        self._restart_at = None
        self._restarts = 0
        self._recovering = False
        self._exit_handled = False
        self.activity_active = False
        self.initialized = False
        self.peak = 0.0
        self.status = {'state': 'stopped', 'peak': 0.0, 'endpoints': []}

    @property
    def running(self):
        return self._process is not None and self._process.poll() is None

    def start(self):
        if self.running or self._closing:
            return
        from pet_core import stable_application_path
        path = stable_application_path()
        command = [sys.executable, str(path)] if path.suffix.lower() == '.py' else [str(path)]
        self._exit_handled = False
        self._finished_emitted = False
        try:
            self._process = self._factory(command + ['--audio-probe-child'], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except OSError as error:
            self._log('audio_child_start_failed', error=str(error))
            self._process = None
            self._schedule_restart(str(error))
            self._timer.start()
            return
        self._last_heartbeat = self._clock()
        self._deadline = self._restart_at = None
        self._recovering = False
        process, messages = self._process, self._messages
        writes = self._writes = queue.Queue(maxsize=8)

        def read_pipe():
            try:
                while True:
                    line = process.stdout.readline(32769)
                    if not line or len(line) > 32768:
                        break
                    try:
                        messages.put_nowait(json.loads(line))
                    except (ValueError, queue.Full):
                        pass
            except (OSError, ValueError):
                pass
            finally:
                try: process.stdout.close()
                except (OSError, ValueError): pass

        def write_pipe():
            try:
                while process.poll() is None:
                    try:
                        value = writes.get(timeout=.2)
                    except queue.Empty:
                        continue
                    process.stdin.write(json.dumps(value) + '\n')
                    process.stdin.flush()
                    if value.get('stop'):
                        break
            except (OSError, ValueError):
                pass
            finally:
                try: process.stdin.close()
                except (OSError, ValueError): pass

        threading.Thread(target=read_pipe, name='audio-pipe', daemon=True).start()
        threading.Thread(target=write_pipe, name='audio-control-pipe', daemon=True).start()
        self._set_status({'state': 'starting', 'attempt': self._restarts, 'peak': 0.0})
        self._timer.start()
        self._send({'enabled': self._enabled, 'endpoint': self._endpoint})

    def _finish(self):
        if not self._finished_emitted:
            self._finished_emitted = True
            self.finished.emit()

    def _log(self, name, **fields):
        try:
            from diagnostics import event
            event(name, **fields)
        except ImportError:
            pass

    def _send(self, value):
        if not self.running or self._writes is None:
            return
        # Pipe writes run off the GUI thread. Only the latest configuration matters.
        while not self._writes.empty():
            try:
                self._writes.get_nowait()
            except queue.Empty:
                break
        try:
            self._writes.put_nowait({**value, 'generation': self._generation})
        except queue.Full:
            pass

    def _publish(self, value):
        value = bool(value and self._enabled and not self._closing and not self._recovering)
        if value != self.activity_active:
            self.activity_active = value
            self.activityChanged.emit(value)

    def _level(self, value):
        value = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else 0.0
        value = max(0.0, min(1.0, value)) if self._enabled and not self._closing else 0.0
        if value != self.peak:
            self.peak = value
            self.levelChanged.emit(value)

    def _set_status(self, value):
        merged = {**self.status, **value}
        if merged != self.status:
            self.status = merged
            self.statusChanged.emit(dict(merged))

    def set_enabled(self, enabled):
        if self._closing or bool(enabled) == self._enabled:
            return
        self._enabled = bool(enabled)
        self._generation += 1
        self._publish(False)
        self._level(0)
        self._send({'enabled': self._enabled, 'endpoint': self._endpoint})
        if not self._enabled:
            self.initialized = False
            self._set_status({'state': 'disabled', 'peak': 0.0})

    def set_endpoint(self, endpoint):
        if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 512 or '\0' in endpoint:
            raise ValueError('Invalid output endpoint ID')
        if endpoint == self._endpoint or self._closing:
            return
        self._endpoint = endpoint
        self._generation += 1
        self._publish(False)
        self._level(0)
        self._restarts = 0
        self._send({'enabled': self._enabled, 'endpoint': endpoint, 'retry': True})
        if not self.running and self._process is not None:
            self._restart_at = self._clock()
            self._timer.start()

    def retry(self):
        if self._closing:
            return
        self._restarts = 0
        self._generation += 1
        self._publish(False)
        self._level(0)
        if self.running:
            self._send({'enabled': self._enabled, 'endpoint': self._endpoint, 'retry': True})
        else:
            self._restart_at = self._clock()
            self._timer.start()

    def _schedule_restart(self, error):
        self.initialized = False
        self._publish(False)
        self._level(0)
        if not self._closing and self._restarts < self.MAX_RESTARTS:
            self._restarts += 1
            self._restart_at = self._clock() + (1 if self._restarts == 1 else 3)
            self._set_status({'state': 'retrying', 'attempt': self._restarts, 'error': str(error)[:240], 'peak': 0.0})
        else:
            self._restart_at = None
            self._set_status({'state': 'stopped' if self._closing else 'error', 'error': str(error)[:240], 'peak': 0.0})

    def _poll(self):
        while not self._messages.empty():
            message = self._messages.get_nowait()
            if not isinstance(message, dict) or not isinstance(message.get('active'), bool) or message.get('generation') != self._generation:
                continue
            self._last_heartbeat = self._clock()
            if self._closing or self._recovering:
                continue
            self.initialized = message.get('initialized') is True
            self._publish(message['active'])
            self._level(message.get('peak', 0.0))
            fields = {key: message[key] for key in ('state', 'endpointId', 'endpointName', 'muted', 'attempt', 'error') if key in message}
            endpoints = message.get('endpoints')
            if isinstance(endpoints, list):
                fields['endpoints'] = [{key: item[key] for key in ('id', 'name', 'isDefault') if key in item}
                    for item in endpoints[:32] if isinstance(item, dict) and isinstance(item.get('id'), str) and len(item['id']) <= 512]
            self._set_status({**fields, 'peak': self.peak})
        if not self.running:
            if not self._exit_handled and self._process is not None:
                self._exit_handled = True
                self._log('audio_child_exit', code=self._process.returncode)
                if self._closing and self._process.returncode == 0:
                    self._set_status({'state': 'stopped', 'error': '', 'peak': 0.0})
                else:
                    self._schedule_restart(f'Audio child exited ({self._process.returncode})')
                self._finish()
            if self._closing:
                self._timer.stop()
                self._finish()
            elif self._restart_at is not None and self._clock() >= self._restart_at:
                self._generation += 1
                self._restart_at = None
                self.start()
            elif self._restart_at is None:
                self._timer.stop()
        elif self._deadline is not None and self._clock() >= self._deadline:
            self._kill_owned()
            self._deadline = None
        elif not self._closing and not self._recovering and self._clock() - self._last_heartbeat > 5:
            self._log('audio_child_unresponsive')
            self._recovering = True
            self.initialized = False
            self._publish(False)
            self._level(0)
            self._set_status({'state': 'retrying', 'error': 'Audio meter heartbeat timed out', 'peak': 0.0})
            self._deadline = self._clock() + 2
            self._send({'stop': True})

    def stop(self):
        if self._closing:
            return
        self._closing = True
        self.initialized = False
        self._restart_at = None
        self._publish(False)
        self._level(0)
        self._deadline = self._clock() + 2.0
        self._timer.start()
        self._send({'stop': True})
        if not self.running:
            self._finish()

    def _kill_owned(self):
        if self.running:
            self._log('audio_child_kill', pid=self._process.pid)
            try:
                self._process.kill()
            except OSError as error:
                self._log('audio_child_kill_failed', error=str(error))

    def force_stop(self):
        self._closing = True
        self.initialized = False
        self._restart_at = None
        self._publish(False)
        self._level(0)
        self._kill_owned()
        self._deadline = None
