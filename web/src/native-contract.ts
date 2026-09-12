/** Compare real Core IDs before an unknown parameter could become a virtual parameter. */
export function validateNativeIdentity(refinement: unknown, parameters: string[], drawables: string[]): void {
  if (!refinement || typeof refinement !== 'object') return;
  const contract = refinement as Record<string, unknown>;
  if ((contract.nativeParameterIds === undefined) !== (contract.nativeDrawableIds === undefined))
    throw new Error('Invalid model identity contract: both native ID lists are required.');
  for (const [key, actual] of [['nativeParameterIds', parameters], ['nativeDrawableIds', drawables]] as const) {
    const expected = contract[key];
    if (expected === undefined) continue; // Older Maple metadata did not declare a complete identity.
    if (!Array.isArray(expected) || !expected.length || expected.some(x => typeof x !== 'string' || !x)
        || new Set(expected).size !== expected.length) throw new Error(`Invalid model identity contract: ${key}.`);
    const actualSet = new Set(actual);
    const expectedSet = new Set(expected);
    const missing = expected.filter(x => !actualSet.has(x));
    const extra = actual.filter(x => !expectedSet.has(x));
    if (actualSet.size !== actual.length || missing.length || extra.length)
      throw new Error(`MOC3 does not match model metadata (${key}); missing: ${missing.join(', ') || 'none'}; unexpected: ${extra.join(', ') || 'none'}.`);
  }
  if (contract.motionPolishVersion === 1 && contract.pettingHeadDrawables !== undefined) {
    for (const name of pettingHeadDrawables(contract))
      if (!drawables.includes(name)) throw new Error(`Missing native petting head material: ${name}.`);
  }
}

/** The head hit region follows visible painted face/fringe meshes, never trailing hair or ribbons. */
export function pettingHeadDrawables(refinement: Record<string, unknown> | undefined, headDrawable?: string): Set<string> {
  const declared = refinement?.pettingHeadDrawables;
  if (refinement?.motionPolishVersion === 1 && declared !== undefined) {
    if (!Array.isArray(declared) || !declared.length || new Set(declared).size !== declared.length
        || declared.some(name => typeof name !== 'string' || !/^(?:(?:profile|mid)_[lr]_)?(?:face_base|hair_front)$/.test(name)))
      throw new Error('Invalid petting head material declaration.');
    return new Set(declared);
  }
  const result = new Set<string>(headDrawable ? [headDrawable] : []);
  const turn = refinement?.turn as Record<string, unknown> | undefined;
  for (const value of Object.values(turn || {}))
    if (Array.isArray(value) && typeof value[0] === 'string' && value[0].includes('face')) result.add(value[0]);
  return result;
}

/** R5 loops add one source frame for interpolation back to the start pose.
 * A handoff stops at the authored end, before that corrective frame, using
 * the native queue's clock rather than host duration estimates or frame counts.
 */
export function secondsToAuthoredEndpoint(elapsed: number, duration: number, loopCorrection: number): number {
  if (![elapsed, duration, loopCorrection].every(Number.isFinite) || duration <= 0 || elapsed < 0 || loopCorrection < 0)
    throw new Error('Invalid native climb motion clock.');
  const period = duration + loopCorrection;
  const time = elapsed % period;
  if (Math.abs(time - duration) <= 1e-9) return 0;
  return time < duration ? duration - time : period - time + duration;
}
