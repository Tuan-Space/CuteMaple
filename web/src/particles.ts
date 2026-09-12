import type { EffectCommand, EffectName, Geometry, MotionContext } from './protocol';

export type Shape = 'leaf' | 'note' | 'star' | 'heart' | 'bubble' | 'petal' | 'sleep';
export interface Particle { shape: Shape; x: number; y: number; vx: number; vy: number;
  radius: number; age: number; life: number; angle: number; spin: number }
const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x));
const recipes: Record<EffectName, Shape[]> = {
  keyboard: ['leaf', 'star'], audio: ['note', 'bubble'], happy: ['heart', 'petal', 'star'],
  leaf: ['leaf'], note: ['note'], star: ['star'], heart: ['heart'], bubble: ['bubble'],
  petal: ['petal'], sleep: ['sleep'],
};

/** Bounded simulation in viewport coordinates; has no DOM or input-provider dependencies. */
export class ParticleField {
  static readonly MAX = 24;
  particles: Particle[] = [];
  private time = 0;
  private emitted = new Map<string, number>();
  private token?: number;
  private paused = false;
  private enabled = true;
  private rect: [number, number, number, number] = [0, 0, 1, 1];
  constructor(private random: () => number = Math.random) {}
  play(token: number): void { this.token = token; this.clear(); this.rect = [0, 0, 1, 1]; }
  pause(value: boolean): void { this.paused = value; if (value) this.clear(); }
  context(value: MotionContext): void {
    if (value.token !== this.token) return;
    this.enabled = value.effectsEnabled;
    const [x, y, w, h] = value.visibleRect;
    const left = clamp(x, 0, 1), top = clamp(y, 0, 1);
    this.rect = [left, top, Math.max(0, clamp(x + w, left, 1) - left), Math.max(0, clamp(y + h, top, 1) - top)];
    if (!this.enabled || Math.min(this.rect[2], this.rect[3]) < .15) this.clear();
  }
  clear(): void { this.particles = []; this.emitted.clear(); }
  emit(command: EffectCommand, geometry: Geometry): boolean {
    if (!(command.name in recipes)) return false;
    if (command.token !== this.token || this.paused || !this.enabled) return false;
    const interval = command.name === 'keyboard' ? .4 : command.name === 'audio' ? .45 : .16;
    if (this.time - (this.emitted.get(command.name) ?? -Infinity) < interval) return false;
    const [left, top, width, height] = this.rect;
    if (Math.min(width, height) < .15 || this.particles.length >= ParticleField.MAX) return false;
    const source = command.x === undefined ? geometry.anchors.head || [.5, .3] : [command.x, command.y!];
    if (!source.every(Number.isFinite)) return false;
    this.emitted.set(command.name, this.time);
    const intensity = clamp(command.intensity ?? 1, 0, 1);
    if (intensity === 0) return false;
    for (const shape of recipes[command.name]) {
      if (this.particles.length >= ParticleField.MAX) break;
      const radius = (.028 + this.random() * .012) * (.7 + .3 * intensity);
      const margin = radius + .025; // Include outline and soft highlight, not just the path center.
      const side = this.particles.length % 2 ? 1 : -1;
      const tier = (Math.floor(this.particles.length / 2) % 3 - 1) * .085;
      const x = clamp(source[0] + (side * .2) + (this.random() - .5) * .06, left + margin, left + width - margin);
      const y = clamp(source[1] + (tier - .01), top + margin, top + height - margin);
      // Steer toward free screen space. Subsequent boundary reflection keeps the whole glyph visible.
      const sx = x - left < .18 ? 1 : left + width - x < .18 ? -1 : side;
      const sy = y - top < .14 ? 1 : -1;
      this.particles.push({shape, x, y, radius, age: 0, life: 1.5 + this.random() * .5,
        vx: sx * (.055 + this.random() * .055),
        vy: sy * (.06 + this.random() * .035),
        angle: (this.random() - .5) * .6, spin: (this.random() - .5) * .7});
    }
    return true;
  }
  update(delta: number): void {
    if (this.paused) return;
    const dt = clamp(Number.isFinite(delta) ? delta : 0, 0, .1);
    this.time += dt;
    const [x, y, w, h] = this.rect;
    for (const p of this.particles) {
      const margin = p.radius + .025;
      p.age += dt; p.x += p.vx * dt; p.y += p.vy * dt; p.angle += p.spin * dt;
      if (p.x < x + margin || p.x > x + w - margin) p.vx *= -1;
      if (p.y < y + margin || p.y > y + h - margin) p.vy *= -1;
      p.x = clamp(p.x, x + margin, x + w - margin);
      p.y = clamp(p.y, y + margin, y + h - margin);
    }
    this.particles = this.particles.filter(p => p.age < p.life);
  }
}

/** A separate DOM surface above WebGL, without a native child-window stacking dependency. */
export class ParticleCanvas {
  readonly field = new ParticleField();
  private drawnCount = 0;
  constructor(private canvas: HTMLCanvasElement) {}
  draw(): void {
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight;
    const ratio = Math.max(1, Math.min(window.devicePixelRatio || 1, 3));
    if (this.canvas.width !== Math.round(width * ratio)) this.canvas.width = Math.round(width * ratio);
    if (this.canvas.height !== Math.round(height * ratio)) this.canvas.height = Math.round(height * ratio);
    const c = this.canvas.getContext('2d');
    if (!c) return;
    c.setTransform(ratio, 0, 0, ratio, 0, 0); c.clearRect(0, 0, width, height);
    this.drawnCount = 0;
    for (const p of this.field.particles) {
      c.save(); c.translate(p.x * width, p.y * height); c.rotate(p.angle);
      const r = p.radius * Math.min(width, height);
      c.globalAlpha = Math.min(1, (1 - p.age / p.life) * 2);
      c.fillStyle = p.shape === 'note' ? '#a67bca' : ['heart', 'petal'].includes(p.shape) ? '#ed9aac' : '#efc472';
      c.strokeStyle = 'rgba(113,79,66,.85)'; c.lineWidth = Math.max(.8, r * .1);
      c.shadowColor = 'rgba(255,255,255,.9)'; c.shadowBlur = 2;
      c.beginPath();
      if (p.shape === 'star' || p.shape === 'leaf') {
        const count = p.shape === 'star' ? 10 : 14;
        for (let i = 0; i < count; i++) { const a = i * Math.PI * 2 / count - Math.PI / 2;
          const s = i % 2 ? .42 : 1; const x = Math.cos(a) * r * s, y = Math.sin(a) * r * s;
          i ? c.lineTo(x, y) : c.moveTo(x, y); }
        c.closePath(); c.fill(); c.stroke();
      } else if (p.shape === 'heart') {
        c.moveTo(0, r * .8); c.bezierCurveTo(-r * 1.5, -r * .1, -r * .6, -r * 1.3, 0, -r * .45);
        c.bezierCurveTo(r * .6, -r * 1.3, r * 1.5, -r * .1, 0, r * .8); c.fill(); c.stroke();
      } else if (p.shape === 'note') {
        c.ellipse(-r * .3, r * .45, r * .35, r * .22, -.3, 0, Math.PI * 2); c.fill();
        c.moveTo(0, r * .4); c.lineTo(0, -r * .8); c.lineTo(r * .6, -r * .5); c.stroke();
      } else if (p.shape === 'sleep') {
        c.font = `${Math.max(9, r * 1.8)}px sans-serif`; c.fillText('z', -r * .5, r * .5);
      } else {
        c.ellipse(0, 0, r, r * (p.shape === 'petal' ? .5 : 1), -.5, 0, Math.PI * 2);
        if (p.shape === 'bubble') { c.fillStyle = 'rgba(188,223,239,.35)'; c.fill(); c.strokeStyle = '#91bfd2'; c.stroke(); }
        else { c.fill(); c.stroke(); }
      }
      c.restore();
      this.drawnCount++;
    }
  }
  clear(): void { this.field.clear(); this.draw(); }
  capture(): Record<string, unknown> {
    this.draw();
    const width = this.canvas.width, height = this.canvas.height;
    const pixels = this.canvas.getContext('2d')?.getImageData(0, 0, width, height).data;
    let visiblePixels = 0, left = width, top = height, right = -1, bottom = -1;
    if (pixels) for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 8) {
      const position = (i - 3) / 4, x = position % width, y = Math.floor(position / width);
      visiblePixels++; left = Math.min(left, x); right = Math.max(right, x); top = Math.min(top, y); bottom = Math.max(bottom, y);
    }
    return {count: this.field.particles.length, kinds: [...new Set(this.field.particles.map(p => p.shape))],
      drawnCount: this.drawnCount, visiblePixels,
      pixelBounds: visiblePixels ? [left / width, top / height, (right - left + 1) / width, (bottom - top + 1) / height] : [0, 0, 0, 0],
      particles: this.field.particles.map(p => ({kind: p.shape, x: p.x, y: p.y, radius: p.radius}))};
  }
}
