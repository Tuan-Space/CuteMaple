import test from 'node:test';
import assert from 'node:assert/strict';
import { resolvePointAnchor, validatePointAnchor, bindMaterialAnchor, resolveMaterialAnchor,
  type PointAnchor } from '../src/contact-anchor';

test('a pose contact remains continuous when the sleeping artwork disappears', () => {
  const anchor: PointAnchor = {point: [.5, .935], parameter: 'ParamPoseSleep',
    points: [[0, .5, .935], [.82, .5, .935], [.92, .5, .945], [1, .5, .95]]};
  validatePointAnchor(anchor, new Set(['ParamPoseSleep']));
  const entering = Array.from({length: 101}, (_, i) => resolvePointAnchor(anchor, i/100));
  const waking = Array.from({length: 101}, (_, i) => resolvePointAnchor(anchor, 1-i/100));
  entering.forEach((point, i) => {
    assert.ok(Math.abs(point[1]-waking[100-i][1]) < 1e-12);
    if (i) assert.ok(Math.abs(point[1]-entering[i-1][1]) <= .00101);
  });
  assert.deepEqual(resolvePointAnchor(anchor, -1), [.5, .935]);
  assert.deepEqual(resolvePointAnchor(anchor, 2), [.5, .95]);
  // These are synthetic contract fixtures, not a measured model contact table.
});

test('pose contact curves reject unknown parameters, malformed or unordered data', () => {
  const base: PointAnchor = {point: [.5, .9], parameter: 'ParamPoseSleep', points: [[0, .5, .9], [1, .5, .95]]};
  for (const mutation of [ {...base, parameter: 'missing'}, {...base, points: undefined},
    {...base, points: [[0, .5, .9], [0, .5, .95]]}, {...base, points: [[0, .5, .9], [1, .5, NaN]]} ])
    assert.throws(() => validatePointAnchor(mutation as PointAnchor, new Set(['ParamPoseSleep'])));
  assert.throws(() => resolvePointAnchor(base, NaN));
  assert.deepEqual(resolvePointAnchor({point: [.5, .9]}), [.5, .9]);
});

test('a painted material contact follows native vertices through nonrigid motion', () => {
  // The first triangle is deliberately unrelated; topology and UVs select the second.
  const uvs = new Float32Array([.5,.5, 1,.5, .5,1, 0,0, .25,0, 0,.25]);
  const binding = bindMaterialAnchor([.0625,.125], uvs, new Uint16Array([0,1,2, 3,4,5]));
  assert.deepEqual(binding, {vertices:[3,4,5], weights:[.25,.25,.5]});
  const positions = new Float32Array([99,99, 99,99, 99,99, 2,4, 6,4, 2,10]);
  assert.deepEqual(resolveMaterialAnchor(binding, positions), [3,7]);
  // Move only the selected corner. No nearest-edge or nearest-vertex switching occurs.
  positions[10] = 8;
  positions[11] = -2;
  assert.deepEqual(resolveMaterialAnchor(binding, positions), [6,1]);
});

test('material binding accepts reversed winding and a shared triangle boundary', () => {
  const uvs = [0,0, 1,0, 0,1, 1,1];
  for (const indices of [[2,1,0, 1,2,3], [0,1,2, 3,2,1]]) {
    const binding = bindMaterialAnchor([.5,.5], uvs, indices);
    assert.deepEqual(resolveMaterialAnchor(binding, [0,0, 2,0, 0,2, 2,2]), [1,1]);
  }
});

test('material binding does not infer a contact outside valid native triangles', () => {
  const uvs = [0,0, 1,0, 0,1];
  for (const point of [[.9,.9], [-.01,0], [0,Infinity], [NaN,0]])
    assert.throws(() => bindMaterialAnchor(point as [number,number], uvs, [0,1,2]));
  for (const indices of [[0,1], [0,1,3], [-1,1,2], [0,1.5,2], [0,0,0]])
    assert.throws(() => bindMaterialAnchor([.1,.1], uvs, indices));
  assert.throws(() => bindMaterialAnchor([.1,.1], [0,0,NaN,0,0,1], [0,1,2]));
  const binding = bindMaterialAnchor([.1,.1], uvs, [0,1,2]);
  assert.throws(() => resolveMaterialAnchor(binding, [0,0]));
  assert.throws(() => resolveMaterialAnchor(binding, [0,0,1,NaN,0,1]));
});
