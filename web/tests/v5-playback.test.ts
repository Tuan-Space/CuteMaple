import test from 'node:test';
import assert from 'node:assert/strict';
import { Playback, ModelDriver } from '../src/playback';
import { ParticleField } from '../src/particles';
import { defaultContext, MotionResponse } from '../src/motion-context';
import { parseCommand } from '../src/protocol';

test('paused explicit play evaluates the new pose before geometry without time or events', () => {
  let value = 0, boundary: () => void, marker: (s: string) => void;
  const events: any[] = [], steps: number[] = [];
  const driver: ModelDriver = {states: ['idle', 'climb_left'],
    play: (_c, b, m, staticStart) => { assert.equal(staticStart, true); boundary = b; marker = m!; },
    sampleStartPose: () => { value = .7; boundary(); marker('top_grab'); },
    update: dt => { steps.push(dt); }, draw() {}, gaze() {}, expression() {}, dispose() {},
    geometry: () => ({bounds: [0, 0, 1, 1], anchors: {left: [value, .4]}})};
  const p = new Playback(driver, e => events.push(e));
  p.pause(true); p.play({type: 'play', name: 'climb_left', token: 2, playback: 'loop'});
  assert.equal(events.length, 1); assert.equal(events[0].anchors.left[0], .7);
  p.tick(0); p.tick(100000); assert.deepEqual(steps, []);
  p.pause(false); p.tick(200000); p.tick(200016);
  assert.equal(steps.reduce((a, b) => a + b, 0), .016);
  assert.equal(events.filter(e => ['cycle', 'marker', 'finished'].includes(e.type)).length, 0);
});

test('native endpoint callback and following wrapped phase produce continuous climb travel', () => {
  let phase = 0, callback: () => void;
  const events: any[] = [];
  const driver: ModelDriver = {states: ['climb_right'], play: (_c, b) => {callback = b;}, update() {},
    draw() {}, gaze() {}, expression() {}, dispose() {},
    geometry: () => ({bounds: [0, 0, 1, 1], anchors: {}, climbPhase: phase})};
  const p = new Playback(driver, e => events.push(e));
  p.play({type: 'play', name: 'climb_right', token: 1, playback: 'loop'});
  p.sendGeometry(); assert.equal(events.length, 0, 'old geometry cannot acquire the new token');
  p.tick(0); p.tick(16);
  const travel: number[] = [];
  for (const [next, boundary] of [[.95, false], [1, true], [1, false], [.03, false], [.8, false], [1, true], [.02, false]] as const) {
    phase = next; if (boundary) callback(); p.sendGeometry();
    const e = events.at(-1); travel.push(e.climbCycle + e.climbPhase);
  }
  assert.deepEqual(travel, [.95, 1, 1, 1.03, 1.8, 2, 2.02]);
});

test('effect generations and motion tokens cannot leak across state changes', () => {
  assert.throws(() => parseCommand('{"type":"effect","name":"wind","token":2}'));
  assert.throws(() => parseCommand('{"type":"effect","name":"note","token":2,"generation":"old"}'));
  assert.throws(() => parseCommand('{"type":"effect","name":"audio","token":2,"x":0.5}'));
  const field = new ParticleField(() => .5);
  const geometry = {bounds: [0, 0, 1, 1] as [number, number, number, number], anchors: {head: [.5, .3] as [number, number]}};
  field.play(1); assert.ok(field.emit({type: 'effect', name: 'audio', token: 1}, geometry));
  field.play(2); assert.equal(field.particles.length, 0);
  assert.equal(field.emit({type: 'effect', name: 'audio', token: 1}, geometry), false);
  field.pause(true); assert.equal(field.emit({type: 'effect', name: 'audio', token: 2}, geometry), false);
});

test('dust requires the current fan and leaves its surface in the wrist-to-fan direction', () => {
  const field = new ParticleField(() => .5); field.play(1);
  const geometry = {bounds: [0, 0, 1, 1] as [number, number, number, number],
    anchors: {head: [.5, .2] as [number, number], freeHand: [.6, .6] as [number, number], fanTip: [.75, .45] as [number, number]}};
  assert.ok(field.emit({type: 'effect', name: 'clean_dust', token: 1}, geometry));
  assert.equal(field.particles.length, 3);
  for (const particle of field.particles) {
    assert.equal(particle.shape, 'dust');
    assert.ok(Math.abs(particle.x - .7275) < .001 && Math.abs(particle.y - .4725) < .001);
    assert.ok(particle.vx > 0 && particle.vy < 0);
    assert.ok(Math.hypot(particle.x - .5, particle.y - .2) > .2);
  }
  const origin = {...field.particles[0]}; field.update(.1);
  assert.ok(field.particles[0].x > origin.x && field.particles[0].y < origin.y);
});

test('missing or offscreen fan never emits dust from the forehead or an override coordinate', () => {
  const field = new ParticleField(() => .5); field.play(1);
  const bounds: [number, number, number, number] = [0, 0, 1, 1];
  assert.equal(field.emit({type: 'effect', name: 'clean_dust', token: 1}, {bounds, anchors: {head: [.5, .2]}}), false);
  assert.equal(field.emit({type: 'effect', name: 'dust', token: 1}, {bounds, anchors: {head: [.5, .2], freeHand: [.6, .6]}}), false);
  const anchors = {head: [.5, .2] as [number, number], fanTip: [.8, .5] as [number, number], freeHand: [.6, .6] as [number, number]};
  assert.equal(field.emit({type: 'effect', name: 'dust', token: 1, x: .5, y: .2}, {bounds, anchors}), false);
  field.context({...defaultContext('clean_ground', 1), visibleRect: [0, 0, .3, .8]});
  assert.equal(field.emit({type: 'effect', name: 'clean_dust', token: 1}, {bounds, anchors}), false);
  assert.deepEqual(field.particles, []);
});

test('dust dissipates at the viewport edge without reflecting toward the pet', () => {
  const field = new ParticleField(() => .5); field.play(1);
  const geometry = {bounds: [0, 0, 1, 1] as [number, number, number, number],
    anchors: {head: [.5, .2] as [number, number], fanTip: [.99, .5] as [number, number], freeHand: [.95, .5] as [number, number]}};
  assert.ok(field.emit({type: 'effect', name: 'clean_dust', token: 1}, geometry));
  for (let i = 0; i < 8; i++) {
    field.update(.1);
    assert.ok(field.particles.every(p => p.vx > 0));
  }
  assert.deepEqual(field.particles, []);
});

test('simultaneous effect channels remain bounded and whole glyphs stay inside the visible rectangle', () => {
  const f = new ParticleField(() => .5); f.play(4);
  f.context({...defaultContext('swing_idle', 4), visibleRect: [.2, .15, .6, .7]});
  const geometry = {bounds: [0, 0, 1, 1] as [number, number, number, number], anchors: {head: [0, 0] as [number, number]}};
  for (let n = 0; n < 100; n++) {
    for (const name of ['keyboard', 'audio', 'clean_dust', 'happy'] as const) f.emit({type: 'effect', name, token: 4}, geometry);
    f.update(.05);
    assert.ok(f.particles.length <= 24);
    for (const p of f.particles) {
      assert.ok(p.x - p.radius >= .2 && p.x + p.radius <= .8);
      assert.ok(p.y - p.radius >= .15 && p.y + p.radius <= .85);
    }
  }
  assert.ok(f.particles.some(p => p.shape === 'note'));
  for (let i = 0; i < 25; i++) f.update(.1);
  assert.equal(f.particles.length, 0);
});

test('wind is bounded, has independent left/right response and damps stale velocity', () => {
  const m = new MotionResponse(); m.reset('fall_float');
  m.set({...defaultContext('fall_float'), vx: 2, vy: 3});
  m.update(.1);
  assert.ok(m.ribbons[1] < 0 && m.ribbons[3] < 0, 'downward motion lifts cloth tails');
  assert.notEqual(m.ribbons[0], m.ribbons[2]);
  for (let i = 0; i < 100; i++) m.update(.05);
  assert.ok(m.ribbons.every(v => Math.abs(v) < .001));
  assert.equal(m.bodyGazeAllowed('idle'), false);
  m.reset('idle'); assert.equal(m.bodyGazeAllowed('idle'), true);
  m.set({...defaultContext('idle'), attached: true}); assert.equal(m.bodyGazeAllowed('idle'), false);
});
