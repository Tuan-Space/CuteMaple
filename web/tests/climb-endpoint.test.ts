import test from 'node:test';
import assert from 'node:assert/strict';
import { Playback, type ModelDriver } from '../src/playback';
import { parseCommand, type PlayCommand } from '../src/protocol';
import { secondsToAuthoredEndpoint, validateNativeIdentity } from '../src/native-contract';

function fixture() {
  let time = 0, name = '', callback = () => {};
  const updates: number[] = [], starts: boolean[] = [], reports: Record<string, any>[] = [];
  const period = 1.6 + 1 / 30;
  const driver: ModelDriver = {
    states: ['climb_left', 'climb_to_top_left', 'clean_climb_left_enter', 'idle'],
    play(command, boundary, _marker, staticStart) { name = command.name; time = 0; callback = boundary; starts.push(!!staticStart); },
    sampleStartPose() { time = 0; },
    climbEndpointSeconds: () => secondsToAuthoredEndpoint(time, 1.6, 1 / 30),
    update(seconds) { time += seconds; updates.push(seconds); if (time >= period) { time -= period; callback(); } },
    geometry: () => ({bounds: [0, 0, 1, 1], anchors: {gripLeft: [.3, .34]},
      climbPhase: name === 'climb_left' ? Math.min(time / 1.6, 1) : 0}),
    draw() {}, gaze() {}, expression() {}, dispose() {},
  };
  const playback = new Playback(driver, event => reports.push(event));
  const play = (next = 'climb_left', token = 1) => playback.play({type: 'play', name: next, token,
    playback: next === 'climb_to_top_left' ? 'one_shot' : 'loop'} as PlayCommand);
  const arm = (request = 1, enabled = true, token = 1) => playback.climbEndpoint({type: 'climb-endpoint', name: 'climb_left', token, request, enabled});
  const advance = () => { playback.tick(0); for (let ms = 250; ms <= 2000; ms += 250) playback.tick(ms); };
  return {playback, reports, starts, updates, play, arm, advance, time: () => time};
}

test('slow frames stop at actual authored endpoint before the R5 corrective frame, exactly once', () => {
  const f = fixture(); f.play(); f.arm(); f.advance();
  assert.ok(Math.abs(f.time() - 1.6) < 1e-9);
  const endpoints = f.reports.filter(row => row.climbEndpoint);
  assert.equal(endpoints.length, 1);
  assert.ok(Math.abs(endpoints[0].climbPhase - 1) < 1e-9);
  assert.deepEqual(endpoints[0].climbEndpoint, {request: 1, cycle: 0});
  assert.equal(f.reports.filter(row => row.type === 'cycle').length, 0);
  const steps = f.updates.length;
  f.playback.tick(60000); f.playback.sendGeometry();
  assert.equal(f.updates.length, steps);
  assert.equal(f.reports.filter(row => row.climbEndpoint).length, 1);
});

test('held climb plays the matching handoff as a real static start, excluding bridge latency', () => {
  const f = fixture(); f.play(); f.arm(); f.advance();
  f.play('climb_to_top_left', 2);
  assert.deepEqual(f.starts, [false, true]);
  assert.equal(f.reports.at(-1).name, 'climb_to_top_left');
  assert.equal(f.reports.at(-1).climbPhase, 0);
  f.playback.tick(60000);
  assert.equal(f.time(), 0);
  f.playback.tick(60016);
  assert.ok(Math.abs(f.time() - .016) < 1e-9);
});

test('pause cancels the pending request; stale retries and tokens cannot hold the resumed action', () => {
  const f = fixture(); f.play(); f.arm(); f.playback.pause(true); f.playback.pause(false);
  f.arm(); f.arm(2, true, 999); f.advance();
  assert.equal(f.reports.filter(row => row.climbEndpoint).length, 0);
  assert.ok(f.reports.some(row => row.type === 'cycle'));
});

test('pause at held endpoint resumes without spending time held; new request may hold current endpoint', () => {
  const f = fixture(); f.play(); f.arm(); f.advance();
  f.playback.pause(true); f.playback.tick(50000); f.playback.pause(false); f.arm(2);
  f.playback.tick(60000); f.playback.tick(60016);
  assert.ok(Math.abs(f.time() - 1.6) < 1e-9);
  assert.deepEqual(f.reports.filter(row => row.climbEndpoint).map(row => row.climbEndpoint.request), [1, 2]);
});

test('explicit cancel and replacement state clear requests without creating a synthetic endpoint', () => {
  for (const replace of [false, true]) {
    const f = fixture(); f.play(); f.arm();
    if (replace) f.play('idle', 2); else f.arm(1, false);
    f.advance();
    assert.equal(f.reports.filter(row => row.climbEndpoint).length, 0);
  }
});

test('an invalid real endpoint pose cannot be acknowledged as phase one', () => {
  const f = fixture(); f.play(); f.playback.driver.geometry = () => ({bounds: [0, 0, 1, 1], anchors: {}, climbPhase: .9});
  f.arm(); assert.throws(f.advance, /did not evaluate/);
  assert.equal(f.reports.filter(row => row.climbEndpoint).length, 0);
});

test('native clock arithmetic keeps R5 correction and previous completed loops distinct', () => {
  assert.equal(secondsToAuthoredEndpoint(1.6, 1.6, 1 / 30), 0);
  assert.ok(secondsToAuthoredEndpoint(1.61, 1.6, 1 / 30) > 1.6);
  assert.ok(Math.abs(secondsToAuthoredEndpoint(2 * (1.6 + 1 / 30) + .6, 1.6, 1 / 30) - 1) < 1e-9);
  assert.throws(() => secondsToAuthoredEndpoint(NaN, 1.6, 0), /Invalid native/);
});

test('endpoint command rejects wrong state, token, request, flag and generation types', () => {
  const command = {type: 'climb-endpoint', name: 'climb_left', token: 4, request: 2, enabled: true, generation: 1};
  assert.deepEqual(parseCommand(JSON.stringify(command)), command);
  for (const patch of [{name: 'idle'}, {token: '4'}, {request: 0}, {enabled: 1}, {generation: '1'}])
    assert.throws(() => parseCommand(JSON.stringify({...command, ...patch})), /Invalid/);
});

test('native identity rejects same-count wrong MOC IDs and malformed complete lists', () => {
  const contract = {nativeParameterIds: ['ParamA', 'ParamB'], nativeDrawableIds: ['arm', 'leg']};
  validateNativeIdentity(contract, ['ParamB', 'ParamA'], ['leg', 'arm']);
  for (const [parameters, meshes] of [[['ParamA', 'ParamC'], ['arm', 'leg']], [['ParamA', 'ParamB'], ['arm', 'shoe']]])
    assert.throws(() => validateNativeIdentity(contract, parameters, meshes), /MOC3 does not match/);
  assert.throws(() => validateNativeIdentity({nativeParameterIds: ['A', 'A']}, ['A', 'B'], []), /Invalid model identity/);
  assert.throws(() => validateNativeIdentity({nativeDrawableIds: []}, [], []), /Invalid model identity/);
  validateNativeIdentity({}, ['legacy'], ['legacy']);
});
