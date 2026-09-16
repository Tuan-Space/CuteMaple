#include "platform.h"
#include <cstdio>
#include <iomanip>
using namespace maple;
namespace {
constexpr double step_limit=15,work_limit=45,supervisor_limit=60;
fs::path profile;
#ifdef MAPLE_CLEANER_TEST
std::string test_case="success";
#endif
std::vector<std::wstring> child_args(std::vector<std::wstring> args){
    args.insert(args.end(),{L"--profile",profile.wstring()});
#ifdef MAPLE_CLEANER_TEST
    args.insert(args.end(),{L"--test-case",wide(test_case)});
#endif
    return args;
}
fs::path folder(const std::string& op){require(valid_id(op),"Invalid operation ID");return profile/L"cleanup"/L"operations"/wide(op);}
fs::path step_file(const fs::path& dir,const std::string& step,const std::string& suffix){
    auto it=std::find(steps.begin(),steps.end(),step);require(it!=steps.end(),"Unknown step");
    std::ostringstream out;out<<"step-"<<std::setw(2)<<std::setfill('0')<<(it-steps.begin())<<"-"<<step<<"-"<<suffix<<".json";return dir/wide(out.str());
}
J request(const std::string& op){
    auto dir=folder(op);DirectoryGuard guard(dir);auto r=read_json(dir/L"request.json");
    require(r.is_object()&&r.value("schema",0)==schema&&r.value("operation_id","")==op&&r.value("profile","")==utf8(profile.wstring())&&r.value("steps",J())==J(steps),"Request format mismatch");
    double age=now()-r.value("requested_at",0.);require(age>=0&&age<=supervisor_limit,"Expired request");
    require(r.value("helper","")==utf8(executable().wstring())&&r.value("helper_sha256","")==digest(executable()),"Helper identity mismatch");
    require(valid_identity(r.value("client",J())),"Client identity invalid");return r;
}
std::string active(){auto j=read_json(profile/L"cleanup"/L"active.json");return j.is_object()?j.value("operation_id",""):"";}
bool privilege(const wchar_t* name){HANDLE raw;if(!OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY|TOKEN_ADJUST_PRIVILEGES,&raw))return false;Handle token(raw);TOKEN_PRIVILEGES p{};p.PrivilegeCount=1;if(!LookupPrivilegeValueW(nullptr,name,&p.Privileges[0].Luid))return false;p.Privileges[0].Attributes=SE_PRIVILEGE_ENABLED;SetLastError(0);return AdjustTokenPrivileges(token.h,FALSE,&p,0,nullptr,nullptr)&&GetLastError()==ERROR_SUCCESS;}
bool nt_set(ULONG cls,void* input,ULONG size,J& calls,std::optional<int> integer={}){
    using NtSet=LONG(NTAPI*)(ULONG,PVOID,ULONG);
    auto fn=(NtSet)GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtSetSystemInformation");require(fn!=nullptr,"Native memory API unavailable");
    LONG status=fn(cls,input,size);std::ostringstream text;text<<"0x"<<std::hex<<std::uppercase<<std::setw(8)<<std::setfill('0')<<ULONG(status);
    calls.push_back(J{{"api","NtSetSystemInformation"},{"dll","ntdll"},{"informationClass",cls},{"inputSize",size},{"inputInteger",integer?J(*integer):J()},{"ntstatus",ULONG(status)},{"ntstatusHex",text.str()},{"succeeded",status>=0}});return status>=0;
}
bool working_set_fallback(J& calls){
    std::vector<DWORD> pids(65536);DWORD bytes;require(EnumProcesses(pids.data(),DWORD(pids.size()*sizeof(DWORD)),&bytes)!=0,"Process enumeration");unsigned attempted=0,ok=0,denied=0;J returns=J::array();
    for(size_t i=0;i<bytes/sizeof(DWORD);i++){
        auto pid=pids[i];if(pid<=4||pid==GetCurrentProcessId())continue;attempted++;
        Handle process(OpenProcess(PROCESS_SET_QUOTA|PROCESS_QUERY_LIMITED_INFORMATION,FALSE,pid));
        if(!process){denied++;continue;}
        auto returned=EmptyWorkingSet(process.h);DWORD error=returned?0:GetLastError();if(returned)ok++;
        returns.push_back(J{{"pid",pid},{"opened",true},{"returnValue",returned},{"lastError",error}});
    }
    bool success=ok>0||attempted==0;calls.push_back(J{{"api","EmptyWorkingSet"},{"dll","psapi"},{"attempted",attempted},{"succeededCount",ok},{"accessDeniedCount",denied},{"returns",returns},{"succeeded",success}});return success;
}
bool perform(const std::string& step,J& row){
    auto& calls=row["nativeCalls"];calls=J::array();
#ifdef MAPLE_CLEANER_TEST
    // Fault injection exists only in a separately named test executable.
    // This build never invokes a real memory cleanup API, even when elevated.
    if(test_case=="hang"&&step==steps[0])for(;;)Sleep(1000);
    if(test_case=="crash"&&step==steps[0])TerminateProcess(GetCurrentProcess(),99);
    if(test_case=="slow")Sleep(800);
    if(test_case=="fail"&&step==steps[2])return false;
    calls.push_back(J{{"api","TEST_ONLY"},{"succeeded",true}});return true;
#else
    const wchar_t* required=step=="system_file_cache"?SE_INCREASE_QUOTA_NAME:step=="registry_cache"?nullptr:SE_PROF_SINGLE_PROCESS_NAME;
    if(required){bool enabled=privilege(required);row["privileges"][utf8(required)]=enabled;if(!enabled){row["message"]="无法启用该步骤所需权限";return false;}}
    if(step=="empty_working_sets"){int value=2;return nt_set(80,&value,sizeof(value),calls,value)||working_set_fallback(calls);}
    if(step=="flush_modified_pages"||step=="purge_standby_list"){int value=step=="flush_modified_pages"?3:4;return nt_set(80,&value,sizeof(value),calls,value);}
    if(step=="system_file_cache"){BOOL ok=SetSystemFileCacheSize(SIZE_T(-1),SIZE_T(-1),0);DWORD error=ok?0:GetLastError();calls.push_back(J{{"api","SetSystemFileCacheSize"},{"dll","kernel32"},{"returnValue",ok},{"lastError",error},{"succeeded",ok!=0}});return ok!=0;}
    if(step=="registry_cache")return nt_set(155,nullptr,0,calls);
    if(step=="combine_memory"){
        struct Combine{HANDLE handle;ULONG_PTR pages;ULONG flags;};static_assert(sizeof(Combine)==24);Combine input{};bool ok=nt_set(130,&input,sizeof(input),calls);calls.back()["pagesCombined"]=input.pages;return ok;
    }
    throw Error("Unknown step",ERROR_INVALID_PARAMETER);
#endif
}
int step_main(const std::string& step,const std::string& op,const std::string& token){
    require(valid_id(token),"Invalid worker token");auto dir=folder(op);DirectoryGuard guard(dir);auto req=request(op);auto launch=read_json(step_file(dir,step,"launch"));auto self=identity();
    require(launch.is_object()&&launch.value("token","")==token&&launch.value("step","")==step&&launch.value("operation_id","")==op&&launch.value("worker",J())==self&&launch.value("request_sha256","")==digest(dir/L"request.json"),"Worker launch mismatch");
    auto parent=observe(launch.at("supervisor"));require(parent_pid()==launch["supervisor"]["pid"].get<DWORD>()&&!poll(parent.h),"Worker parent mismatch");
    J row={{"schema",schema},{"operation_id",op},{"step",step},{"token",token},{"evidenceKind","windows-native-cleanup-step"},{"evidenceVersion",1},{"implementation","cpp-msvc"},{"processElevated",elevated()},{"supervisor",launch["supervisor"]},{"worker",self},{"started_at",now()},{"ok",false},{"status","running"},{"privileges",J::object()},{"nativeCalls",J::array()}};
    double start=monotonic();write_json(step_file(dir,step,"started"),row,true);
    try{Mutex mutex("step");require(mutex.acquire(),"Another cleanup step still owns execution");auto before=memory();row["before_available"]=before["available"];row["before_percent"]=before["percent"];row["ok"]=perform(step,row);auto after=memory();row["after_available"]=after["available"];row["after_percent"]=after["percent"];if(!row.contains("message"))row["message"]=row["ok"].get<bool>()?"清理步骤完成":"系统接口返回失败，详见调用记录";}
    catch(const std::exception& e){row["ok"]=false;row["message"]=e.what();}
    row["status"]=row["ok"].get<bool>()?"succeeded":"failed";row["exit_code"]=row["ok"].get<bool>()?0:20;row["completed_at"]=now();row["duration_seconds"]=monotonic()-start;write_json(step_file(dir,step,"result"),row,true);return row["exit_code"].get<int>();
}
int exit_code(const std::string& status){return status=="succeeded"?0:status=="partial"?10:status=="cancelled"?12:status=="timed_out"?13:11;}
int supervise(const std::string& op){
    auto dir=folder(op);DirectoryGuard guard(dir);auto req=request(op);auto client=observe(req["client"]);auto self=identity();double start=monotonic(),work_start=0;
    J state={{"schema",schema},{"operation_id",op},{"status","starting"},{"evidenceKind","windows-cleanup-supervisor"},{"evidenceVersion",1},{"implementation","cpp-msvc"},{"message","清理助手已接单，等待客户端确认"},{"supervisor",self},{"steps",J::object()},{"workers",J::array()},{"started_at",now()},{"request_sha256",digest(dir/L"request.json")},{"client_observed",false},{"before_available",nullptr},{"after_available",nullptr},{"available_increase",nullptr},{"guardTriggered",false},{"guards",J::array()},{"limits",{{"stepSeconds",15},{"workSeconds",45},{"supervisorSeconds",60},{"handshakeSeconds",10}}}};
    {FileLock transaction(profile/L"cleanup"/L"transaction.lock",true,.2);require(active()==op&&now()-req["requested_at"].get<double>()<=15&&!fs::exists(dir/L"launch-failure.json"),"Stale supervisor request");write_json(dir/L"started.json",state,true);write_json(dir/L"status.json",state);}
    auto publish=[&](){state["heartbeat_at"]=now();write_json(dir/L"status.json",state);};
    auto cancelled=[&](){auto c=read_json(dir/L"cancel.json");return poll(client.h).has_value()||(c.is_object()&&c.value("operation_id","")==op);};
    std::string terminal="failed",message="清理未完成";std::optional<Child> worker;Job job;
    try{
        while(monotonic()-start<10&&!cancelled()){
            auto ack=read_json(dir/L"client-observed.json");if(ack.is_object()&&ack.value("client",J())==req["client"]&&ack.value("supervisor",J())==self){state["client_observed"]=true;break;}Sleep(20);
        }
        state["handshake_duration_seconds"]=monotonic()-start;require(state["client_observed"].get<bool>(),"Client exit observer not established");
        Mutex mutex("supervisor");require(mutex.acquire(),"Another cleanup operation owns execution");work_start=monotonic();terminal="succeeded";
        for(const auto& step:steps){
            if(cancelled()){terminal="cancelled";break;}
            if(monotonic()-work_start>=work_limit||monotonic()-start>=supervisor_limit-3){terminal="timed_out";state["guardTriggered"]=true;state["guards"].push_back({{"scope","work-or-supervisor"}});break;}
            auto token=random_id();J launch={{"schema",schema},{"operation_id",op},{"step",step},{"token",token},{"supervisor",self},{"request_sha256",state["request_sha256"]}};
            state["status"]="running";state["step"]=step;state["message"]="正在清理："+step;
            worker.emplace(job.spawn(child_args({L"--clean-step",wide(step),L"--operation",wide(op),L"--token",wide(token)}),[&](const J& id){launch["worker"]=id;state["workers"].push_back({{"step",step},{"worker",id}});write_json(step_file(dir,step,"launch"),launch,true);publish();}));
            double began=monotonic(),last=began;std::string interruption;
            while(!poll(worker->handle.h)){
                if(cancelled()){interruption="cancelled";break;}
                if(monotonic()-began>=step_limit||monotonic()-work_start>=work_limit||monotonic()-start>=supervisor_limit-3){interruption="timed_out";state["guardTriggered"]=true;state["guards"].push_back({{"scope","step-or-work-or-supervisor"},{"step",step},{"stepSeconds",monotonic()-began}});break;}
                if(monotonic()-last>=.5){publish();last=monotonic();}Sleep(20);
            }
            if(!interruption.empty()){state["status"]="cancelling";state["message"]="正在等待本次工作进程退出";publish();require(TerminateProcess(worker->handle.h,interruption=="timed_out"?72:73)!=0,"Terminate owned worker");WaitForSingleObject(worker->handle.h,2000);terminal=interruption;}
            auto code=poll(worker->handle.h);J row=read_json(step_file(dir,step,"result"));
            bool consistent=row.is_object()&&row.value("operation_id","")==op&&row.value("step","")==step&&row.value("token","")==token&&row.value("worker",J())==worker->id&&code&&(code==0u||code==20u)&&row.value("exit_code",-1)==int(*code)&&row.value("ok",false)==(*code==0);
            if(!interruption.empty()||!consistent)row=J{{"ok",false},{"status",interruption.empty()?"failed":interruption},{"message",interruption.empty()?"步骤结果缺失或与进程退出码不一致":"清理步骤已停止"},{"worker",worker->id},{"exit_code",code?J(*code):J()}};
            row["exit_verified"]=code.has_value();row["duration_seconds"]=monotonic()-began;state["steps"][step]=row;state["workers"].back()["exit_verified"]=code.has_value();state["workers"].back()["exit_code"]=code?J(*code):J();
            if(state["before_available"].is_null()&&row.contains("before_available"))state["before_available"]=row["before_available"];
            if(row.contains("after_available"))state["after_available"]=row["after_available"];
            if(!row.value("ok",false)&&terminal=="succeeded")terminal="partial";
            publish();if(!code){terminal="failed";message="所属工作进程尚未退出，未继续清理";break;}worker.reset();if(!interruption.empty())break;
        }
    }catch(const std::exception& e){terminal="failed";message=e.what();}
    // A final result is never proof of exit. The GUI additionally verifies all
    // owned process identities and the supervisor's actual exit code.
    if(worker&&!poll(worker->handle.h)){job.terminate(74);WaitForSingleObject(worker->handle.h,2000);auto code=poll(worker->handle.h);if(!state["workers"].empty()){state["workers"].back()["exit_verified"]=code.has_value();state["workers"].back()["exit_code"]=code?J(*code):J();}if(!code)terminal="failed";}
    for(const auto& step:steps)if(!state["steps"].contains(step))state["steps"][step]=J{{"ok",false},{"status","not_run"},{"message","前序异常或取消，未执行"},{"exit_verified",true},{"exit_code",nullptr}};
    if(terminal=="partial"){bool any=false;for(const auto& row:state["steps"].items())any|=row.value().value("ok",false);if(!any)terminal="failed";}
    if(terminal=="succeeded")message="六项清理全部完成";else if(terminal=="partial")message="部分清理步骤失败，请查看详情";else if(terminal=="cancelled")message="本次清理已取消";else if(terminal=="timed_out")message="清理超时，已停止所属工作进程";
    if(state["before_available"].is_number()&&state["after_available"].is_number())state["available_increase"]=state["after_available"].get<int64_t>()-state["before_available"].get<int64_t>();
    state["status"]=terminal;state["message"]=message;state["completed_at"]=now();state["duration_seconds"]=monotonic()-start;state["work_duration_seconds"]=work_start?J(monotonic()-work_start):J();state["expected_exit_code"]=exit_code(terminal);write_json(dir/L"result.json",state,true);publish();return exit_code(terminal);
}
int serve(const J& owner,const std::string& token){
    require(valid_id(token),"Invalid session token");auto parent=observe(owner);require(sid(parent.h,true)==sid(GetCurrentProcess(),true),"Different Windows login");
    auto pipe_name=L"\\\\.\\pipe\\CuteMaple-Cleanup-"+wide(token);
    auto sddl=L"D:P(A;;GA;;;SY)(A;;0x12019b;;;"+sid(parent.h,true)+L")S:(ML;;NW;;;ME)";PSECURITY_DESCRIPTOR descriptor=nullptr;
    require(ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(),SDDL_REVISION_1,&descriptor,nullptr)!=0,"Pipe security descriptor");
    SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES),descriptor,FALSE};Handle pipe(CreateNamedPipeW(pipe_name.c_str(),PIPE_ACCESS_DUPLEX|FILE_FLAG_FIRST_PIPE_INSTANCE,PIPE_TYPE_MESSAGE|PIPE_READMODE_MESSAGE|PIPE_NOWAIT|PIPE_REJECT_REMOTE_CLIENTS,1,16384,16384,0,&security));LocalFree(descriptor);require(bool(pipe),"Create local pipe");
    Job job;std::optional<Child> worker;std::string operation;bool connected=false,authenticated=false,closing=false,replied=false;double start=monotonic(),connected_at=0,closed_at=0;
    auto shutdown=[&](){if(!closing){closing=true;closed_at=monotonic();if(worker)write_json(folder(operation)/L"cancel.json",J{{"operation_id",operation},{"requested_at",now()}});}};
    while(true){
        if(worker&&poll(worker->handle.h)){worker.reset();operation.clear();}
        if(poll(parent.h)||(!authenticated&&monotonic()-start>12))shutdown();
        if(closing){if(!worker)return 0;if(monotonic()-closed_at>=3){job.terminate(74);WaitForSingleObject(worker->handle.h,2000);return 0;}Sleep(20);continue;}
        if(!connected){BOOL ok=ConnectNamedPipe(pipe.h,nullptr);DWORD e=ok?0:GetLastError();if(!e||e==ERROR_PIPE_CONNECTED){ULONG pid=0;if(!GetNamedPipeClientProcessId(pipe.h,&pid)||pid!=owner["pid"].get<DWORD>()||poll(parent.h)){DisconnectNamedPipe(pipe.h);continue;}connected=true;replied=false;connected_at=monotonic();}else if(e==ERROR_NO_DATA)DisconnectNamedPipe(pipe.h);else require(e==ERROR_PIPE_LISTENING,"Connect pipe");}
        if(connected){
            char buffer[16384];DWORD n=0;BOOL ok=ReadFile(pipe.h,buffer,sizeof(buffer),&n,nullptr);DWORD e=ok?0:GetLastError();
            if(ok&&n&&!replied){
                try{
                    J msg=J::parse(buffer,buffer+n);require(msg.is_object()&&msg.value("version",0)==1&&msg.value("token","")==token&&valid_id(msg.value("request","")),"Session message rejected");authenticated=true;J response;
                    try{
                        auto command=msg.value("command","");
                        if(command=="status")response={{"ok",true},{"status","authorized"},{"operation",worker?J(operation):J()}};
                        else if(command=="shutdown"){shutdown();response={{"ok",true},{"status","closing"}};}
                        else{
                            require(command=="start"||command=="cancel","Unknown command");auto op=msg.value("operation","");auto dir=folder(op);DirectoryGuard op_guard(dir);auto req=request(op);require(req["client"]==owner,"Different request owner");
                            if(command=="cancel"){require(worker&&operation==op,"Operation already finished");write_json(dir/L"cancel.json",J{{"operation_id",op},{"requested_at",now()}});response={{"ok",true},{"status","cancelling"}};}
                            else if(worker)response={{"ok",operation==op},{"status","busy"},{"operation_id",operation}};
                            else{require(active()==op&&now()-req["requested_at"].get<double>()<=15&&!fs::exists(dir/L"started.json")&&!fs::exists(dir/L"launch-failure.json"),"Request already consumed or stale");worker.emplace(job.spawn(child_args({L"--memory-clean-helper",L"--operation",wide(op)})));operation=op;response={{"ok",true},{"status","queued"},{"operation_id",op}};}
                        }
                    }catch(const Error& ex){response={{"ok",false},{"status","failed"},{"message",ex.what()},{"error_code",ex.code}};}
                    response["request"]=msg["request"];response["session"]=token;auto data=response.dump();DWORD sent=0;require(data.size()<=16384&&WriteFile(pipe.h,data.data(),DWORD(data.size()),&sent,nullptr)&&sent==data.size(),"Pipe response");replied=true;
                }catch(const std::exception&){DisconnectNamedPipe(pipe.h);connected=false;}
            }else if(!ok&&e!=ERROR_NO_DATA){DisconnectNamedPipe(pipe.h);connected=false;}
            if(connected&&monotonic()-connected_at>5){DisconnectNamedPipe(pipe.h);connected=false;}
        }
        Sleep(10);
    }
}
}
int WINAPI wWinMain(HINSTANCE,HINSTANCE,PWSTR,int){
    try{
        // Every child is this exact on-disk image. Keep the directory and image
        // pinned for the full session so a same-user rename cannot substitute a
        // different executable between request verification and CreateProcess.
        DirectoryGuard executable_directory(executable().parent_path());
        auto executable_lock=regular(executable(),GENERIC_READ,OPEN_EXISTING,FILE_SHARE_READ);
        int argc;LPWSTR* argv=CommandLineToArgvW(GetCommandLineW(),&argc);require(argv!=nullptr,"Command line");std::map<std::wstring,std::wstring> args;
        try{for(int i=1;i<argc;i++){std::wstring key=argv[i];require(!args.count(key),"Duplicate argument");if(key==L"--diagnose"||key==L"--clean-session"||key==L"--memory-clean-helper")args[key]=L"";else{require(i+1<argc,"Missing argument");args[key]=argv[++i];}}}catch(...){LocalFree(argv);throw;}LocalFree(argv);
        std::vector<std::wstring> allowed={L"--diagnose",L"--clean-session",L"--memory-clean-helper",L"--clean-step",L"--profile",L"--report",L"--session",L"--owner-pid",L"--owner-created",L"--operation",L"--token"};
#ifdef MAPLE_CLEANER_TEST
        allowed.push_back(L"--test-case");if(args.count(L"--test-case"))test_case=utf8(args[L"--test-case"]);
        require(test_case=="success"||test_case=="hang"||test_case=="crash"||test_case=="slow"||test_case=="fail","Invalid test case");
#endif
        for(const auto& [key,value]:args)require(std::find(allowed.begin(),allowed.end(),key)!=allowed.end(),"Unknown argument");
        int modes=int(args.count(L"--diagnose")+args.count(L"--clean-session")+args.count(L"--memory-clean-helper")+args.count(L"--clean-step"));require(modes==1,"One mode required");
        if(args.count(L"--diagnose")){
            J result={{"component","cleaner"},{"implementation","cpp-msvc"},{"version","2.1.0"},{"protocolVersion",schema},{"sessionProtocolVersion",1},{"qtImported",false},{"privilegedOperationPerformed",false},{"steps",steps}};
#ifdef MAPLE_CLEANER_TEST
            result["testBuild"]=true;
#else
            result["testBuild"]=false;
#endif
            if(args.count(L"--report"))write_json(fs::path(args[L"--report"]),result);return 0;
        }
#ifndef MAPLE_CLEANER_TEST
        if(!elevated())return 5;
#endif
        require(args.count(L"--profile"),"Profile required");profile=fs::path(args[L"--profile"]);DirectoryGuard guard(profile);
        if(args.count(L"--clean-session")){require(args.count(L"--owner-pid")&&args.count(L"--owner-created")&&args.count(L"--session"),"Session identity required");return serve(J{{"pid",std::stoull(args[L"--owner-pid"])},{"creationFiletime",std::stoull(args[L"--owner-created"])}},utf8(args[L"--session"]));}
        require(args.count(L"--operation"),"Operation required");auto op=utf8(args[L"--operation"]);
        if(args.count(L"--memory-clean-helper"))return supervise(op);
        require(args.count(L"--token"),"Worker token required");return step_main(utf8(args[L"--clean-step"]),op,utf8(args[L"--token"]));
    }catch(const std::exception&){return 2;}
}
