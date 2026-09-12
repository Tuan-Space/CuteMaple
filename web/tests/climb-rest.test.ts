import test from 'node:test';
import assert from 'node:assert/strict';
import {Playback, type ModelDriver} from '../src/playback';
import {secondsToAuthoredEndpoint} from '../src/native-contract';
import {parseCommand} from '../src/protocol';

function fixture() {
  let time=0,total=0,head=0,callback=()=>{};
  const reports: Record<string,any>[]=[];
  const driver:ModelDriver={states:['climb_left','idle'],motionPolishEnabled:true,
    play(_c,cb){time=0;callback=cb;}, sampleStartPose(){time=0;},setClimbHold(){},
    updateClimbHold(dt){head+=dt;},
    climbEndpointSeconds:()=>secondsToAuthoredEndpoint(time,1,1/60),
    update(dt){time+=dt;total+=dt;if(time>=1+1/60-1e-10){time-=1+1/60;time=Math.max(0,time);callback();}},
    geometry:()=>({bounds:[0,0,1,1],anchors:{},climbPhase:Math.min(1,time)}),
    draw(){},gaze(){},expression(){},dispose(){}};
  const player=new Playback(driver,e=>reports.push(e));
  player.play({type:'play',name:'climb_left',token:1,playback:'loop'});
  const control=(resting:boolean,run=1,token=1)=>player.climbControl({type:'climb-control',name:'climb_left',token,run,resting});
  return {player,control,reports,time:()=>time,total:()=>total,head:()=>head};
}

for(const hz of [30,60,144]) test(`rest keeps head alive and exactly two native cycles at ${hz}Hz`,()=>{
  const f=fixture();f.control(true);let ms=0;f.player.tick(ms);
  for(let i=0;i<hz*30;i++)f.player.tick(ms+=1000/hz);
  assert.equal(f.total(),0);assert.ok(f.head()>29.9);
  f.control(false,2);f.player.tick(ms);
  for(let i=0;i<hz*3;i++)f.player.tick(ms+=1000/hz);
  assert.ok(Math.abs(f.total()-(2+1/60))<1e-8);
  assert.equal(f.reports.filter(x=>x.type==='climb-rest').length,1);
  f.control(false,3);f.player.tick(ms);
  for(let i=0;i<hz*3;i++)f.player.tick(ms+=1000/hz);
  assert.ok(Math.abs(f.total()-(4+3/60))<1e-8);
  assert.equal(f.reports.filter(x=>x.type==='climb-rest').length,2);
});

test('panel holds exact partial pose, preserves burst and rejects stale control',()=>{
  const f=fixture();f.control(false,2);f.player.tick(0);f.player.tick(200);
  const before=f.total();f.control(true,2);f.player.tick(1000);f.player.tick(1200);
  assert.equal(f.total(),before);assert.ok(f.head()>0);
  f.control(false,1);assert.equal(f.player.diagnostics().climbResting,true);
  f.control(false,2,999);assert.equal(f.player.diagnostics().climbResting,true);
  f.player.pause(true);const head=f.head();f.player.tick(9000);f.player.tick(9100);assert.equal(f.head(),head);
  f.player.pause(false);f.control(false,2);f.player.tick(20000);
  assert.equal(f.total(),before);
  for(let i=1;i<=30;i++)f.player.tick(20000+i*100);
  assert.ok(Math.abs(f.total()-(2+1/60))<1e-8);
});

test('roof endpoint wins over ordinary burst rest',()=>{
  const f=fixture();f.control(false,2);
  f.player.climbEndpoint({type:'climb-endpoint',name:'climb_left',token:1,request:1,enabled:true});
  f.player.tick(0);for(let i=1;i<=12;i++)f.player.tick(i*200);
  assert.equal(f.reports.filter(x=>x.type==='climb-rest').length,0);
  assert.equal(f.reports.filter(x=>x.climbEndpoint).length,1);
  assert.ok(Math.abs(f.total()-1)<1e-8);
});

test('invalid local hold messages rejected',()=>{
  for(const value of [0,-1,1.2,NaN]) assert.throws(()=>parseCommand(JSON.stringify({type:'climb-control',name:'climb_left',token:1,run:value,resting:true})));
});

test('uneven frames and state interruption never add a third cycle',()=>{
  const f=fixture();f.control(false,2);let ms=0;f.player.tick(ms);
  for(let i=0;i<90;i++)f.player.tick(ms+=[5,17,43,8,120,31][i%6]);
  assert.ok(Math.abs(f.total()-(2+1/60))<1e-8);
  assert.equal(f.reports.filter(x=>x.type==='climb-rest').length,1);
  f.player.play({type:'play',name:'idle',token:2,playback:'loop'});
  f.control(false,3,1);
  assert.equal(f.player.diagnostics().climbResting,false);
});
