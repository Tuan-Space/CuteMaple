"""Cleanup execution/transport regression tests. Native cleanup is never invoked."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
import reference_cleanup_helper as helper
import cleanup_protocol as protocol
import cleanup_process as processes
import memory_cleaner as client

CLIENT = {"pid": 100, "creationFiletime": 1000}
SUPERVISOR = {"pid": 200, "creationFiletime": 2000}


@pytest.fixture
def operation(tmp_path, monkeypatch):
    # test_app configures a process-wide no-system-integration environment at
    # collection time. This fixture has isolated paths and mocked scheduling;
    # test the singleflight boundary rather than that unrelated early bypass.
    monkeypatch.delenv("MEINIFENG_DISABLE_CLEAN_TASK", raising=False)
    profile = tmp_path / "profile"
    profile.mkdir()
    executable = tmp_path / "helper-source-fixture.py"
    executable.write_text("# A hash fixture only, never executed.\n")
    monkeypatch.setattr(helper, "helper_path", lambda: executable)
    monkeypatch.setattr(client, "helper_path", lambda: executable)
    monkeypatch.setattr(client, "profile_path", lambda: profile)
    with protocol.transaction_lock(profile):
        request = protocol.create_operation(profile, executable, CLIENT)
    folder = protocol.operation_path(profile, request["operation_id"])
    return profile, executable, request, folder


def acknowledge(folder, request):
    protocol.write_json(folder / "client-observed.json", {"supervisor": SUPERVISOR, "client": request["client"]})


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def sleep(self, duration): self.now += duration


class Mutex:
    def __init__(self, *_): self.closed = False
    def acquire(self): return True
    def close(self): self.closed = True


class FakeWorker:
    def __init__(self, index, *, stuck=False, code=0):
        self.identity = {"pid": 300 + index, "creationFiletime": 3000 + index}
        self.code = None if stuck else code
        self.terminated = False
        self.closed = False
    def poll(self): return self.code
    def terminate(self, code): self.terminated = True; self.code = code
    def wait(self, _): return self.code
    def close(self): self.closed = True


class Job:
    def __init__(self, folder, *, stuck=None, fail=None, forged=None, on_spawn=None):
        self.folder, self.stuck, self.fail, self.forged = folder, stuck, fail, forged
        self.on_spawn, self.workers, self.closed = on_spawn, [], False
    def spawn(self, command, cwd, before_resume):
        step, op, token = command
        worker = FakeWorker(len(self.workers), stuck=step == self.stuck, code=20 if step == self.fail else 0)
        before_resume(worker.identity)
        self.workers.append(worker)
        if self.on_spawn: self.on_spawn(step)
        if step != self.stuck:
            protocol.write_json(helper._step_file(self.folder, step, "result"), {
                "operation_id": op, "step": step, "token": "forged" if step == self.forged else token,
                "worker": worker.identity, "exit_code": worker.code, "ok": worker.code == 0,
                "status": "succeeded" if worker.code == 0 else "failed", "message": "fixture",
                "before_available": 1000, "after_available": 2000})
        return worker
    def close(self): self.closed = True


def run_supervisor(operation, job, clock=None, **kwargs):
    profile, _, request, folder = operation
    acknowledge(folder, request)
    clock = clock or Clock()
    code = helper.supervise(profile, request["operation_id"], job_factory=lambda: job,
                            mutex_factory=Mutex, identity_factory=lambda: SUPERVISOR,
                            alive=lambda _: True, clock=clock, sleep=clock.sleep,
                            command_factory=lambda step, op, token, _: [step, op, token], **kwargs)
    return code, protocol.read_json(folder / "result.json")


def test_real_entry_constants_correct_registry_and_kernel32(monkeypatch):
    calls = []
    class Function:
        def __call__(self, *args): calls.append(args); return 1
    api = SimpleNamespace(SetSystemFileCacheSize=Function())
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **_: (calls.append(name), api)[1])
    assert helper._shrink_system_file_cache()[0]
    assert calls[0] == "kernel32"
    maximum = ctypes.c_size_t(-1).value
    assert calls[1] == (maximum, maximum, 0)
    monkeypatch.setattr(helper, "_nt_set", lambda cls, payload: (calls.append((cls, payload)), (True, "ok"))[1])
    monkeypatch.setattr(sys, "getwindowsversion", lambda: (10, 0))
    assert helper.perform_step("registry_cache") == (True, "ok")
    assert calls[-1] == (155, None)
    with pytest.raises(ValueError): helper.perform_step("arbitrary-command")


def test_request_is_immutable_and_rejects_replaced_helper(operation):
    profile, executable, request, folder = operation
    with pytest.raises(FileExistsError):
        protocol.write_json(folder / "request.json", {"operation_id": "b" * 32}, immutable=True)
    executable.write_text("# modified fixture\n")
    with pytest.raises(ValueError, match="清理程序已变化"):
        protocol.load_request(profile, request["operation_id"], helper=executable)
    with pytest.raises(ValueError): protocol.operation_path(profile, "../other")


def test_transaction_lock_excludes_a_second_process(operation):
    profile, _, _, _ = operation
    script = "from pathlib import Path\nfrom cleanup_protocol import transaction_lock\nimport sys\ntry:\n with transaction_lock(Path(sys.argv[1])): raise SystemExit(9)\nexcept BlockingIOError: raise SystemExit(0)\n"
    with protocol.transaction_lock(profile):
        result = subprocess.run([sys.executable, "-B", "-c", script, str(profile)], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_all_steps_return_matching_results_and_real_worker_exits(operation):
    job = Job(operation[3])
    code, result = run_supervisor(operation, job)
    assert code == 0 and result["status"] == "succeeded"
    assert len(result["steps"]) == 6 and all(row["exit_verified"] for row in result["steps"].values())
    assert result["available_increase"] == 1000
    assert result['guardTriggered'] is False and result['guards'] == []
    assert result['work_duration_seconds'] == 0
    assert result['limits'] == {'stepSeconds': 15., 'workSeconds': 45., 'supervisorSeconds': 60., 'handshakeSeconds': 10.}
    assert job.closed and all(worker.closed for worker in job.workers)


def test_worker_failure_is_partial_not_success(operation):
    code, result = run_supervisor(operation, Job(operation[3], fail="registry_cache"))
    assert code == 10 and result["status"] == "partial"
    assert not result["steps"]["registry_cache"]["ok"]


def test_forged_step_receipt_does_not_pass(operation):
    code, result = run_supervisor(operation, Job(operation[3], forged="registry_cache"))
    assert code == 10 and result["steps"]["registry_cache"]["status"] == "failed"


def test_stuck_step_is_terminated_at_15_seconds_then_no_more_steps(operation):
    clock, job = Clock(), Job(operation[3], stuck="flush_modified_pages")
    code, result = run_supervisor(operation, job, clock)
    assert code == 13 and result["status"] == "timed_out"
    assert 15 <= clock.now < 15.2
    assert len(job.workers) == 2 and job.workers[-1].terminated
    assert result["steps"]["combine_memory"]["status"] == "not_run"
    assert result['guardTriggered'] is True and result['guards']
    assert result['work_duration_seconds'] >= 15


def test_cancel_terminates_only_current_owned_worker(operation):
    folder, request = operation[3], operation[2]
    def cancel(_):
        protocol.write_json(folder / "cancel.json", {"operation_id": request["operation_id"]})
    job = Job(folder, stuck="empty_working_sets", on_spawn=cancel)
    code, result = run_supervisor(operation, job)
    assert code == 12 and result["status"] == "cancelled"
    assert len(job.workers) == 1 and job.workers[0].terminated


def test_no_client_observer_never_starts_a_privileged_step(operation):
    profile, _, request, folder = operation
    clock = Clock()
    code = helper.supervise(profile, request["operation_id"],
        job_factory=lambda: pytest.fail("must not create job"), identity_factory=lambda: SUPERVISOR,
        alive=lambda _: True, clock=clock, sleep=clock.sleep)
    assert code == 11
    result = protocol.read_json(folder / "result.json")
    assert not result["workers"] and not result["client_observed"]


def test_begin_duplicate_request_preserves_active_operation(operation, monkeypatch):
    profile, _, request, folder = operation
    original = (folder / "request.json").read_bytes()
    monkeypatch.setattr(client, "session_is_valid", lambda: True)
    monkeypatch.setattr(client.subprocess, "run", lambda *_, **__: pytest.fail("must not schedule a duplicate"))
    result = client.begin_cleanup()
    assert not result["ok"] and result["status"] == "busy"
    assert protocol.active_operation(profile) == request["operation_id"]
    assert (folder / "request.json").read_bytes() == original


def test_client_checks_exit_code_not_just_terminal_json(operation, monkeypatch):
    profile, _, request, folder = operation
    protocol.write_json(folder / "started.json", {"operation_id": request["operation_id"], "supervisor": SUPERVISOR})
    protocol.write_json(folder / "result.json", {"operation_id": request["operation_id"], "status": "succeeded", "workers": [], "steps": {}})
    class Process:
        def __init__(self, identity): assert identity == SUPERVISOR
        def poll(self): return 11
        def close(self): pass
    monkeypatch.setattr(client, "ObservedProcess", Process)
    monkeypatch.setattr(client, "identity_alive", lambda _: False)
    result = client.read_cleanup(request["operation_id"])
    assert result["status"] == "failed" and result["processExitVerified"] and result["exitCodeVerified"]
    assert protocol.active_operation(profile) is None


def test_missing_historical_exit_code_recovers_failed_only(operation, monkeypatch):
    profile, _, request, folder = operation
    protocol.write_json(folder / "started.json", {"operation_id": request["operation_id"], "supervisor": SUPERVISOR})
    protocol.write_json(folder / "status.json", {"operation_id": request["operation_id"], "status": "running", "workers": []})
    monkeypatch.setattr(client, "ObservedProcess", lambda _: (_ for _ in ()).throw(ProcessLookupError("gone")))
    monkeypatch.setattr(client, "identity_alive", lambda _: False)
    result = client.read_cleanup(request["operation_id"])
    assert result["terminal"] and result["processExitVerified"] and not result["exitCodeVerified"]
    assert result["status"] == "failed" and not result["ok"]
    assert protocol.active_operation(profile) is None


def test_supervisor_gone_but_worker_alive_keeps_lease(operation, monkeypatch):
    profile, _, request, folder = operation
    worker = {"pid": 300, "creationFiletime": 3000}
    protocol.write_json(folder / "started.json", {"operation_id": request["operation_id"], "supervisor": SUPERVISOR})
    protocol.write_json(folder / "status.json", {"operation_id": request["operation_id"], "status": "running", "workers": [{"worker": worker}]})
    monkeypatch.setattr(client, "ObservedProcess", lambda _: (_ for _ in ()).throw(ProcessLookupError("gone")))
    monkeypatch.setattr(client, "identity_alive", lambda value: value == worker)
    result = client.read_cleanup(request["operation_id"])
    assert not result["terminal"] and not result["processExitVerified"]
    assert protocol.active_operation(profile) == request["operation_id"]


@pytest.mark.parametrize("task_state,mutex_idle,expected", [(3, True, True), (4, True, False), (3, False, False)])
def test_expired_unclaimed_queue_requires_idle_task_and_mutex(operation, monkeypatch, task_state, mutex_idle, expected):
    profile, _, request, folder = operation
    monkeypatch.setattr(client.time, "time", lambda: request["requested_at"] + 70)
    monkeypatch.setattr(client, "_query_task_state", lambda: task_state)
    class IdleMutex(Mutex):
        def acquire(self): return mutex_idle
    monkeypatch.setattr(client, "ExecutionMutex", IdleMutex)
    assert client._recover_queued(profile, request["operation_id"]) is expected
    assert (protocol.active_operation(profile) is None) is expected
    if expected:
        result = protocol.read_json(folder / "launch-failure.json")
        assert result["status"] == "failed" and not result["exitCodeVerified"]


def test_expired_request_cannot_be_claimed_after_queue_recovery(operation, monkeypatch):
    profile, _, request, folder = operation
    monkeypatch.setattr(helper.time, "time", lambda: request["requested_at"] + 70)
    with pytest.raises(ValueError, match="过期"):
        helper.supervise(profile, request["operation_id"], identity_factory=lambda: SUPERVISOR)
    assert not (folder / "started.json").exists()


def test_profile_task_names_isolate_test_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(client, "profile_path", lambda: tmp_path / "美腻枫")
    assert client.task_name() == "美腻枫_内存清理"
    monkeypatch.setattr(client, "profile_path", lambda: tmp_path / "qa-profile")
    assert client.task_name().startswith("美腻枫_内存清理_")
    assert len(client.task_name().rsplit("_", 1)[1]) == 12


def test_client_refreshes_final_written_during_exit_poll(operation, monkeypatch):
    profile, _, request, folder = operation
    code, final = run_supervisor(operation, Job(folder))
    assert code == 0
    (folder / "result.json").unlink()
    protocol.write_json(folder / "status.json", {"operation_id": request["operation_id"], "status": "running", "workers": []})
    class Process:
        def __init__(self, _): pass
        def poll(self):
            protocol.write_json(folder / "result.json", final, immutable=True)
            return 0
        def close(self): pass
    monkeypatch.setattr(client, "ObservedProcess", Process)
    monkeypatch.setattr(client, "identity_alive", lambda _: False)
    result = client.read_cleanup(request["operation_id"])
    assert result["ok"] and result["status"] == "succeeded" and result["processExitVerified"]
    assert protocol.active_operation(profile) is None


def test_zero_exit_with_missing_steps_is_failed(operation, monkeypatch):
    _, _, request, folder = operation
    _, final = run_supervisor(operation, Job(folder))
    final["steps"].pop("registry_cache")
    protocol.write_json(folder / "result.json", final)
    class Process:
        def __init__(self, _): pass
        def poll(self): return 0
        def close(self): pass
    monkeypatch.setattr(client, "ObservedProcess", Process)
    monkeypatch.setattr(client, "identity_alive", lambda _: False)
    result = client.read_cleanup(request["operation_id"])
    assert result["status"] == "failed" and not result["ok"] and result["processExitVerified"]


def test_total_work_budget_does_not_become_six_step_timeouts(operation):
    clock = Clock()
    class DelayedJob(Job):
        def spawn(self, *args, **kwargs):
            worker = super().spawn(*args, **kwargs)
            ready_at, success_code = clock.now + 12, worker.code
            worker.poll = lambda: worker.code if worker.terminated else (success_code if clock.now >= ready_at else None)
            return worker
    job = DelayedJob(operation[3])
    code, result = run_supervisor(operation, job, clock)
    assert code == 13 and result["status"] == "timed_out"
    assert 45 <= clock.now < 45.2
    assert len(job.workers) == 4 and job.workers[-1].terminated


def test_cancel_before_start_runs_no_worker(operation):
    protocol.write_json(operation[3] / "cancel.json", {"operation_id": operation[2]["operation_id"]})
    job = Job(operation[3])
    code, result = run_supervisor(operation, job)
    assert code == 12 and not job.workers
    assert all(row["status"] == "not_run" for row in result["steps"].values())


@pytest.mark.skipif(os.name != "nt", reason="Windows process ownership")
def test_real_owned_job_child_exits_and_does_not_import_cleanup(tmp_path):
    # This child prints nothing and invokes no privileged action or helper CLI.
    marker = tmp_path / "benign-child.txt"
    job, worker, observer = processes.WorkerJob(), None, None
    try:
        registered = []
        worker = job.spawn([sys.executable, "-B", "-c",
            "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('benign'); time.sleep(.15)", str(marker)],
            tmp_path, before_resume=lambda identity: registered.append(identity))
        assert registered == [worker.identity]
        observer = processes.ObservedProcess(worker.identity)
        assert worker.wait(5) == 0 and observer.poll() == 0
        assert marker.read_text() == "benign"
    finally:
        job.close()
        if worker is not None: worker.wait(2); worker.close()
        if observer is not None: observer.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows process ownership")
def test_real_job_close_kills_only_its_owned_sleeping_child(tmp_path):
    job, worker = processes.WorkerJob(), None
    try:
        worker = job.spawn([sys.executable, "-B", "-c", "import time; time.sleep(30)"], tmp_path)
        assert worker.poll() is None
        job.close()
        assert worker.wait(5) is not None
        assert not processes.identity_alive(worker.identity)
    finally:
        job.close()
        if worker is not None: worker.wait(2); worker.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows process ownership")
def test_failed_identity_registration_cannot_leave_suspended_child(tmp_path, monkeypatch):
    original = processes.OwnedWorker
    captured = []
    def fail(api, handle, pid):
        captured.append({"pid": int(pid), "creationFiletime": processes.creation_time(api, handle)})
        raise ValueError("simulated identity bookkeeping failure")
    monkeypatch.setattr(processes, "OwnedWorker", fail)
    job = processes.WorkerJob()
    try:
        with pytest.raises(ValueError, match="bookkeeping"):
            job.spawn([sys.executable, "-B", "-c", "import time; time.sleep(30)"], tmp_path)
        assert len(captured) == 1 and not processes.identity_alive(captured[0])
    finally:
        job.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows process ownership")
def test_named_mutex_excludes_different_process_without_cleanup():
    mutex = processes.ExecutionMutex("step")
    assert mutex.acquire()
    try:
        script = "from cleanup_process import ExecutionMutex\nm=ExecutionMutex('step')\na=m.acquire()\nm.close()\nraise SystemExit(9 if a else 0)\n"
        result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, timeout=5,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
    finally:
        mutex.close()


def test_raw_ntstatus_is_recorded_numerically_and_without_changing_native_arguments(monkeypatch):
    received = []
    class Function:
        def __call__(self, cls, pointer, size):
            received.append((cls, pointer, size))
            return -1073741821  # STATUS_INVALID_INFO_CLASS, actual prior failure.
    trace = []
    monkeypatch.setattr(helper, '_native_trace', trace)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_args, **_kwargs: SimpleNamespace(NtSetSystemInformation=Function()))
    ok, message = helper._nt_set(155, None)
    assert not ok and message == 'NTSTATUS 0xC0000003'
    assert received == [(155, None, 0)]
    assert trace == [{'api': 'NtSetSystemInformation', 'dll': 'ntdll', 'informationClass': 155,
                      'inputSize': 0, 'inputInteger': None, 'ntstatus': 0xC0000003,
                      'ntstatusHex': '0xC0000003', 'succeeded': False}]


def test_raw_win32_failure_records_bool_and_last_error(monkeypatch):
    class Function:
        def __call__(self, *_): return 0
    trace = []
    monkeypatch.setattr(helper, '_native_trace', trace)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_args, **_kwargs: SimpleNamespace(SetSystemFileCacheSize=Function()))
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 5)
    assert helper._shrink_system_file_cache() == (False, 'WinError 5')
    assert trace == [{'api': 'SetSystemFileCacheSize', 'dll': 'kernel32', 'returnValue': 0,
                      'lastError': 5, 'succeeded': False}]


@pytest.mark.parametrize('ack_at,expected', [(5.4, 0), (9.8, 0), (10.2, 11)])
def test_slow_scheduler_return_can_be_acknowledged_without_native_work_before_ack(operation, monkeypatch, ack_at, expected):
    profile, _, request, folder = operation
    clock, job = Clock(), Job(folder)
    def sleep(duration):
        clock.sleep(duration)
        if clock.now >= ack_at and not (folder/'client-observed.json').exists():
            acknowledge(folder, request)
    def create_job():
        assert clock.now >= ack_at and (folder/'client-observed.json').is_file()
        return job
    monkeypatch.setattr(helper, 'perform_step', lambda _: pytest.fail('supervisor must not invoke a native cleanup API'))
    code = helper.supervise(profile, request['operation_id'], job_factory=create_job, mutex_factory=Mutex,
        identity_factory=lambda: SUPERVISOR, alive=lambda _: True, clock=clock, sleep=sleep,
        command_factory=lambda step, op, token, _: [step, op, token])
    result = protocol.read_json(folder/'result.json')
    assert code == expected and 0 <= result['handshake_duration_seconds'] <= 10.05
    if expected:
        assert not job.workers and result['work_duration_seconds'] is None
    else:
        assert result['handshake_duration_seconds'] >= ack_at and len(job.workers) == 6
        assert result['duration_seconds'] < 60 and result['work_duration_seconds'] == 0
