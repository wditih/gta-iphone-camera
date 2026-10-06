// SPDX-License-Identifier: GPL-3.0-only
#include "phone/phonecore.h"
#include <iostream>
#include <limits>
#include <stdexcept>

static int checks=0;
static void require(bool ok,const char* reason) {
    ++checks; if (!ok) throw std::runtime_error(reason);
}
static bool near(float a,float b) { return std::fabs(a-b)<0.0001f; }
int main() {
    using namespace phonecore;
    try {
        float identity[12]={1,0,0,0,1,0,0,0,1,0,0,0};
        Pose p;
        require(fromARKit(identity,p),"identity packet");
        require(near(p.rotation.w,1),"identity camera faces GTA +Y");
        identity[9]=2; identity[10]=3; identity[11]=-4;
        require(fromARKit(identity,p),"translated packet");
        require(near(p.position.x,2)&&near(p.position.y,4)&&near(p.position.z,3),"axis mapping");
        identity[0]=-1;
        require(!fromARKit(identity,p),"reject reflected basis");
        identity[0]=std::numeric_limits<float>::quiet_NaN();
        require(!fromARKit(identity,p),"reject NaN"); identity[0]=1;
        fromARKit(identity,p);
        Calibration ref; Pose start{{100,200,50},{},55}; ref.set(p,start);
        auto same=ref.evaluate(p,2);
        require(near(same.position.x,100)&&near(same.position.z,50),"calibration has no jump");
        p.position.z+=1;
        require(near(ref.evaluate(p,2).position.z,52),"up is up at scaled translation");
        start.rotation=rquat::fromAxisAngle(0,0,1,1.570796327f);
        ref.set(p,start); p.position.y+=1;
        require(near(ref.evaluate(p,1).position.x,99),"heading-aligned walk");
        const auto turn=rquat::fromAxisAngle(0,0,1,0.5f);
        Pose tilted{{0,0,0},rquat::fromAxisAngle(0,1,0,1.2f),50};
        Pose gameBase{{0,0,3},rquat::fromAxisAngle(1,0,0,0.4f),55};
        ref.set(tilted,gameBase);
        require(std::fabs(ref.evaluate(tilted,1).rotation.dot(gameBase.rotation))>0.9999f,"tilted calibration has no rotation jump");
        tilted.rotation=turn*tilted.rotation;
        const auto turned=ref.evaluate(tilted,1);
        require(std::fabs(turned.rotation.dot(turn*gameBase.rotation))>0.9999f,"physical world yaw remains yaw for rolled phone");
        require(near(yaw(rquat::fromAxisAngle(1,0,0,1.570796327f)),0),"vertical camera heading stays finite and stable");
        Track t;
        Pose a{{0,0,3},{},45}; Pose b{{10,0,3},rquat::fromAxisAngle(0,0,1,1.0f),70};
        require(t.append(100,a)&&t.append(1100,b),"record samples");
        require(!t.append(1100,b)&&!t.append(900,b),"seek and duplicate time rejected");
        require(t.evaluate(600,p)&&near(p.position.x,5)&&near(p.fov,57.5f),"clock interpolation");
        const Pose expected=p;
        t.evaluate(1100,p); t.evaluate(100,p); t.evaluate(600,p);
        require(near(p.position.x,expected.position.x)&&near(p.rotation.z,expected.rotation.z),"seek deterministic");
        for (int fps : {24,30,60}) {
            Pose at;
            require(t.evaluate(100+fps*(1000.0f/fps),at)&&near(at.position.x,10),"export fps independent");
        }
        std::stringstream stream; require(t.write(stream),"persist take");
        Track loaded; require(Track::read(stream,loaded),"load take");
        loaded.evaluate(600,p); require(near(p.position.x,5)&&near(p.rotation.z,expected.rotation.z),"persistence round trip");
        std::stringstream broken("PHONECAM 1\n100 0 0 0 0 0 0 1 45\n90 0 0 0 0 0 0 1 45\n");
        require(!Track::read(broken,loaded),"reject damaged take");
        require(loaded.samples.size()==2,"failed load preserves existing take");
        Filter f; f.reset(a); p=f.update(b,0.016f,2);
        require(valid(p)&&p.position.x>0&&p.position.x<10,"finite smoothing");
        Packet wire{}; std::memcpy(wire.magic,"PCAM",4); wire.version=1; wire.scale=1;
        wire.cutoff=2; wire.lens=35; wire.fps=30; std::memcpy(wire.matrix,identity,sizeof identity);
        Packet parsed;
        require(decode(&wire,sizeof wire,parsed,p),"bridge wire contract");
        require(!decode(&wire,sizeof wire-1,parsed,p),"reject truncated datagram");
        wire.fps=0; require(!decode(&wire,sizeof wire,parsed,p),"reject invalid rate");
        std::cout<<checks<<" camera core checks passed\n"; return 0;
    } catch (const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
