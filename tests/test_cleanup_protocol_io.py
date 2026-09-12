"""Real filesystem status publication; no cleaner, task, UI, or privilege calls."""
from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import stat
import threading
import time

import pytest

import cleanup_protocol as protocol


def _held_reader(path, pipe):
    """An independent Windows process keeps its actual read handle open."""
    try:
        with protocol._json_io_lock(Path(path)):
            with protocol._open_json_read(Path(path)) as stream:
                pipe.send('opened')
                if not pipe.poll(5):
                    raise TimeoutError('parent did not release the reader')
                pipe.recv()
                pipe.send(json.loads(stream.read().decode('utf-8')))
    except BaseException as exc:
        pipe.send({'error': repr(exc)})
    finally:
        pipe.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows share-delete semantics')
def test_atomic_replace_while_another_process_retains_old_snapshot(tmp_path):
    target = tmp_path / 'status.json'
    protocol.write_json(target, {'generation': 1, 'message': '清理中'})
    parent, child = multiprocessing.get_context('spawn').Pipe()
    process = multiprocessing.get_context('spawn').Process(target=_held_reader, args=(str(target), child))
    process.start()
    child.close()
    try:
        assert parent.poll(5), 'real reader failed to open'
        assert parent.recv() == 'opened'
        errors = []
        attempting = threading.Event()
        def publish():
            attempting.set()
            try:
                protocol.write_json(target, {'generation': 2, 'message': '已完成'})
            except BaseException as exc:
                errors.append(exc)
        writer = threading.Thread(target=publish)
        writer.start()
        assert attempting.wait(1)
        time.sleep(0.02)
        assert writer.is_alive(), 'writer must wait until the reader closes'
        parent.send('read old snapshot')
        assert parent.poll(5)
        assert parent.recv() == {'generation': 1, 'message': '清理中'}
        writer.join(3)
        assert not writer.is_alive() and not errors
        assert protocol.read_json(target) == {'generation': 2, 'message': '已完成'}
        process.join(5)
        assert process.exitcode == 0
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert not list(tmp_path.glob('*.tmp'))


def test_real_read_json_can_be_preempted_without_blocking_publication(tmp_path, monkeypatch):
    target = tmp_path / 'status.json'
    protocol.write_json(target, {'revision': 1})
    opened, release = threading.Event(), threading.Event()
    original = protocol._open_json_read
    received = []

    class PreemptedReader:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def fileno(self): return self.stream.fileno()
        def read(self, size):
            opened.set()
            assert release.wait(3)
            return self.stream.read(size)

    # Only delay scheduling. No native handle, file operation, or error is mocked.
    monkeypatch.setattr(protocol, '_open_json_read', lambda p: PreemptedReader(original(p)))
    thread = threading.Thread(target=lambda: received.append(protocol.read_json(target)))
    thread.start()
    try:
        assert opened.wait(2)
        timer = threading.Timer(0.04, release.set)
        timer.start()
        protocol.write_json(target, {'revision': 2})
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert received == [{'revision': 1}]
    assert protocol.read_json(target) == {'revision': 2}


def test_repeated_real_readers_never_observe_partial_or_missing_json(tmp_path):
    target = tmp_path / 'status.json'
    protocol.write_json(target, {'revision': 0, 'payload': '0' * 4096})
    stop, started = threading.Event(), threading.Barrier(4)
    samples, errors = [], []

    def reader():
        count = 0
        started.wait(3)
        while not stop.is_set():
            row = protocol.read_json(target)
            if not row or row.get('payload') != str(row.get('revision')) * 4096:
                errors.append(row)
            count += 1
        samples.append(count)

    readers = [threading.Thread(target=reader) for _ in range(3)]
    for thread in readers: thread.start()
    try:
        started.wait(3)
        for revision in range(1, 121):
            protocol.write_json(target, {'revision': revision, 'payload': str(revision) * 4096})
    finally:
        stop.set()
        for thread in readers: thread.join(5)
    assert all(not thread.is_alive() for thread in readers)
    assert len(samples) == 3 and all(count > 0 for count in samples)
    assert errors == []
    assert protocol.read_json(target)['revision'] == 120


@pytest.mark.skipif(os.name != 'nt', reason='Windows legacy read sharing')
def test_legacy_reader_released_within_bound_allows_actual_replace(tmp_path):
    target = tmp_path / 'status.json'
    protocol.write_json(target, {'revision': 1})
    opened, close = threading.Event(), threading.Event()

    def legacy_reader():
        with target.open('rb') as stream:
            assert stream.read()
            opened.set()
            close.wait(0.04)

    thread = threading.Thread(target=legacy_reader)
    thread.start()
    try:
        assert opened.wait(2)
        protocol.write_json(target, {'revision': 2})
        assert protocol.read_json(target) == {'revision': 2}
    finally:
        close.set()
        thread.join(3)


@pytest.mark.skipif(os.name != 'nt', reason='Windows legacy read sharing')
def test_permanently_held_legacy_reader_still_fails_and_preserves_old_file(tmp_path):
    target = tmp_path / 'status.json'
    protocol.write_json(target, {'revision': 1})
    original = target.read_bytes()
    started = time.monotonic()
    with target.open('rb'):
        with pytest.raises(OSError) as error:
            protocol.write_json(target, {'revision': 2})
    duration = time.monotonic() - started
    assert error.value.winerror in (5, 32, 33)
    assert protocol._REPLACE_RETRY_SECONDS <= duration < 1.5
    assert target.read_bytes() == original
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.skipif(os.name != 'nt', reason='Windows read-only attribute')
def test_read_only_target_is_not_unprotected_or_reported_success(tmp_path):
    target = tmp_path / 'status.json'
    protocol.write_json(target, {'revision': 1})
    target.chmod(stat.S_IREAD)
    try:
        with pytest.raises(OSError):
            protocol.write_json(target, {'revision': 2})
        assert protocol.read_json(target) == {'revision': 1}
        assert target.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    finally:
        target.chmod(stat.S_IWRITE | stat.S_IREAD)
    assert not list(tmp_path.glob('*.tmp'))


def test_non_sharing_failure_is_not_retried(tmp_path, monkeypatch):
    calls = []
    def fail(*args):
        calls.append(args)
        raise OSError(28, 'Disk full')
    monkeypatch.setattr(protocol.os, 'replace', fail)
    with pytest.raises(OSError, match='Disk full'):
        protocol.write_json(tmp_path / 'status.json', {'revision': 1})
    assert len(calls) == 1
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('data', [b'[]', b'null', b'{', b'\xff', b'{"x":"' + b'x' * 1_000_000 + b'"}'],
                         ids=['array', 'null', 'malformed', 'invalid-utf8', 'oversized'])
def test_invalid_or_oversized_status_is_not_accepted(tmp_path, data):
    target = tmp_path / 'status.json'
    target.write_bytes(data)
    assert protocol.read_json(target) is None


def test_missing_and_directory_status_are_not_accepted(tmp_path):
    assert protocol.read_json(tmp_path / 'missing') is None
    assert protocol.read_json(tmp_path) is None


def test_handle_size_is_checked_after_open(tmp_path, monkeypatch):
    target = tmp_path / 'status.json'
    protocol.write_json(target, {'ok': True})
    large = tmp_path / 'large.json'
    large.write_bytes(b' ' * 1_000_001)
    original = protocol._open_json_read
    monkeypatch.setattr(protocol, '_open_json_read', lambda _: original(large))
    assert protocol.read_json(target) is None


def test_read_is_bounded_if_file_grows_after_size_check(tmp_path, monkeypatch):
    target = tmp_path / 'status.json'
    protocol.write_json(target, {'ok': True})
    original = protocol._open_json_read
    requested = []
    class GrowingReader:
        def __init__(self): self.stream = original(target)
        def __enter__(self): return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def fileno(self): return self.stream.fileno()
        def read(self, size):
            requested.append(size)
            return b' ' * size
    monkeypatch.setattr(protocol, '_open_json_read', lambda _: GrowingReader())
    assert protocol.read_json(target) is None
    assert requested == [1_000_001]


def test_immutable_receipt_still_refuses_overwrite(tmp_path):
    target = tmp_path / 'result.json'
    protocol.write_json(target, {'first': True}, immutable=True)
    with pytest.raises(FileExistsError):
        protocol.write_json(target, {'forged': True}, immutable=True)
    assert protocol.read_json(target) == {'first': True}


@pytest.mark.skipif(os.name != 'nt', reason='Windows OS-owned byte locks')
def test_dead_reader_releases_os_lock_without_removing_sidecar(tmp_path):
    target = tmp_path / 'status.json'
    protocol.write_json(target, {'generation': 1})
    parent, child = multiprocessing.get_context('spawn').Pipe()
    process = multiprocessing.get_context('spawn').Process(target=_held_reader, args=(str(target), child))
    process.start()
    child.close()
    try:
        assert parent.poll(5) and parent.recv() == 'opened'
        process.terminate()  # Only this test's identified child; no cleaner/API.
        process.join(5)
        assert not process.is_alive()
        protocol.write_json(target, {'generation': 2})
        assert protocol.read_json(target) == {'generation': 2}
        assert (tmp_path / '.status.json.io.lock').is_file()
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(5)


@pytest.mark.skipif(os.name != 'nt', reason='Windows sidecar lock')
def test_live_reader_cannot_hold_writer_beyond_lock_budget(tmp_path):
    target = tmp_path / 'status.json'
    protocol.write_json(target, {'generation': 1})
    started = time.monotonic()
    with protocol._json_io_lock(target):
        with pytest.raises(OSError) as error:
            protocol.write_json(target, {'generation': 2})
    assert error.value.winerror == 33
    assert protocol._IO_LOCK_SECONDS <= time.monotonic() - started < 1.5
    assert protocol.read_json(target) == {'generation': 1}
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.skipif(os.name != 'nt', reason='Windows sidecar lock')
def test_sidecar_directory_is_not_followed_or_replaced(tmp_path):
    target = tmp_path / 'status.json'
    target.write_text('{"original": true}', encoding='utf-8')
    (tmp_path / '.status.json.io.lock').mkdir()
    assert protocol.read_json(target) is None
    with pytest.raises((OSError, ValueError)):
        protocol.write_json(target, {'forged': True})
    assert json.loads(target.read_text()) == {'original': True}


def test_symlink_is_rejected_even_if_path_precheck_races(tmp_path, monkeypatch):
    target = tmp_path / 'actual.json'
    protocol.write_json(target, {'outside': True})
    link = tmp_path / 'link.json'
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f'Host cannot create test symlinks: {exc}')
    assert protocol.read_json(link) is None
    monkeypatch.setattr(Path, 'is_symlink', lambda _: False)
    assert protocol.read_json(link) is None
