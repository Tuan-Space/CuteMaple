"""Relink the supplied animation projects without changing curves or model data."""
from pathlib import Path
import argparse, hashlib, re, sys
from xml.sax.saxutils import escape
sys.path.insert(0,str(Path(__file__).resolve().parent/'authoring'))
from native_caff import read_project
from image2live2d.backends.live2d.cmo3.caff import CaffEntry,pack_caff
FILES=re.compile(rb'(<file\s+xs\.n="(file|srcFile)"[^>]*>)([^<]*)(</file>)')
def relink(project:Path, model:Path, portable=False):
 if not model.is_file():raise FileNotFoundError(model)
 entries=read_project(project);main=next(e for e in entries if e.path=='main.xml');before=main.content
 matches=list(FILES.finditer(before))
 if sorted(m[2] for m in matches)!=[b'file',b'srcFile']:raise ValueError('Expected one animation and one model path')
 def replace(m):
  target=model if m[2]==b'srcFile' else project
  text=target.name if portable else str(target.resolve())
  return m[1]+escape(text).encode('utf-8')+m[4]
 after=FILES.sub(replace,before)
 assert FILES.sub(lambda m:m[1]+m[4],before)==FILES.sub(lambda m:m[1]+m[4],after)
 if before!=after:
  data=pack_caff([CaffEntry(e.path,after if e.path=='main.xml' else e.content,tag=e.tag,obfuscated=e.obfuscated,compress=e.compress) for e in entries],key=42)
  temporary=project.with_suffix('.can3.tmp');temporary.write_bytes(data);temporary.replace(project)
 return {'project':project.name,'model':model.name,'portable':portable,'animationContentSha256':hashlib.sha256(FILES.sub(lambda m:m[1]+m[4],after)).hexdigest()}
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--portable',action='store_true');options=parser.parse_args()
 directory=options.root.resolve()/'assets/authoring/model'
 for project in sorted(directory.glob('*.can3')):print(relink(project,directory/'Maple.cmo3',options.portable))
if __name__=='__main__':main()
