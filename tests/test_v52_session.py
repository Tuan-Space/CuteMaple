"""No UAC or memory cleanup: protocol and benign local pipe integration only."""
from pathlib import Path
import threading
import uuid
import pytest
import cleanup_session as session
import cleanup_protocol as protocol
import memory_cleaner as client
from cleanup_process import current_identity, ObservedProcess


def test_local_pipe_session_handshake_and_shutdown(tmp_path):
    owner=current_identity(); token=uuid.uuid4().hex; errors=[]
    def server():
        try: session.serve(tmp_path,owner,token,lambda *a: pytest.fail('No operation may start'),Path(__file__))
        except Exception as exc: errors.append(exc)
    thread=threading.Thread(target=server,daemon=True); thread.start()
    connection=session.SessionClient(token,ObservedProcess(owner))
    try:
        assert connection.request('status')['status']=='authorized'
        assert connection.request('status')['operation'] is None
        assert connection.request('shutdown')['ok']
    finally:
        connection.close(); thread.join(5)
    assert not thread.is_alive()
    assert not errors


class Worker:
    def __init__(self): self.identity={'pid':2,'creationFiletime':2}; self.code=None; self.closed=False
    def poll(self): return self.code
    def wait(self,*_): return self.code
    def close(self): self.closed=True
class Job:
    def __init__(self): self.spawns=[]; self.closed=False
    def spawn(self,args,cwd): self.spawns.append(args); self.worker=Worker(); return self.worker
    def close(self): self.closed=True


@pytest.fixture
def manager(tmp_path):
    helper=tmp_path/'fixture.py'; helper.write_text('# never executed\n')
    owner={'pid':123,'creationFiletime':456}; job=Job()
    profile=tmp_path/'profile'; profile.mkdir()
    with protocol.transaction_lock(profile): request=protocol.create_operation(profile,helper,owner)
    manager=session.SessionOperations(profile,owner,lambda *args:list(args),helper,lambda:job)
    yield manager,request,job
    manager.close()


def test_session_single_flight_cancel_and_owned_exit(manager):
    manager,request,job=manager; op=request['operation_id']
    assert manager.dispatch('start',op)['ok']
    assert manager.dispatch('start',op)['status']=='busy'
    assert len(job.spawns)==1 and job.spawns[0][:3]==['--memory-clean-helper','--operation',op]
    assert manager.dispatch('cancel',op)['status']=='cancelling'
    assert protocol.read_json(protocol.operation_path(manager.profile,op)/'cancel.json')['operation_id']==op
    worker=job.worker; worker.code=11
    assert manager.dispatch('status')['operation'] is None
    assert worker.closed


@pytest.mark.parametrize('command,operation',[('exec','anything'),('start','../bad'),('cancel',None)])
def test_session_only_accepts_fixed_commands(manager,command,operation):
    manager,_,job=manager
    with pytest.raises(ValueError): manager.dispatch(command,operation)
    assert not job.spawns


def test_session_rejects_different_owner_and_expired_request(manager,monkeypatch):
    manager,request,job=manager
    manager.owner={'pid':999,'creationFiletime':123}
    with pytest.raises(PermissionError): manager.dispatch('start',request['operation_id'])
    manager.owner=request['client']
    monkeypatch.setattr(session.time,'time',lambda:request['requested_at']+16)
    with pytest.raises(ValueError): manager.dispatch('start',request['operation_id'])
    assert not job.spawns


def test_missing_helper_is_not_authorization_required(monkeypatch):
    monkeypatch.delenv('MEINIFENG_DISABLE_CLEAN_TASK',raising=False)
    monkeypatch.setattr(client,'helper_command',lambda *args: (_ for _ in ()).throw(FileNotFoundError('fixture')))
    assert client.begin_cleanup()['status']=='helper_missing'
    assert client.request_session_authorization()['status']=='helper_missing'


def test_security_block_is_reported_explicitly():
    error=OSError('blocked'); error.winerror=225
    assert client.cleanup_start_error(error)['status']=='security_blocked'


def test_backend_contains_no_task_registration_or_scheduling():
    import inspect, cleanup_helper
    source=inspect.getsource(client)+inspect.getsource(cleanup_helper)
    assert '"schtasks"' not in source
    assert '--install-clean-task' not in source


def test_pipe_rejects_wrong_token_then_accepts_original_owner(tmp_path,monkeypatch):
    owner=current_identity();token=uuid.uuid4().hex;errors=[]
    def server():
        try:session.serve(tmp_path,owner,token,lambda *a:pytest.fail('No worker'),Path(__file__))
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=server,daemon=True);thread.start()
    connection=session.SessionClient(token,ObservedProcess(owner));original=session.write_message
    try:
        assert connection.request('status')['ok']
        def corrupt(api,handle,value):
            if 'command' in value:value=dict(value,token='0'*32)
            return original(api,handle,value)
        with monkeypatch.context() as patch:
            patch.setattr(session,'write_message',corrupt)
            with pytest.raises(OSError):connection.request('status',timeout=1)
        assert connection.request('status')['ok']
        assert connection.request('shutdown')['ok']
    finally:connection.close();thread.join(5)
    assert not thread.is_alive() and not errors


def test_session_exits_after_owner_process_crash(tmp_path):
    import json, subprocess, sys, time
    # Only starts the benign broker and status handshake; no UAC or cleanup workers.
    script=tmp_path/'owner.py'; evidence=tmp_path/'broker.json'
    script.write_text('''import json,os,subprocess,sys,uuid
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from cleanup_process import current_identity,ObservedProcess,creation_time,kernel
from cleanup_session import SessionClient
owner=current_identity();token=uuid.uuid4().hex
code="import json,sys;from pathlib import Path;from cleanup_session import serve;serve(Path(sys.argv[1]),json.loads(sys.argv[2]),sys.argv[3],lambda *a: (_ for _ in ()).throw(RuntimeError('No workers allowed')),Path(sys.argv[4]))"
child=subprocess.Popen([sys._base_executable,'-c',code,sys.argv[2],json.dumps(owner),token,__file__],cwd=sys.argv[1])
identity={'pid':child.pid,'creationFiletime':creation_time(kernel(),int(child._handle))}
client=SessionClient(token,ObservedProcess(identity))
assert client.request('status')['ok']
Path(sys.argv[3]).write_text(json.dumps(identity))
sys.stdin.readline()
os._exit(17)
''',encoding='utf-8')
    root=Path(__file__).resolve().parents[1]
    owner=subprocess.Popen([sys.executable,str(script),str(root),str(tmp_path/'profile'),str(evidence)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    observed=None
    try:
        deadline=time.monotonic()+12
        while not evidence.exists() and owner.poll() is None and time.monotonic()<deadline:time.sleep(.02)
        assert evidence.exists(), owner.communicate(timeout=2)[1].decode(errors='replace') if owner.poll() is not None else 'Benign session handshake did not finish'
        observed=ObservedProcess(json.loads(evidence.read_text()))
        owner.communicate(b'exit\n',timeout=4);assert owner.returncode==17
        deadline=time.monotonic()+5
        while observed.poll() is None and time.monotonic()<deadline:time.sleep(.02)
        assert observed.poll()==0
    finally:
        if owner.poll() is None:owner.kill();owner.wait()
        if observed:observed.close()


def test_launch_security_code_survives_terminal_record(tmp_path):
    from monitor_ui import cleanup_summary
    protocol.operation_path(tmp_path,'a'*32).mkdir(parents=True)
    result=client._failure_before_start(tmp_path,'a'*32,'worker blocked',225)
    assert result['terminal'] and not result['ok'] and result['error_code']==225
    assert 'Windows 防护' in cleanup_summary(result)

@pytest.mark.parametrize('code,status',[(1223,'cancelled'),(225,'security_blocked'),(5,'failed')])
def test_uac_api_failure_is_distinct_without_real_elevation(tmp_path,monkeypatch,code,status):
    from types import SimpleNamespace
    native=session.ctypes.WinDLL
    class ShellCall:
        def __call__(self,_):session.ctypes.set_last_error(code);return False
    shell=SimpleNamespace(ShellExecuteExW=ShellCall())
    monkeypatch.setattr(session.ctypes,'WinDLL',lambda name,**kwargs:shell if name=='shell32' else native(name,**kwargs))
    monkeypatch.setattr(session,'_client',None)
    result=session.authorize(lambda *a:['fixture-never-executed.exe',*a],tmp_path)
    assert result['status']==status and not result['ok'] and result['error_code']==code
    assert result['cancelled']==(code==1223)


def test_valid_session_reuses_authorization_and_normal_exit_closes_it(tmp_path,monkeypatch):
    calls=[]
    class Connection:
        def alive(self):return True
        def request(self,command,**kwargs):calls.append(command);return {'ok':True}
        def close(self):calls.append('close')
    monkeypatch.setattr(session,'_client',Connection())
    assert session.authorize(lambda *a:pytest.fail('No second UAC'),tmp_path)['valid']
    session.close_session()
    assert calls==['shutdown','close'] and not session.session_valid()
