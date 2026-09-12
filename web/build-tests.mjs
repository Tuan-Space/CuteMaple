import { build } from 'esbuild';
import { readdir } from 'node:fs/promises';
const files = (await readdir('tests')).filter(x => x.endsWith('.test.ts'));
await build({ entryPoints: files.map(x => `tests/${x}`), outdir: '.test-dist',
  outExtension: { '.js': '.mjs' }, bundle: true, platform: 'node', format: 'esm', target: 'node20' });
