"""Exercise the native TEST binary through the unchanged Python client protocol.

This never requests UAC or invokes real cleanup. The separately compiled test
worker has no code path to a native cleanup API.
"""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT));sys.path.insert(0,str(ROOT/"src"))


def run(executable, output):
    executable = executable.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    profile = output / 'profile'
    profile.mkdir(exist_ok=True)
    os.environ['MEINIFENG_PROFILE_DIRECTORY'] = str(profile)
    os.environ.pop('MEINIFENG_DISABLE_CLEAN_TASK', None)
    import cleanup_protocol as protocol
    import cleanup_session as session
    import cleanup_process as processes
    import memory_cleaner as client
    client.helper_path = lambda: executable
    client.helper_command = lambda *args: [str(executable), *args]
    diagnostic = output / 'diagnostic.json'
    p = subprocess.run([str(executable), '--diagnose', '--report', str(diagnostic)], timeout=15)
    assert p.returncode == 0
    assert json.loads(diagnostic.read_text())['testBuild'] is True
    owner = processes.current_identity()
    results = []

    def start(case):
        token = uuid.uuid4().hex
        p = subprocess.Popen([str(executable), '--clean-session', '--session', token,
                              '--owner-pid', str(owner['pid']), '--owner-created', str(owner['creationFiletime']),
                              '--profile', str(profile), '--test-case', case], creationflags=subprocess.CREATE_NO_WINDOW)
        observed = processes.ObservedProcess({'pid': p.pid, 'creationFiletime': processes.creation_time(processes.kernel(), int(p._handle))})
        candidate = session.SessionClient(token, observed)
        try:
            assert candidate.request('status', timeout=10)['ok']
        except BaseException:
            p.terminate(); p.wait(10); observed.close(); raise
        session._client = candidate
        return p

    def collect(operation, *, cancel=False):
        deadline = time.monotonic() + 67
        cancelled = False
        while time.monotonic() < deadline:
            value = client.read_cleanup(operation)
            if cancel and not cancelled and value and value.get('workers'):
                assert client.cancel_cleanup(operation)['ok']; cancelled = True
            if value and value.get('terminal'):
                assert value['processExitVerified'] and value['exitCodeVerified'], value
                assert not any(processes.identity_alive(w['worker']) for w in value['workers'])
                return value
            time.sleep(.03)
        raise AssertionError(f'No complete result: {value}')

    for case, expected in [('success', 'succeeded'), ('slow', 'succeeded'), ('fail', 'partial'),
                           ('crash', 'partial'), ('hang', 'timed_out'), ('cancel', 'cancelled')]:
        p = start('hang' if case == 'cancel' else case)
        try:
            request = client.begin_cleanup()
            assert request['ok'], request
            duplicate = client.begin_cleanup()
            assert duplicate['status'] == 'busy' and duplicate['operation_id'] == request['operation_id'], duplicate
            value = collect(request['operation_id'], cancel=case == 'cancel')
            assert value['status'] == expected, value
            assert set(value['steps']) == set(protocol.STEPS)
            results.append({'case': case, 'passed': True, 'result': value})
            if case in ('success', 'fail', 'crash'):
                second = client.begin_cleanup(); assert second['ok'], second
                assert collect(second['operation_id'])['status'] == expected
            print(case + ': passed', flush=True)
        finally:
            session.close_session()
            assert p.wait(10) == 0
    # Production-only mode is checked separately; the test binary rejects
    # foreign identities, stale messages and bad tokens in exactly the same pipe.
    p = start('success')
    try:
        original_write = session.write_message
        def corrupt(api, handle, message):
            return original_write(api, handle, dict(message, token='0' * 32))
        session.write_message = corrupt
        try:
            session._client.request('status', timeout=.2)
            raise AssertionError('Wrong token accepted')
        except (OSError, TimeoutError):
            pass
        finally:
            session.write_message = original_write
        assert session._client.request('status')['ok']
        results.append({'case': 'wrong-token', 'passed': True})
    finally:
        session.close_session(); assert p.wait(10) == 0
    p = start('success')
    try:
        for fault in ('owner', 'hash', 'expired', 'steps'):
            with protocol.transaction_lock(profile):
                req = protocol.create_operation(profile, executable, owner)
            path = protocol.operation_path(profile, req['operation_id']) / 'request.json'
            if fault == 'owner': req['client'] = dict(owner, pid=owner['pid'] + 1)
            if fault == 'hash': req['helper_sha256'] = '0' * 64
            if fault == 'expired': req['requested_at'] -= 100
            if fault == 'steps': req['steps'] = ['arbitrary-command']
            protocol.write_json(path, req)
            response = session._client.request('start', req['operation_id'])
            assert not response['ok'], (fault, response)
            assert not (path.parent / 'started.json').exists()
            protocol.release_lease(profile, req['operation_id'])
            results.append({'case': 'reject-' + fault, 'passed': True})
    finally:
        session.close_session(); assert p.wait(10) == 0
    for victim in ('supervisor', 'broker'):
        p = start('hang')
        try:
            req = client.begin_cleanup(); assert req['ok'], req
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                state = client.read_cleanup(req['operation_id'])
                if state and state.get('workers'): break
                time.sleep(.02)
            assert state.get('workers'), state
            if victim == 'broker': p.terminate()
            else:
                api = processes.kernel()
                handle = api.OpenProcess(1 | processes.QUERY, False, state['supervisor']['pid'])
                assert handle
                try:
                    assert processes.creation_time(api, handle) == state['supervisor']['creationFiletime']
                    assert api.TerminateProcess(handle, 91)
                finally: api.CloseHandle(handle)
            value = collect(req['operation_id'])
            assert value['status'] == 'failed' and not value['ok'], value
            results.append({'case': victim + '-crash', 'passed': True, 'result': value})
        finally:
            session.close_session(); p.wait(10)
    # A foreign process cannot use the original client's authenticated pipe.
    p = start('success')
    try:
        payload = json.dumps(session._client.process.identity)
        code = "import sys,json;sys.path.insert(0,str(__import__('pathlib').Path(sys.argv[1])/'src'));from cleanup_session import SessionClient;from cleanup_process import ObservedProcess;c=SessionClient(sys.argv[2],ObservedProcess(json.loads(sys.argv[3])));c.request('status',timeout=.3)"
        stranger = subprocess.run([sys.executable, '-c', code, str(ROOT), session._client.token, payload], capture_output=True, timeout=5)
        assert stranger.returncode != 0
        assert session._client.request('status')['ok']
        results.append({'case': 'reject-foreign-process', 'passed': True})
    finally:
        session.close_session(); assert p.wait(10) == 0
    owner_script = output / 'owner_probe.py'
    owner_evidence = output / 'owner-probe.json'
    owner_script.write_text('''import json,os,subprocess,sys,uuid
from pathlib import Path
sys.path.insert(0,str(__import__('pathlib').Path(sys.argv[1])/'src'))
from cleanup_process import current_identity,ObservedProcess,creation_time,kernel
from cleanup_session import SessionClient
owner=current_identity();token=uuid.uuid4().hex
child=subprocess.Popen([sys.argv[2],'--clean-session','--session',token,'--owner-pid',str(owner['pid']),'--owner-created',str(owner['creationFiletime']),'--profile',sys.argv[3],'--test-case','success'],creationflags=subprocess.CREATE_NO_WINDOW)
identity={'pid':child.pid,'creationFiletime':creation_time(kernel(),int(child._handle))}
client=SessionClient(token,ObservedProcess(identity));assert client.request('status')['ok']
Path(sys.argv[4]).write_text(json.dumps(identity))
sys.stdin.readline();os._exit(17)
''', encoding='utf-8')
    owner_child = subprocess.Popen([sys.executable, str(owner_script), str(ROOT), str(executable),
                                   str(profile), str(owner_evidence)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 12
        while not owner_evidence.exists() and owner_child.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert owner_evidence.exists(), owner_child.communicate(timeout=1)
        broker_id = json.loads(owner_evidence.read_text())
        owner_child.communicate(b'quit\n', timeout=5)
        deadline = time.monotonic() + 6
        while processes.identity_alive(broker_id) and time.monotonic() < deadline:
            time.sleep(.02)
        assert not processes.identity_alive(broker_id)
        results.append({'case': 'owner-crash', 'passed': True})
    finally:
        if owner_child.poll() is None: owner_child.terminate(); owner_child.wait(5)
    report = {'passed': True, 'realCleanupPerformed': False, 'executable': str(executable),
              'sha256': protocol.digest(executable), 'results': results}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.executable, args.output)
