"""Separate v5 paint mechanically; retain v4 and the source RGBA unmodified."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import cv2
import numpy as np
from PIL import Image
from prepare_v4_layers import ROOT, rgba, polygon, transfer, warp


def prepare(output=None):
    source=ROOT/'assets/authoring/revisions/v4'
    output=Path(output or ROOT/'assets/authoring/revisions/v5').resolve()
    if output==source or source in output.parents:
        raise ValueError('v4 is a preserved baseline')
    output.mkdir(parents=True,exist_ok=True)
    (output/'layers').mkdir(exist_ok=True)
    manifest=json.loads((source/'layers.json').read_text(encoding='utf8'))
    manifest['version']=5
    audit={'sourceManifestSha256':hashlib.sha256((source/'layers.json').read_bytes()).hexdigest(),
           'method':'mechanical source-pixel extraction and affine registration', 'ribbonTransfers':{}}
    art={}
    for layer in manifest['layers']:
        art[layer['id']]=rgba(source/layer['file'])
    original=rgba(ROOT/manifest['source'])
    # These are the inner returns of the same painted ribbon, previously
    # assigned to adjacent hair/costume at the polygon partition. Preserve
    # every original RGBA value; empty space between the two strips stays empty.
    outlines={
        'l':[(365,251),(406,260),(418,371),(423,411),(407,478),(387,550),
             (365,609),(350,652),(337,719),(312,785),(299,835),(312,899),
             (309,938),(247,942),(233,898),(235,847),(256,777),(273,713),
             (285,663),(299,610),(283,571),(279,535),(294,458),(318,369),(337,290)],
        'r':[(660,251),(627,261),(617,371),(615,411),(631,478),(650,550),
             (672,609),(691,652),(706,719),(733,785),(749,835),(733,899),
             (730,941),(791,944),(806,898),(810,847),(789,777),(772,713),
             (756,663),(741,610),(755,571),(757,535),(743,458),(720,369),(698,290)]}
    for side,points in outlines.items():
        mask=polygon(points)&(original[:,:,3]>0)
        # Geometry decides ownership. Never classify the final mask by color:
        # the source includes neutral highlights, white flowers and gold edges.
        # Earrings are separate front objects and remain on their head chain.
        mask &= art['ornament_'+side][:,:,3]==0
        target=art['ribbon_'+side]
        add=mask&(target[:,:,3]<original[:,:,3])
        target[add]=original[add]
        removed={}
        for layer in manifest['layers']:
            name=layer['id']
            if name=='ribbon_'+side or name.startswith('ornament_') or layer.get('view')!='front':
                continue
            pixels=art[name]
            same=add&(pixels[:,:,3]>0)&np.all(pixels[:,:,:3]==original[:,:,:3],axis=2)
            if same.any():
                pixels[same]=0
                removed[name]=int(same.sum())
        audit['ribbonTransfers'][side]={'sourcePolygon':points,'restoredPixels':int(add.sum()),
            'sourceRgbaExact':bool(np.array_equal(target[add],original[add])),
            'removedDuplicateSourcePixels':removed,'alphaMultiplier':1.0}
    audit['profileRibbonOwnership']=[]
    audit['bodyRibbonFragments']=[]
    for prefix in ('profile_l_','profile_r_','mid_l_','mid_r_'):
        name=prefix+'skirt';pixels=art[name]
        count,labels,stats,_=cv2.connectedComponentsWithStats((pixels[:,:,3]>16).astype('uint8'))
        largest=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]));main=stats[largest]
        for index in range(1,count):
            box=stats[index];component=labels==index
            if index==largest or box[4]<20 or not (box[0]+box[2]<main[0]+20 or box[0]>main[0]+main[2]-20):
                continue
            rgb=pixels[component,:3].astype(int)
            # Color is only a classification seed for a complete connected
            # original component; it never selects or removes individual pixels.
            if np.mean((rgb[:,2]>rgb[:,0]+8)&(rgb[:,2]>rgb[:,1]-5))<.60:
                continue
            distances={side:float(cv2.distanceTransform((art[prefix+'ribbon_'+side][:,:,3]==0).astype('uint8'),cv2.DIST_L2,3)[component].mean()) for side in ('l','r')}
            target_name=prefix+'ribbon_'+min(distances,key=distances.get)
            target=art[target_name];move=component&(pixels[:,:,3]>target[:,:,3]);target[move]=pixels[move]
            pixels[component]=0
            underlay=prefix+'costume_underlay_skirt'
            if underlay in art:art[underlay][component]=0
            audit['bodyRibbonFragments'].append({'from':[name,underlay],'to':target_name,
                'bounds':box[:4].tolist(),'pixels':int(component.sum()),'rgbaUnchanged':True})
    for prefix in ('profile_l_','profile_r_','mid_l_','mid_r_'):
        for side,other in (('l','r'),('r','l')):
            name,target_name=prefix+'ribbon_'+side,prefix+'ribbon_'+other
            pixels,target=art[name],art[target_name]
            count,labels,stats,_=cv2.connectedComponentsWithStats((pixels[:,:,3]>16).astype('uint8'))
            if count<=2:
                continue
            largest=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))
            neighbors=cv2.dilate((target[:,:,3]>16).astype('uint8'),np.ones((3,3),np.uint8))>0
            for index in range(1,count):
                component=labels==index
                if index==largest or stats[index,cv2.CC_STAT_AREA]<32 or not np.any(component&neighbors):
                    continue
                # A hard vertical partition detached this section from the
                # rest of the SAME physical strip in the neighboring layer.
                # Transfer its exact RGBA; no material is painted or erased.
                move=component&(pixels[:,:,3]>target[:,:,3])
                target[move]=pixels[move]
                pixels[component]=0
                audit['profileRibbonOwnership'].append({'from':name,'to':target_name,
                    'bounds':stats[index,:4].tolist(),'pixels':int(component.sum()),'rgbaUnchanged':True})
    layers=manifest['layers']
    by_id={x['id']:x for x in layers}
    def variant(source_id,name,**settings):
        layer=copy.deepcopy(by_id[source_id])
        layer.update(id=name,file='layers/'+name+'.png',opacity=0,**settings)
        for key in ('family','view','zone','has_seated_variant','occlusion_side'):
            layer.pop(key,None)
        layers.append(layer)
        art[name]=art[source_id].copy()
        return layer
    variant('skirt_swing','skirt_sleep',pose='sleep',draw_order=96)
    for side in ('l','r'):
        for pose in ('sleep','climb'):
            variant('leg_'+side+'_swing','leg_'+side+'_'+pose,pose=pose,draw_order=82)
        # Retain the already approved fan drawing. Its shaft source endpoint
        # is registered exactly to the corresponding hand's canonical wrist.
        name='clean_fan_'+side
        wrist=(.495,.518) if side=='l' else (.526,.518)
        sx=-1 if side=='l' else 1
        mat=[[sx,0,wrist[0]*1024-sx*548],[0,1,wrist[1]*1024-543]]
        art[name]=warp(art['fan'],mat)
        layers.append({'id':name,'role':'accessory','file':'layers/'+name+'.png',
                       'draw_order':165,'opacity':0,'side':side,'clean_fan':True,
                       'shaftSourceUv':list(wrist),'tipSourceUv':[(wrist[0]*1024+sx*26)/1024,(wrist[1]*1024-65)/1024]})
    for layer in layers:
        Image.fromarray(art[layer['id']]).save(output/layer['file'])
    manifest['v5Provenance']=audit
    (output/'layers.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    (output/'layer-extraction-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    print(len(prepare(args.output)['layers']))
