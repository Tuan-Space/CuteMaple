import type { MotionContext } from './protocol';
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
export function defaultContext(state: string, token = 0): MotionContext {
  const attached = state.includes('climb') || state.startsWith('swing_');
  const dragging = state.startsWith('drag_'), falling = state === 'fall_float';
  return {type: 'context', token, attached, dragging, falling, grounded: !attached && !dragging && !falling,
    vx: 0, vy: 0, visibleRect: [0, 0, 1, 1], effectsEnabled: true};
}
/** Independent, damped cloth responses. The authored roots and support contacts are never moved here. */
export class MotionResponse {
  context = defaultContext('idle');
  ribbons = [0, 0, 0, 0];
  private age = 0;
  private time = 0;
  set(context: MotionContext): void {
    this.context = {...context, vx: clamp(context.vx, -3, 3), vy: clamp(context.vy, -3, 3)};
    this.age = 0;
  }
  reset(state: string): void { this.context = defaultContext(state); this.ribbons.fill(0); this.age = this.time = 0; }
  update(dt: number): number[] {
    this.age += dt; this.time += dt;
    const fresh = this.age < .35;
    const x = fresh ? this.context.vx : 0, y = fresh ? this.context.vy : 0;
    const speed = Math.min(1, Math.hypot(x, y));
    const targets = [-x * .5 + Math.sin(this.time * 4.1) * speed * .035, -y * .55,
      -x * .6 + Math.sin(this.time * 3.6 + 1.4) * speed * .04, -y * .48];
    for (let i = 0; i < 4; i++) this.ribbons[i] += (clamp(targets[i], -1, 1) - this.ribbons[i]) * (1 - Math.exp(-dt / (i < 2 ? .24 : .31)));
    return this.ribbons;
  }
  bodyGazeAllowed(state: string): boolean {
    const c = this.context;
    return c.grounded && !c.attached && !c.dragging && !c.falling && (state === 'idle' || state.startsWith('walk_'));
  }
}
