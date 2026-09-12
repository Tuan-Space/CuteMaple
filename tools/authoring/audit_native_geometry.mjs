// Read a genuine MOC3 with the bundled, unmodified official Core. No UI/export.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const corePath = path.join(root, 'third_party/CubismSdkForWeb-5-r.5/Core/live2dcubismcore.min.js');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
function bounds(values) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let i = 0; i < values.length; i += 2) {
    minX = Math.min(minX, values[i]); maxX = Math.max(maxX, values[i]);
    minY = Math.min(minY, values[i + 1]); maxY = Math.max(maxY, values[i + 1]);
  }
  return [minX, minY, maxX - minX, maxY - minY];
}
function unique(values, label, errors) {
  if (values.some(value => typeof value !== 'string' || !value) || new Set(values).size !== values.length)
    errors.push(`Missing or duplicate ${label} IDs`);
}
function inspectDrawable(model, index, errors) {
  const d = model.drawables, id = d.ids[index];
  const positions = Array.from(d.vertexPositions[index]), textureUvs = Array.from(d.vertexUvs[index]);
  const indices = Array.from(d.indices[index]);
  const finiteVertices = positions.every(Number.isFinite);
  if (positions.length < 6 || positions.length % 2 || !finiteVertices)
    errors.push(`Invalid/nonfinite native vertices: ${id}`);
  if (textureUvs.length !== positions.length || !textureUvs.every(Number.isFinite) ||
      textureUvs.some(value => value < -1e-6 || value > 1 + 1e-6))
    errors.push(`Invalid/nonfinite/out-of-range native UVs: ${id}`);
  if (!indices.length || indices.length % 3 || indices.some(value => !Number.isInteger(value) || value < 0 || value >= positions.length / 2))
    errors.push(`Invalid native triangle indices: ${id}`);
  if (!Number.isInteger(d.textureIndices[index]) || d.textureIndices[index] < 0)
    errors.push(`Invalid native texture page: ${id}`);
  const renderOrder = model.getRenderOrders()[index];
  if (!Number.isInteger(d.drawOrders[index]) || !Number.isInteger(renderOrder))
    errors.push(`Invalid native draw/render order: ${id}`);
  let degenerate = 0, negative = 0, positive = 0, absoluteArea = 0;
  if (indices.every(value => value >= 0 && value < positions.length / 2)) {
    for (let i = 0; i < indices.length; i += 3) {
      const [a, b, c] = indices.slice(i, i + 3).map(value => value * 2);
      const area = (positions[b] - positions[a]) * (positions[c + 1] - positions[a + 1]) -
        (positions[b + 1] - positions[a + 1]) * (positions[c] - positions[a]);
      if (Math.abs(area) < 1e-10) degenerate++; else if (area < 0) negative++; else positive++;
      absoluteArea += Math.abs(area) / 2;
    }
  }
  return { id, opacity: d.opacities[index], drawOrder: d.drawOrders[index], renderOrder,
    textureIndex: d.textureIndices[index], vertexCount: positions.length / 2, finiteVertices,
    bounds: bounds(positions), uvBounds: bounds(textureUvs), positions, textureUvs, indices,
    triangleStatistics: { count: indices.length / 3, degenerate, negative, positive, absoluteArea } };
}

async function main() {
  const args = process.argv.slice(2);
  if (args.includes('--help')) {
    process.stdout.write('Usage: node tools/authoring/audit_native_geometry.mjs MODEL.moc3 --output NEW-REPORT.json\n');
    return;
  }
  if (args.length !== 3 || args[1] !== '--output') throw new Error('Expected MODEL.moc3 --output NEW-REPORT.json');
  const source = path.resolve(args[0]), output = path.resolve(args[2]);
  if (fs.existsSync(output)) throw new Error(`Refusing to overwrite audit: ${output}`);
  const report = { schemaVersion: 1, tool: 'official-cubism-native-geometry-audit', passed: false,
    source, errors: [], sourceHashesAtStart: {}, sourceHashesAtEnd: {}, sourcesUnchanged: false,
    parameters: [], drawables: [], validationScope: 'Default native geometry/UV inventory only; no texture, animation or visual acceptance.' };
  let moc, model;
  try {
    const bytes = fs.readFileSync(source), coreBytes = fs.readFileSync(corePath);
    report.sourceBytes = bytes.length;
    report.sourceSha256 = sha(bytes);
    report.sourceHashesAtStart = { [source]: report.sourceSha256, [corePath]: sha(coreBytes) };
    if (bytes.length < 64 || bytes.subarray(0, 4).toString() !== 'MOC3') throw new Error('Missing genuine MOC3 header');
    globalThis.require = createRequire(import.meta.url);
    globalThis.__dirname = path.dirname(corePath);
    console.log = (...values) => process.stderr.write(values.join(' ') + '\n');
    vm.runInThisContext(coreBytes.toString('utf8'));
    await new Promise(resolve => setTimeout(resolve, 0));
    const core = globalThis.Live2DCubismCore;
    core.Logging.csmSetLogFunction(() => {});
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    if (core.Moc.prototype.hasMocConsistency(buffer) !== 1) throw new Error('Official Core rejected MOC consistency');
    moc = core.Moc.fromArrayBuffer(buffer);
    if (!moc) throw new Error('Official Core could not load MOC');
    model = core.Model.fromMoc(moc);
    if (!model || !model.drawables.count) throw new Error('No native drawable model');
    model.update();
    report.coreVersion = core.Version.csmGetVersion();
    report.mocVersion = core.Version.csmGetMocVersion(buffer);
    report.canvasInfo = { width: model.canvasinfo.CanvasWidth, height: model.canvasinfo.CanvasHeight,
      originX: model.canvasinfo.CanvasOriginX, originY: model.canvasinfo.CanvasOriginY,
      pixelsPerUnit: model.canvasinfo.PixelsPerUnit };
    if (!Object.values(report.canvasInfo).every(Number.isFinite) ||
        report.canvasInfo.width <= 0 || report.canvasInfo.height <= 0 || report.canvasInfo.pixelsPerUnit <= 0)
      report.errors.push('Invalid native canvas geometry');
    unique(model.drawables.ids, 'drawable', report.errors);
    unique(model.parameters.ids, 'parameter', report.errors);
    report.parameters = model.parameters.ids.map((id, i) => ({ id, minimum: model.parameters.minimumValues[i],
      maximum: model.parameters.maximumValues[i], default: model.parameters.defaultValues[i], value: model.parameters.values[i] }));
    for (const p of report.parameters)
      if (![p.minimum, p.maximum, p.default, p.value].every(Number.isFinite) || p.minimum > p.maximum || p.default < p.minimum || p.default > p.maximum)
        report.errors.push(`Invalid native parameter values: ${p.id}`);
    report.drawables = model.drawables.ids.map((_, index) => inspectDrawable(model, index, report.errors));
  } catch (error) {
    report.errors.push(String(error?.message ?? error));
  } finally {
    model?.release(); moc?._release();
    for (const [name, expected] of Object.entries(report.sourceHashesAtStart)) {
      try { report.sourceHashesAtEnd[name] = sha(fs.readFileSync(name)); }
      catch (error) { report.errors.push(`Cannot recheck input ${name}: ${error.message}`); }
      if (report.sourceHashesAtEnd[name] !== expected) report.errors.push(`Input changed during native audit: ${name}`);
    }
    report.sourcesUnchanged = Object.keys(report.sourceHashesAtStart).length > 0 &&
      Object.entries(report.sourceHashesAtStart).every(([name, value]) => report.sourceHashesAtEnd[name] === value);
  }
  report.passed = report.sourcesUnchanged && report.errors.length === 0;
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
  process.stdout.write(JSON.stringify({ passed: report.passed, output, drawables: report.drawables.length, errors: report.errors }) + '\n');
  process.exitCode = report.passed ? 0 : 1;
}
main().catch(error => { process.stderr.write(String(error?.message ?? error) + '\n'); process.exitCode = 1; });
