"""Source-rig visual review; these are NOT native Cubism screenshots."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image,ImageDraw
from build_rig import ROOT,Rig
from diagnose_rig import DiagnosticRenderer,motion_parameters


def review(output,folder=None):
    folder=Path(folder or ROOT/'assets/authoring/revisions/v5').resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Use a new review directory')
    rig=Rig.model_validate_json((folder/'Maple.rig.json').read_text(encoding='utf8'))
    renderer=DiagnosticRenderer(rig,folder,512)
    poses=[('idle',0),('sleep_enter',.5),('sleep_enter',.8),('sleep_loop',.5)]
    poses += [(f'climb_{side}',p) for side in ('left','right') for p in (0,.25,.5,.75,1)]
    poses += [(f'clean_ground_{stage}',p) for stage in ('enter','exit') for p in (0,.25,.5,.75,1)]
    poses += [('clean_ground',.46)]
    poses += [(f'clean_{site}_{stage}',.5) for site in ('top','climb_left','climb_right') for stage in ('enter','exit')]
    poses += [(f'climb_to_top_{side}',t/1.8) for side in ('left','right') for t in (.32,.85,1.2,1.65)]
    poses += [(state,p) for state in ('drag_left','drag_right','fall_float','land') for p in (0,.5,1)]
    paths=[folder/'Maple.rig.json',*[folder/t.path for t in rig.textures],
           *{folder/'runtime/motions'/f'{state}.motion3.json' for state,_ in poses}]
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    output.mkdir(parents=True)
    sheet=Image.new('RGB',(5*256,((len(poses)+4)//5)*282),'#404040')
    report={'nativeRuntimeValidation':False,'inputHashes':hashes,'samples':[],'visualReview':'pending'}
    for i,(state,phase) in enumerate(poses):
        params=motion_parameters(folder/'runtime/motions'/f'{state}.motion3.json',phase)
        image,stats=renderer.render(params)
        label=f'{i:02}-{state}-{phase:.3f}'
        image.save(output/(label+'.png'))
        triple=Image.new('RGB',(1536,512))
        for j,color in enumerate(('black','white','#b4b4b4')):
            base=Image.new('RGBA',image.size,color)
            if j==2:
                pen=ImageDraw.Draw(base)
                for y in range(0,512,16):
                    for x in range(0,512,16):
                        if (x//16+y//16)%2==0:pen.rectangle((x,y,x+15,y+15),fill='#e1e1e1')
            base.alpha_composite(image);triple.paste(base.convert('RGB'),(j*512,0))
        triple.save(output/(label+'-backgrounds.png'))
        thumb=image.resize((256,256),Image.Resampling.LANCZOS);xy=(i%5*256,i//5*282)
        sheet.paste(thumb,xy,thumb)
        ImageDraw.Draw(sheet).text((xy[0]+3,xy[1]+258),label,fill='white')
        report['samples'].append({'file':label+'.png','parameters':params,**stats})
        print(label,flush=True)
    sheet.save(output/'sheet.png')
    report['inputsChanged']=[p for p,h in hashes.items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--assets',type=Path)
    args=parser.parse_args();review(args.output,args.assets)
