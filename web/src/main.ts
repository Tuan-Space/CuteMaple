import { CubismFramework, LogLevel, Option } from '@framework/live2dcubismframework';
import { MapleModel } from './model';
import { contactTopology } from './native-diagnostics';
import { Playback } from './playback';
import { RendererLifecycle } from './lifecycle';
import { ParticleCanvas } from './particles';
import { localAssetUrl, parseCommand, type Command, type Reporter } from './protocol';

declare const qt: { webChannelTransport: unknown };
declare const QWebChannel: new (transport: unknown, callback: (channel: {
  objects: { petBridge: { command: { connect(callback: (raw: string) => void): void }; report(raw: string): void } }
}) => void) => unknown;

const canvas = document.getElementById('pet') as HTMLCanvasElement;
const decorations = new ParticleCanvas(document.getElementById('effects') as HTMLCanvasElement);
let player: Playback | undefined;
let abort: AbortController | undefined;
const lifecycle = new RendererLifecycle();
const hostGeneration = Number(new URLSearchParams(location.search).get('generation') || 0);
let fatal = false;
let report: Reporter = event => console.info(JSON.stringify(event));
let pending: Command[] = [];

function fail(error: unknown): void {
  if (!lifecycle.fail()) return;
  fatal = true; abort?.abort(); player?.pause(true);
  decorations.clear();
  const message = error instanceof Error ? error.message : String(error);
  report({ type: 'error', message });
}

async function configure(reference: string): Promise<void> {
  const generation = lifecycle.begin();
  abort?.abort();
  abort = new AbortController();
  player?.dispose();
  player = undefined;
  fatal = false;
  pending = [];
  const modelUrl = localAssetUrl(reference, location.href);
  const gl = canvas.getContext('webgl', { alpha: true, antialias: true,
    premultipliedAlpha: true, preserveDrawingBuffer: false });
  if (!gl) throw new Error('WebGL is unavailable in QtWebEngine.');
  if (!CubismFramework.isStarted()) {
    const options = new Option();
    options.logFunction = message => console.debug(message);
    options.loggingLevel = LogLevel.LogLevel_Warning;
    if (!CubismFramework.startUp(options)) throw new Error('Cubism Core startup failed.');
    CubismFramework.initialize();
  }
  const model = new MapleModel(canvas, gl);
  try {
    await model.load(modelUrl, abort.signal);
    if (!lifecycle.current(generation) || gl.isContextLost()) { model.dispose(); return; }
    player = new Playback(model, report, decorations.field);
    model.draw();
    if (gl.isContextLost() || !lifecycle.ready(generation)) { model.dispose(); return; }
    report({ type: 'ready', backend: 'live2d', sdk: '5-r.5', states: model.states, locomotion: model.locomotion,
      capabilities: ['climb-endpoint-v1', 'climb-rest-v1', ...(model.motionPolishEnabled ? ['motion-polish-v1'] : [])] });
    for (const command of pending) dispatch(command);
    pending = [];
  } catch (error) {
    model.dispose();
    if (lifecycle.current(generation)) throw error;
  }
}

function dispatch(command: Command): void {
  if ((command as { generation?: number }).generation !== undefined && (command as { generation?: number }).generation !== hostGeneration) return;
  if (command.type === 'stop') { lifecycle.stop(); fatal = true; abort?.abort(); player?.dispose(); player = undefined; pending = []; decorations.clear(); return; }
  if (lifecycle.phase === 'failed' || lifecycle.phase === 'stopped') return;
  if (command.type === 'configure') { configure(command.modelUrl).catch(fail); return; }
  if (!player) {
    if (command.type === 'gaze' || command.type === 'context' || command.type === 'resize') pending = pending.filter(item => item.type !== command.type);
    if (pending.length < 64) pending.push(command);
    return;
  }
  switch (command.type) {
    case 'play': player.play(command); break;
    case 'effect': player.effect(command); break;
    case 'context': player.context(command); break;
    case 'climb-endpoint': player.climbEndpoint(command); break;
    case 'climb-control': player.climbControl(command); break;
    case 'pause': player.pause(command.paused === true); break;
    case 'gaze': player.driver.gaze(command.x, command.y, command.enabled === true); break;
    case 'expression': player.expression(command); break;
    case 'resize': player.driver.draw(); player.sendGeometry(); break;
    default: throw new Error('Unknown renderer command.');
  }
}

function frame(time: number): void {
  if (!fatal) {
    try { player?.tick(time); decorations.draw(); }
    catch (error) { fatal = true; fail(error); }
  }
  requestAnimationFrame(frame);
}

canvas.addEventListener('webglcontextlost', event => {
  event.preventDefault();
  fail(new Error('WebGL context was lost. Reload the Live2D renderer.'));
});
window.addEventListener('pagehide', () => { lifecycle.stop(); fatal = true; abort?.abort(); player?.dispose(); player = undefined; });
window.addEventListener('error', event => fail(event.error || event.message));
if (typeof qt !== 'undefined' && typeof QWebChannel !== 'undefined') {
  new QWebChannel(qt.webChannelTransport, channel => {
    const bridge = channel.objects.petBridge;
    report = event => bridge.report(JSON.stringify({ ...event, generation: hostGeneration }));
    bridge.command.connect(raw => { try { dispatch(parseCommand(raw)); } catch (error) { fail(error); } });
    report({ type: 'connected', protocol: 1 });
  });
} else {
  // A developer can serve the repository root locally and supply an explicit model URL.
  const model = new URLSearchParams(location.search).get('model');
  if (model) configure(model).then(() => {
    if (player?.driver.states.includes('idle')) player.play({ type: 'play', name: 'idle', token: 1, playback: 'loop' });
  }).catch(fail);
  else fail('Qt WebChannel bridge is unavailable.');
}
requestAnimationFrame(frame);

// Enabled only by the explicit local verification page; absent in the pet's normal URL.
if (new URLSearchParams(location.search).get('diagnostics') === '1') {
  const capture = () => {
    if (!player) return undefined;
    player.driver.draw();
    const gl = canvas.getContext('webgl')!;
    const pixels = new Uint8Array(canvas.width * canvas.height * 4);
    gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    let visiblePixels = 0;
    let left = canvas.width, top = canvas.height, right = -1, bottom = -1;
    for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 10) {
      visiblePixels++;
      const position = (i - 3) / 4;
      const x = position % canvas.width, y = canvas.height - 1 - Math.floor(position / canvas.width);
      left = Math.min(left, x); right = Math.max(right, x);
      top = Math.min(top, y); bottom = Math.max(bottom, y);
    }
    const model = (player.driver as MapleModel).getModel().getModel();
    // Core resolves equal authored draw orders into the actual native order.
    // The official renderer consumes this array, including tie ordering.
    const renderOrders = model.getRenderOrders();
    let vertexHash = 2166136261, floatCount = 0;
    for (const vertices of model.drawables.vertexPositions) {
      floatCount += vertices.length;
      const words = new Uint32Array(vertices.buffer, vertices.byteOffset, vertices.length);
      for (const word of words) vertexHash = Math.imul(vertexHash ^ word, 16777619) >>> 0;
    }
    const parameters = Object.fromEntries(model.parameters.ids.map((name, index) => [name, model.parameters.values[index]]));
    const drawables = model.drawables.ids.map((name, index) => {
      const vertices = model.drawables.vertexPositions[index];
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (let vertex = 0; vertex < vertices.length; vertex += 2) {
        minX = Math.min(minX, vertices[vertex]); maxX = Math.max(maxX, vertices[vertex]);
        minY = Math.min(minY, vertices[vertex + 1]); maxY = Math.max(maxY, vertices[vertex + 1]);
      }
      return { id: name, opacity: model.drawables.opacities[index],
        drawOrder: model.drawables.drawOrders[index], renderOrder: renderOrders[index],
        vertexCount: vertices.length / 2,
        finiteVertices: vertices.every(Number.isFinite),
        bounds: [minX, minY, maxX - minX, maxY - minY],
        masks: Array.from(model.drawables.masks[index]).map(mask => model.drawables.ids[mask]),
        textureIndex: model.drawables.textureIndices[index],
        ...contactTopology(name, vertices, model.drawables.vertexUvs[index], model.drawables.indices[index]) };
    });
    const effects = decorations.capture();
    const composite = document.createElement('canvas'); composite.width = canvas.width; composite.height = canvas.height;
    const compositeContext = composite.getContext('2d')!;
    compositeContext.drawImage(canvas, 0, 0);
    compositeContext.drawImage(document.getElementById('effects') as HTMLCanvasElement, 0, 0, canvas.width, canvas.height);
    return { visiblePixels, glError: gl.getError(), png: canvas.toDataURL('image/png'),
      generation: hostGeneration, playback: player.diagnostics(), effects,
      nativeVertexSignature: {floatCount, hash32: vertexHash}, compositePng: composite.toDataURL('image/png'),
      pixelBounds: visiblePixels ? [left / canvas.width, top / canvas.height,
        (right - left + 1) / canvas.width, (bottom - top + 1) / canvas.height] : [0, 0, 0, 0],
      geometry: player.driver.geometry(), parameters, drawables,
      ...((player.driver as MapleModel).motionPolishEnabled ? {
        motionPhysicsValues: (player.driver as MapleModel).motionPhysicsSnapshot()} : {}),
      canvasInfo: { width: model.canvasinfo.CanvasWidth, height: model.canvasinfo.CanvasHeight,
        pixelsPerUnit: model.canvasinfo.PixelsPerUnit } };
  };
  const diagnostics = window as unknown as {
    __cutemapleCapture: () => unknown;
    __cutemapleCompare: (before: Record<string, number>, after: Record<string, number>) => unknown;
  };
  diagnostics.__cutemapleCapture = capture;
  diagnostics.__cutemapleCompare = (before, after) => {
    if (!player) return undefined;
    const model = (player.driver as MapleModel).getModel();
    const raw = model.getModel().parameters;
    const saved = new Float32Array(raw.values);
    const apply = (values: Record<string, number>) => {
      raw.values.set(saved);
      for (const [name, value] of Object.entries(values)) {
        const index = raw.ids.indexOf(name);
        if (index < 0) throw new Error(`Missing diagnostic parameter: ${name}`);
        model.setParameterValueByIndex(index, value);
      }
      model.update();
      return capture();
    };
    try { return { before: apply(before), after: apply(after) }; }
    finally { raw.values.set(saved); model.update(); player.driver.draw(); }
  };
}
