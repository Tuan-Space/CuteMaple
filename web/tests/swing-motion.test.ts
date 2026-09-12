import test from 'node:test';
import assert from 'node:assert/strict';
import { SwingMotion } from '../src/swing-motion';
import { Playback, type ModelDriver } from '../src/playback';
import { parseCommand } from '../src/protocol';
import { ParticleField } from '../src/particles';

test('swing crosses its center without Cubism padding and counts three complete turns', () => {
  const swing = new SwingMotion(); swing.enter('swing_cycle');
  let cycles = swing.advance(2.039);
  const before = swing.pose().swing;
  cycles += swing.advance(.001); const center = swing.pose().swing;
  cycles += swing.advance(.001); const after = swing.pose().swing;
  assert.ok(before < -.006 && Math.abs(center) < 1e-10 && after > .006);
  cycles += swing.advance(1.019);
  assert.equal(cycles, 3);
});

test('envelope integration is independent of frame rate, including interrupted state changes', () => {
  const run = (hz: number) => {
    const swing = new SwingMotion(); swing.enter('swing_cycle');
    for (const [state, duration] of [['swing_cycle', .43], ['clean_top_enter', .36],
      ['clean_top', .8], ['clean_top_exit', .65], ['swing_idle', 2]] as const) {
      swing.enter(state);
      for (let left = duration; left > 1e-10;) { const dt = Math.min(left, 1/hz); swing.advance(dt); left -= dt; }
    }
    return swing.diagnostics();
  };
  const reference = run(60);
  for (const hz of [30, 144]) for (const key of ['swingPhase', 'swingAmplitude', 'swingFrequency'])
    assert.ok(Math.abs(run(hz)[key]-reference[key]) < 1e-10, `${hz}Hz ${key}`);
  assert.ok(Math.abs(reference.swingFrequency-1/3.87) < 1e-12);
});

test('cycle to idle preserves position and velocity, settling over the requested 1.2 seconds', () => {
  const swing = new SwingMotion(); swing.enter('swing_cycle'); swing.advance(3.06-.00001);
  const before = swing.pose().swing; swing.advance(.00001);
  const center = swing.pose().swing, phase = swing.diagnostics().swingPhase;
  swing.enter('swing_idle'); assert.equal(swing.pose().swing, center);
  assert.equal(swing.diagnostics().swingPhase, phase);
  swing.advance(.00001); const after = swing.pose().swing;
  assert.ok(Math.abs((center-before)/.00001-(after-center)/.00001) < 1e-5);
  swing.advance(1.2);
  assert.equal(swing.diagnostics().swingAmplitude, .2);
  assert.equal(swing.diagnostics().swingFrequency, 1/3.87);
});

test('petting expires at three seconds, has two directions and never owns body or hand parameters', () => {
  for (const side of ['left', 'right']) {
    const swing = new SwingMotion(); swing.enter('swing_idle'); swing.advance(1.2);
    swing.pet('swing_petting_'+side, true); swing.advance(.6);
    assert.equal(swing.petting().weight, 1);
    assert.equal(Math.sign(swing.petting().headZ), side === 'left' ? -1 : 1);
    assert.deepEqual(Object.keys(swing.pose()), ['swing', 'headZ', 'legA']);
    swing.pet('swing_petting_'+side, true); swing.advance(2.4);
    assert.equal(swing.petting().weight, 0, 'repeat active messages cannot extend the finite reaction');
    swing.pet('swing_petting_'+side, true); swing.advance(.5);
    swing.enter('clean_top_enter'); assert.equal(swing.petting().weight, 0);
    swing.pet('swing_petting_'+side, true); swing.advance(.5);
    assert.equal(swing.petting().weight, 0, 'cleaning cannot acquire a petting overlay');
  }
});

test('playback freezes the swing clock and rejects stale petting tokens and paused inputs', () => {
  const swing = new SwingMotion(), events: any[] = [], expressions: string[] = [];
  let boundary: () => void;
  const driver: ModelDriver = {states: ['swing_cycle', 'swing_idle', 'land'], motionPolishEnabled: true,
    play(c, b) { swing.enter(c.name); boundary = b; },
    update(dt) { const crossed = swing.advance(dt); for (let i=0;i<crossed;i++) boundary(); },
    expression(name, active) { expressions.push(name); swing.pet(name, active); },
    interruptPetting() { swing.cancelPetting(); },
    geometry: () => ({bounds:[0,0,1,1], anchors:{}}), draw() {}, gaze() {}, dispose() {}};
  const playback = new Playback(driver, e => events.push(e));
  playback.play({type:'play', name:'swing_cycle', token:4, playback:'counted_loop', cycles:3});
  playback.tick(0); playback.tick(100);
  playback.expression({type:'expression', name:'swing_petting_left', active:true, token:3});
  assert.equal(expressions.length, 0);
  playback.expression({type:'expression', name:'swing_petting_left', active:true, token:4});
  playback.tick(200); playback.pause(true);
  const frozen = swing.diagnostics(); playback.tick(999999);
  assert.deepEqual(swing.diagnostics(), frozen); assert.equal(frozen.swingPettingWeight, 0);
  playback.expression({type:'expression', name:'swing_petting_right', active:true, token:4});
  assert.equal(expressions.length, 1);
  playback.pause(false); playback.tick(2000000); playback.tick(2000010);
  assert.ok(swing.diagnostics().swingPhase-frozen.swingPhase < .07);
  assert.throws(() => parseCommand('{"type":"expression","name":"swing_petting_left","active":true}'), /Invalid swing/);
});

test('new brush material contacts take precedence over the legacy fan', () => {
  const field = new ParticleField(() => .5); field.play(1);
  assert.ok(field.emit({type:'effect',name:'clean_dust',token:1}, {bounds:[0,0,1,1], anchors:{
    brushGrip:[.3,.4], brushTip:[.5,.6], freeHand:[.9,.9], fanTip:[.8,.7]}}));
  assert.ok(field.particles.every(p => Math.abs(p.x-.47)<1e-8 && Math.abs(p.y-.57)<1e-8 && p.vx>0 && p.vy>0));
});

test('active swing petting eases to gentle motion without resetting phase, velocity or counted turns', () => {
  const swing = new SwingMotion(); swing.enter('swing_cycle');
  const step = .000001;
  let cycles = swing.advance(1.4-step);
  const before = swing.pose().swing; swing.advance(step);
  const current = swing.pose().swing, phase = swing.diagnostics().swingPhase;
  swing.pet('swing_petting_left', true);
  assert.equal(swing.pose().swing, current); assert.equal(swing.diagnostics().swingPhase, phase);
  swing.advance(step); const after = swing.pose().swing;
  assert.ok(Math.abs((current-before)/step-(after-current)/step) < .0001);
  cycles += swing.advance(1.2-step);
  assert.equal(swing.diagnostics().swingAmplitude, .2);
  assert.equal(swing.diagnostics().swingFrequency, 1/3.87);
  cycles += swing.advance(1.8);
  assert.equal(swing.petting().weight, 0);
  assert.equal(swing.diagnostics().swingAmplitude, .2, 'expiry begins a smooth recovery');
  cycles += swing.advance(1.2);
  assert.equal(swing.diagnostics().swingAmplitude, 1);
  assert.equal(swing.diagnostics().swingFrequency, 1/1.02);
  assert.equal(cycles, Math.floor(swing.diagnostics().swingPhase/(Math.PI*2)), 'the original turn origin is retained');
});

test('pet expiry integration and interrupted recovery remain continuous across frame rates', () => {
  const run = (hz: number) => {
    const swing = new SwingMotion(); swing.enter('swing_cycle'); swing.advance(1.8);
    swing.pet('swing_petting_right', true);
    for (let left = 4.2; left > 1e-10;) { const dt = Math.min(left, 1/hz); swing.advance(dt); left -= dt; }
    return swing.diagnostics();
  };
  const reference = run(60);
  for (const hz of [.1, 30, 144]) for (const key of ['swingPhase', 'swingAmplitude', 'swingFrequency'])
    assert.ok(Math.abs(run(hz)[key]-reference[key]) < 1e-10, `${hz}Hz ${key}`);
  const swing = new SwingMotion(); swing.enter('swing_cycle'); swing.advance(2);
  swing.pet('swing_petting_left', true); swing.advance(.4);
  const before = swing.pose().swing, phase = swing.diagnostics().swingPhase;
  assert.ok(swing.cancelPetting()); assert.equal(swing.pose().swing, before);
  assert.equal(swing.diagnostics().swingPhase, phase); swing.advance(1.2);
  assert.equal(swing.diagnostics().swingAmplitude, 1);
  swing.pet('swing_petting_right', true); swing.advance(.2);
  swing.enter('clean_top_enter'); swing.advance(1.2);
  assert.equal(swing.diagnostics().swingAmplitude, .2);
  assert.equal(swing.diagnostics().swingFrequency, 1/3.87);
});

test('detaching revokes late petting, support context and cleanup callbacks from the old token', () => {
  const swing = new SwingMotion(), events: unknown[] = [], expressions: unknown[] = [], contexts: unknown[] = [];
  const callbacks: {boundary: () => void; marker?: (name: string) => void}[] = [];
  const driver: ModelDriver = {states:['swing_idle', 'clean_top_enter', 'drag_right'], motionPolishEnabled:true,
    play(command, boundary, marker) { swing.enter(command.name); callbacks.push({boundary, marker}); },
    expression(name, active) { expressions.push({name, active}); swing.pet(name, active); },
    context(value) { contexts.push(value); }, geometry:() => ({bounds:[0,0,1,1], anchors:{}}),
    update(dt) { swing.advance(dt); }, draw() {}, gaze() {}, dispose() {}};
  const player = new Playback(driver, event => events.push(event));
  player.play({type:'play',name:'swing_idle',token:10,playback:'loop'});
  player.expression({type:'expression',name:'swing_petting_left',active:true,token:10});
  player.tick(0); player.tick(100);
  assert.ok(swing.petting().weight > 0);
  player.play({type:'play',name:'drag_right',token:11,playback:'loop'});
  assert.equal(swing.petting().weight, 0);
  const count = expressions.length;
  player.expression({type:'expression',name:'swing_petting_right',active:true,token:10});
  player.expression({type:'expression',name:'swing_petting_left',active:false,token:10});
  assert.equal(expressions.length, count);
  player.play({type:'play',name:'clean_top_enter',token:12,playback:'one_shot'});
  player.play({type:'play',name:'drag_right',token:13,playback:'loop'});
  events.length = 0; // Discard the legitimate geometry emitted before detachment.
  callbacks[2].boundary(); callbacks[2].marker?.('clean_sweep');
  player.context({type:'context',token:12,grounded:false,attached:true,dragging:false,falling:false,
    vx:0,vy:0,visibleRect:[0,0,1,1],effectsEnabled:true});
  assert.deepEqual(events, []); assert.deepEqual(contexts, []);
});
