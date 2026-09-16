#pragma once
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <sddl.h>
#include <bcrypt.h>
#include <psapi.h>
#include <tlhelp32.h>
#include <shellapi.h>
#include <filesystem>
#include <fstream>
#include <functional>
#include <optional>
#include <sstream>
#include <vector>
#include <map>
#include <chrono>
#include "../../third_party/nlohmann-json-3.11.3/json.hpp"

namespace maple {
using J = nlohmann::json;
namespace fs = std::filesystem;
inline constexpr int schema = 4;
inline const std::vector<std::string> steps = {"empty_working_sets", "flush_modified_pages", "purge_standby_list", "system_file_cache", "registry_cache", "combine_memory"};
struct Error : std::runtime_error {
    DWORD code;
    explicit Error(std::string message, DWORD value=GetLastError()) : std::runtime_error(message+" ("+std::to_string(value)+")"),code(value) {}
};
inline void require(bool ok, const char* message) { if(!ok) throw Error(message); }
struct Handle {
    HANDLE h=nullptr;
    Handle()=default;
    explicit Handle(HANDLE v):h(v){ if(v==INVALID_HANDLE_VALUE) h=nullptr; }
    ~Handle(){ if(h) CloseHandle(h); }
    Handle(const Handle&)=delete;
    Handle& operator=(const Handle&)=delete;
    Handle(Handle&& o) noexcept:h(o.h){o.h=nullptr;}
    Handle& operator=(Handle&& o) noexcept { if(h) CloseHandle(h); h=o.h; o.h=nullptr; return *this; }
    explicit operator bool() const {return h!=nullptr;}
};
inline std::wstring wide(const std::string& s) {
    int n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s.data(),int(s.size()),nullptr,0);
    if(!s.empty()) require(n>0,"Invalid UTF-8");
    std::wstring out(n,L'\0'); if(n) MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s.data(),int(s.size()),out.data(),n); return out;
}
inline std::string utf8(const std::wstring& s) {
    int n=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,s.data(),int(s.size()),nullptr,0,nullptr,nullptr);
    if(!s.empty()) require(n>0,"Invalid UTF-16");
    std::string out(n,'\0'); if(n) WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,s.data(),int(s.size()),out.data(),n,nullptr,nullptr); return out;
}
inline double now(){return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();}
inline double monotonic(){return double(GetTickCount64())/1000.;}
inline bool valid_id(const std::string& s){return s.size()==32 && s.find_first_not_of("0123456789abcdef")==s.npos;}
inline std::string hex(const unsigned char* p,size_t n){const char* h="0123456789abcdef";std::string s;for(size_t i=0;i<n;i++){s+=h[p[i]>>4];s+=h[p[i]&15];}return s;}
inline std::string random_id(){unsigned char b[16];require(BCryptGenRandom(nullptr,b,16,BCRYPT_USE_SYSTEM_PREFERRED_RNG)==0,"Random token failed");return hex(b,16);}
inline std::string sha_bytes(const std::string& data){
    BCRYPT_ALG_HANDLE alg=nullptr; BCRYPT_HASH_HANDLE hash=nullptr;
    require(BCryptOpenAlgorithmProvider(&alg,BCRYPT_SHA256_ALGORITHM,nullptr,0)==0,"SHA256 provider");
    unsigned char out[32];
    auto code=BCryptCreateHash(alg,&hash,nullptr,0,nullptr,0,0);
    if(code==0) code=BCryptHashData(hash,(PUCHAR)data.data(),ULONG(data.size()),0);
    if(code==0) code=BCryptFinishHash(hash,out,32,0);
    if(hash) BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(alg,0);
    require(code==0,"SHA256 failed"); return hex(out,32);
}
inline fs::path executable(){std::wstring p(32768,L'\0');DWORD n=GetModuleFileNameW(nullptr,p.data(),DWORD(p.size()));require(n>0 && n<p.size(),"Executable path");p.resize(n);return fs::path(p);}
// Hold each directory without FILE_SHARE_DELETE to prevent a junction/rename
// replacement during privileged reads or publication. Never create directories here.
struct DirectoryGuard {
    std::vector<Handle> handles;
    explicit DirectoryGuard(const fs::path& p){
        require(p.is_absolute() && p.root_name().wstring().size()==2,"Local absolute directory required");
        fs::path current=p.root_path();
        for(const auto& part:p.relative_path()){
            require(part!=L".." && part!=L".","Noncanonical directory");current/=part;
            Handle h(CreateFileW(current.c_str(),FILE_READ_ATTRIBUTES,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT,nullptr));
            require(bool(h),"Open directory"); FILE_ATTRIBUTE_TAG_INFO info{};
            require(GetFileInformationByHandleEx(h.h,FileAttributeTagInfo,&info,sizeof(info))!=0,"Directory attributes");
            require((info.FileAttributes&FILE_ATTRIBUTE_DIRECTORY) && !(info.FileAttributes&FILE_ATTRIBUTE_REPARSE_POINT),"Directory link rejected");handles.push_back(std::move(h));
        }
    }
};
inline Handle regular(const fs::path& p,DWORD access=GENERIC_READ,DWORD creation=OPEN_EXISTING,DWORD sharing=FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE){
    Handle h(CreateFileW(p.c_str(),access,sharing,nullptr,creation,FILE_FLAG_OPEN_REPARSE_POINT,nullptr));
    require(bool(h),"Open regular file"); BY_HANDLE_FILE_INFORMATION info{};
    require(GetFileInformationByHandle(h.h,&info)!=0,"File attributes");
    require(!(info.dwFileAttributes&(FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT)) && info.nNumberOfLinks==1,"Link or directory rejected");return h;
}
inline std::string read_bytes(const fs::path& p,size_t maximum=1000000){
    auto h=regular(p); LARGE_INTEGER size{};require(GetFileSizeEx(h.h,&size)!=0 && size.QuadPart>=0 && uint64_t(size.QuadPart)<=maximum,"File size invalid");
    std::string s(size_t(size.QuadPart),'\0'); DWORD n=0;require(ReadFile(h.h,s.data(),DWORD(s.size()),&n,nullptr)!=0 && n==s.size(),"Read file");return s;
}
inline std::string digest(const fs::path& p){return sha_bytes(read_bytes(p,64*1024*1024));}
struct FileLock {
    Handle file; OVERLAPPED offset{};
    FileLock(const fs::path& p,bool exclusive,double timeout=.2):file(regular(p,GENERIC_READ|GENERIC_WRITE,OPEN_ALWAYS,FILE_SHARE_READ|FILE_SHARE_WRITE)){
        double deadline=monotonic()+timeout;
        while(!LockFileEx(file.h,LOCKFILE_FAIL_IMMEDIATELY|(exclusive?LOCKFILE_EXCLUSIVE_LOCK:0),0,1,0,&offset)){
            DWORD code=GetLastError();if(code!=ERROR_LOCK_VIOLATION || monotonic()>=deadline) throw Error("JSON lock failed",code);Sleep(5);
        }
    }
    ~FileLock(){UnlockFileEx(file.h,0,1,0,&offset);}
};
inline fs::path sidecar(const fs::path& p){return p.parent_path()/(L"."+p.filename().wstring()+L".io.lock");}
inline J read_json(const fs::path& p){
    if(GetFileAttributesW(p.c_str())==INVALID_FILE_ATTRIBUTES){auto e=GetLastError();if(e==ERROR_FILE_NOT_FOUND || e==ERROR_PATH_NOT_FOUND)return J();throw Error("JSON access",e);}
    FileLock lock(sidecar(p),false);return J::parse(read_bytes(p));
}
inline void write_json(const fs::path& p,const J& value,bool immutable=false){
    DirectoryGuard guard(p.parent_path());std::string data=value.dump(2);fs::path tmp=immutable?p:p.parent_path()/wide("."+utf8(p.filename().wstring())+"."+random_id()+".tmp");
    try {
        FileLock lock(sidecar(p),true);
        {auto out=regular(tmp,GENERIC_WRITE,CREATE_NEW,0);DWORD n;require(WriteFile(out.h,data.data(),DWORD(data.size()),&n,nullptr)!=0 && n==data.size(),"Write JSON");require(FlushFileBuffers(out.h)!=0,"Flush JSON");}
        if(!immutable){double deadline=monotonic()+.2;while(!MoveFileExW(tmp.c_str(),p.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)){DWORD e=GetLastError();if((e!=5&&e!=32&&e!=33)||monotonic()>=deadline)throw Error("Publish JSON",e);Sleep(5);}}
    }catch(...){if(!immutable)DeleteFileW(tmp.c_str());throw;}
}
inline uint64_t creation(HANDLE h){FILETIME c,e,k,u;require(GetProcessTimes(h,&c,&e,&k,&u)!=0,"Process creation time");return (uint64_t(c.dwHighDateTime)<<32)|c.dwLowDateTime;}
inline J identity(HANDLE h=GetCurrentProcess()){return J{{"pid",GetProcessId(h)},{"creationFiletime",creation(h)}};}
inline bool valid_identity(const J& j){return j.is_object()&&j.contains("pid")&&j["pid"].is_number_unsigned()&&j["pid"].get<uint64_t>()>0&&j["pid"].get<uint64_t>()<=MAXDWORD&&j.contains("creationFiletime")&&j["creationFiletime"].is_number_unsigned();}
inline std::optional<DWORD> poll(HANDLE h){auto r=WaitForSingleObject(h,0);if(r==WAIT_TIMEOUT)return {};require(r==WAIT_OBJECT_0,"Wait process");DWORD code;require(GetExitCodeProcess(h,&code)!=0,"Exit code");return code;}
inline Handle observe(const J& j){require(valid_identity(j),"Invalid identity");Handle h(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,FALSE,j["pid"].get<DWORD>()));require(bool(h),"Observe process");if(creation(h.h)!=j["creationFiletime"].get<uint64_t>())throw Error("Reused process ID",ERROR_NOT_FOUND);return h;}
inline bool alive(const J& j){try{auto h=observe(j);return !poll(h.h).has_value();}catch(const Error& e){if(e.code==ERROR_INVALID_PARAMETER||e.code==ERROR_NOT_FOUND)return false;throw;}}
inline DWORD parent_pid(){Handle snapshot(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0));require(bool(snapshot),"Process snapshot");PROCESSENTRY32W entry{};entry.dwSize=sizeof(entry);for(BOOL ok=Process32FirstW(snapshot.h,&entry);ok;ok=Process32NextW(snapshot.h,&entry))if(entry.th32ProcessID==GetCurrentProcessId())return entry.th32ParentProcessID;throw Error("Parent not found",ERROR_NOT_FOUND);}
inline std::wstring sid(HANDLE process,bool logon){
    HANDLE raw;require(OpenProcessToken(process,TOKEN_QUERY,&raw)!=0,"Open token");Handle token(raw);DWORD n=0;auto cls=logon?TokenGroups:TokenUser;GetTokenInformation(token.h,cls,nullptr,0,&n);std::vector<unsigned char>b(n);require(GetTokenInformation(token.h,cls,b.data(),n,&n)!=0,"Token information");PSID target=nullptr;
    if(logon){auto groups=(TOKEN_GROUPS*)b.data();for(DWORD i=0;i<groups->GroupCount;i++)if((groups->Groups[i].Attributes&SE_GROUP_LOGON_ID)==SE_GROUP_LOGON_ID)target=groups->Groups[i].Sid;}
    else target=((TOKEN_USER*)b.data())->User.Sid;
    require(target!=nullptr,"SID missing");LPWSTR text=nullptr;require(ConvertSidToStringSidW(target,&text)!=0,"SID text");std::wstring result(text);LocalFree(text);return result;
}
inline bool elevated(){HANDLE raw;require(OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY,&raw)!=0,"Token query");Handle token(raw);TOKEN_ELEVATION value{};DWORD n;require(GetTokenInformation(token.h,TokenElevation,&value,sizeof(value),&n)!=0,"Elevation query");return value.TokenIsElevated!=0;}
struct Mutex {
    Handle handle; bool acquired=false;
    explicit Mutex(const std::string& scope):handle(CreateMutexW(nullptr,FALSE,wide("Local\\CuteMapleCleanup-"+scope+"-"+sha_bytes(utf8(sid(GetCurrentProcess(),false))).substr(0,24)).c_str())){require(bool(handle),"Mutex create");}
    bool acquire(){auto r=WaitForSingleObject(handle.h,0);acquired=(r==WAIT_OBJECT_0||r==WAIT_ABANDONED);require(acquired||r==WAIT_TIMEOUT,"Mutex wait");return acquired;}
    ~Mutex(){if(acquired)ReleaseMutex(handle.h);}
};
inline std::wstring quote(const std::wstring& s){std::wstring out=L"\"";size_t slashes=0;for(auto c:s){if(c==L'\\'){slashes++;continue;}if(c==L'\"')out.append(slashes*2+1,L'\\');else out.append(slashes,L'\\');slashes=0;out+=c;}out.append(slashes*2,L'\\');return out+L"\"";}
struct Child {Handle handle;J id;Child(Handle h,J i):handle(std::move(h)),id(std::move(i)){};};
struct Job {
    Handle handle;
    Job():handle(CreateJobObjectW(nullptr,nullptr)){require(bool(handle),"Job create");JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};limits.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;require(SetInformationJobObject(handle.h,JobObjectExtendedLimitInformation,&limits,sizeof(limits))!=0,"Job configure");}
    Child spawn(const std::vector<std::wstring>& args,std::function<void(const J&)> before={}){
        auto exe=executable();std::wstring line=quote(exe.wstring());for(const auto& a:args)line+=L" "+quote(a);
        STARTUPINFOW startup{};startup.cb=sizeof(startup);startup.dwFlags=STARTF_USESHOWWINDOW;startup.wShowWindow=SW_HIDE;PROCESS_INFORMATION info{};
        require(CreateProcessW(exe.c_str(),line.data(),nullptr,nullptr,FALSE,CREATE_NO_WINDOW|CREATE_SUSPENDED,nullptr,exe.parent_path().c_str(),&startup,&info)!=0,"Create fixed worker");Handle process(info.hProcess),thread(info.hThread);
        try{require(AssignProcessToJobObject(handle.h,process.h)!=0,"Assign worker");auto id=identity(process.h);if(before)before(id);require(ResumeThread(thread.h)!=DWORD(-1),"Resume worker");return Child(std::move(process),id);}catch(...){TerminateProcess(process.h,71);WaitForSingleObject(process.h,2000);throw;}
    }
    void terminate(DWORD code){require(TerminateJobObject(handle.h,code)!=0,"Terminate owned job");}
};
inline J memory(){MEMORYSTATUSEX m{};m.dwLength=sizeof(m);require(GlobalMemoryStatusEx(&m)!=0,"Memory snapshot");return J{{"available",m.ullAvailPhys},{"percent",m.dwMemoryLoad}};}
}
