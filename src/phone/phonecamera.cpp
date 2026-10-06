// SPDX-License-Identifier: GPL-3.0-only
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <WinSock2.h>
#include <Windows.h>
#include "phone/phonecamera.h"
#include "phone/phonecore.h"
#include "game/game.h"
#include "game/worldprobe.h"
#include "replay/marker.h"
#include "capture/render.h"
#include "utils/log.h"
#include "utils/paths.h"
#include <atomic>
#include <memory>
#include <mutex>
#include <thread>
#include <unordered_map>

namespace phonecamera {
namespace {
using namespace phonecore;
enum Mode { Off, LiveMode, Frozen, PlaybackMode };
SOCKET receiver=INVALID_SOCKET;
sockaddr_in bridge{};
Packet packet{};
Pose phone{},output{};
bool havePacket=false,haveOutput=false,calibrate=true,recording=false,collision=true;
bool previousKeys[4]{};
uint32_t appliedSession=0,appliedFlags=0,appliedCalibration=0;
double lastTick=0,lastStatus=0;
double lastAdvance=0;
Mode mode=Off;
Calibration reference;
Filter filter;
Track take;
std::shared_ptr<Track> playback;
std::unordered_map<std::string,std::shared_ptr<Track>> cache;
std::string scope,takeName;
std::mutex resultMutex,writeMutex;
struct LoadResult { std::string scope; std::shared_ptr<Track> track; };
LoadResult loaded;
std::atomic<unsigned> saved{0},failed{0},writing{0};
unsigned seenFailed=0;
const char* problem="";

double clockSeconds() {
    LARGE_INTEGER count,frequency; QueryPerformanceCounter(&count); QueryPerformanceFrequency(&frequency);
    return double(count.QuadPart)/double(frequency.QuadPart);
}
uint64_t hashName(const char* name) {
    uint64_t h=14695981039346656037ull;
    for (size_t i=0;name[i] && i<256;++i) { h^=(unsigned char)name[i]; h*=1099511628211ull; }
    return h;
}
std::string directory() { return paths::sub("PhoneCamera"); }
void finish(const char* why) {
    if (!recording) return;
    recording=false;
    if (!render::active() && game::playbackType()!=1) game::playbackPause();
    if (take.samples.empty()) { problem="Empty take: play the clip while recording"; return; }
    auto snapshot=std::make_shared<Track>(std::move(take));
    playback=snapshot; mode=PlaybackMode;
    cache[scope]=snapshot;
    const std::string bound=scope,name=takeName,dir=directory();
    ++writing;
    std::thread([snapshot,bound,name,dir] {
        std::lock_guard<std::mutex> lock(writeMutex);
        bool ok=false;
        {
            std::ofstream file(dir+name+".tmp",std::ios::binary);
            ok=snapshot->write(file); file.close(); ok=ok && !file.fail();
        }
        if (ok) ok=MoveFileExA((dir+name+".tmp").c_str(),(dir+name).c_str(),MOVEFILE_WRITE_THROUGH)!=FALSE;
        if (ok) {
            std::ofstream active(dir+bound+".active.tmp",std::ios::binary);
            active<<name<<'\n'; active.close(); ok=!active.fail();
            if (ok) ok=MoveFileExA((dir+bound+".active.tmp").c_str(),(dir+bound+".active").c_str(),
                MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)!=FALSE;
        }
        if (ok) { ++saved; logger::write("info","phone camera: saved %zu samples to %s",snapshot->samples.size(),name.c_str()); }
        else { ++failed; logger::write("error","phone camera: save failed for %s (%lu)",name.c_str(),GetLastError()); }
        --writing;
    }).detach();
    logger::write("info","phone camera: stopped (%s); saving %zu samples",why,snapshot->samples.size());
}
bool changeScope() {
    const char* project=game::projectName(); const int index=game::clipIndex();
    if (!project || !*project || index<0 || !game::addr_g_ReplayTimeMs) return false;
    std::vector<uint64_t> identities;
    if (!game::clipIdentities(identities) || (size_t)index>=identities.size()) return false;
    char key[40]; snprintf(key,sizeof key,"%016llx_%016llx",(unsigned long long)hashName(project),
        (unsigned long long)identities[index]);
    if (scope==key) return true;
    finish("clip changed");
    scope=key; playback=cache[scope]; mode=playback?PlaybackMode:Off; calibrate=true; haveOutput=false;
    appliedFlags=0; appliedCalibration=packet.calibration;
    if (playback) return true; // Export clip switches must not wait for disk I/O.
    const std::string bound=scope,dir=directory();
    std::thread([bound,dir] {
        std::shared_ptr<Track> track;
        std::ifstream active(dir+bound+".active",std::ios::binary);
        std::string name; std::getline(active,name);
        if (!name.empty() && name.size()<100 && name.find_first_not_of("0123456789abcdef_.pcam")==std::string::npos &&
            name.find("..") == std::string::npos && name.rfind(bound+"_",0)==0) {
            std::ifstream input(dir+name,std::ios::binary); auto next=std::make_shared<Track>();
            if (Track::read(input,*next)) track=std::move(next);
        }
        std::lock_guard<std::mutex> lock(resultMutex); loaded={bound,track};
    }).detach();
    logger::write("info","phone camera: bound to %s",scope.c_str());
    return true;
}
bool readFrame(void* self,Pose& p) {
    const auto b=(const uint8_t*)self;
    const Vec3 right((float*)(b+rdirector::OFF_FrameMatrixA));
    const Vec3 forward((float*)(b+rdirector::OFF_FrameMatrixB));
    const Vec3 up((float*)(b+rdirector::OFF_FrameMatrixC));
    if (!finite(right)||!finite(forward)||!finite(up) || std::fabs(right.lengthSq()-1)>0.02f ||
        std::fabs(forward.lengthSq()-1)>0.02f || std::fabs(up.lengthSq()-1)>0.02f ||
        std::fabs(dot(right,forward))>0.02f || std::fabs(dot(right,up))>0.02f ||
        std::fabs(dot(forward,up))>0.02f || dot(cross(right,forward),up)<0.98f) return false;
    p={Vec3((float*)(b+rdirector::OFF_FramePosition)),rquat::fromBasis(right,forward,up),
        *(float*)(b+rdirector::OFF_FrameFov)};
    return valid(p);
}
void writeFrame(void* self,const Pose& p) {
    auto b=(uint8_t*)self;
    rquat::toMatrixRows(p.rotation,(float*)(b+rdirector::OFF_FrameMatrixA),
        (float*)(b+rdirector::OFF_FrameMatrixB),(float*)(b+rdirector::OFF_FrameMatrixC));
    p.position.store((float*)(b+rdirector::OFF_FramePosition));
    *(float*)(b+rdirector::OFF_FrameFov)=p.fov;
}
void receive(double now) {
    for (int i=0;i<256;++i) {
        char data[256]; sockaddr_in from{}; int fromSize=sizeof from;
        const int n=recvfrom(receiver,data,sizeof data,0,(sockaddr*)&from,&fromSize);
        if (n==SOCKET_ERROR) break;
        Packet next; Pose nextPhone;
        if (from.sin_addr.s_addr!=htonl(INADDR_LOOPBACK) || !decode(data,n,next,nextPhone)) continue;
        if (now-next.sentSeconds>0.5 || next.sentSeconds>now+0.1) continue;
        if (havePacket && next.session==packet.session && next.sequence<=packet.sequence) continue;
        packet=next; phone=nextPhone; bridge=from; havePacket=true;
    }
}
bool fresh(double now) { return havePacket && now-packet.sentSeconds<=0.5; }
void startTake(float time) {
    if (render::active() || game::playbackType()==1) return;
    if (scope.empty() || !havePacket || !game::transportReady()) { problem="Editor transport or named project unavailable"; return; }
    take=Track{}; recording=true; mode=LiveMode; calibrate=true; lastAdvance=clockSeconds();
    char suffix[40]; snprintf(suffix,sizeof suffix,"_%016llx.pcam",(unsigned long long)GetTickCount64());
    takeName=scope+suffix; problem="";
    game::playbackSetSpeed(1.0f); game::playbackPlay();
    logger::write("info","phone camera: recording at %.3f ms",time);
}
void status(double now,float time) {
    if (!havePacket || now-lastStatus<0.1) return;
    lastStatus=now;
    float lo=0,hi=0; game::clipRange(lo,hi);
    const char* names[]={"off","live","frozen","playback"};
    char text[512];
    const int n=snprintf(text,sizeof text,
        "PSTS{\"mode\":\"%s\",\"recording\":%s,\"time_ms\":%.3f,\"lo_ms\":%.3f,\"hi_ms\":%.3f,"
        "\"fps\":%u,\"saved\":%u,\"writing\":%u,\"error\":\"%s\"}",names[mode],recording?"true":"false",
        time,lo,hi,packet.fps,saved.load(),writing.load(),problem);
    if (n>0 && n<(int)sizeof text) sendto(receiver,text,n,0,(sockaddr*)&bridge,sizeof bridge);
}
bool hotkey(int index,int key) {
    DWORD pid=0; GetWindowThreadProcessId(GetForegroundWindow(),&pid);
    const bool down=pid==GetCurrentProcessId() && (GetAsyncKeyState(VK_CONTROL)&0x8000) && (GetAsyncKeyState(key)&0x8000);
    const bool edge=down&&!previousKeys[index]; previousKeys[index]=down; return edge;
}
}

void install() {
    if (game::isEnhanced()) return; // Target tested route is Legacy Story Mode only.
    char executable[MAX_PATH]{}; GetModuleFileNameA(nullptr,executable,MAX_PATH);
    const char* basename=strrchr(executable,'\\'); basename=basename?basename+1:executable;
    if (_stricmp(basename,"GTA5.exe")) return;
    const auto ini=paths::file("PhoneCamera.ini");
    if (!GetPrivateProfileIntA("PhoneCamera","Enabled",1,ini.c_str())) return;
    collision=GetPrivateProfileIntA("PhoneCamera","Collision",1,ini.c_str())!=0;
    WSADATA wsa{}; if (WSAStartup(MAKEWORD(2,2),&wsa)) return;
    receiver=socket(AF_INET,SOCK_DGRAM,IPPROTO_UDP);
    sockaddr_in bindTo{}; bindTo.sin_family=AF_INET; bindTo.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    bindTo.sin_port=htons(9092);
    u_long nonblocking=1;
    if (receiver==INVALID_SOCKET || bind(receiver,(sockaddr*)&bindTo,sizeof bindTo)==SOCKET_ERROR ||
        ioctlsocket(receiver,FIONBIO,&nonblocking)==SOCKET_ERROR) {
        logger::write("error","phone camera: loopback UDP 9092 unavailable (%d)",WSAGetLastError());
        if(receiver!=INVALID_SOCKET) closesocket(receiver); receiver=INVALID_SOCKET; WSACleanup(); return;
    }
    // Background persistence must never outlive an unloaded ASI. Removing this
    // build requires closing the game; camera modes themselves toggle live.
    HMODULE pinned=nullptr; GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,
        reinterpret_cast<LPCSTR>(&install),&pinned);
    logger::write("info","phone camera: ready on 127.0.0.1:9092; Ctrl+F6 live, Ctrl+F7 record, Ctrl+F9 replay, Ctrl+F10 recenter");
}

bool apply(void* self) {
    if (receiver==INVALID_SOCKET || !self) return false;
    const double now=clockSeconds(); receive(now);
    Pose stock;
    if (!changeScope() || !readFrame(self,stock)) {
        finish("editor unavailable"); problem="Open a named project and edit a clip"; status(now,0); return false;
    }
    {
        std::unique_lock<std::mutex> lock(resultMutex,std::try_to_lock);
        if (lock.owns_lock() && loaded.scope==scope) {
            if (!recording && !playback) { playback=loaded.track; cache[scope]=playback; if(playback && mode==Off) mode=PlaybackMode; }
            loaded.scope.clear();
        }
    }
    const float time=*(float*)game::addr_g_ReplayTimeMs;
    if (!std::isfinite(time) || time<0) return false;
    float lo=0,hi=0;
    if (!game::clipRange(lo,hi)) return false;
    const float clipTime=std::max(0.0f,time-lo);
    const bool exporting=render::active() || game::playbackType()==1;
    const bool input=fresh(now);
    if (!exporting && input && (packet.session!=appliedSession || packet.flags!=appliedFlags)) {
        if (packet.flags&Record) { if(!recording) startTake(time); }
        else if (recording) finish("phone stopped");
        else if (packet.flags&Playback) mode=PlaybackMode;
        else if (packet.flags&Live) { mode=LiveMode; calibrate=true; }
        else if (mode==LiveMode) mode=Frozen;
        appliedSession=packet.session; appliedFlags=packet.flags;
    }
    if (!exporting && input && packet.calibration!=appliedCalibration) {
        if(recording) finish("recalibrated");
        calibrate=true; mode=LiveMode; appliedCalibration=packet.calibration;
    }
    if (!exporting && hotkey(0,VK_F6)) { finish("live toggled"); mode=(mode==LiveMode||mode==Frozen)?Off:LiveMode; calibrate=true; haveOutput=false; }
    if (!exporting && hotkey(1,VK_F7)) { if(recording) finish("keyboard stopped"); else if(input) startTake(time); }
    if (!exporting && hotkey(2,VK_F9)) { finish("replay selected"); mode=mode==PlaybackMode?Off:PlaybackMode; }
    if (!exporting && hotkey(3,VK_F10)) { finish("recentered"); mode=LiveMode; calibrate=true; }
    if (!exporting && !input && mode==LiveMode) { finish("tracking lost"); mode=Frozen; problem="Tracking lost: camera frozen; reconnect or recenter"; }
    if (!exporting && mode==Frozen && input && (packet.flags&Live) && !(packet.flags&Record)) { mode=LiveMode; calibrate=true; problem=""; }
    if (failed.load()!=seenFailed) { seenFailed=failed.load(); problem="Saving failed: check the RockstarEditorPlus log"; }
    Pose target=haveOutput?output:stock;
    bool override=false;
    if ((mode==PlaybackMode || exporting) && playback) override=playback->evaluate(clipTime,target);
    else if (!exporting && mode==LiveMode && input && !game::replayBusy()) {
        if (calibrate) { reference.set(phone,haveOutput?output:stock); filter.reset(reference.game); calibrate=false; }
        target=reference.evaluate(phone,packet.scale);
        target.fov=std::clamp(float(2*std::atan(12.0/packet.lens)*180/3.141592653589793),1.0f,130.0f);
        target=filter.update(target,lastTick?float(now-lastTick):0.016f,packet.cutoff);
        const Vec3 delta=target.position-output.position;
        if (collision && haveOutput && delta.length()>0.0001f) {
            if (!worldprobe::available()) { target.position=output.position; problem="Collision probe unavailable: translation held"; }
            else {
                float from[3],to[3]; output.position.store(from); target.position.store(to); worldprobe::Hit hit;
                const bool found=worldprobe::line(from,to,&hit);
                if (!hit.submitted) { target.position=output.position; problem="Collision query failed: translation held"; }
                else if (found)
                    target.position=output.position+delta*std::max(0.0f,hit.t-0.1f/delta.length());
            }
        }
        override=valid(target);
        if (recording && !game::replayBusy()) {
            if (!take.samples.empty() && clipTime<take.samples.back().timeMs-1) finish("playhead moved backwards");
            else if (take.samples.size()>=Track::maxSamples) finish("sample limit reached");
            else if (override && take.append(clipTime,target)) lastAdvance=now;
            if (recording && !take.samples.empty() && now-lastAdvance>1.0) finish("playhead stopped");
        }
    }
    else if (!exporting && mode==Frozen && haveOutput) override=true;
    lastTick=now;
    if (override) { writeFrame(self,target); output=target; haveOutput=true; }
    status(now,time);
    return override;
}
} // namespace phonecamera
