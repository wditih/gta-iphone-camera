// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include "replay/quat.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <vector>

namespace phonecore {
using spline::Vec3;
using rquat::Quat;
struct Pose { Vec3 position; Quat rotation; float fov = 50.0f; };
inline bool finite(const Vec3& v) {
    return std::isfinite(v.x) && std::isfinite(v.y) && std::isfinite(v.z);
}
inline bool valid(const Pose& p) {
    const float norm = p.rotation.dot(p.rotation);
    return finite(p.position) && p.position.lengthSq() < 1e10f &&
        std::isfinite(norm) && norm > 0.98f && norm < 1.02f &&
        std::isfinite(p.fov) && p.fov >= 1 && p.fov <= 130;
}
inline float dot(Vec3 a, Vec3 b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
inline Vec3 cross(Vec3 a, Vec3 b) {
    return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};
}
// ARKit +X right, +Y up, -Z forward -> RAGE +X right, +Y forward, +Z up.
inline Vec3 map(Vec3 a) { return {a.x, -a.z, a.y}; }
inline bool fromARKit(const float m[12], Pose& result) {
    for (int i=0; i<12; ++i)
        if (!std::isfinite(m[i]) || std::fabs(m[i]) > 1000) return false;
    const Vec3 x(m), y(m+3), z(m+6);
    for (auto v : {x,y,z}) if (std::fabs(v.lengthSq()-1)>0.02f) return false;
    if (std::fabs(dot(x,y))>0.02f || std::fabs(dot(x,z))>0.02f ||
        std::fabs(dot(y,z))>0.02f || dot(cross(x,y),z)<0.98f) return false;
    result.position = map(Vec3(m+9));
    result.rotation = rquat::fromBasis(map(x),map(z)*-1.0f,map(y));
    return valid(result);
}
inline Vec3 rotate(Quat q, Vec3 v) {
    float a[3],b[3],c[3]; rquat::toMatrixRows(q,a,b,c);
    return Vec3(a)*v.x+Vec3(b)*v.y+Vec3(c)*v.z;
}
inline float yaw(Quat q) {
    const auto f=rotate(q,{0,1,0}); return std::atan2(-f.x,f.y);
}
struct Calibration {
    Pose phone, game;
    void set(const Pose& p, const Pose& g) { phone=p; game=g; }
    Pose evaluate(const Pose& p, float scale) const {
        Pose out=game;
        const float h=yaw(game.rotation)-yaw(phone.rotation);
        const Vec3 delta=(p.position-phone.position)*scale;
        out.position=game.position+Vec3{std::cos(h)*delta.x-std::sin(h)*delta.y,
            std::sin(h)*delta.x+std::cos(h)*delta.y,delta.z};
        out.rotation=(game.rotation*phone.rotation.conjugate()*p.rotation).normalized();
        return out;
    }
};
inline Pose interpolate(const Pose& a,const Pose& b,float u) {
    return {a.position+(b.position-a.position)*u,
        rquat::slerp(a.rotation,b.rotation,u),a.fov+(b.fov-a.fov)*u};
}
class Filter {
    bool ready=false;
    Pose state;
public:
    void reset(Pose p) { state=p; ready=true; }
    Pose update(Pose p,float dt,float cutoff) {
        if (!ready || cutoff<=0) { reset(p); return state; }
        dt=std::clamp(dt,0.001f,0.1f);
        const float speed=(p.position-state.position).length()/dt;
        const float angle=2*std::acos(std::clamp(std::fabs(state.rotation.dot(p.rotation)),0.0f,1.0f));
        const float adaptive=std::clamp(cutoff+speed*0.35f+angle/dt*0.1f,0.1f,120.0f);
        state=interpolate(state,p,1-std::exp(-6.283185307f*adaptive*dt));
        return state;
    }
};
struct Sample { float timeMs; Pose pose; };
class Track {
public:
    static constexpr size_t maxSamples=216000;
    std::vector<Sample> samples;
    bool append(float time,Pose p) {
        if (!std::isfinite(time) || time<0 || !valid(p) || samples.size()>=maxSamples)
            return false;
        if (!samples.empty() && time<=samples.back().timeMs) return false;
        samples.push_back({time,p}); return true;
    }
    bool evaluate(float time,Pose& p) const {
        if (samples.empty() || !std::isfinite(time)) return false;
        if (time<=samples.front().timeMs) { p=samples.front().pose; return true; }
        if (time>=samples.back().timeMs) { p=samples.back().pose; return true; }
        const auto b=std::upper_bound(samples.begin(),samples.end(),time,
            [](float t,const Sample& s){return t<s.timeMs;});
        const auto a=b-1;
        p=interpolate(a->pose,b->pose,(time-a->timeMs)/(b->timeMs-a->timeMs));
        return valid(p);
    }
    bool write(std::ostream& out) const {
        out<<"PHONECAM 1\n"<<std::setprecision(9);
        for (const auto& s:samples) {
            const auto& p=s.pose;
            out<<s.timeMs<<' '<<p.position.x<<' '<<p.position.y<<' '<<p.position.z<<' '
                <<p.rotation.x<<' '<<p.rotation.y<<' '<<p.rotation.z<<' '
                <<p.rotation.w<<' '<<p.fov<<'\n';
        }
        return bool(out);
    }
    static bool read(std::istream& in,Track& out) {
        std::string magic,line;
        if (!std::getline(in,magic) || magic!="PHONECAM 1") return false;
        Track next;
        while (std::getline(in,line)) {
            if (line.empty()) continue;
            std::istringstream row(line);
            Sample s;
            auto& p=s.pose;
            if (!(row>>s.timeMs>>p.position.x>>p.position.y>>p.position.z>>
                p.rotation.x>>p.rotation.y>>p.rotation.z>>p.rotation.w>>p.fov)) return false;
            row>>std::ws;
            if (!row.eof() || !next.append(s.timeMs,p)) return false;
        }
        if (in.bad() || next.samples.empty()) return false;
        out=std::move(next); return true;
    }
};

enum : uint32_t { Live=1,Record=2,Playback=4 };
#pragma pack(push,1)
struct Packet {
    char magic[4]; uint32_t version,session,flags,calibration; uint64_t sequence;
    double sentSeconds;
    float matrix[12],scale,cutoff,lens; uint32_t fps;
};
#pragma pack(pop)
static_assert(sizeof(Packet)==100);
inline bool decode(const void* bytes,size_t length,Packet& packet,Pose& pose) {
    if (length!=sizeof(Packet)) return false;
    Packet next; std::memcpy(&next,bytes,sizeof next);
    if (std::memcmp(next.magic,"PCAM",4) || next.version!=1 || (next.flags&~7u) ||
        !std::isfinite(next.sentSeconds)) return false;
    if (!std::isfinite(next.scale) || next.scale<0.1f || next.scale>12 ||
        !std::isfinite(next.cutoff) || next.cutoff<0 || next.cutoff>120 ||
        !std::isfinite(next.lens) || next.lens<5 || next.lens>600 ||
        (next.fps!=24 && next.fps!=25 && next.fps!=30 && next.fps!=60)) return false;
    if (!fromARKit(next.matrix,pose)) return false;
    packet=next; return true;
}
} // namespace phonecore
