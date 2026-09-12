import { CubismModelSettingJson } from '@framework/cubismmodelsettingjson';
import { CubismFramework } from '@framework/live2dcubismframework';
import { CubismUserModel } from '@framework/model/cubismusermodel';
import { CubismMatrix44 } from '@framework/math/cubismmatrix44';
import { CubismMotion, MotionBehavior } from '@framework/motion/cubismmotion';
import { CubismMotionQueueEntry } from '@framework/motion/cubismmotionqueueentry';
import { CubismUpdateScheduler } from '@framework/motion/cubismupdatescheduler';
import { CubismPhysicsUpdater } from '@framework/motion/cubismphysicsupdater';
import { CubismPoseUpdater } from '@framework/motion/cubismposeupdater';
import { ICubismUpdater } from '@framework/motion/icubismupdater';
import { CubismModel } from '@framework/model/cubismmodel';
import { CubismEyeBlink } from '@framework/effect/cubismeyeblink';
import { CubismShaderManager_WebGL } from '@framework/rendering/cubismshader_webgl';
import { CubismIdHandle } from '@framework/id/cubismid';
import type { Geometry, PlayCommand, Point, MotionContext } from './protocol';
import { MotionResponse } from './motion-context';
import { localAssetUrl } from './protocol';
import type { ModelDriver } from './playback';
import { controlsBlink, bindMotionMarkers } from './motion-data';
import { resolvePointAnchor, validatePointAnchor, bindMaterialAnchor, resolveMaterialAnchor,
  type PointAnchor, type MaterialAnchorBinding } from './contact-anchor';
import { secondsToAuthoredEndpoint, validateNativeIdentity, pettingHeadDrawables } from './native-contract';
import { SwingMotion, SWING_PETTING_NAMES } from './swing-motion';
import { physicsOutputParameters } from './native-diagnostics';

type Binding = { id: string; value: number; blend?: 'Add' | 'Multiply' | 'Overwrite' };
type DrawableAnchor = { drawable: string; vertex?: number; atlasUv?: Point;
  edge?: 'top' | 'bottom' | 'left' | 'right' };
type Anchor = PointAnchor | DrawableAnchor;
type AnchorMap = Record<string, Anchor>;
interface Metadata {
  refinement?: Record<string, unknown>;
  locomotion?: Record<string, unknown>;
  anchors?: AnchorMap;
  states?: Record<string, { anchors?: AnchorMap; disableBlink?: boolean; disableGaze?: boolean }>;
  expressions?: Record<string, Binding[]>;
  parameters?: Partial<Record<'eyeX' | 'eyeY' | 'headX' | 'headY' | 'headZ' | 'bodyZ' | 'breath', string>>;
}
interface MotionResource { bytes: ArrayBuffer; index: number; hasEyeCurves: boolean }

const clamp = (value: number, low = -1, high = 1) => Math.max(low, Math.min(high, value));
const id = (name: string): CubismIdHandle => CubismFramework.getIdManager().getId(name);

async function binary(url: string, signal: AbortSignal): Promise<ArrayBuffer> {
  const response = await fetch(url, { signal, cache: 'no-store' });
  if (!response.ok) throw new Error(`Cannot load ${new URL(url).pathname} (${response.status}).`);
  const bytes = await response.arrayBuffer();
  if (!bytes.byteLength) throw new Error(`Empty model asset: ${new URL(url).pathname}`);
  return bytes;
}

class EffectUpdater extends ICubismUpdater {
  constructor(order: number, private apply: (model: CubismModel, delta: number) => void) { super(order); }
  onLateUpdate(model: CubismModel, delta: number): void { this.apply(model, delta); }
}

/** Native Cubism model and renderer. No substitute meshes or image-frame motion are generated here. */
export class MapleModel extends CubismUserModel implements ModelDriver {
  states: string[] = [];
  private setting: CubismModelSettingJson;
  private metadata: Metadata = {};
  private motions = new Map<string, MotionResource>();
  private expressions = new Map<string, Binding[]>();
  private weights = new Map<string, { current: number; target: number }>();
  private parameters = new Set<string>();
  private scheduler = new CubismUpdateScheduler();
  private textures: WebGLTexture[] = [];
  private eyeIds: CubismIdHandle[] = [];
  private blink: CubismEyeBlink;
  private currentState = 'idle';
  private currentMotion?: CubismMotion;
  private currentEntry?: CubismMotionQueueEntry;
  private hasEyeCurves = false;
  private gazeTarget: Point = [0, 0];
  private gazeCurrent: Point = [0, 0];
  private gazeEnabled = true;
  private climbHoldValues?: number[];
  private climbRestWeight = 0;
  private climbRestTarget = 0;
  private time = 0;
  private disposed = false;
  private motionResponse = new MotionResponse();
  private swing = new SwingMotion();
  private swingBoundary?: () => void;
  private bodyGaze: Point = [0, 0];
  private nativeUpdateCount = 0;
  private staticSampleCount = 0;
  private physicsOutputs: string[] = [];
  private motionPhysicsValues: Record<string, number> = {};
  private matrix = new CubismMatrix44();
  private fitX = 1;
  private fitY = 1;
  private materialAnchors = new Map<DrawableAnchor, MaterialAnchorBinding>();

  constructor(private canvas: HTMLCanvasElement, private gl: WebGLRenderingContext) { super(); }
  get locomotion(): Record<string, unknown> { return this.metadata.locomotion || {}; }
  get motionPolishEnabled(): boolean { return this.metadata.refinement?.motionPolishVersion === 1; }
  diagnostics(): Record<string, number> { return {motionTimeSeconds: this._motionManager._userTimeSeconds,
    nativeUpdateCount: this.nativeUpdateCount, staticSampleCount: this.staticSampleCount,
    ...(this.motionPolishEnabled ? this.swing.diagnostics() : {})}; }
  motionPhysicsSnapshot(): Record<string, number> { return {...this.motionPhysicsValues}; }

  async load(url: string, signal: AbortSignal): Promise<void> {
    const settingsBytes = await binary(url, signal);
    const json = JSON.parse(new TextDecoder().decode(settingsBytes));
    if (json.Version !== 3 || !json.FileReferences?.Moc || !json.FileReferences?.Textures?.length)
      throw new Error('A real Cubism model3.json with MOC3 and textures is required.');
    this.setting = new CubismModelSettingJson(settingsBytes, settingsBytes.byteLength);
    const asset = (reference: string) => localAssetUrl(reference, url);
    this.loadModel(await binary(asset(this.setting.getModelFileName()), signal), true);
    if (!this._model || !this._moc) throw new Error('Cubism rejected the model MOC3 data.');
    for (const name of this._model.getModel().parameters.ids) this.parameters.add(name);

    if (json.CuteMaple?.Metadata) {
      this.metadata = JSON.parse(new TextDecoder().decode(await binary(asset(json.CuteMaple.Metadata), signal)));
    }
    const core = this._model.getModel();
    validateNativeIdentity(this.metadata.refinement, Array.from(core.parameters.ids), Array.from(core.drawables.ids));
    this.validateAnchors();
    const pending: Promise<void>[] = [];
    if (this.setting.getPhysicsFileName()) pending.push((async () => {
      const bytes = await binary(asset(this.setting.getPhysicsFileName()), signal);
      if (this.motionPolishEnabled)
        this.physicsOutputs = physicsOutputParameters(JSON.parse(new TextDecoder().decode(bytes)), this.parameters);
      this.loadPhysics(bytes, bytes.byteLength);
      if (!this._physics) throw new Error('Cannot load model physics.');
      this.scheduler.addUpdatableList(new CubismPhysicsUpdater(this._physics));
    })());
    if (this.setting.getPoseFileName()) pending.push((async () => {
      const bytes = await binary(asset(this.setting.getPoseFileName()), signal);
      this.loadPose(bytes, bytes.byteLength);
      if (!this._pose) throw new Error('Cannot load model pose data.');
      this.scheduler.addUpdatableList(new CubismPoseUpdater(this._pose));
    })());
    for (let groupIndex = 0; groupIndex < this.setting.getMotionGroupCount(); groupIndex++) {
      const group = this.setting.getMotionGroupName(groupIndex);
      if (!this.setting.getMotionCount(group)) continue;
      pending.push((async () => {
        // One authored action per group; additional variants can be chosen by the host in a later format.
        const bytes = await binary(asset(this.setting.getMotionFileName(group, 0)), signal);
        const data = JSON.parse(new TextDecoder().decode(bytes));
        if (!(data.Meta?.Duration > 0) || !Array.isArray(data.Curves) || !data.Curves.length)
          throw new Error(`Invalid or empty motion: ${group}`);
        const testMotion = this.loadMotion(bytes, bytes.byteLength, group, undefined, undefined,
          this.setting, group, 0, true);
        if (!testMotion) throw new Error(`Cubism rejected motion: ${group}`);
        testMotion.release();
        this.motions.set(group, { bytes, index: 0, hasEyeCurves: controlsBlink(data.Curves) });
      })());
    }
    for (let i = 0; i < this.setting.getExpressionCount(); i++) {
      const name = this.setting.getExpressionName(i);
      pending.push((async () => {
        const bytes = await binary(asset(this.setting.getExpressionFileName(i)), signal);
        const expression = JSON.parse(new TextDecoder().decode(bytes));
        if (!Array.isArray(expression.Parameters)) throw new Error(`Invalid expression: ${name}`);
        this.expressions.set(name, expression.Parameters.map((p: {Id: string; Value: number; Blend?: string}) =>
          ({ id: p.Id, value: p.Value, blend: p.Blend || 'Add' })));
      })());
    }
    // Do not release the model while another resource parse is still using it.
    const results = await Promise.allSettled(pending);
    for (const result of results) if (result.status === 'rejected') throw result.reason;
    if (signal.aborted) throw new DOMException('Model loading cancelled', 'AbortError');
    this.states = Array.from(this.motions.keys()).sort();
    if (!this.states.length) throw new Error('The model does not contain authored motion groups.');
    for (const [name, bindings] of Object.entries(this.metadata.expressions || {})) this.expressions.set(name, bindings);
    const defaults: Record<string, Binding[]> = {
      keyboard: [{ id: 'ParamTyping', value: 1, blend: 'Overwrite' }],
      audio: [{ id: 'ParamListening', value: 1, blend: 'Overwrite' }],
      dizzy: [{ id: 'ParamDizzy', value: 1, blend: 'Overwrite' }],
      smile: [{ id: 'ParamEyeLSmile', value: 1 }, { id: 'ParamEyeRSmile', value: 1 }, { id: 'ParamMouthForm', value: 1 }],
      blush: [{ id: 'ParamCheek', value: 1 }],
      glasses: [{ id: 'ParamGlasses', value: 1, blend: 'Overwrite' }],
      fan: [{ id: 'ParamFan', value: 1, blend: 'Overwrite' }],
      maple: [{ id: 'ParamMaple', value: 1, blend: 'Overwrite' }],
    };
    for (const [name, bindings] of Object.entries(defaults)) if (!this.expressions.has(name)) this.expressions.set(name, bindings);
    this.setupEffects();
    this.createRenderer(Math.max(1, this.canvas.width), Math.max(1, this.canvas.height));
    this.getRenderer().startUp(this.gl);
    this.getRenderer().setIsPremultipliedAlpha(true);
    await this.loadShaders(signal);
    for (let i = 0; i < this.setting.getTextureCount(); i++) {
      const texture = await this.loadTexture(asset(this.setting.getTextureFileName(i)), signal);
      this.getRenderer().bindTexture(i, texture);
    }
    this._model.update();
    this._model.saveParameters();
    this.setInitialized(true);
  }

  private setupEffects(): void {
    for (let i = 0; i < this.setting.getEyeBlinkParameterCount(); i++) this.eyeIds.push(this.setting.getEyeBlinkParameterId(i));
    if (this.eyeIds.length === 0) for (const eye of ['ParamEyeLOpen', 'ParamEyeROpen'])
      if (this.parameters.has(eye)) this.eyeIds.push(id(eye));
    this.blink = CubismEyeBlink.create(this.setting);
    this.blink.setParameterIds(this.eyeIds);
    this.scheduler.addUpdatableList(new EffectUpdater(200, model => {
      if (this.isSleeping() || this.hasEyeCurves || this.metadata.states?.[this.currentState]?.disableBlink) return;
      const base: number[] = [];
      for (let i = 0; i < this.eyeIds.length; i++) base.push(model.getParameterValueById(this.eyeIds[i]));
      // Multiply the automatic eyelid closure by the motion's eyelid state.
      this.blink.updateParameters(model, this.effectDelta);
      for (let i = 0; i < this.eyeIds.length; i++) model.multiplyParameterValueById(this.eyeIds[i], base[i]);
    }));
    this.scheduler.addUpdatableList(new EffectUpdater(300, (_model, delta) => {
      const amount = 1 - Math.exp(-delta / 0.18);
      for (const [name, weight] of this.weights) {
        weight.current += (weight.target - weight.current) * amount;
        for (const binding of this.expressions.get(name) || []) {
          if (this.motionResponse.context.attached && ['ParamTyping', 'ParamListening', 'ParamTypingPulse'].includes(binding.id)) continue;
          this.applyBinding(binding, weight.current);
        }
        if (weight.target === 0 && weight.current < 0.001) this.weights.delete(name);
      }
    }));
    this.scheduler.addUpdatableList(new EffectUpdater(400, (_model, delta) => {
      const enabled = this.gazeEnabled && !this.isSleeping() && !this.metadata.states?.[this.currentState]?.disableGaze;
      const petting = this.motionPolishEnabled ? this.swing.pettingWeight : 0;
      const amount = 1 - Math.exp(-delta / 0.12);
      this.climbRestWeight += (this.climbRestTarget-this.climbRestWeight)*(1-Math.exp(-delta/.22));
      for (let i = 0; i < 2; i++) this.gazeCurrent[i] += ((enabled ? this.gazeTarget[i] : 0) - this.gazeCurrent[i]) * amount;
      this.add(this.parameter('eyeX', 'ParamEyeBallX'), this.gazeCurrent[0]);
      this.add(this.parameter('eyeY', 'ParamEyeBallY'), this.gazeCurrent[1]);
      this.add(this.parameter('headX', 'ParamAngleX'), this.gazeCurrent[0] * (11.5-5.5*this.climbRestWeight) * (1-petting));
      this.add(this.parameter('headY', 'ParamAngleY'), this.gazeCurrent[1] * (8-4*this.climbRestWeight) * (1-petting));
      this.add(this.parameter('headZ', 'ParamAngleZ'), this.gazeCurrent[0] * (-2.3+1.1*this.climbRestWeight) * (1-petting));
      if (['climb_left','climb_right'].includes(this.currentState)) {
        const direction = this.currentState === 'climb_left' ? -1 : 1;
        this.add('ParamHeadTurn', -direction*.5*this.climbRestWeight);
      }
      const body = enabled && this.motionResponse.bodyGazeAllowed(this.currentState);
      for (let i = 0; i < 2; i++) this.bodyGaze[i] += ((body ? this.gazeCurrent[i] : 0) - this.bodyGaze[i]) * (1 - Math.exp(-delta / .3));
      // A support-state change resets these values immediately in play().
      this.add('ParamBodyAngleX', this.bodyGaze[0] * 1.5);
      this.add('ParamBodyAngleY', this.bodyGaze[1] * .5);
      this.add('ParamBodyAngleZ', this.bodyGaze[0] * -.25);
    }));
    this.scheduler.addUpdatableList(new EffectUpdater(500, (_model, delta) => {
      this.time += delta;
      const breath = (1 + Math.sin(this.time * Math.PI * 2 / (this.isSleeping() ? 5 : 3.8))) * 0.5;
      this.add(this.parameter('breath', 'ParamBreath'), breath * 0.5);
      const audio = this.weights.get('audio')?.current || 0;
      const typing = this.weights.get('keyboard')?.current || 0;
      if (this.motionResponse.context.attached) {
        const response = 1-(this.motionPolishEnabled ? this.swing.pettingWeight : 0);
        this.add(this.parameter('headY', 'ParamAngleY'), response * -1.5 * typing * (.65 + .35 * Math.sin(this.time * 5)));
        this.add(this.parameter('headZ', 'ParamAngleZ'), response * 2 * audio * (.7 + .3 * Math.sin(this.time * 2.5)));
      } else {
        this.add(this.parameter('bodyZ', 'ParamBodyAngleZ'), Math.sin(this.time * 3) * audio * 2);
        this.add('ParamTypingPulse', (0.5 + Math.sin(this.time * 11) * 0.5) * typing);
      }
      const ribbon = this.motionResponse.update(delta);
      for (const [i, name] of ['ParamRibbonLX', 'ParamRibbonLY', 'ParamRibbonRX', 'ParamRibbonRY'].entries()) this.add(name, ribbon[i]);
    }));
    this.scheduler.addUpdatableList(new EffectUpdater(550, () => {
      if (!this.motionPolishEnabled) return;
      const pet = this.swing.petting();
      for (const eye of ['ParamEyeLOpen', 'ParamEyeROpen'])
        this._model.multiplyParameterValueById(id(eye), 1-pet.weight);
      for (const name of ['ParamCheek', 'ParamEyeLSmile', 'ParamEyeRSmile', 'ParamMouthForm'])
        this._model.setParameterValueById(id(name), 1, pet.weight);
      this.add('ParamAngleY', pet.headY); this.add('ParamAngleZ', pet.headZ);
    }));
  }

  private effectDelta = 0;
  private validateAnchors(): void {
    this.materialAnchors.clear();
    const groups = [this.metadata.anchors || {}, ...Object.values(this.metadata.states || {}).map(state => state.anchors || {})];
    for (const anchors of groups) for (const [name, anchor] of Object.entries(anchors)) {
      if ('sourceUv' in anchor) throw new Error(`Uninstalled source contact: ${name}.`);
      if ('point' in anchor) {
        if ('atlasUv' in anchor || 'drawable' in anchor) throw new Error(`Ambiguous contact: ${name}.`);
        validatePointAnchor(anchor, this.parameters);
        continue;
      }
      const index = this._model.getDrawableIndex(id(anchor.drawable));
      if (index < 0) throw new Error(`Missing contact drawable ${anchor.drawable} (${name}).`);
      if (anchor.atlasUv !== undefined) {
        if (anchor.vertex !== undefined || anchor.edge !== undefined) throw new Error(`Ambiguous material contact: ${name}.`);
        this.materialAnchors.set(anchor, bindMaterialAnchor(anchor.atlasUv,
          this._model.getDrawableVertexUvs(index), this._model.getDrawableVertexIndices(index)));
      }
      if (anchor.vertex !== undefined && (!Number.isInteger(anchor.vertex) || anchor.vertex < 0 || anchor.vertex >= this._model.getDrawableVertexCount(index)))
        throw new Error(`Invalid contact vertex ${anchor.vertex} in ${anchor.drawable}.`);
      if (anchor.edge !== undefined && !['top', 'bottom', 'left', 'right'].includes(anchor.edge))
        throw new Error(`Invalid contact edge: ${name}`);
    }
  }
  private async loadShaders(signal: AbortSignal): Promise<void> {
    // R5 loads GLSL asynchronously; ready must wait for actual linked programs.
    const path = new URL('./shaders/', location.href).href;
    const names = ['vertshadersrc.vert', 'vertshadersrcmasked.vert', 'vertshadersrcsetupmask.vert',
      'fragshadersrcsetupmask.frag', 'fragshadersrcpremultipliedalpha.frag',
      'fragshadersrcmaskpremultipliedalpha.frag', 'fragshadersrcmaskinvertedpremultipliedalpha.frag',
      'vertshadersrccopy.vert', 'fragshadersrccopy.frag', 'fragshadersrccolorblend.frag',
      'fragshadersrcalphablend.frag', 'vertshadersrcblend.vert', 'fragshadersrcpremultipliedalphablend.frag'];
    await Promise.all(names.map(name => binary(new URL(name, path).href, signal)));
    this.getRenderer().loadShaders(path);
    const shader = CubismShaderManager_WebGL.getInstance().getShader(this.gl);
    const deadline = performance.now() + 10000;
    while (!shader._isShaderLoaded) {
      if (signal.aborted) throw new DOMException('Shader loading cancelled', 'AbortError');
      if (performance.now() > deadline || !shader._isShaderLoading) throw new Error('Cubism WebGL shaders failed to load.');
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    // R5 reserves three unused Normal/Over blend slots at the end of this array.
    if (shader._shaderSets.slice(0, 11).some(set => !set.shaderProgram) ||
        shader._shaderSets.some(set => set.shaderProgram && !this.gl.getProgramParameter(set.shaderProgram, this.gl.LINK_STATUS)))
      throw new Error('Cubism WebGL shader program compilation failed.');
  }
  private parameter(key: keyof NonNullable<Metadata['parameters']>, fallback: string): string {
    return this.metadata.parameters?.[key] || fallback;
  }
  private isSleeping(): boolean { return this.currentState === 'sleep_enter' || this.currentState === 'sleep_loop'; }
  private add(name: string, amount: number): void {
    if (this.parameters.has(name)) this._model.addParameterValueById(id(name), amount);
  }
  private applyBinding(binding: Binding, weight: number): void {
    if (!this.parameters.has(binding.id)) return;
    const target = id(binding.id);
    switch (binding.blend) {
      case 'Overwrite': this._model.setParameterValueById(target, binding.value, weight); break;
      case 'Multiply': this._model.multiplyParameterValueById(target, binding.value, weight); break;
      default: this._model.addParameterValueById(target, binding.value, weight);
    }
  }

  play(command: PlayCommand, boundary: () => void, marker?: (name: string) => void, staticStart = false): void {
    const resource = this.motions.get(command.name);
    if (!resource) throw new Error(`Missing authored motion: ${command.name}`);
    // Each invocation owns its callback, including while a previous invocation fades out.
    if (this.motionPolishEnabled) this.swing.enter(command.name);
    const continuousCycle = this.motionPolishEnabled && this.swing.ownsCycles;
    this.swingBoundary = continuousCycle ? boundary : undefined;
    const motion: CubismMotion = this.loadMotion(resource.bytes, resource.bytes.byteLength, command.name,
      continuousCycle ? () => {} : boundary, undefined, this.setting, command.name, resource.index, true);
    if (!motion) throw new Error(`Cannot play authored motion: ${command.name}`);
    bindMotionMarkers(motion, marker);
    motion.setLoop(command.playback !== 'one_shot');
    motion.setLoopFadeIn(false);
    if (Number.isFinite(command.fade)) {
      motion.setFadeInTime(clamp(command.fade, 0, 2));
      motion.setFadeOutTime(clamp(command.fade, 0, 2));
    }
    if (staticStart) {
      this._motionManager.stopAllMotions();
      motion.setFadeInTime(0); motion.setFadeOutTime(0);
      // Curve fades override motion fades, so both must be reset for a true start pose.
      for (const parameter of this.parameters) {
        motion.setParameterFadeInTime(id(parameter), 0);
        motion.setParameterFadeOutTime(id(parameter), 0);
      }
    }
    motion.setEffectIds(this.eyeIds, []);
    this.currentState = command.name;
    this.climbRestWeight = 0;
    this.climbRestTarget = 0;
    this.motionResponse.reset(command.name);
    this.bodyGaze = [0, 0];
    this.hasEyeCurves = resource.hasEyeCurves;
    const handle = this._motionManager.startMotionPriority(motion, true, 3);
    this.currentMotion = motion;
    this.currentEntry = this._motionManager.getCubismMotionQueueEntry(handle);
  }

  climbEndpointSeconds(): number | undefined {
    if (!['climb_left', 'climb_right'].includes(this.currentState) || !this.currentEntry?.isStarted()) return undefined;
    const motion = this.currentMotion;
    if (!motion || !motion.getLoop()) return undefined;
    const correction = motion.getMotionBehavior() === MotionBehavior.MotionBehavior_V2 ? 1 / motion._sourceFrameRate : 0;
    return secondsToAuthoredEndpoint(this._motionManager._userTimeSeconds - this.currentEntry.getStartTime(),
      motion.getLoopDuration(), correction);
  }

  sampleStartPose(): void {
    if (!this.isInitialized() || this.disposed) throw new Error('Cannot sample an unavailable model.');
    this._model.loadParameters();
    this._motionManager.updateMotion(this._model, 0);
    this.captureMotionPhysics();
    this._model.saveParameters();
    this.applySwingPose();
    this.gazeCurrent = [0, 0]; this.bodyGaze = [0, 0];
    for (const [name, weight] of this.weights) {
      weight.current = weight.target;
      for (const binding of this.expressions.get(name) || []) {
        if (this.motionResponse.context.attached && ['ParamTyping', 'ParamListening', 'ParamTypingPulse'].includes(binding.id)) continue;
        this.applyBinding(binding, weight.current);
      }
    }
    // Reinitialize the existing physics state at the selected pose without elapsed-time integration.
    this._physics?.stabilization(this._model);
    this._model.update();
    this.staticSampleCount++; this.nativeUpdateCount++;
  }

  context(value: MotionContext): void {
    this.motionResponse.set(value);
    if (!this.motionResponse.bodyGazeAllowed(this.currentState)) this.bodyGaze = [0, 0];
  }

  gaze(x: number, y: number, enabled: boolean): void {
    this.gazeTarget = [clamp(Number.isFinite(x) ? x : 0), clamp(Number.isFinite(y) ? y : 0)];
    this.gazeEnabled = enabled;
  }

  expression(name: string, active: boolean, intensity: number): void {
    if ((SWING_PETTING_NAMES as readonly string[]).includes(name)) {
      if (this.motionPolishEnabled) {
        if (active) this.swing.pet(name, true);
        else this.interruptPetting();
      }
      return;
    }
    if (!this.expressions.has(name)) throw new Error(`Unknown Live2D expression: ${name}`);
    const binding = this.expressions.get(name)!;
    if (active && !binding.some(item => this.parameters.has(item.id)))
      throw new Error(`Model is missing parameters for expression: ${name}`);
    const current = this.weights.get(name)?.current || 0;
    this.weights.set(name, { current, target: active ? clamp(intensity, 0, 1) : 0 });
  }

  update(seconds: number): void {
    if (!this.isInitialized() || this.disposed) return;
    this._model.loadParameters();
    this._motionManager.updateMotion(this._model, seconds);
    this.captureMotionPhysics();
    this._model.saveParameters();
    const cycles = this.motionPolishEnabled ? this.swing.advance(seconds) : 0;
    this.applySwingPose();
    this.effectDelta = seconds;
    this.scheduler.onLateUpdate(this._model, seconds);
    this._model.update();
    this.nativeUpdateCount++;
    const boundary = this.swingBoundary;
    for (let i = 0; i < cycles && boundary === this.swingBoundary; i++) boundary?.();
  }

  setClimbHold(held: boolean): void {
    this.climbRestTarget = held ? 1 : 0;
    if (!held) { this.climbHoldValues = undefined; return; }
    if (this.climbHoldValues || !this.isInitialized()) return;
    const count = this._model.getParameterCount();
    const painted = Array.from({length:count}, (_,i) => this._model.getParameterValueByIndex(i));
    // Preserve the final drawn cloth/support pose, but use the unmodified
    // native head baseline so last frame's gaze is never accumulated twice.
    this._model.loadParameters();
    const head = new Set(['ParamHeadTurn','ParamAngleX','ParamAngleY','ParamAngleZ',
      'ParamEyeBallX','ParamEyeBallY','ParamEyeLOpen','ParamEyeROpen']);
    for (let i=0; i<count; i++) {
      if (head.has(this._model.getParameterId(i).getString())) painted[i]=this._model.getParameterValueByIndex(i);
      this._model.setParameterValueByIndex(i,painted[i]);
    }
    this.climbHoldValues = painted;
  }

  updateClimbHold(seconds: number): void {
    if (!this.climbHoldValues || this.disposed) return;
    this.climbHoldValues.forEach((value,i) => this._model.setParameterValueByIndex(i,value));
    this.time += seconds;
    const enabled = this.gazeEnabled;
    for (let i=0;i<2;i++) this.gazeCurrent[i] += ((enabled ? this.gazeTarget[i] : 0)-this.gazeCurrent[i])*(1-Math.exp(-seconds/.12));
    this.climbRestWeight += (1-this.climbRestWeight)*(1-Math.exp(-seconds/.22));
    const direction = this.currentState === 'climb_left' ? -1 : 1;
    this.add('ParamHeadTurn', -direction*.5*this.climbRestWeight);
    this.add('ParamAngleX', this.gazeCurrent[0]*(11.5-5.5*this.climbRestWeight));
    this.add('ParamAngleY', this.gazeCurrent[1]*(8-4*this.climbRestWeight)
      + .35*this.climbRestWeight*Math.sin(this.time*Math.PI/3));
    this.add('ParamAngleZ', this.gazeCurrent[0]*(-2.3+1.1*this.climbRestWeight));
    this.add('ParamEyeBallX',this.gazeCurrent[0]); this.add('ParamEyeBallY',this.gazeCurrent[1]);
    this.blink.updateParameters(this._model,seconds);
    // No motion manager, body breath, sleeve/skirt physics, or cycle callbacks.
    this._model.update(); this.nativeUpdateCount++;
  }

  private captureMotionPhysics(): void {
    // Snapshot actual motion values before the scheduler overwrites its declared
    // physics destinations. Support/control parameters remain final-pose checks.
    if (this.motionPolishEnabled)
      this.motionPhysicsValues = Object.fromEntries(this.physicsOutputs.map(name => [name, this._model.getParameterValueById(id(name))]));
  }

  private applySwingPose(): void {
    if (!this.motionPolishEnabled || !this.swing.active) return;
    const pose = this.swing.pose();
    for (const [name, value] of Object.entries({ParamSwing: pose.swing, ParamAngleZ: pose.headZ,
      ParamLegLA: pose.legA, ParamLegRA: pose.legA})) this._model.setParameterValueById(id(name), value);
  }

  interruptPetting(): void {
    if (!this.swing.cancelPetting() || !this.isInitialized() || this.disposed) return;
    // Refresh the same native time without motion callbacks or advancing physics.
    this._model.loadParameters(); this.applySwingPose(); this.effectDelta = 0;
    this.scheduler.onLateUpdate(this._model, 0); this._model.update();
  }

  draw(): void {
    if (!this.isInitialized() || this.disposed) return;
    const gl = this.gl;
    const ratio = Math.max(1, Math.min(window.devicePixelRatio || 1, 3));
    const width = Math.max(1, Math.round(this.canvas.clientWidth * ratio));
    const height = Math.max(1, Math.round(this.canvas.clientHeight * ratio));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width; this.canvas.height = height;
    }
    const info = this._model.getModel().canvasinfo;
    const canvasAspect = info.CanvasWidth / info.CanvasHeight;
    const viewAspect = width / height;
    this.fitX = Math.min(1, canvasAspect / viewAspect);
    this.fitY = Math.min(1, viewAspect / canvasAspect);
    this.matrix.setMatrix(new Float32Array([
      2 * info.PixelsPerUnit / info.CanvasWidth * this.fitX, 0, 0, 0,
      0, 2 * info.PixelsPerUnit / info.CanvasHeight * this.fitY, 0, 0,
      0, 0, 1, 0,
      (2 * info.CanvasOriginX / info.CanvasWidth - 1) * this.fitX,
      (2 * info.CanvasOriginY / info.CanvasHeight - 1) * this.fitY, 0, 1,
    ]));
    gl.viewport(0, 0, width, height);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    this.getRenderer().setMvpMatrix(this.matrix);
    this.setRenderTargetSize(width, height);
    this.getRenderer().setRenderState(null, [0, 0, width, height]);
    this.getRenderer().drawModel();
  }

  private point(x: number, y: number): Point {
    const clipX = this.matrix.transformX(x);
    const clipY = this.matrix.transformY(y);
    return [(clipX + 1) / 2, (1 - clipY) / 2];
  }

  geometry(): Geometry {
    let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
    let headLeft = Infinity, headRight = -Infinity, headTop = Infinity, headBottom = -Infinity;
    const head = this.metadata.anchors?.head;
    const heads = pettingHeadDrawables(this.metadata.refinement, head && 'drawable' in head ? head.drawable : undefined);
    for (let i = 0; i < this._model.getDrawableCount(); i++) {
      if (this._model.getDrawableOpacity(i) < 0.01) continue;
      const isHead = heads.has(this._model.getDrawableId(i).getString());
      const vertices = this._model.getDrawableVertices(i);
      for (let j = 0; j < vertices.length; j += 2) {
        const p = this.point(vertices[j], vertices[j + 1]);
        left = Math.min(left, p[0]); right = Math.max(right, p[0]);
        top = Math.min(top, p[1]); bottom = Math.max(bottom, p[1]);
        if (isHead) {
          headLeft = Math.min(headLeft, p[0]); headRight = Math.max(headRight, p[0]);
          headTop = Math.min(headTop, p[1]); headBottom = Math.max(headBottom, p[1]);
        }
      }
    }
    if (!Number.isFinite(left)) { left = 0; right = 1; top = 0; bottom = 1; }
    const anchors: Record<string, Point> = { head: [(left + right) / 2, top + (bottom - top) * 0.18],
      ground: [(left + right) / 2, bottom], left: [left, (top + bottom) / 2],
      right: [right, (top + bottom) / 2], top: [(left + right) / 2, top] };
    const defined = { ...this.metadata.anchors, ...this.metadata.states?.[this.currentState]?.anchors };
    for (const [name, anchor] of Object.entries(defined)) {
      if ('point' in anchor) {
        const point = resolvePointAnchor(anchor, anchor.parameter
          ? this._model.getParameterValueById(id(anchor.parameter)) : undefined);
        anchors[name] = [0.5 + (point[0] - 0.5) * this.fitX,
          0.5 + (point[1] - 0.5) * this.fitY];
      } else {
        const index = this._model.getDrawableIndex(id(anchor.drawable));
        if (index < 0) continue;
        if (['fanTip', 'brushTip', 'brushGrip'].includes(name) && this._model.getDrawableOpacity(index) < .1) continue;
        const vertices = this._model.getDrawableVertices(index);
        if (anchor.atlasUv !== undefined) {
          const binding = this.materialAnchors.get(anchor);
          if (!binding) throw new Error(`Unbound material contact: ${name}.`);
          const point = resolveMaterialAnchor(binding, vertices);
          anchors[name] = this.point(point[0], point[1]);
        }
        else if (Number.isInteger(anchor.vertex) && anchor.vertex >= 0 && anchor.vertex * 2 + 1 < vertices.length)
          anchors[name] = this.point(vertices[anchor.vertex * 2], vertices[anchor.vertex * 2 + 1]);
        else if (anchor.edge && vertices.length) {
          const axis = anchor.edge === 'left' || anchor.edge === 'right' ? 0 : 1;
          const sign = anchor.edge === 'left' || anchor.edge === 'top' ? -1 : 1;
          let extreme = -Infinity;
          const points: Point[] = [];
          for (let i = 0; i < vertices.length; i += 2) {
            const point = this.point(vertices[i], vertices[i + 1]);
            const amount = point[axis] * sign;
            if (amount > extreme + 1e-5) { extreme = amount; points.length = 0; }
            if (Math.abs(amount - extreme) < 1e-5) points.push(point);
          }
          anchors[name] = [points.reduce((total, point) => total + point[0], 0) / points.length,
            points.reduce((total, point) => total + point[1], 0) / points.length];
        }
        else if (vertices.length) {
          let x = 0, y = 0;
          for (let i = 0; i < vertices.length; i += 2) { x += vertices[i]; y += vertices[i + 1]; }
          anchors[name] = this.point(x * 2 / vertices.length, y * 2 / vertices.length);
        }
      }
    }
    const progress = this.parameters.has('ParamTransitionProgress')
      ? this._model.getParameterValueById(id('ParamTransitionProgress')) : undefined;
    const climbPhase = this.parameters.has('ParamClimbPhase')
      ? this._model.getParameterValueById(id('ParamClimbPhase')) : undefined;
    const headBounds: Geometry['headBounds'] = Number.isFinite(headLeft)
      ? [headLeft, headTop, headRight-headLeft, headBottom-headTop] : undefined;
    return { bounds: [left, top, right - left, bottom - top], anchors, headBounds, transitionProgress: progress, climbPhase };

  }

  private async loadTexture(url: string, signal: AbortSignal): Promise<WebGLTexture> {
    const bytes = await binary(url, signal);
    const objectUrl = URL.createObjectURL(new Blob([bytes]));
    try {
      const image = new Image();
      await new Promise<void>((resolve, reject) => {
        const abort = () => { image.src = ''; reject(new DOMException('Texture loading cancelled', 'AbortError')); };
        image.onload = () => { signal.removeEventListener('abort', abort); resolve(); };
        image.onerror = () => { signal.removeEventListener('abort', abort); reject(new Error(`Invalid texture: ${new URL(url).pathname}`)); };
        signal.addEventListener('abort', abort, { once: true });
        if (signal.aborted) abort(); else image.src = objectUrl;
      });
      const gl = this.gl;
      const texture = gl.createTexture();
      if (!texture) throw new Error('Cannot allocate WebGL texture.');
      this.textures.push(texture);
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, 1);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
      gl.bindTexture(gl.TEXTURE_2D, null);
      return texture;
    } finally { URL.revokeObjectURL(objectUrl); }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.scheduler.release();
    this.release();
    for (const texture of this.textures) this.gl.deleteTexture(texture);
    this.textures = [];
    this.motions.clear();
    this.setting?.release();
  }
}
