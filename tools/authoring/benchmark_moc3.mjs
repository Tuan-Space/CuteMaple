// CPU-only measurement of actual native Core updates along authored motions.
// This does not measure Qt/GPU performance or replace visual/desktop QA.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const [modelArgument, outputArgument] = process.argv.slice(2);
if (!modelArgument || !outputArgument) throw new Error('Usage: node benchmark_moc3.mjs MODEL.model3.json NEW-REPORT.json');
const modelPath = path.resolve(modelArgument), output = path.resolve(outputArgument);
if (fs.existsSync(output)) throw new Error('Use a new report path; previous evidence is preserved');
const modelDirectory = path.dirname(modelPath);
function resource(reference) {
  const resolved = path.resolve(modelDirectory, reference);
  const relative = path.relative(modelDirectory, resolved);
  if (relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('Resource escapes model directory');
  return resolved;
}
const settings = JSON.parse(fs.readFileSync(modelPath, 'utf8').replace(/^\uFEFF/, ''));
const bytes = fs.readFileSync(resource(settings.FileReferences.Moc));
globalThis.require = createRequire(import.meta.url);
globalThis.__dirname = path.join(root, 'third_party/CubismSdkForWeb-5-r.5/Core');
vm.runInThisContext(fs.readFileSync(path.join(globalThis.__dirname, 'live2dcubismcore.min.js'), 'utf8'));
await new Promise(resolve => setTimeout(resolve, 0));
const core = globalThis.Live2DCubismCore;
const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
if (core.Moc.prototype.hasMocConsistency(buffer) !== 1) throw new Error('Official Core rejected MOC consistency');
const moc = core.Moc.fromArrayBuffer(buffer);
let model;
function evaluate(curve, seconds) {
  const values = curve.Segments;
  let startTime = values[0], startValue = values[1];
  for (let i = 2; i < values.length;) {
    if (values[i] !== 1 || i + 6 >= values.length) throw new Error('This benchmark requires authored restricted cubic curves');
    const [a, av, b, bv, end, endValue] = values.slice(i + 1, i + 7);
    const duration = end - startTime;
    if (Math.abs(a - startTime - duration / 3) > 2e-6 || Math.abs(b - end + duration / 3) > 2e-6)
      throw new Error('Unrestricted Bezier curve cannot be sampled as linear time');
    if (seconds <= end) {
      const t = Math.min(1, Math.max(0, (seconds - startTime) / duration)), u = 1 - t;
      return u ** 3 * startValue + 3 * u ** 2 * t * av + 3 * u * t ** 2 * bv + t ** 3 * endValue;
    }
    startTime = end; startValue = endValue; i += 7;
  }
  return startValue;
}
function distribution(values) {
  const sorted = [...values].sort((a, b) => a - b);
  return { samples: sorted.length, medianMs: sorted[Math.floor(sorted.length / 2)],
    p95Ms: sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * .95) - 1)],
    maximumMs: sorted.at(-1), meanMs: sorted.reduce((a, b) => a + b, 0) / sorted.length };
}
try {
  model = core.Model.fromMoc(moc);
  if (!model) throw new Error('Native model creation failed');
  const index = new Map(Array.from(model.parameters.ids, (id, i) => [id, i]));
  const all = [], motions = {}, sourceHashes = {};
  for (let i = 0; i < 120; i++) model.update();
  for (const [name, entries] of Object.entries(settings.FileReferences.Motions)) {
    const motionPath = resource(entries[0].File), source = fs.readFileSync(motionPath);
    const motion = JSON.parse(source.toString('utf8').replace(/^\uFEFF/, ''));
    const lanes = motion.Curves.filter(curve => curve.Target === 'Parameter');
    const times = [];
    for (let frame = 0; frame < 90; frame++) {
      model.parameters.values.set(model.parameters.defaultValues);
      for (const curve of lanes) {
        if (!index.has(curve.Id)) throw new Error(`Missing native parameter ${curve.Id}`);
        model.parameters.values[index.get(curve.Id)] = evaluate(curve, motion.Meta.Duration * frame / 89);
      }
      model.drawables.resetDynamicFlags();
      const start = performance.now();
      model.update();
      times.push(performance.now() - start);
      if (model.drawables.vertexPositions.some(vertices => vertices.some(value => !Number.isFinite(value))))
        throw new Error(`Non-finite native vertex in ${name} at frame ${frame}`);
    }
    motions[name] = distribution(times); all.push(...times);
    sourceHashes[entries[0].File] = crypto.createHash('sha256').update(source).digest('hex');
  }
  if (!all.length) throw new Error('No authored motions to benchmark');
  const report = { createdAt: new Date().toISOString(), scope: 'CPU native Core only; authored motion curves without framework physics, Qt, GPU, capture or hardware interaction',
    modelPath, moc3Sha256: crypto.createHash('sha256').update(bytes).digest('hex'), sourceHashes,
    coreVersion: core.Version.csmGetVersion(), nodeVersion: process.version,
    warmupUpdates: 120, drawables: model.drawables.count,
    vertices: Array.from(model.drawables.vertexCounts).reduce((a, b) => a + b, 0),
    allVerticesFinite: true, overall: distribution(all), motions };
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
  process.stdout.write(JSON.stringify({ report: output, overall: report.overall, vertices: report.vertices }) + '\n');
} finally { model?.release(); moc?._release(); }
