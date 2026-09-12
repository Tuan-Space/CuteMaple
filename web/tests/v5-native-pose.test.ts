import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { CubismFramework } from '../../third_party/CubismSdkForWeb-5-r.5/Framework/src/live2dcubismframework';
import { CubismModelSettingJson } from '../../third_party/CubismSdkForWeb-5-r.5/Framework/src/cubismmodelsettingjson';

test('production static sampler replaces actual native drag geometry with seated and wall poses', async () => {
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
  // Exercise the production Core/motion sampler without claiming WebGL or desktop coverage.
  const driver = new MapleModel({} as HTMLCanvasElement, null as any);
  const harness = driver as any;
  const callbacks: string[] = [];
  try {
    const setting = bytes('Maple.model3.json');
    harness.setting = new CubismModelSettingJson(setting, setting.byteLength);
    harness.loadModel(bytes('Maple.moc3'), true);
    const model = driver.getModel(), core = model.getModel();
    harness.parameters = new Set(core.parameters.ids);
    harness.metadata = JSON.parse(fs.readFileSync(path.join(directory, 'Maple.pet.json'), 'utf8'));
    const faceIndex = core.drawables.ids.indexOf('face_base');
    const triangle = Array.from(core.drawables.indices[faceIndex]).slice(0, 3) as number[];
    const uv = core.drawables.vertexUvs[faceIndex];
    const weights = [.2, .3, .5];
    const atlasUv = [0, 1].map(axis => triangle.reduce((sum, vertex, i) => sum+uv[vertex*2+axis]*weights[i], 0));
    harness.metadata.anchors.materialQA = {drawable: 'face_base', atlasUv};
    harness.validateAnchors();
    for (const name of ['drag_right', 'climb_left', 'swing_idle']) harness.motions.set(name, {bytes: bytes(`motions/${name}.motion3.json`), index: 0, hasEyeCurves: false});
    harness.setupEffects(); driver.setInitialized(true); model.saveParameters();
    const parameter = (id: string) => core.parameters.values[core.parameters.ids.indexOf(id)];
    const vertices = () => Array.from(core.drawables.vertexPositions[core.drawables.ids.indexOf('face_base')]);
    driver.play({type: 'play', name: 'drag_right', token: 1, playback: 'loop'}, () => callbacks.push('old'));
    driver.update(.1);
    const before = vertices();
    for (const [name, expected] of [['swing_idle', 1], ['climb_left', -1]] as const) {
      driver.play({type: 'play', name, token: 2, playback: 'loop', fade: .25}, () => callbacks.push('boundary'), marker => callbacks.push(marker), true);
      driver.sampleStartPose();
      assert.ok(Math.abs(parameter(name === 'swing_idle' ? 'ParamPoseSwing' : 'ParamPoseClimb') - expected) < 1e-5);
      assert.equal(parameter('ParamArmPoseMode'), 1);
      assert.notDeepEqual(vertices(), before);
      const frozen = vertices();
      assert.ok(frozen.every(Number.isFinite));
      assert.deepEqual(vertices(), frozen);
      const raw = core.drawables.vertexPositions[faceIndex];
      const expectedRaw = [0, 1].map(axis => triangle.reduce((sum, vertex, i) => sum+raw[vertex*2+axis]*weights[i], 0));
      const expectedContact = harness.point(expectedRaw[0], expectedRaw[1]);
      const actual = driver.geometry().anchors.materialQA;
      assert.ok(actual.every((value, axis) => Math.abs(value-expectedContact[axis]) < 1e-7),
        'production geometry must carry the actual native painted point through pose changes');
    }
    assert.deepEqual(callbacks, [], 'static evaluation must not cross any actual motion boundary');
    harness.metadata.anchors.materialQA = {drawable:'face_base', sourceUv: [.5,.5]};
    assert.throws(() => harness.validateAnchors(), /Uninstalled source contact/);
  } finally { driver.dispose(); CubismFramework.dispose(); CubismFramework.cleanUp(); }
});
