import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { CubismFramework } from '../../third_party/CubismSdkForWeb-5-r.5/Framework/src/live2dcubismframework';
import { CubismMotion } from '../../third_party/CubismSdkForWeb-5-r.5/Framework/src/motion/cubismmotion';
import { bindMotionMarkers } from '../src/motion-data';
import { REQUIRED_STATES } from '../src/protocol';

const motionDirectories = [path.resolve('../assets/live2d/Maple/motions'),
  path.resolve('../assets/authoring/revisions/v5-1-motion-polish-20260911/runtime/motions')];
test('installed and v5 authored Maple motions pass the real Cubism R5 consistency checker', async () => {
    // The official Core detects Node and expects its CommonJS runtime globals.
    const runtime = globalThis as typeof globalThis & { require: ReturnType<typeof createRequire>; __dirname: string };
    runtime.require = createRequire(import.meta.url);
    runtime.__dirname = path.resolve('../third_party/CubismSdkForWeb-5-r.5/Core');
    try {
      vm.runInThisContext(fs.readFileSync('../third_party/CubismSdkForWeb-5-r.5/Core/live2dcubismcore.min.js', 'utf8'));
    } catch (error) { throw new Error(`Official Core initialization: ${(error as Error).message}`); }
    // Its embedded module resolves asynchronously in Node; the desktop bridge
    // similarly arrives after the Core script has initialized in the browser.
    await new Promise(resolve => setTimeout(resolve, 0));
    await import('../../third_party/CubismSdkForWeb-5-r.5/Framework/src/rendering/cubismrenderer_webgl');
    assert.equal(CubismFramework.startUp(), true);
    CubismFramework.initialize();
    try {
      for (const motionDirectory of motionDirectories) {
        assert.ok(fs.existsSync(motionDirectory), `Missing required motion fixture: ${motionDirectory}`);
        for (const state of REQUIRED_STATES) assert.ok(fs.existsSync(path.join(motionDirectory, `${state}.motion3.json`)));
        const authored = fs.readdirSync(motionDirectory).filter(name => name.endsWith('.motion3.json')).map(name => name.replace('.motion3.json', ''));
        for (const state of authored) {
          const bytes = fs.readFileSync(path.join(motionDirectory, `${state}.motion3.json`));
          const data = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
          const motion = CubismMotion.create(data, data.byteLength, undefined, undefined, true);
          assert.ok(motion, `Native Cubism rejected ${state}`);
          assert.ok(motion.getLoopDuration() > 0, `${state} has no native duration`);
          const markers: string[] = [];
          bindMotionMarkers(motion, value => markers.push(value));
          motion.getFiredEvent(-1, motion.getLoopDuration());
          const expected = JSON.parse(bytes.toString()).UserData?.map((event: {Value: string}) => event.Value) || [];
          assert.deepEqual(markers, expected.filter((name: string) => ['top_grab', 'wall_release', 'settled'].includes(name)));
          motion.release();
        }
      }
    } finally { CubismFramework.dispose(); CubismFramework.cleanUp(); }
  });
