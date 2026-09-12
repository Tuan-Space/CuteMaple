import test from 'node:test';
import assert from 'node:assert/strict';
import { pettingHeadDrawables, validateNativeIdentity } from '../src/native-contract';

const declared = ['face_base', 'hair_front', 'profile_l_face_base', 'profile_l_hair_front'];
const refinement = {motionPolishVersion:1, pettingHeadDrawables:declared, turn:{front:['face_base','torso']}};

test('new head declaration is versioned, excludes tails, and must name real native materials', () => {
  assert.deepEqual([...pettingHeadDrawables(refinement)], declared);
  assert.deepEqual([...pettingHeadDrawables({...refinement, motionPolishVersion:0}, 'face_base')], ['face_base']);
  assert.deepEqual([...pettingHeadDrawables({turn:{left:['profile_l_face_base','profile_l_torso']}}, 'face_base')],
    ['face_base','profile_l_face_base']);
  for (const values of [[], ['hair_back'], ['ribbon_l'], ['face_base','face_base'], ['torso']])
    assert.throws(() => pettingHeadDrawables({...refinement, pettingHeadDrawables:values}), /Invalid petting/);
  assert.doesNotThrow(() => validateNativeIdentity(refinement, [], declared));
  assert.throws(() => validateNativeIdentity(refinement, [], ['face_base']), /Missing native petting/);
});

test('production geometry includes visible fringe vertices, ignores hidden views and long trailing parts', async () => {
  // Call the actual production geometry method with small native-vertex arrays.
  // No MOC, Core process, renderer or authoring IR is loaded by this test.
  // SDK enum declarations read the Core namespace while importing this class;
  // geometry below uses only the supplied arrays and never calls that namespace.
  (globalThis as any).Live2DCubismCore = {};
  const { MapleModel } = await import('../src/model');
  const parts = [
    {id:'face_base', opacity:1, vertices:[.42,.16,.61,.38]},
    {id:'hair_front', opacity:1, vertices:[.31,.04,.71,.38]},
    {id:'profile_l_face_base', opacity:0, vertices:[-.3,-.4,.8,.9]},
    {id:'profile_l_hair_front', opacity:0, vertices:[-.5,-.6,.9,1]},
    {id:'hair_back', opacity:1, vertices:[.2,.03,.8,.9]},
    {id:'ribbon_l', opacity:1, vertices:[0,0,1,1]},
  ];
  const harness = {metadata:{refinement}, currentState:'swing_idle', parameters:new Set(),
    point:(x:number,y:number) => [x*.8+.1,y*.8+.1],
    _model:{getDrawableCount:() => parts.length, getDrawableOpacity:(i:number) => parts[i].opacity,
      getDrawableId:(i:number) => ({getString:() => parts[i].id}), getDrawableVertices:(i:number) => parts[i].vertices}};
  const geometry = () => MapleModel.prototype.geometry.call(harness as any);
  const box = geometry().headBounds!;
  const expected = [.31*.8+.1,.04*.8+.1,(.71-.31)*.8,(.38-.04)*.8];
  assert.ok(box.every((value, i) => Math.abs(value-expected[i]) < 1e-12));
  assert.ok(.1*.8+.1 >= box[1] && .1*.8+.1 < box[1]+box[3], 'top-of-head strokes are included');
  assert.ok(.8*.8+.1 > box[1]+box[3], 'the back hair tail is not a head hit');
  parts[0].opacity=0; parts[1].opacity=0; parts[2].opacity=1; parts[3].opacity=1;
  const turned = geometry().headBounds!;
  assert.ok(turned[0] < 0 && turned[1] < 0, 'the selected head follows the actual visible view and current vertices');
  harness.metadata.refinement = {...refinement, motionPolishVersion:0};
  assert.equal(geometry().headBounds, undefined, 'legacy fallback does not silently include declared new views');
});
