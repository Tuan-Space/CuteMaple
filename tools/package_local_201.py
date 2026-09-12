"""Assemble and checksum the already verified local 2.0.1 directory."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,zipfile
p=argparse.ArgumentParser();p.add_argument('package',type=Path);args=p.parse_args()
root=Path(__file__).resolve().parents[1];pkg=args.package.resolve();assert pkg.parent==root/'dist' and pkg.name=='CuteMaple-2.0.1'
def sha(path):
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
snap=json.loads((pkg/'SOURCE-SNAPSHOT.json').read_text(encoding='utf-8-sig'))
for name,digest in snap['sourceModulesSha256'].items():assert sha(root/name)==digest.lower(),name
app=pkg/'CuteMaple-Live2D'
for name,digest in snap['runtimeResourcesSha256'].items():
 # app.ico is embedded into the executable by Nuitka, not shipped twice.
 if Path(name).as_posix().replace('\\','/')=='assets/app.ico':continue
 assert sha(app/name)==digest.lower(),name
motions=json.loads((app/'assets/live2d/Maple/Maple.model3.json').read_text(encoding='utf-8'))['FileReferences']['Motions'];assert len(motions)==19
assert not any('clean_' in n or 'sprites_v2' in n for n in [str(f.relative_to(app)) for f in app.rglob('*')])
qa=pkg/'QA';shutil.copytree(root/'docs/verification/2.0.1',qa,dirs_exist_ok=True)
for name in ['packaged-runtime.json','helper-runtime.json','smoke-summary.json','desktop-startup.json']:
 src=root/'artifacts/v201/package-smoke'/name
 if src.exists():shutil.copyfile(src,qa/name)
shutil.copyfile(root/'README.md',pkg/'README.md')
shutil.copyfile(root/'docs/2.0.1-Live2D-only.md',pkg/'2.0.1-说明.md')
release={'version':'2.0.1','sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'runtimeGroups':19,'githubUpdated':False,'realPrivilegedCleanup':'user test; not executed','longTermUsage':'user test; no new soak claim'}
(pkg/'RELEASE.json').write_text(json.dumps(release,indent=2)+'\n',encoding='utf-8',newline='\n')
(pkg/'SHA256.txt').write_text(''.join(f'{sha(f)}  {f.relative_to(pkg).as_posix()}\n' for f in sorted(pkg.rglob('*')) if f.is_file() and f.name!='SHA256.txt'),encoding='utf-8',newline='\n')
files=[f for f in sorted(pkg.rglob('*')) if f.is_file()];out=root.parent/'CuteMaple-2.0.1-Windows-x64.zip'
with zipfile.ZipFile(out,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for f in files:z.write(f,pkg.name+'/'+f.relative_to(pkg).as_posix())
with zipfile.ZipFile(out) as z:
 assert z.testzip() is None
 for f in files:
  with z.open(pkg.name+'/'+f.relative_to(pkg).as_posix()) as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==sha(f)
digest=sha(out);out.with_suffix('.zip.sha256.txt').write_text(digest+'  '+out.name+'\n',encoding='utf-8',newline='\n')
result={'zip':str(out),'sha256':digest,'files':len(files),'directoryBytes':sum(f.stat().st_size for f in files),'zipBytes':out.stat().st_size}
(root/'artifacts/v201/package-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
