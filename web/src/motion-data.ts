/** Detect real authored eyelid closure, ignoring constant-open baseline curves. */
export function controlsBlink(curves: { Id: string; Segments?: number[] }[]): boolean {
  for (const curve of curves) {
    if (!['ParamEyeLOpen', 'ParamEyeROpen', 'EyeBlink'].includes(curve.Id)) continue;
    const segments = curve.Segments || [];
    if (segments.length < 2) continue;
    if (Math.abs(segments[1] - 1) > 1e-5) return true;
    for (let index = 2; index < segments.length;) {
      const kind = segments[index];
      const points = kind === 1 ? 3 : 1;
      for (let point = 0; point < points; point++)
        if (Math.abs(segments[index + 2 + point * 2] - 1) > 1e-5) return true;
      index += 1 + points * 2;
    }
  }
  return false;
}
/** Attach callbacks to this motion instance, so fading predecessors keep their own token. */
export function bindMotionMarkers(motion: { getFiredEvent(before: number, now: number): string[] },
  marker?: (name: string) => void): void {
  const firedEvents = motion.getFiredEvent.bind(motion);
  motion.getFiredEvent = (before, now) => {
    const events = firedEvents(before, now);
    for (const name of events) {
      if (['top_grab', 'wall_release', 'settled'].includes(name)) marker?.(name);
    }
    return events;
  };
}
