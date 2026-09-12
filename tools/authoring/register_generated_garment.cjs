/** Technical import of imagegen-painted garment layers: chroma matte, canvas
 * registration, and preservation of the original waist attachment pixels.
 * Does not paint folds or synthesize costume details. Originals stay untouched.
 * Requires the workspace's Sharp package (NODE_PATH may identify its bundle).
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const sharp = require('sharp');
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const clamp = (x, a=0, b=1) => Math.max(a, Math.min(b, x));

async function load(file) {
  return sharp(file).ensureAlpha().raw().toBuffer({resolveWithObject: true});
}
function seam(data, width, height) {
  for (let y = Math.floor(height*.45); y < height*.65; y++) {
    let start = -1, best = [0,0];
    for (let x = Math.floor(width*.25); x < width*.75; x++) {
      const visible = data[(y*width+x)*4+3] > 160;
      if (visible && start < 0) start=x;
      if (!visible && start >= 0) { if (x-start>best[1]-best[0]) best=[start,x]; start=-1; }
    }
    if (best[1]-best[0] > width*.09) {
      // The first antialiased row may contain a partial horizontal edge.
      // Use the following row for width, retaining the original top Y.
      let left=width,right=0;
      for(let x=Math.floor(width*.25);x<width*.75;x++) if(data[((y+1)*width+x)*4+3]>160){left=Math.min(left,x);right=Math.max(right,x+1);}
      return {y, left, right};
    }
  }
  throw new Error('Cannot identify an unambiguous horizontal waist attachment');
}
async function main() {
  const [generated, original, output] = process.argv.slice(2).map(x => path.resolve(x));
  if (!generated || !original || !output || [generated,original].includes(output) || fs.existsSync(output))
    throw new Error('Supply generated original new-output paths; never overwrite art');
  const source = await load(generated), reference = await load(original);
  const {width:w,height:h} = source.info;
  if (reference.info.width !== 1024 || reference.info.height !== 1024) throw new Error('Expected 1024px source registration');
  const pixels=Buffer.from(source.data), distance=new Float32Array(w*h);
  for(let i=0;i<w*h;i++) distance[i]=Math.max(255-pixels[i*4],pixels[i*4+1],255-pixels[i*4+2]);
  let cleared=0, edge=0;
  for(let y=0;y<h;y++) for(let x=0;x<w;x++) {
    const i=y*w+x, j=i*4, d=distance[i];
    if(d<20) { pixels.fill(0,j,j+4); cleared++; continue; }
    let a=1;
    if(d<95) {
      let nearest=0;
      for(let r=1;r<=3 && !nearest;r++) for(let dy=-r;dy<=r;dy++) for(let dx=-r;dx<=r;dx++) {
        if(x+dx<0||x+dx>=w||y+dy<0||y+dy>=h) continue;
        const value=distance[(y+dy)*w+x+dx];
        if(value>=95) nearest=Math.max(nearest,value);
      }
      a=nearest ? clamp(d/nearest) : 0;
      if(a>0 && a<1) edge++;
    }
    for(let c=0;c<3;c++) pixels[j+c]=a ? Math.round(clamp((pixels[j+c]-(c===1?0:255)*(1-a))/a,0,255)) : 0;
    pixels[j+3]=Math.round(a*255);
  }
  const from=seam(pixels,w,h), to=seam(reference.data,1024,1024);
  const lastRow=(data,width,height)=>{for(let y=height-1;y>=0;y--)for(let x=0;x<width;x++)if(data[(y*width+x)*4+3]>160)return y;throw new Error('Empty material')};
  // Uniform scale preserves drawn fold proportions; the literal waist strip
  // blends into that cloth. Never crop an overlong train at the canvas edge.
  const scale=Math.min((to.right-to.left)/(from.right-from.left),
    (lastRow(reference.data,1024,1024)-to.y)/(lastRow(pixels,w,h)-from.y));
  const tx=(to.left+to.right)/2-scale*(from.left+from.right)/2, ty=to.y-scale*from.y;
  const result=Buffer.alloc(1024*1024*4);
  for(let y=0;y<1024;y++) for(let x=0;x<1024;x++) {
    const sx=(x-tx)/scale, sy=(y-ty)/scale, xx=Math.floor(sx), yy=Math.floor(sy), dx=sx-xx,dy=sy-yy;
    if(xx<0||yy<0||xx+1>=w||yy+1>=h) continue;
    let a=0, rgb=[0,0,0];
    for(const [ox,oy,weight] of [[0,0,(1-dx)*(1-dy)],[1,0,dx*(1-dy)],[0,1,(1-dx)*dy],[1,1,dx*dy]]) {
      const j=((yy+oy)*w+xx+ox)*4, alpha=pixels[j+3]/255*weight;
      a+=alpha;
      for(let c=0;c<3;c++) rgb[c]+=pixels[j+c]*alpha;
    }
    const j=(y*1024+x)*4;
    for(let c=0;c<3;c++) result[j+c]=a ? Math.round(rgb[c]/a) : 0;
    result[j+3]=Math.round(a*255);
  }
  // The attachment band is literal original material, not an AI approximation.
  const keepUntil=to.y+25, blendUntil=keepUntil+20;
  for(let y=0;y<blendUntil;y++) for(let x=0;x<1024;x++) {
    const j=(y*1024+x)*4;
    if(y<keepUntil) { reference.data.copy(result,j,j,j+4); continue; }
    const t=(y-keepUntil)/(blendUntil-keepUntil), mix=t*t*(3-2*t);
    const a=reference.data[j+3]/255*(1-mix),b=result[j+3]/255*mix,total=a+b;
    for(let c=0;c<3;c++) result[j+c]=total ? Math.round((reference.data[j+c]*a+result[j+c]*b)/total) : 0;
    result[j+3]=Math.round(total*255);
  }
  if(!result.subarray(0,keepUntil*1024*4).equals(reference.data.subarray(0,keepUntil*1024*4))) throw new Error('Waist attachment mismatch');
  fs.mkdirSync(path.dirname(output),{recursive:true});
  await sharp(result,{raw:{width:1024,height:1024,channels:4}}).png().toFile(output);
  const audit={generated,generatedSha256:sha(generated),original,originalSha256:sha(original),output,outputSha256:sha(output),
    method:'imagegen art with deterministic chroma matte and waist registration',sourceSize:[w,h],outputSize:[1024,1024],
    sourceSeam:from,targetSeam:to,scale,translation:[tx,ty],literalOriginalRows:keepUntil,waistPixelsIdentical:true,
    clearedBackgroundPixels:cleared,edgePixels:edge,creativePainting:'image_gen built-in',nativeVisualAcceptance:false};
  fs.writeFileSync(output.replace(/\.png$/,'.registration.json'),JSON.stringify(audit,null,2)+'\n');
  console.log(JSON.stringify(audit));
}
main().catch(error=>{console.error(error);process.exitCode=1});
