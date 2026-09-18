"""Local, parent-bound cleanup sessions. No persistence and no cleanup API calls."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import ctypes
from ctypes import wintypes as w
import hmac
import json
import os
from pathlib import Path
import threading
import time
import uuid

from cleanup_process import (kernel, creation_time, current_identity, ObservedProcess,
                             OwnedWorker, WorkerJob, valid_identity)
from cleanup_protocol import (valid_operation, load_request, active_operation, operation_path,
                              write_json, START_TIMEOUT)

MAX_MESSAGE = 16384
PIPE_ACCESS = 0x12019B  # Read/write/synchronize, excluding CREATE_PIPE_INSTANCE.
_lock = threading.RLock()
_client = None


def pipe_api():
    api = kernel()
    definitions = {
        'CreateNamedPipeW': ([w.LPCWSTR,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,ctypes.c_void_p],w.HANDLE),
        'ConnectNamedPipe': ([w.HANDLE,ctypes.c_void_p],w.BOOL),
        'DisconnectNamedPipe': ([w.HANDLE],w.BOOL),
        'CreateFileW': ([w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE],w.HANDLE),
        'SetNamedPipeHandleState': ([w.HANDLE,ctypes.POINTER(w.DWORD),ctypes.c_void_p,ctypes.c_void_p],w.BOOL),
        'ReadFile': ([w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p],w.BOOL),
        'WriteFile': ([w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p],w.BOOL),
        'GetNamedPipeClientProcessId': ([w.HANDLE,ctypes.POINTER(w.DWORD)],w.BOOL),
        'GetNamedPipeServerProcessId': ([w.HANDLE,ctypes.POINTER(w.DWORD)],w.BOOL),
        'GetProcessId': ([w.HANDLE],w.DWORD),
    }
    for name,(args,result) in definitions.items():
        fn=getattr(api,name); fn.argtypes=args; fn.restype=result
    return api


def process_logon_sid(handle):
    """Logon SID binds both pipe access and elevation to the original login."""
    api=kernel(); adv=ctypes.WinDLL('advapi32',use_last_error=True)
    adv.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)]; adv.OpenProcessToken.restype=w.BOOL
    adv.GetTokenInformation.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]; adv.GetTokenInformation.restype=w.BOOL
    adv.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]; adv.ConvertSidToStringSidW.restype=w.BOOL
    class Group(ctypes.Structure): _fields_=[('sid',ctypes.c_void_p),('attributes',w.DWORD)]
    class Groups(ctypes.Structure): _fields_=[('count',w.DWORD),('groups',Group*1)]
    token=w.HANDLE(); size=w.DWORD()
    if not adv.OpenProcessToken(handle,8,ctypes.byref(token)): raise ctypes.WinError(ctypes.get_last_error())
    try:
        adv.GetTokenInformation(token,2,None,0,ctypes.byref(size))
        data=ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token,2,data,size,ctypes.byref(size)): raise ctypes.WinError(ctypes.get_last_error())
        count=ctypes.cast(data,ctypes.POINTER(w.DWORD)).contents.value
        entries=(Group*count).from_buffer(data,Groups.groups.offset)
        for group in entries:
            if group.attributes & 0xC0000000 == 0xC0000000:
                text=ctypes.c_void_p()
                if not adv.ConvertSidToStringSidW(group.sid,ctypes.byref(text)): raise ctypes.WinError(ctypes.get_last_error())
                try: return ctypes.wstring_at(text)
                finally: api.LocalFree(text)
        raise PermissionError('Missing logon SID')
    finally: api.CloseHandle(token)


def pipe_name(token):
    if not valid_operation(token): raise ValueError('Invalid session token')
    return r'\\.\pipe\CuteMaple-Cleanup-' + token


def read_message(api,handle):
    data=ctypes.create_string_buffer(MAX_MESSAGE); n=w.DWORD()
    if not api.ReadFile(handle,data,MAX_MESSAGE,ctypes.byref(n),None):
        code=ctypes.get_last_error()
        if code==232: return None
        raise ctypes.WinError(code)
    if not n.value: return None
    value=json.loads(data.raw[:n.value].decode('utf-8'))
    if not isinstance(value,dict): raise ValueError('Invalid session message')
    return value


def write_message(api,handle,value):
    data=json.dumps(value,separators=(',',':'),ensure_ascii=True).encode('utf-8')
    if len(data)>MAX_MESSAGE: raise ValueError('Session message too large')
    n=w.DWORD()
    if not api.WriteFile(handle,data,len(data),ctypes.byref(n),None): raise ctypes.WinError(ctypes.get_last_error())
    if n.value != len(data): raise OSError('Incomplete session message')


class SessionClient:
    def __init__(self,token,process): self.token,self.process=token,process
    def alive(self): return self.process.poll() is None
    def request(self,command,operation=None,timeout=5):
        if command not in {'status','start','cancel','shutdown'}: raise ValueError('Unknown session command')
        api=pipe_api(); deadline=time.monotonic()+timeout; handle=None
        try:
            while time.monotonic()<deadline and self.alive():
                handle=api.CreateFileW(pipe_name(self.token),PIPE_ACCESS,0,None,3,0,None)
                if handle != w.HANDLE(-1).value: break
                handle=None; code=ctypes.get_last_error()
                if code not in (2,231): raise ctypes.WinError(code)
                time.sleep(.02)
            if handle is None: raise TimeoutError('会话助手连接超时或已退出')
            peer=w.DWORD()
            if (not api.GetNamedPipeServerProcessId(handle,ctypes.byref(peer))
                    or peer.value != self.process.identity['pid'] or not self.alive()):
                raise PermissionError('会话助手身份不匹配')
            mode=w.DWORD(3)  # Message read + NOWAIT; every IPC wait is bounded.
            if not api.SetNamedPipeHandleState(handle,ctypes.byref(mode),None,None): raise ctypes.WinError(ctypes.get_last_error())
            request=uuid.uuid4().hex
            write_message(api,handle,{'version':1,'token':self.token,'request':request,'command':command,'operation':operation})
            while time.monotonic()<deadline and self.alive():
                result=read_message(api,handle)
                if result is not None:
                    if result.get('request')!=request or result.get('session')!=self.token: raise PermissionError('过期会话结果')
                    return result
                time.sleep(.01)
            raise TimeoutError('会话助手未返回响应')
        finally:
            if handle is not None: api.CloseHandle(handle)
    def close(self): self.process.close()


def session_valid():
    with _lock:
        try: return _client is not None and _client.alive()
        except OSError: return False


def session_request(command,operation=None):
    with _lock:
        if _client is None: return {'ok':False,'status':'authorization_required','message':'本次运行尚未授权'}
        return _client.request(command,operation)


def authorize(command_factory,profile):
    """Called only by an explicit user action on a background thread."""
    global _client
    with _lock:
        if session_valid(): return {'ok':True,'valid':True,'status':'authorized','message':'本次运行已授权'}
        token=uuid.uuid4().hex; owner=current_identity()
        command=command_factory('--clean-session','--session',token,'--owner-pid',str(owner['pid']),
                                '--owner-created',str(owner['creationFiletime']),'--profile',str(profile))
        import subprocess
        class Execute(ctypes.Structure):
            _fields_=[('cbSize',w.DWORD),('fMask',w.ULONG),('hwnd',w.HWND),('verb',w.LPCWSTR),
                ('file',w.LPCWSTR),('parameters',w.LPCWSTR),('directory',w.LPCWSTR),('show',ctypes.c_int),
                ('instance',w.HINSTANCE),('idList',ctypes.c_void_p),('className',w.LPCWSTR),('classKey',w.HKEY),
                ('hotKey',w.DWORD),('icon',w.HANDLE),('process',w.HANDLE)]
        shell=ctypes.WinDLL('shell32',use_last_error=True)
        shell.ShellExecuteExW.argtypes=[ctypes.POINTER(Execute)]; shell.ShellExecuteExW.restype=w.BOOL
        info=Execute(cbSize=ctypes.sizeof(Execute),fMask=0x40,verb='runas',file=command[0],
                     parameters=subprocess.list2cmdline(command[1:]),show=0)
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            code=ctypes.get_last_error()
            return {'ok':False,'cancelled':code==1223,'status':'cancelled' if code==1223 else ('security_blocked' if code in (225,226) else 'failed'),
                    'error_code':code,'message':'已取消本次授权' if code==1223 else f'无法启动会话助手（{code}）'}
        api=pipe_api()
        try: process=OwnedWorker(api,info.process,api.GetProcessId(info.process))
        except BaseException:
            api.CloseHandle(info.process); raise
        candidate=SessionClient(token,process)
        try:
            result=candidate.request('status',timeout=10)
            if not result.get('ok'): raise OSError(result.get('message','会话助手未就绪'))
            if _client: _client.close()
            _client=candidate
            return {'ok':True,'valid':True,'status':'authorized','message':'本次运行已授权'}
        except BaseException:
            # Closing the retained process handle is not permission to kill it.
            # Unclaimed servers self-expire after the bounded startup handshake.
            candidate.close(); raise


def close_session():
    global _client
    with _lock:
        if _client:
            try: _client.request('shutdown',timeout=2)
            except (OSError,ValueError): pass
            finally: _client.close(); _client=None


class SessionOperations:
    """Only starts the fixed supervisor for an immutable request from this owner."""
    def __init__(self,profile,owner,command_factory,helper,job_factory=WorkerJob):
        self.profile,self.owner,self.command_factory,self.helper=profile,owner,command_factory,helper
        self.job=job_factory(); self.worker=None; self.operation=None; self.closing=False; self.closed_at=None
    def poll(self):
        if self.worker and self.worker.poll() is not None:
            self.worker.close(); self.worker=None; self.operation=None
    def dispatch(self,command,operation=None):
        self.poll()
        if command=='status': return {'ok':True,'status':'authorized','operation':self.operation}
        if command=='shutdown':
            self.shutdown(); return {'ok':True,'status':'closing'}
        if command not in {'start','cancel'} or not valid_operation(operation): raise ValueError('Invalid cleanup command')
        if self.closing: raise RuntimeError('Session is closing')
        request=load_request(self.profile,operation,helper=self.helper)
        if request['client']!=self.owner: raise PermissionError('Cleanup request owner mismatch')
        if command=='cancel':
            if operation!=self.operation: return {'ok':False,'status':'failed','message':'本次操作已经结束或不属于此会话'}
            write_json(operation_path(self.profile,operation)/'cancel.json',{'operation_id':operation,'requested_at':time.time()})
            return {'ok':True,'status':'cancelling'}
        if self.worker:
            return {'ok':operation==self.operation,'status':'busy','operation_id':self.operation}
        if (active_operation(self.profile)!=operation or time.time()-request['requested_at']>START_TIMEOUT
                or (operation_path(self.profile,operation)/'started.json').exists()
                or (operation_path(self.profile,operation)/'launch-failure.json').exists()):
            raise ValueError('Expired or already consumed cleanup request')
        command=self.command_factory('--memory-clean-helper','--operation',operation,'--profile',str(self.profile))
        self.worker=self.job.spawn(command,self.helper.parent)
        self.operation=operation
        return {'ok':True,'status':'queued','operation_id':operation}
    def shutdown(self):
        self.closing=True
        if self.closed_at is None: self.closed_at=time.monotonic()
        if self.operation:
            write_json(operation_path(self.profile,self.operation)/'cancel.json',{'operation_id':self.operation,'requested_at':time.time()})
    def close(self):
        self.job.close()  # Nested supervisors/workers belong only to this session.
        if self.worker:
            self.worker.wait(2); self.worker.close(); self.worker=None


def serve(profile,owner,token,command_factory,helper):
    if not valid_identity(owner): raise ValueError('Invalid session owner')
    name=pipe_name(token); api=pipe_api(); parent=ObservedProcess(owner)
    descriptor=ctypes.c_void_p(); handle=None; manager=None
    adv=ctypes.WinDLL('advapi32',use_last_error=True)
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype=w.BOOL
    class Security(ctypes.Structure): _fields_=[('length',w.DWORD),('descriptor',ctypes.c_void_p),('inherit',w.BOOL)]
    try:
        sid=process_logon_sid(parent.handle)
        if sid!=process_logon_sid(api.GetCurrentProcess()): raise PermissionError('Authorization must use the same Windows login')
        # Explicit medium integrity permits the ordinary owner to contact this
        # elevated server; the logon DACL, token and process binding still apply.
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(f'D:P(A;;GA;;;SY)(A;;0x{PIPE_ACCESS:x};;;{sid})S:(ML;;NW;;;ME)',1,ctypes.byref(descriptor),None):
            raise ctypes.WinError(ctypes.get_last_error())
        security=Security(ctypes.sizeof(Security),descriptor,False)
        handle=api.CreateNamedPipeW(name,3|0x80000,4|2|1|8,1,MAX_MESSAGE,MAX_MESSAGE,0,ctypes.byref(security))
        if handle==w.HANDLE(-1).value: handle=None; raise ctypes.WinError(ctypes.get_last_error())
        manager=SessionOperations(profile,owner,command_factory,helper)
        connected=False; authenticated=False; started=time.monotonic(); connected_at=started
        while True:
            manager.poll()
            if parent.poll() is not None or (not authenticated and time.monotonic()-started>12): manager.shutdown()
            if manager.closing:
                if manager.worker is None or time.monotonic()-manager.closed_at>=3: return 0
                time.sleep(.02); continue
            if not connected:
                ok=api.ConnectNamedPipe(handle,None); code=0 if ok else ctypes.get_last_error()
                if code in (0,535):
                    peer=w.DWORD()
                    if not api.GetNamedPipeClientProcessId(handle,ctypes.byref(peer)) or peer.value!=owner['pid'] or parent.poll() is not None:
                        api.DisconnectNamedPipe(handle); continue
                    connected=True; connected_at=time.monotonic()
                elif code==232: api.DisconnectNamedPipe(handle)
                elif code!=536: raise ctypes.WinError(code)
            if connected:
                try:
                    message=read_message(api,handle)
                    if message is not None:
                        if (message.get('version')!=1 or not isinstance(message.get('token'),str)
                                or not hmac.compare_digest(message['token'],token) or not valid_operation(message.get('request'))):
                            raise PermissionError('Invalid session identity')
                        authenticated=True
                        try: response=manager.dispatch(message.get('command'),message.get('operation'))
                        except (OSError,ValueError,RuntimeError) as exc:
                            response={'ok':False,'status':'failed','message':str(exc),'error_code':getattr(exc,'winerror',None)}
                        write_message(api,handle,dict(response,request=message['request'],session=token))
                        # Client reads before closing; do not discard its response.
                        connected_at=time.monotonic()-4
                    if time.monotonic()-connected_at>5:
                        api.DisconnectNamedPipe(handle); connected=False
                except (OSError,ValueError):
                    api.DisconnectNamedPipe(handle); connected=False
            time.sleep(.01)
    finally:
        if manager: manager.close()
        if handle: api.CloseHandle(handle)
        if descriptor.value: api.LocalFree(descriptor)
        parent.close()
