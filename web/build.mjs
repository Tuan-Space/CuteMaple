import { build } from 'esbuild';
import { copyFile, cp, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
process.chdir(root);
await mkdir('dist', { recursive: true });
await build({ entryPoints: ['src/main.ts'], outfile: 'dist/app.js', bundle: true,
  format: 'iife', platform: 'browser', target: 'chrome108', minify: true,
  legalComments: 'eof', tsconfig: 'tsconfig.json' });
const sdk = '../third_party/CubismSdkForWeb-5-r.5';
await copyFile(`${sdk}/Core/live2dcubismcore.min.js`, 'dist/live2dcubismcore.min.js');
await cp(`${sdk}/Framework/Shaders/WebGL`, 'dist/shaders', { recursive: true });
await copyFile('index.html', 'dist/index.html');
await mkdir('dist/licenses', { recursive: true });
for (const [source, name] of [
  [`${sdk}/LICENSE.md`, 'Cubism-SDK-LICENSE.md'],
  [`${sdk}/NOTICE.md`, 'Cubism-SDK-NOTICE.md'],
  [`${sdk}/Core/LICENSE.md`, 'Cubism-Core-LICENSE.md'],
  [`${sdk}/Core/RedistributableFiles.txt`, 'Cubism-Core-RedistributableFiles.txt'],
  [`${sdk}/Framework/LICENSE.md`, 'Cubism-Framework-LICENSE.md'],
  [`${sdk}/PROVENANCE.json`, 'Cubism-PROVENANCE.json'],
]) await copyFile(source, `dist/licenses/${name}`);
const files = ['index.html', 'app.js', 'live2dcubismcore.min.js', ...(await readdir('dist/shaders')).map(name => `shaders/${name}`)];
const hashes = Object.fromEntries(await Promise.all(files.map(async file =>
  [file, createHash('sha256').update(await readFile(`dist/${file}`)).digest('hex')])));
await writeFile('dist/build-manifest.json', JSON.stringify({ sdk: '5-r.5', files: hashes }, null, 2) + '\n');
console.log('Built offline Cubism Web R5 renderer in web/dist.');
