"""Build a reproducible screenshot inventory and contact sheets for human review."""
import argparse,hashlib,json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont


def run():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--pattern',default='j225-matrix-*');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);manifest=[];sheets=[]
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',12)
    for folder in sorted(a.root.glob(a.pattern)):
        if not folder.is_dir():continue
        files=sorted(folder.glob('*.png'))
        # Only actual captures, excluding fixture images.
        report=folder/'report.json'
        if report.exists():
            data=json.loads(report.read_text(encoding='utf-8'))
            names={x['name'] for x in data.get('captures',[])} if 'captures' in data else set(data.get('screens',[]))
            files=[f for f in files if f.stem in names]
        for start in range(0,len(files),20):
            page=Image.new('RGB',(1440,1325),'#dddddd');draw=ImageDraw.Draw(page);entries=[]
            for n,file in enumerate(files[start:start+20]):
                source=Image.open(file);size=source.size;source.thumbnail((350,235))
                x=(n%4)*360;y=(n//4)*265
                draw.text((x+4,y+3),file.stem,font=font,fill='black');page.paste(source,(x+5,y+25))
                record={'file':str(file).replace('\\','/'),'size':size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()};manifest.append(record);entries.append(record['file'])
            output=a.output/f'{folder.name}-{start//20+1:02}.jpg';page.save(output,quality=90);sheets.append({'sheet':str(output),'files':entries})
    (a.output/'inventory.json').write_text(json.dumps({'captures':manifest,'sheets':sheets},indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'captures':len(manifest),'sheets':len(sheets)}))


if __name__=='__main__':run()
