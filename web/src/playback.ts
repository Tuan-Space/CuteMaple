import type { ClimbControlCommand, ClimbEndpointCommand, Command, EffectCommand, Geometry, MotionContext, PlayCommand, Reporter } from './protocol';
import { SWING_PETTING_NAMES } from './swing-motion';
import { ParticleField } from './particles';

export interface ModelDriver {
  states: string[];
  readonly motionPolishEnabled?: boolean;
  interruptPetting?(): void;
  play(command: PlayCommand, nativeBoundary: () => void, marker?: (name: string) => void, staticStart?: boolean): void;
  sampleStartPose?(): void;
  climbEndpointSeconds?(): number | undefined;
  setClimbHold?(held: boolean): void;
  updateClimbHold?(seconds: number): void;
  context?(value: MotionContext): void;
  diagnostics?(): Record<string, number>;
  update(seconds: number): void;
  draw(): void;
  gaze(x: number, y: number, enabled: boolean): void;
  expression(name: string, active: boolean, intensity: number): void;
  geometry(): Geometry;
  dispose(): void;
}

/** Boundaries come from the model's native motion or continuous swing clock. */
export class Playback {
  private current?: PlayCommand;
  private cycle = 0;
  private revision = 0;
  private lastTime?: number;
  private geometryElapsed = 0;
  private paused = false;
  private sampling = false;
  private evaluated = false;
  private pendingEffects: EffectCommand[] = [];
  private boundaryEndpointPending = false;
  private endpointRequest?: ClimbEndpointCommand;
  private endpointHeld = false;
  private latestEndpointRequest = 0;
  private climbRun = 0;
  private climbRemaining = 0;
  private climbResting = false;
  private passedClimbEndpoint = false;

  constructor(readonly driver: ModelDriver, private report: Reporter, readonly particles?: ParticleField) {}
  diagnostics(): Record<string, unknown> { return {paused: this.paused, token: this.current?.token,
    name: this.current?.name, cycle: this.cycle, evaluated: this.evaluated,
    climbEndpointHeld: this.endpointHeld, climbRun: this.climbRun,
    climbResting: this.climbResting, climbRemaining: this.climbRemaining, ...this.driver.diagnostics?.()}; }

  play(command: PlayCommand): void {
    if (!this.driver.states.includes(command.name)) throw new Error(`Missing Live2D motion: ${command.name}`);
    const endpointHandoff = this.endpointHeld && (command.name === this.current?.name.replace('climb_', 'climb_to_top_')
      || command.name === `clean_${this.current?.name}_enter`);
    const staticStart = this.paused || endpointHandoff ||
      (this.driver.motionPolishEnabled === true && ['climb_left','climb_right'].includes(command.name));
    const revision = ++this.revision;
    this.current = { ...command };
    this.cycle = 0;
    this.boundaryEndpointPending = false;
    this.endpointRequest = undefined; this.endpointHeld = false; this.latestEndpointRequest = 0;
    this.climbRun = 0; this.climbRemaining = 0; this.climbResting = false; this.passedClimbEndpoint = false;
    this.driver.setClimbHold?.(false);
    this.lastTime = undefined;
    this.evaluated = false;
    this.pendingEffects = [];
    this.particles?.play(command.token);
    this.driver.play(command, () => {
      // Fade-out motions can finish after an interrupt. Never advance the new state with that event.
      if (revision !== this.revision || this.sampling || this.paused) return;
      this.cycle += 1;
      this.boundaryEndpointPending = true;
      this.report({ type: command.playback === 'one_shot' ? 'finished' : 'cycle',
        token: command.token, name: command.name, cycle: this.cycle });
    }, marker => {
      if (revision === this.revision && !this.sampling && !this.paused) this.report({ type: "marker", token: command.token, name: command.name, marker });
    }, staticStart);
    if (staticStart) {
      if (!this.driver.sampleStartPose) throw new Error('Model does not support static motion sampling.');
      this.sampling = true;
      try { this.driver.sampleStartPose(); this.evaluated = true; this.driver.draw(); }
      finally { this.sampling = false; }
      this.sendGeometry();
    }
  }

  pause(value: boolean): void {
    this.paused = value; this.lastTime = undefined; this.particles?.pause(value);
    if (value) this.driver.interruptPetting?.();
    if (value) { this.pendingEffects = []; this.endpointRequest = undefined; this.endpointHeld = false; }
  }
  climbEndpoint(command: ClimbEndpointCommand): void {
    if (command.token !== this.current?.token || command.name !== this.current?.name) return;
    if (!command.enabled) {
      if (command.request === this.endpointRequest?.request) {
        this.endpointRequest = undefined; this.endpointHeld = false; this.lastTime = undefined;
      }
      return;
    }
    if (this.paused || command.request <= this.latestEndpointRequest) return;
    if (!this.driver.climbEndpointSeconds) throw new Error('Native climb endpoint sampling is unavailable.');
    this.latestEndpointRequest = command.request;
    this.endpointRequest = { ...command };
  }
  climbControl(command: ClimbControlCommand): void {
    if (command.token !== this.current?.token || command.name !== this.current?.name || command.run < this.climbRun) return;
    if (!this.driver.setClimbHold || !this.driver.updateClimbHold) throw new Error('Climb rest is unavailable.');
    if (command.run > this.climbRun) {
      this.climbRun = command.run;
      this.climbRemaining = 2;
    }
    if (command.resting === this.climbResting && this.evaluated) return;
    this.climbResting = command.resting;
    this.lastTime = undefined;
    if (!this.evaluated) {
      if (!this.driver.sampleStartPose) throw new Error('Climb rest needs native sampling.');
      this.sampling = true;
      try { this.driver.sampleStartPose(); this.evaluated = true; }
      finally { this.sampling = false; }
    }
    this.driver.setClimbHold(command.resting);
    this.sendGeometry();
  }
  context(value: MotionContext): void {
    if (value.token !== this.current?.token) return;
    this.driver.context?.(value); this.particles?.context(value);
  }
  expression(command: Extract<Command, {type: 'expression'}>): void {
    if ((SWING_PETTING_NAMES as readonly string[]).includes(command.name)
      && (!this.driver.motionPolishEnabled || command.token !== this.current?.token || this.paused)) return;
    this.driver.expression(command.name, command.active === true,
      Number.isFinite(command.intensity) ? command.intensity! : 1);
  }
  effect(value: EffectCommand): void {
    if (this.paused || value.token !== this.current?.token) return;
    if (!this.evaluated) { if (this.pendingEffects.length < 4) this.pendingEffects.push(value); return; }
    this.particles?.emit(value, this.driver.geometry());
  }

  tick(milliseconds: number): void {
    if (this.lastTime === undefined) this.lastTime = milliseconds;
    const elapsed = Math.max(0, (milliseconds - this.lastTime) / 1000);
    this.lastTime = milliseconds;
    if (!this.paused && this.climbResting && !this.endpointHeld && elapsed > 0) {
      this.driver.updateClimbHold?.(Math.min(elapsed, .25));
      this.particles?.update(Math.min(elapsed, .1));
      this.geometryElapsed += elapsed;
      if (this.geometryElapsed >= 1/30) { this.geometryElapsed %= 1/30; this.sendGeometry(); }
    } else if (!this.paused && !this.endpointHeld && elapsed > 0) {
      // A suspended window does not jump across many actions when it resumes.
      // Split short slow frames so native physics and loop callbacks see stable time steps.
      let remaining = Math.min(elapsed, 0.25);
      let advanced = 0;
      while (remaining > 1e-8) {
        const boundedClimb = this.climbRun > 0 && this.climbRemaining > 0;
        const toEndpoint = (this.endpointRequest || (boundedClimb && !this.passedClimbEndpoint))
          ? this.driver.climbEndpointSeconds?.() : undefined;
        const step = Math.min(remaining, 1 / 60, toEndpoint ?? Infinity);
        this.driver.update(step);
        this.evaluated = true;
        advanced += step;
        remaining -= step;
        if (this.passedClimbEndpoint && (this.driver.geometry().climbPhase ?? 1) < 1-1e-5)
          this.passedClimbEndpoint = false;
        if (toEndpoint !== undefined && toEndpoint <= step + 1e-9) {
          const phase = this.driver.geometry().climbPhase;
          if (!Number.isFinite(phase) || Math.abs(phase - 1) > 1e-5)
            throw new Error(`Native climb did not evaluate its authored endpoint (phase=${phase}).`);
          // Core.update has completed. Hold this exact pose until a matching
          // host handoff; never spend the rest of this rAF inside the next loop.
          if (this.endpointRequest) {
            this.endpointHeld = true;
            this.sendGeometry(this.endpointRequest.request);
            break;
          }
          this.passedClimbEndpoint = true;
          this.climbRemaining--;
          this.sendGeometry();
          if (this.climbRemaining === 0) {
            this.climbResting = true;
            this.driver.setClimbHold?.(true);
            this.report({type:'climb-rest', token:this.current?.token, name:this.current?.name,
              run:this.climbRun, cycles:2});
            break;
          }
        }
      }
      this.particles?.update(Math.min(advanced, .1));
      for (const effect of this.pendingEffects) this.effect(effect);
      this.pendingEffects = [];
      this.geometryElapsed += elapsed;
      const name = this.current?.name || '';
      const preciseContact = name.startsWith('climb_') || ['sleep_enter', 'sleep_exit', 'land'].includes(name);
      const interval = preciseContact ? 1 / 60 : 1 / 15;
      if (this.geometryElapsed >= interval && !this.endpointHeld) { this.geometryElapsed %= interval; this.sendGeometry(); }
    }
    this.driver.draw();
  }

  sendGeometry(endpointRequest?: number): void {
    if (!this.current || !this.evaluated) return;
    const geometry = this.driver.geometry();
    // R5 may fire a loop callback while Core still holds that loop's endpoint.
    // Keep the endpoint paired with its old cycle until the first wrapped sample.
    const endpoint = Number.isFinite(geometry.climbPhase) && geometry.climbPhase >= 1 - 1e-5;
    const climbCycle = this.boundaryEndpointPending && endpoint ? Math.max(0, this.cycle - 1) : this.cycle;
    if (!endpoint) this.boundaryEndpointPending = false;
    this.report({ type: 'geometry', token: this.current.token, name: this.current.name, ...geometry, climbCycle,
      climbResting:this.climbResting, climbRun:this.climbRun, climbRemaining:this.climbRemaining,
      ...(endpointRequest === undefined ? {} : { climbEndpoint: { request: endpointRequest, cycle: climbCycle } }) });
  }

  dispose(): void { ++this.revision; this.particles?.clear(); this.driver.dispose(); }
}
