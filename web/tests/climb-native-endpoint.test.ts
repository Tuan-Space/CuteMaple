import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { CubismFramework } from '../../third_party/CubismSdkForWeb-5-r.5/Framework/src/live2dcubismframework';
import { CubismModelSettingJson } from '../../third_party/CubismSdkForWeb-5-r.5/Framework/src/cubismmodelsettingjson';
import { Playback } from '../src/playback';
import { validateNativeIdentity } from '../src/native-contract';

test('actual installed MOC and R5 queue stop at authored climb phase one, then sample real handoff phase zero', async () => {
  const runtime = globalThis as any;
  runtime.require = createRequire(import.meta.url);
  runtime.__dirname = path.resolve('../third_party/CubismSdkForWeb-5-r.5/Core');
  vm.runInThisContext(fs.readFileSync(path.join(runtime.__dirname, 'live2dcubismcore.min.js'), 'utf8'));
  await new Promise(resolve => setTimeout(resolve, 0));
  const { MapleModel } = await import('../src/model');
  await import('../../third_party/CubismSdkForWeb-5-r.5/Framework/src/rendering/cubismrenderer_webgl');
  assert.equal(CubismFramework.startUp(), true); CubismFramework.initialize();
  const directory = path.resolve('../assets/live2d/Maple');
  const bytes = (name: string) => { const b = fs.readFileSync(path.join(directory, name)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); };
  const driver = new MapleModel({} as HTMLCanvasElement, null as any);
  const harness = driver as any;
  try {
    const setting = bytes('Maple.model3.json');
    harness.setting = new CubismModelSettingJson(setting, setting.byteLength);
    harness.loadModel(bytes('Maple.moc3'), true);
    const model = driver.getModel(), core = model.getModel();
    harness.parameters = new Set(core.parameters.ids);
    harness.metadata = JSON.parse(fs.readFileSync(path.join(directory, 'Maple.pet.json'), 'utf8'));
    // This Core-only harness skips load()'s fetch/WebGL work, so perform its
    // metadata validation and native-UV contact binding before playback.
    validateNativeIdentity(harness.metadata.refinement, Array.from(core.parameters.ids), Array.from(core.drawables.ids));
    harness.validateAnchors();
    driver.states = ['climb_left', 'climb_to_top_left'];
    for (const name of driver.states) harness.motions.set(name, {bytes: bytes(`motions/${name}.motion3.json`), index: 0, hasEyeCurves: false});
    harness.setupEffects(); driver.setInitialized(true); model.saveParameters();
    driver.draw = () => {}; // No WebGL or desktop claim: actual Core + production motion evaluation.
    const rows: Record<string, any>[] = [];
    const player = new Playback(driver, row => rows.push(row));
    player.play({type: 'play', name: 'climb_left', token: 1, playback: 'loop', durationMs: 17, fade: .25});
    player.climbEndpoint({type: 'climb-endpoint', name: 'climb_left', token: 1, request: 1, enabled: true});
    player.tick(0);
    try { for (let ms = 230; ms < 4000; ms += 230) player.tick(ms); }
    catch (error) { throw new Error(JSON.stringify({clock: driver.diagnostics(), start: harness.currentEntry.getStartTime(),
      duration: harness.currentMotion.getLoopDuration(), fadeIn: harness.currentMotion._fadeInSeconds,
      weight: harness.currentMotion._lastWeight, phase: driver.geometry().climbPhase}), {cause: error}); }
    const endpoints = rows.filter(row => row.climbEndpoint);
    assert.equal(endpoints.length, 1);
    assert.ok(Math.abs(endpoints[0].climbPhase - 1) <= 1e-5);
    assert.ok(Object.values(endpoints[0].anchors).length > 0);
    const held = driver.diagnostics();
    const vertices = Array.from(core.drawables.vertexPositions[0]);
    player.tick(50000);
    assert.deepEqual(driver.diagnostics(), held);
    assert.deepEqual(Array.from(core.drawables.vertexPositions[0]), vertices);
    player.play({type: 'play', name: 'climb_to_top_left', token: 2, playback: 'one_shot', fade: .25});
    assert.ok(driver.diagnostics().staticSampleCount > held.staticSampleCount);
    assert.equal(rows.at(-1)?.name, 'climb_to_top_left');
    assert.ok(Math.abs(rows.at(-1)?.transitionProgress || 0) < 1e-5);
    assert.ok(Math.abs(rows.at(-1)?.climbPhase || 0) < 1e-5);
    assert.ok(Array.from(core.drawables.vertexPositions[0]).every(Number.isFinite));
    // Also arm after ordinary native cycles have already wrapped. The queued
    // entry start time changes at a loop boundary; a process-wide clock alone
    // would stop at the wrong phase here.
    player.play({type: 'play', name: 'climb_left', token: 3, playback: 'loop', fade: .25});
    player.tick(51000);
    for (let ms = 51230; ms <= 53300; ms += 230) player.tick(ms);
    player.climbEndpoint({type: 'climb-endpoint', name: 'climb_left', token: 3, request: 2, enabled: true});
    for (let ms = 53530; ms <= 56300; ms += 230) player.tick(ms);
    const later = rows.filter(row => row.climbEndpoint && row.token === 3);
    assert.equal(later.length, 1);
    assert.ok(later[0].climbEndpoint.cycle >= 1);
    assert.ok(Math.abs(later[0].climbPhase - 1) <= 1e-5);
  } finally { driver.dispose(); CubismFramework.dispose(); CubismFramework.cleanUp(); }
});
