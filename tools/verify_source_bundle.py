"""Validate current editing/runtime inputs without using any author-local paths."""
from pathlib import Path
import argparse,hashlib,json,re,sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'authoring'))
from native_caff import read_project
from prepare_editor_project import FILES

def check(root):
 root=root.resolve();model=root/'assets/authoring/model';source=root/'assets/authoring/source';runtime=root/'assets/live2d/Maple'
 def local(base,ref):
  path=(base/ref).resolve()
  if not path.is_relative_to(root) or not path.is_file():raise ValueError(f'Missing/non-local resource: {ref}')
  with path.open('rb') as stream:
   if stream.read(100).startswith(b'version https://git-lfs.github.com/spec'):raise ValueError(f'LFS pointer instead of file: {path}')
  return path
 manifest_path=root/'FILE-HASHES.json'
 if manifest_path.exists():
  manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
  for name,expected in manifest.items():
   path=local(root,name)
   with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
   if actual!=expected:raise ValueError(f'Source archive hash mismatch: {name}')
 for name in ('main.py','src/main.py','scripts/build.ps1','config/requirements-build.txt',
              'assets/authoring/Maple.psd','web/package-lock.json','web/dist/app.js',
              'native/cleaner/cleaner.cpp','installer/CuteMaple.iss','third_party/CubismSdkForWeb-5-r.5/Core/live2dcubismcore.min.js'):
  local(root,name)
 layers=json.loads(local(source,'layers.json').read_text(encoding='utf-8'))
 for layer in layers['layers']:local(source,layer['file'])
 rig=json.loads(local(source,'Maple.rig.json').read_text(encoding='utf-8'))
 for texture in rig['textures']:local(source,texture['path'])
 cmo=local(model,'Maple.cmo3');entries=read_project(cmo)
 xml=next(e.content for e in entries if e.path=='main.xml')
 if re.search(rb'[A-Za-z]:[/\\][^<>"\r\n]+',xml):raise ValueError('CMO contains an absolute file dependency')
 textures=[e.path for e in entries if e.path.endswith('.png')]
 if len(textures)<len(rig['textures']):raise ValueError('CMO embedded textures incomplete')
 projects=[]
 for p in model.glob('*.can3'):
  text=next(e.content for e in read_project(p) if e.path=='main.xml');refs=list(FILES.finditer(text))
  assert len(refs)==2
  for m in refs:
   value=m[3].decode('utf-8');target=local(model,value)
   assert target==(cmo if m[2]==b'srcFile' else p).resolve()
  projects.append({'file':p.name,'animationContentSha256':hashlib.sha256(FILES.sub(lambda m:m[1]+m[4],text)).hexdigest()})
 setting=json.loads(local(runtime,'Maple.model3.json').read_text(encoding='utf-8'));refs=setting['FileReferences']
 for key in ['Moc','Physics','DisplayInfo']:
  if key in refs:local(runtime,refs[key])
 for ref in refs['Textures']:local(runtime,ref)
 for records in refs['Motions'].values():
  for record in records:local(runtime,record['File'])
 for record in refs.get('Expressions',[]):local(runtime,record['File'])
 assert len(refs['Motions'])==19 and not any(x.startswith('clean_') for x in refs['Motions'])
 meta=json.loads(local(runtime,setting['CuteMaple']['Metadata']).read_text(encoding='utf-8'));assert local(runtime,meta['authoringSource'])==cmo
 return {'passed':True,'runtimeGroups':19,'layers':len(layers['layers']),'rigTextures':len(rig['textures']),'embeddedTextures':len(textures),'projects':projects,'externalFilesRequired':False}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('root',type=Path,nargs='?',default=Path(__file__).resolve().parents[1]);p.add_argument('--report',type=Path);a=p.parse_args();result=check(a.root);text=json.dumps(result,ensure_ascii=False,indent=2)
 if a.report:a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(text,encoding='utf-8')
 print(text)
