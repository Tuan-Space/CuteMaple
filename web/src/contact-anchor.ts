import type { Point } from './protocol';

export interface PointAnchor {
  point: Point;
  parameter?: string;
  points?: [number, number, number][];
}

export interface MaterialAnchorBinding {
  vertices: [number, number, number];
  weights: [number, number, number];
}

/** Bind once to native atlas topology; later frames follow the same painted point. */
export function bindMaterialAnchor(atlasUv: Point, uvs: ArrayLike<number>,
  indices: ArrayLike<number>): MaterialAnchorBinding {
  if (!Array.isArray(atlasUv) || atlasUv.length !== 2
    || !atlasUv.every(value => Number.isFinite(value) && value >= 0 && value <= 1)
    || uvs.length < 6 || uvs.length % 2 || !indices.length || indices.length % 3)
    throw new Error('Invalid material contact or native topology.');
  for (let i = 0; i < uvs.length; i++)
    if (!Number.isFinite(uvs[i])) throw new Error('Nonfinite native contact UV.');
  for (let i = 0; i < indices.length; i++)
    if (!Number.isInteger(indices[i]) || indices[i] < 0 || indices[i] * 2 + 1 >= uvs.length)
      throw new Error('Invalid native contact triangle.');
  for (let i = 0; i < indices.length; i += 3) {
    const a = indices[i], b = indices[i+1], c = indices[i+2];
    const bx = uvs[b*2]-uvs[a*2], by = uvs[b*2+1]-uvs[a*2+1];
    const cx = uvs[c*2]-uvs[a*2], cy = uvs[c*2+1]-uvs[a*2+1];
    const determinant = bx*cy-by*cx;
    if (Math.abs(determinant) < 1e-14) continue;
    const x = atlasUv[0]-uvs[a*2], y = atlasUv[1]-uvs[a*2+1];
    const v = (x*cy-y*cx)/determinant, w = (bx*y-by*x)/determinant;
    const weights: [number, number, number] = [1-v-w, v, w];
    if (weights.some(weight => weight < -1e-7 || weight > 1+1e-7)) continue;
    // Tolerate only floating-point boundary noise, never extrapolate a contact.
    const positive = weights.map(weight => Math.max(0, weight));
    const total = positive.reduce((sum, weight) => sum+weight, 0);
    return {vertices: [a, b, c], weights: positive.map(weight => weight/total) as typeof weights};
  }
  throw new Error('Material contact lies outside native drawable UV triangles.');
}

/** Native positions, without fitted-canvas conversion or nearest-edge resampling. */
export function resolveMaterialAnchor(binding: MaterialAnchorBinding, vertices: ArrayLike<number>): Point {
  const result: Point = [0, 0];
  for (let i = 0; i < 3; i++) {
    const index = binding.vertices[i]*2;
    if (index+1 >= vertices.length || !Number.isFinite(vertices[index]) || !Number.isFinite(vertices[index+1]))
      throw new Error('Native material contact position is unavailable.');
    result[0] += vertices[index]*binding.weights[i];
    result[1] += vertices[index+1]*binding.weights[i];
  }
  return result;
}

/** A contact plane authored together with the pose, independent of hidden art. */
export function validatePointAnchor(anchor: PointAnchor, parameters: ReadonlySet<string>): void {
  if (!Array.isArray(anchor.point) || anchor.point.length !== 2 || !anchor.point.every(Number.isFinite))
    throw new Error('Invalid authored contact point.');
  if (anchor.parameter === undefined && anchor.points === undefined) return;
  if (typeof anchor.parameter !== 'string' || !parameters.has(anchor.parameter)
    || !Array.isArray(anchor.points) || anchor.points.length < 2
    || anchor.points.some((row, i, rows) => !Array.isArray(row) || row.length !== 3
      || !row.every(Number.isFinite) || (i > 0 && row[0] <= rows[i - 1][0])))
    throw new Error('Invalid pose contact curve.');
}

export function resolvePointAnchor(anchor: PointAnchor, parameterValue?: number): Point {
  if (!anchor.points) return [...anchor.point];
  if (!Number.isFinite(parameterValue)) throw new Error('Contact pose parameter is unavailable.');
  const points = anchor.points, value = parameterValue!;
  if (value <= points[0][0]) return [points[0][1], points[0][2]];
  const last = points[points.length - 1];
  if (value >= last[0]) return [last[1], last[2]];
  for (let i = 1; i < points.length; i++) {
    const a = points[i-1], b = points[i];
    if (value <= b[0]) {
      const weight = (value-a[0])/(b[0]-a[0]);
      return [a[1]+weight*(b[1]-a[1]), a[2]+weight*(b[2]-a[2])];
    }
  }
  throw new Error('Invalid contact curve interval.');
}
