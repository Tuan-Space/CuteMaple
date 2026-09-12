import test from 'node:test';
import assert from 'node:assert/strict';
import { Playback, type ModelDriver } from '../src/playback';
import { localAssetUrl, parseCommand, type PlayCommand } from '../src/protocol';
import { controlsBlink } from '../src/motion-data';

function fixture() {
  const callbacks: (() => void)[] = [];
  const steps: number[] = [];
  const events: Record<string, unknown>[] = [];
  const driver: ModelDriver = {
    states: ['idle', 'land', 'happy'],
    play: (_command, callback) => { callbacks.push(callback); },
    update: delta => { steps.push(delta); }, draw() {}, gaze() {}, expression() {}, dispose() {},
    geometry: () => ({ bounds: [0, 0, 1, 1], anchors: { ground: [0.5, 1] } }),
  };
  const playback = new Playback(driver, event => events.push(event));
  const play = (name: string, token: number, playbackKind: PlayCommand['playback'] = 'loop') =>
    playback.play({ type: 'play', name, token, playback: playbackKind });
  return { playback, callbacks, steps, events, play };
}

test('only native boundaries emit cycles; duration metadata never substitutes for actual motion', () => {
  const f = fixture();
  f.play('idle', 1);
  f.playback.tick(0); f.playback.tick(1000);
  assert.equal(f.events.filter(e => e.type === 'cycle').length, 0);
  f.callbacks[0](); f.callbacks[0]();
  assert.deepEqual(f.events.filter(e => e.type === 'cycle').map(e => e.cycle), [1, 2]);
});

test('an interrupted fading motion cannot complete the replacement state', () => {
  const f = fixture();
  f.play('land', 10, 'one_shot');
  f.play('happy', 11, 'counted_loop');
  f.callbacks[0](); f.callbacks[1]();
  assert.deepEqual(f.events.filter(e => e.type === 'finished'), []);
  assert.deepEqual(f.events.filter(e => e.type === 'cycle'), [{type: 'cycle', name: 'happy', token: 11, cycle: 1}]);
});

test('one shot uses finished and counted loops leave state authority with host', () => {
  const f = fixture();
  f.play('land', 1, 'one_shot'); f.callbacks[0]();
  assert.equal(f.events.at(-1)?.type, 'finished');
  f.play('happy', 2, 'counted_loop');
  for (let i = 0; i < 5; i++) f.callbacks[1]();
  assert.equal(f.events.at(-1)?.cycle, 5);
  assert.equal(f.events.at(-1)?.type, 'cycle');
});

test('pause freezes animation and resume excludes time spent paused', () => {
  const f = fixture();
  f.playback.tick(0); f.playback.tick(16);
  const firstElapsed = f.steps.reduce((a, b) => a + b, 0);
  f.playback.pause(true); f.playback.tick(32); f.playback.tick(5000);
  assert.equal(f.steps.reduce((a, b) => a + b, 0), firstElapsed);
  f.playback.pause(false); f.playback.tick(6000); f.playback.tick(6016);
  assert.ok(Math.abs(f.steps.reduce((a, b) => a + b, 0) - 0.032) < 1e-7);
});

test('a suspension is bounded and physics steps never exceed 1/60 seconds', () => {
  const f = fixture();
  f.playback.tick(0); f.playback.tick(60000);
  assert.ok(f.steps.every(x => x <= 1 / 60));
  assert.ok(Math.abs(f.steps.reduce((a, b) => a + b, 0) - 0.25) < 1e-7);
});

test('missing states fail explicitly without invoking a substitute idle motion', () => {
  const f = fixture();
  assert.throws(() => f.play('climb_left', 1), /Missing Live2D motion/);
  assert.equal(f.callbacks.length, 0);
});

test('disposed playback ignores native callbacks', () => {
  const f = fixture(); f.play('land', 1, 'one_shot'); f.playback.dispose(); f.callbacks[0]();
  assert.equal(f.events.filter(e => e.type === 'finished').length, 0);
});

test('model references stay on the local origin', () => {
  const base = 'cutemaple://app/assets/live2d/Maple/Maple.model3.json';
  assert.equal(localAssetUrl('textures/atlas.png', base), 'cutemaple://app/assets/live2d/Maple/textures/atlas.png');
  assert.throws(() => localAssetUrl('https://example.com/model.moc3', base), /local renderer origin/);
  assert.throws(() => localAssetUrl('cutemaple://other/model.moc3', base), /local renderer origin/);
});

test('malformed motion commands are rejected at the bridge boundary', () => {
  assert.throws(() => parseCommand('{"type":"play","name":"land","token":"3","playback":"one_shot"}'), /Invalid motion/);
  assert.throws(() => parseCommand('null'), /Invalid renderer/);
});

test('constant open-eye baselines allow blinking but authored closures own eyelids', () => {
  assert.equal(controlsBlink([{ Id: 'ParamEyeLOpen', Segments: [0, 1, 0, 3, 1] }]), false);
  assert.equal(controlsBlink([{ Id: 'ParamEyeLOpen', Segments: [0, 1, 1, 0.2, 1, 0.4, 0, 0.5, 1] }]), true);
  assert.equal(controlsBlink([{ Id: 'ParamEyeROpen', Segments: [0, 0, 0, 2, 0] }]), true);
  assert.equal(controlsBlink([{ Id: 'ParamEyeBallX', Segments: [0, 0, 0, 2, 1] }]), false);
});


test('authored transition markers carry only their invocation token across interruptions', () => {
  const callbacks: ((name: string) => void)[] = [];
  const reports: Record<string, unknown>[] = [];
  const driver: ModelDriver = {
    states: ['climb_to_top_left', 'idle'],
    play: (_command, _boundary, marker) => { callbacks.push(marker!); },
    update() {}, draw() {}, gaze() {}, expression() {}, dispose() {},
    geometry: () => ({ bounds: [0, 0, 1, 1], anchors: {} }),
  };
  const playback = new Playback(driver, event => reports.push(event));
  playback.play({type: 'play', name: 'climb_to_top_left', token: 23, playback: 'one_shot'});
  callbacks[0]('top_grab');
  playback.play({type: 'play', name: 'idle', token: 24, playback: 'loop'});
  callbacks[0]('wall_release');
  assert.deepEqual(reports.filter(x => x.type === 'marker'), [
    {type: 'marker', name: 'climb_to_top_left', token: 23, marker: 'top_grab'},
  ]);
});
