"""Build complete release ZIPs from real files, including materialized Git LFS assets."""
from pathlib import Path
import argparse,hashlib,json,subprocess,zipfile

def digest(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def package(root,output,version,source=False,committed_files=()):
 root=root.resolve()
 if source:
  names=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode('utf-8').split('\0');files=[root/n for n in names if n]
 else:files=[p for p in root.rglob('*') if p.is_file()]
 files=sorted(files)
 for f in files:
  if not f.resolve().is_relative_to(root) or f.is_symlink():raise ValueError(f'Nonlocal input: {f}')
  with f.open('rb') as stream:
   if stream.read(100).startswith(b'version https://git-lfs.github.com/spec'):raise ValueError(f'Fetch LFS first: {f}')
 entries={f.relative_to(root).as_posix():f for f in files}
 for name in committed_files:
  if not source or name not in entries:raise ValueError(f'Expected tracked source file: {name}')
  content=subprocess.check_output(['git','show','HEAD:'+name],cwd=root)
  if not content.startswith(b'version https://git-lfs.github.com/spec'):
   raise ValueError('Committed-file overrides currently require a materialized LFS object')
  oid=next(line[11:] for line in content.decode().splitlines() if line.startswith('oid sha256:'))
  gitdir=Path(subprocess.check_output(['git','rev-parse','--absolute-git-dir'],cwd=root,text=True).strip())
  original=gitdir/'lfs'/'objects'/oid[:2]/oid[2:4]/oid
  if digest(original)!=oid:raise ValueError(f'Invalid LFS object: {name}')
  entries[name]=original
 prefix=f'CuteMaple-{version}'+('-Source' if source else '')
 manifest={name:digest(f) for name,f in entries.items()}
 with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for name,f in entries.items():z.write(f,prefix+'/'+name)
  z.writestr(prefix+'/FILE-HASHES.json',json.dumps(manifest,ensure_ascii=False,indent=2))
 with zipfile.ZipFile(output) as z:
  assert z.testzip() is None
  for name,expected in manifest.items():
   with z.open(prefix+'/'+name) as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==expected,name
 result={'file':output.name,'sha256':digest(output),'bytes':output.stat().st_size,'files':len(files)+1,'source':source}
 output.with_suffix(output.suffix+'.sha256.txt').write_text(result['sha256']+'  '+output.name+'\n',encoding='utf-8')
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('output',type=Path);p.add_argument('--source',action='store_true');p.add_argument('--version');p.add_argument('--committed-file',action='append',default=[]);a=p.parse_args();version=a.version or (a.root/'VERSION').read_text().strip();print(json.dumps(package(a.root,a.output,version,a.source,a.committed_file)))
