/** One continuous clock for the seated swing, independent of Cubism's loop padding. */
export const SWING_ENVELOPE_SECONDS = 1.2;
export const SWING_PETTING_SECONDS = 3;
export const SWING_IDLE_AMPLITUDE = .1;
export const SWING_PETTING_NAMES = ['swing_petting_left', 'swing_petting_right'] as const;
export const isSwingFamily = (name: string): boolean =>
  ['swing_cycle', 'swing_idle'].includes(name);
const smooth = (v: number): number => { const t = Math.max(0, Math.min(1, v)); return t * t * (3 - 2 * t); };

/** Cubic Hermite envelope retains the current value AND velocity when interrupted. */
class Envelope {
  private age = SWING_ENVELOPE_SECONDS;
  private start: number;
  private speed = 0;
  private target: number;
  constructor(value: number) { this.start = this.target = value; }
  value(at = this.age): {value: number; speed: number} {
    if (at >= SWING_ENVELOPE_SECONDS) return {value: this.target, speed: 0};
    const d = SWING_ENVELOPE_SECONDS, t = Math.min(at / d, 1), delta = this.target - this.start;
    return {value: this.start + delta * (3*t*t - 2*t*t*t) + this.speed*d*(t*t*t - 2*t*t + t),
      speed: at >= d ? 0 : delta*(6*t - 6*t*t)/d + this.speed*(3*t*t - 4*t + 1)};
  }
  set(target: number): void {
    if (target === this.target) return;
    const current = this.value();
    this.start = current.value; this.speed = current.speed; this.target = target; this.age = 0;
  }
  private integral(at: number): number {
    const d = SWING_ENVELOPE_SECONDS, t = Math.min(at / d, 1);
    return d*(this.start*t + (this.target-this.start)*(t*t*t-t*t*t*t/2)
      + this.speed*d*(t*t*t*t/4-2*t*t*t/3+t*t/2)) + Math.max(0, at-d)*this.target;
  }
  advance(dt: number): number {
    const before = this.integral(this.age); this.age += dt;
    return this.integral(this.age) - before;
  }
}

export class SwingMotion {
  private state = '';
  private phase = 0;
  private origin = 0;
  private cycles = 0;
  private amplitude = new Envelope(0);
  private frequency = new Envelope(1 / 3.87);
  private petAge = SWING_PETTING_SECONDS;
  private petSide = 1;
  get active(): boolean { return isSwingFamily(this.state); }
  get ownsCycles(): boolean { return this.state === 'swing_cycle' || this.state === 'swing_idle'; }
  get pettingWeight(): number {
    return smooth(this.petAge/.3) * (1-smooth((this.petAge-2.4)/.6));
  }
  enter(state: string): void {
    const wasActive = this.active;
    this.state = state; this.cancelPetting();
    if (!this.active) { this.phase = 0; return; }
    const period = state === 'swing_cycle' ? 1.02 : 3.87;
    if (!wasActive) {
      this.phase = 0; this.amplitude = new Envelope(0); this.frequency = new Envelope(1/period);
    }
    this.amplitude.set(state === 'swing_cycle' ? 1 : SWING_IDLE_AMPLITUDE);
    this.frequency.set(1/period);
    this.origin = this.phase; this.cycles = 0;
  }
  advance(dt: number): number {
    if (!this.active) return 0;
    if (!Number.isFinite(dt) || dt < 0) throw new Error('Invalid swing time step.');
    // Split at the exact overlay deadline so a long frame does not spend time
    // past expiry under the gentle target or change the phase integral by FPS.
    const petting = this.petAge < SWING_PETTING_SECONDS;
    const first = petting ? Math.min(dt, SWING_PETTING_SECONDS-this.petAge) : dt;
    this.phase += Math.PI*2*this.frequency.advance(first);
    this.amplitude.advance(first);
    this.petAge = Math.min(SWING_PETTING_SECONDS, this.petAge+first);
    if (petting && this.petAge >= SWING_PETTING_SECONDS) this.restoreTargets();
    if (dt > first) {
      this.phase += Math.PI*2*this.frequency.advance(dt-first);
      this.amplitude.advance(dt-first);
    }
    const cycles = Math.floor((this.phase-this.origin)/(Math.PI*2)+1e-10);
    const crossed = cycles-this.cycles; this.cycles = cycles;
    return this.ownsCycles ? crossed : 0;
  }
  pet(name: string, active: boolean): void {
    if (!active) { this.cancelPetting(); return; }
    if (!this.ownsCycles || this.petAge < SWING_PETTING_SECONDS) return;
    this.petSide = name === 'swing_petting_left' ? -1 : 1; this.petAge = 0;
    this.amplitude.set(SWING_IDLE_AMPLITUDE); this.frequency.set(1/3.87);
  }
  cancelPetting(): boolean {
    const active = this.petAge < SWING_PETTING_SECONDS; this.petAge = SWING_PETTING_SECONDS;
    if (active) this.restoreTargets();
    return active;
  }
  private restoreTargets(): void {
    this.amplitude.set(this.state === 'swing_cycle' ? 1 : SWING_IDLE_AMPLITUDE);
    this.frequency.set(1/(this.state === 'swing_cycle' ? 1.02 : 3.87));
  }
  pose(): {swing: number; headZ: number; legA: number} {
    const swing = this.amplitude.value().value*Math.sin(this.phase);
    return {swing, headZ: -3*swing, legA: 4*swing};
  }
  petting(): {weight: number; headY: number; headZ: number} {
    const weight = this.pettingWeight;
    return {weight, headY: weight*(-2+.8*Math.sin(this.petAge*Math.PI*2/1.2)),
      headZ: weight*this.petSide*(2+3*Math.sin(this.petAge*Math.PI*2/1.2))};
  }
  diagnostics(): Record<string, number> {
    return {swingPhase: this.phase, swingAmplitude: this.amplitude.value().value,
      swingFrequency: this.frequency.value().value, swingActive: +this.active,
      swingPettingAge: this.petAge, swingPettingWeight: this.pettingWeight};
  }
}
