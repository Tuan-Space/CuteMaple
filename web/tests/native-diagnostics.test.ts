import test from 'node:test';
import assert from 'node:assert/strict';
import { contactTopology, physicsOutputParameters } from '../src/native-diagnostics';
import { validateNativeIdentity } from '../src/native-contract';

test('diagnostics serialize actual visible brush, hand and replacement cuff topology', () => {
  const vertices = new Float32Array([.1,.2,.3,.4,.5,.6]);
  const uvs = new Float32Array([0,0,1,0,0,1]);
  const indices = new Uint16Array([0,1,2]);
  for (const side of ['l','r']) for (const name of [`clean_brush_${side}`, `clean_hand_${side}_palm`,
    `clean_hand_${side}_fingers`, `clean_sleeve_${side}_lower`, `sleeve_${side}_lower`, `hand_${side}`, `arm_${side}`, `clean_fan_${side}`]) {
    const value = contactTopology(name, vertices, uvs, indices);
    assert.deepEqual(value, {positions:Array.from(vertices), textureUvs:[0,1,1,1,0,0], triangles:[0,1,2]}, name);
    vertices[0] = .8;
    assert.notEqual(value.positions, vertices, 'the report owns an actual snapshot, not mutable Core storage');
  }
  assert.deepEqual(contactTopology('unrelated_decoration', vertices, uvs, indices), {});
  for (const name of ['clean_ground_brush_r', 'clean_ground_hand_r_palm', 'clean_ground_hand_r_fingers',
    'clean_ground_hand_r_relaxed']) {
    assert.deepEqual(contactTopology(name, vertices, uvs, indices), {
      positions:Array.from(vertices), textureUvs:[0,1,1,1,0,0], triangles:[0,1,2]}, name);
  }
});

test('the native identity comparison includes the independent drape axis and complete ground hand', () => {
  const parameters = ['ParamClimbRefine', 'ParamClimbDrapeBlend', 'ParamHandRShape'];
  const drawables = ['profile_l_skirt_climb_drape', 'arm_r', 'clean_ground_hand_r_relaxed'];
  const contract = {motionPolishVersion:1, nativeParameterIds:parameters, nativeDrawableIds:drawables};
  assert.doesNotThrow(() => validateNativeIdentity(contract, parameters, drawables));
  assert.throws(() => validateNativeIdentity(contract, ['ParamClimbRefine', 'ParamHandRShape'], drawables), /ParamClimbDrapeBlend/);
  assert.throws(() => validateNativeIdentity(contract, parameters, ['profile_l_skirt_climb_drape', 'arm_r']), /clean_ground_hand_r_relaxed/);
});

const outputs = ['ParamHairFront','ParamHairSide','ParamHairBack','ParamSkirt','ParamSleeveLHang','ParamSleeveRHang'];
const physics = {Version:3, PhysicsSettings:outputs.map(Id => ({Output:[{Destination:{Target:'Parameter',Id}}]}))};

test('physics destinations come from the loaded resource and must name real native parameters', () => {
  assert.deepEqual(physicsOutputParameters(physics, new Set(outputs)), [...outputs].sort());
  assert.throws(() => physicsOutputParameters(physics, new Set(outputs.slice(1))), /actual native parameter/);
  assert.throws(() => physicsOutputParameters({Version:3}, new Set(outputs)), /Invalid physics/);
  const ribbon = {Version:3, PhysicsSettings:[{Output:[{Destination:{Target:'Parameter',Id:'ParamRibbonLX'}}]}]};
  assert.deepEqual(physicsOutputParameters(ribbon, new Set(['ParamRibbonLX'])), ['ParamRibbonLX'], 'new genuine file-declared outputs do not need another name exception');
});

test('production frame and static sampling preserve all declared motion values before physics', async () => {
  (globalThis as any).Live2DCubismCore = {};
  const { MapleModel } = await import('../src/model');
  const { CubismFramework } = await import('@framework/live2dcubismframework');
  const original = CubismFramework.getIdManager;
  (CubismFramework as any).getIdManager = () => ({getId:(name:string) => name});
  try {
    let authored = 0;
    const values:Record<string,number> = {ParamCleanPoseR:1};
    const core = {loadParameters:() => {}, saveParameters:() => {}, update:() => {},
      getParameterValueById:(name:string) => {assert.ok(outputs.includes(name)); return values[name];}};
    const harness:any = {isInitialized:() => true, disposed:false, motionPolishEnabled:true,
      physicsOutputs:outputs, _model:core, nativeUpdateCount:0, staticSampleCount:0,
      _motionManager:{_userTimeSeconds:1, updateMotion:() => {for (const name of outputs) values[name]=authored;}},
      swing:{advance:() => 0, diagnostics:() => ({})}, applySwingPose:() => {},
      scheduler:{onLateUpdate:() => {for (const name of outputs) values[name]=-.0434080623;}}, weights:new Map(),
      _physics:{stabilization:() => {for (const name of outputs) values[name]=-.08;}},
      captureMotionPhysics:(MapleModel.prototype as any).captureMotionPhysics};
    MapleModel.prototype.update.call(harness, .016);
    assert.equal(values.ParamSleeveLHang, -.0434080623, 'QA must retain actual sleeve physics');
    const snapshot = MapleModel.prototype.motionPhysicsSnapshot.call(harness);
    assert.deepEqual(snapshot, Object.fromEntries(outputs.map(name => [name,0])));
    assert.equal(values.ParamCleanPoseR, 1, 'the diagnostic snapshot never writes a support/control parameter');
    snapshot.ParamSkirt=99;
    assert.equal(harness.motionPhysicsValues.ParamSkirt, 0, 'capture mapping owns its data');
    authored = .025;
    MapleModel.prototype.sampleStartPose.call(harness);
    assert.equal(values.ParamSleeveLHang, -.08);
    assert.deepEqual(MapleModel.prototype.motionPhysicsSnapshot.call(harness), Object.fromEntries(outputs.map(name => [name,.025])));
  } finally { CubismFramework.getIdManager = original; }
});
