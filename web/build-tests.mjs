import { build } from 'esbuild';
import { readdir, mkdir, unlink } from 'node:fs/promises';
const files = (await readdir('tests')).filter(x => x.endsWith('.test.ts'));
await mkdir('.test-dist', {recursive:true});
const outputs = new Set(files.map(x => x.replace(/\.ts$/, '.mjs')));
for (const name of await readdir('.test-dist')) {
  if (name.endsWith('.test.mjs') && !outputs.has(name)) await unlink(`.test-dist/${name}`);
}
await build({ entryPoints: files.map(x => `tests/${x}`), outdir: '.test-dist',
  outExtension: { '.js': '.mjs' }, bundle: true, platform: 'node', format: 'esm', target: 'node20' });
