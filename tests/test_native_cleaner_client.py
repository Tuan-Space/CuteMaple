from pathlib import Path

import cleanup_protocol
import memory_cleaner as client


def test_source_launch_uses_native_binary_without_python(monkeypatch, tmp_path):
    binary = tmp_path / 'CuteMaple-Cleaner.exe'
    binary.write_bytes(b'fixture only')
    monkeypatch.setattr(client, 'helper_path', lambda: binary)
    monkeypatch.setattr(client, 'is_compiled', lambda: False)
    assert client.helper_command('--diagnose') == [str(binary), '--diagnose']


def test_repeated_click_during_supervisor_transaction_is_busy(monkeypatch, tmp_path):
    monkeypatch.delenv('MEINIFENG_DISABLE_CLEAN_TASK', raising=False)
    operation = 'a' * 32
    monkeypatch.setattr(client, 'helper_command', lambda: ['fixture.exe'])
    monkeypatch.setattr(client, 'session_is_valid', lambda: True)
    monkeypatch.setattr(client, 'profile_path', lambda: tmp_path)
    monkeypatch.setattr(client, 'active_operation', lambda _: operation)
    def occupied(_):
        raise BlockingIOError('Supervisor publishing acknowledgement')
    monkeypatch.setattr(client, 'transaction_lock', occupied)
    result = client.begin_cleanup()
    assert result['status'] == 'busy' and result['operation_id'] == operation
    assert result['ok'] is False


def test_production_transport_has_no_python_server():
    import cleanup_session
    assert not hasattr(cleanup_session, 'serve')
    assert not hasattr(cleanup_session, 'SessionOperations')
    assert not (Path(client.__file__).parent / 'cleanup_helper.py').exists()
