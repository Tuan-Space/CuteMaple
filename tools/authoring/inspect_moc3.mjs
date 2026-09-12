// Read-only validation by the unmodified official Cubism Core. No renderer/UI.
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
globalThis.require = createRequire(import.meta.url);
globalThis.__dirname = path.join(root, 'third_party/CubismSdkForWeb-5-r.5/Core');
console.log = (...values) => process.stderr.write(values.join(' ') + '\n');
vm.runInThisContext(fs.readFileSync(path.join(globalThis.__dirname, 'live2dcubismcore.min.js'), 'utf8'));
await new Promise(resolve => setTimeout(resolve, 0));
const core = globalThis.Live2DCubismCore;
core.Logging.csmSetLogFunction(() => {});
const bytes = fs.readFileSync(process.argv[2]);
const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
if (bytes.length < 64 || bytes.subarray(0, 4).toString() !== 'MOC3' ||
    core.Moc.prototype.hasMocConsistency(buffer) !== 1) throw new Error('Official Core rejected MOC consistency');
const moc = core.Moc.fromArrayBuffer(buffer);
if (!moc) throw new Error('Official Core could not load MOC');
let model;
try {
  model = core.Model.fromMoc(moc);
  if (!model || !model.drawables.count) throw new Error('MOC contains no native drawable model');
  process.stdout.write(JSON.stringify({ valid: true, coreVersion: core.Version.csmGetVersion(),
    mocVersion: core.Version.csmGetMocVersion(buffer), parameters: Array.from(model.parameters.ids),
    drawables: Array.from(model.drawables.ids), parts: Array.from(model.parts.ids),
    textureIndices: Array.from(model.drawables.textureIndices) }));
} finally { model?.release(); moc._release(); }
