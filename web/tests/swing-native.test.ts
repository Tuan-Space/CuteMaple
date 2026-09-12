import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { CubismFramework } from '../../third_party/CubismSdkForWeb-5-r.5/Framework/src/live2dcubismframework';
import { CubismModelSettingJson } from '../../third_party/CubismSdkForWeb-5-r.5/Framework/src/cubismmodelsettingjson';
import { MotionBehavior } from '../../third_party/CubismSdkForWeb-5-r.5/Framework/src/motion/cubismmotion';

test('production swing ownership reaches real Core, counts turns and preserves seated hands during petting', async () => {
  const runtime = globalThis as any;
  runtime.require = createRequire(import.meta.url);
  runtime.__dirname = path.resolve('../third_party/CubismSdkForWeb-5-r.5/Core');
  vm.runInThisContext(fs.readFileSync(path.join(runtime.__dirname, 'live2dcubismcore.min.js'), 'utf8'));
  await new Promise(resolve => setTimeout(resolve, 0));
  const { MapleModel } = await import('../src/model');
  await import('../../third_party/CubismSdkForWeb-5-r.5/Framework/src/rendering/cubismrenderer_webgl');
  assert.equal(CubismFramework.startUp(), true); CubismFramework.initialize();
  const directory = path.resolve('../assets/live2d/Maple');
  const bytes = (name: string) => { const b = fs.readFileSync(path.join(directory, name)); return b.buffer.slice(b.byteOffset, b.byteOffset+b.byteLength); };
  const driver = new MapleModel({} as HTMLCanvasElement, null as any), harness = driver as any;
  try {
    const setting = bytes('Maple.model3.json');
    harness.setting = new CubismModelSettingJson(setting, setting.byteLength);
    harness.loadModel(bytes('Maple.moc3'), true);
    const model = driver.getModel(), core = model.getModel();
    harness.parameters = new Set(core.parameters.ids);
    harness.metadata = JSON.parse(fs.readFileSync(path.join(directory, 'Maple.pet.json'), 'utf8'));
    // Exercise the new controller against the existing real model's parameters;
    // this is not native export acceptance for the upcoming authoring revision.
    harness.metadata.refinement.motionPolishVersion = 1;
    // This assertion isolates petting closure from the independently randomized blink.
    for (const state of ['swing_cycle', 'swing_idle'])
      harness.metadata.states[state] = {...harness.metadata.states[state], disableBlink:true};
    for (const name of ['swing_cycle', 'swing_idle',    'land'])
      harness.motions.set(name, {bytes:bytes(`motions/${name}.motion3.json`), index:0, hasEyeCurves:false});
    harness.setupEffects(); driver.setInitialized(true); model.saveParameters();
    const parameter = (name: string) => model.getParameterValueById(CubismFramework.getIdManager().getId(name));
    let cycles = 0;
    driver.play({type:'play',name:'swing_cycle',token:1,playback:'counted_loop',cycles:3,fade:0}, () => cycles++);
    assert.equal(harness.currentMotion.getMotionBehavior(), MotionBehavior.MotionBehavior_V2);
    for (let i=0;i<306;i++) driver.update(.01);
    assert.equal(cycles, 3, 'the padded SDK callbacks cannot drive this family');
    assert.ok(Math.abs(parameter('ParamSwing')) < 1e-6);
    const phase = driver.diagnostics().swingPhase;
    driver.play({type:'play',name:'swing_idle',token:2,playback:'loop',fade:.25}, () => {});
    assert.equal(driver.diagnostics().swingPhase, phase);
    driver.update(.01);
    assert.ok(parameter('ParamSwing') > .05, 'the outgoing zero-valued SDK tail cannot hold the center');
    driver.expression('swing_petting_left', true, 1);
    for (let i=0;i<60;i++) driver.update(.01);
    assert.equal(parameter('ParamEyeLOpen'), 0); assert.equal(parameter('ParamEyeROpen'), 0);
    for (const [name, value] of Object.entries({ParamPoseSwing:1, ParamArmPoseMode:1, ParamArmSupportBlend:1,
      ParamHandLShape:1, ParamHandRShape:1})) assert.ok(Math.abs(parameter(name)-value)<1e-6, name);
    const head = driver.geometry().headBounds;
    assert.ok(head?.every(Number.isFinite) && head[2]>0 && head[3]>0);
    const palms = () => ['hand_l_grip_palm', 'hand_r_grip_palm'].map(name => {
      const index = core.drawables.ids.indexOf(name); assert.ok(index >= 0, name);
      return Array.from(core.drawables.vertexPositions[index]);
    });
    const heldPalms = palms();
    const beforeInterrupt = driver.diagnostics().swingPhase;
    driver.expression('swing_petting_left', false, 1);
    driver.interruptPetting(); // Host sends expression-off before pause.
    assert.equal(driver.diagnostics().swingPettingWeight, 0);
    assert.equal(driver.diagnostics().swingPhase, beforeInterrupt);
    assert.ok(parameter('ParamEyeLOpen') > .9, 'strong interruption refreshes the frozen face immediately');
    assert.deepEqual(palms(), heldPalms, 'ending the head overlay cannot change either actual native palm');
    for (const name of [   'swing_idle']) {
      const before = driver.diagnostics().swingPhase;
      driver.play({type:'play',name,token:3,playback:name.endsWith('enter')||name.endsWith('exit')?'one_shot':'loop'}, () => {});
      assert.equal(driver.diagnostics().swingPhase, before); driver.update(.01);
      assert.ok(Math.abs(parameter('ParamSwing')-harness.swing.pose().swing)<1e-6);
    }
    driver.play({type:'play',name:'land',token:4,playback:'one_shot'}, () => {});
    assert.equal(driver.diagnostics().swingActive, 0);
  } finally { driver.dispose(); CubismFramework.dispose(); CubismFramework.cleanUp(); }
});
