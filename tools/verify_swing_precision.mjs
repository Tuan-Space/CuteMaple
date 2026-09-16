// Verify genuine Editor export with the official Core; never patch MOC data.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
globalThis.require=createRequire(import.meta.url);
globalThis.__dirname=path.join(root,'third_party/CubismSdkForWeb-5-r.5/Core');
vm.runInThisContext(fs.readFileSync(path.join(globalThis.__dirname,'live2dcubismcore.min.js'),'utf8'));
await new Promise(r=>setTimeout(r,0));
const core=globalThis.Live2DCubismCore; core.Logging.csmSetLogFunction(()=>{});
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const [beforePath,afterPath,reportPath]=process.argv.slice(2);
const curves=JSON.parse(fs.readFileSync(path.join(root,'assets/live2d/Maple/motions/swing_idle.motion3.json'),'utf8')).Curves;
function sample(file) {
  const bytes=fs.readFileSync(file), buffer=bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);
  assert.equal(core.Moc.prototype.hasMocConsistency(buffer),1);
  const moc=core.Moc.fromArrayBuffer(buffer), m=core.Model.fromMoc(moc);
  try {
    const set=(id,v)=>{const i=m.parameters.ids.indexOf(id);assert.ok(i>=0,id);m.parameters.values[i]=v;};
    for(const c of curves)set(c.Id,c.Segments[1]);
    const d=m.drawables, identity={parameters:Array.from(m.parameters.ids),drawables:Array.from(d.ids),
      minimum:Array.from(m.parameters.minimumValues),maximum:Array.from(m.parameters.maximumValues),
      defaults:Array.from(m.parameters.defaultValues),keys:m.parameters.keyValues.map(v=>Array.from(v))};
    const topology=hash(JSON.stringify(d.ids.map((id,i)=>({id,uv:Array.from(d.vertexUvs[i]),indices:Array.from(d.indices[i]),
      masks:Array.from(d.masks[i]),texture:d.textureIndices[i],flags:d.constantFlags[i]}))));
    const keyGeometry={};
    for(const s of [-1,0,1]) {set('ParamSwing',s);m.update();keyGeometry[s]=hash(Buffer.concat(d.vertexPositions.map(v=>Buffer.from(v.buffer,v.byteOffset,v.byteLength))));}
    const frames=[];
    for(let j=-100;j<=100;j++) {
      const s=j/1000; set('ParamSwing',s);m.update();
      const points={};
      for(const name of ['seat','rope_l','rope_r','hand_l_grip_palm','hand_r_grip_palm']) {
        const i=d.ids.indexOf(name);assert.ok(i>=0,name);const vertices=d.vertexPositions[i];
        let x=0,y=0; for(let k=0;k<vertices.length;k+=2){x+=vertices[k];y+=vertices[k+1];}
        points[name]=[x/(vertices.length/2),y/(vertices.length/2)];
      }
      frames.push({parameter:s,points});
    }
    return {sha256:hash(bytes),identity,topologySha256:topology,keyGeometry,frames};
  } finally {m.release();moc._release();}
}
const before=sample(beforePath), after=sample(afterPath);
assert.deepEqual(after.identity,before.identity,'Native IDs/ranges/key values changed');
assert.equal(after.topologySha256,before.topologySha256,'UVs/topology/masks changed');
assert.deepEqual(after.keyGeometry,before.keyGeometry,'Original key geometry changed');
const runs=data=>Object.fromEntries(Object.keys(data.frames[0].points).map(name=>{
  let longest=0,run=0;for(let i=1;i<data.frames.length;i++) {
    run=JSON.stringify(data.frames[i].points[name])===JSON.stringify(data.frames[i-1].points[name])?run+1:0;
    longest=Math.max(longest,run);
  }return [name,longest];
}));
const beforeRuns=runs(before),afterRuns=runs(after);
assert.ok(Object.values(afterRuns).every(n=>n===0),'Export still snaps near the center');
assert.ok(beforeRuns.seat>20,'The old-model regression must reproduce');
const report={passed:true,before:before.sha256,after:after.sha256,identityUnchanged:true,
  parameterCount:after.identity.parameters.length,drawableCount:after.identity.drawables.length,
  topologyUnchanged:true,topologySha256:after.topologySha256,keyGeometryUnchanged:true,
  beforeStationarySampleRuns:beforeRuns,afterStationarySampleRuns:afterRuns,
  beforeFrames:before.frames.filter((v,i)=>i%20===0),afterFrames:after.frames.filter((v,i)=>i%20===0)};
fs.writeFileSync(reportPath,JSON.stringify(report,null,2));
console.log(JSON.stringify({...report,beforeFrames:undefined,afterFrames:undefined}));
