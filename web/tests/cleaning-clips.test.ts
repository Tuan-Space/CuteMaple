import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { defaultContext, MotionResponse } from '../src/motion-context';
import { Playback, type ModelDriver } from '../src/playback';
import { REQUIRED_STATES } from '../src/protocol';

const supports = {clean_ground: 'idle', clean_top: 'swing_idle',
  clean_climb_left: 'climb_left', clean_climb_right: 'climb_right'};
const phases = ['enter', 'exit'] as const;
const clips = Object.keys(supports).flatMap(name => phases.map(phase => `${name}_${phase}`));
const authoring = path.resolve('../assets/authoring/revisions/v5');
const runtime = path.join(authoring, 'runtime');
const readJson = (file: string) => JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
const motion = (name: string) => readJson(path.join(runtime, 'motions', `${name}.motion3.json`));
const endpoint = (data: any, end = false): Record<string, number> => Object.fromEntries(
  data.Curves.map((curve: any) => [curve.Id, end ? curve.Segments.at(-1) : curve.Segments[1]]));

test('all eight cleanup phases retain ground or attachment safety before host context arrives', () => {
  for (const name of clips) {
    const expectedAttached = !name.startsWith('clean_ground_');
    const context = defaultContext(name, 91);
    assert.equal(context.attached, expectedAttached, name);
    assert.equal(context.grounded, !expectedAttached, name);
    assert.equal(context.dragging || context.falling, false, name);
    assert.equal(context.token, 91);
    const response = new MotionResponse(); response.reset(name);
    assert.equal(response.context.attached, expectedAttached, `${name} model reset`);
    assert.equal(response.bodyGazeAllowed(name), false, `${name} support cannot receive body gaze`);
  }
});

test('internal cleanup phases finish only on their own native callback and reject interrupted callbacks', () => {
  const callbacks: (() => void)[] = [], events: Record<string, unknown>[] = [];
  const driver: ModelDriver = {states: clips, play: (_command, boundary) => { callbacks.push(boundary); },
    update() {}, draw() {}, gaze() {}, expression() {}, dispose() {},
    geometry: () => ({bounds: [0, 0, 1, 1], anchors: {}})};
  const player = new Playback(driver, event => events.push(event));
  for (const [index, name] of clips.entries()) {
    player.play({type: 'play', name, token: 100 + index, playback: 'one_shot', durationMs: 1});
    player.tick(index * 2000); player.tick(index * 2000 + 1000);
    assert.equal(events.filter(event => event.type === 'finished').length, index, 'host duration cannot finish a phase');
    if (index) callbacks[index - 1]();
    callbacks[index]();
    assert.deepEqual(events.filter(event => event.type === 'finished').at(-1),
      {type: 'finished', name, token: 100 + index, cycle: 1});
  }
  assert.equal(events.filter(event => event.type === 'finished').length, 8);
  assert.equal(events.some(event => event.type === 'cycle'), false);
});

test('v5 authored cleanup phases register eight one shots with matching loop and support endpoints', () => {
  // Authored source contract: this does not claim the new rig has been exported or visually reviewed.
  const metadata = readJson(path.join(runtime, 'Maple.pet.json'));
  const fragment = readJson(path.join(authoring, 'Maple.model3-fragment.json'));
  const archivedStates = [...REQUIRED_STATES, ...Object.keys(supports)];
  assert.deepEqual([...metadata.capabilities.continuousMotions].sort(), archivedStates.sort());
  assert.equal(REQUIRED_STATES.length, 17);
  const internal = ['climb_to_top_left', 'climb_to_top_right', ...clips];
  assert.deepEqual([...metadata.capabilities.internalMotions].sort(), internal.sort());
  assert.deepEqual(Object.keys(fragment.FileReferences.Motions).sort(), [...archivedStates, ...internal].sort());
  for (const [publicName, supportName] of Object.entries(supports)) {
    const loop = motion(publicName), support = motion(supportName);
    const loopStart = endpoint(loop), supportStart = endpoint(support);
    assert.deepEqual(endpoint(loop, true), loopStart, `${publicName} loop seam`);
    assert.deepEqual(loop.UserData.map((event: any) => event.Value), ['clean_sweep']);
    for (const phase of phases) {
      const name = `${publicName}_${phase}`, data = motion(name), duration = phase === 'enter' ? .70 : .65;
      assert.equal(data.Meta.Duration, duration, name);
      assert.equal(data.Meta.Loop, false, name);
      assert.deepEqual(data.UserData, [], `${name} must not announce a sweep before the cleaning loop`);
      assert.equal(metadata.transitions[name].duration, duration, name);
      assert.equal(metadata.transitions[name].playback, 'one_shot', name);
      assert.deepEqual(fragment.FileReferences.Motions[name], [{File: `motions/${name}.motion3.json`}]);
      assert.deepEqual(metadata.states[name].anchors, metadata.states[publicName].anchors, `${name} support/emitter anchors`);
      assert.ok(metadata.states[name].anchors.freeHand && metadata.states[name].anchors.fanTip, name);
      assert.deepEqual(endpoint(data), phase === 'enter' ? supportStart : loopStart, `${name} first pose`);
      assert.deepEqual(endpoint(data, true), phase === 'enter' ? loopStart : supportStart, `${name} final pose`);
    }
    const fan = publicName === 'clean_climb_left' ? 'ParamCleanFanL' : 'ParamCleanFanR';
    const otherFan = fan === 'ParamCleanFanL' ? 'ParamCleanFanR' : 'ParamCleanFanL';
    assert.equal(loopStart[fan], 1, `${publicName} has the free hand fan`);
    assert.equal(loopStart[otherFan], 0, `${publicName} preserves its supporting hand`);
    assert.equal(supportStart[fan], 0, `${publicName} fan is stowed after exit`);
  }
});
