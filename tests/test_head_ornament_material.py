"""Small original-art and native-triangle checks; never instantiate a full rig."""
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import numpy as np
from PIL import Image
import pytest
from tools.authoring import view_registration as reg
from tools.authoring.head_material_registration import (ORNAMENT_BEADS, SCALP_PARTINGS,
    material_sources, head_sampling_correction, ornament_targets, refine_ornament_material_meshes)
from tools.authoring.native_warp_sampling import warp_points, triangle_lattice_weights

SOURCE=Path(__file__).resolve().parents[1]/'assets/authoring/source/layers'
PREFIX={'front':'','left':'profile_l_','left_mid':'mid_l_','right':'profile_r_','right_mid':'mid_r_'}
TURNS=[-1.,-.8,-.7999,-.75,-.7001,-.7,-.5,-.3,-.2999,-.25,-.2001,-.2,0.,.2,.2001,.25,.2999,.3,.5,.7,.7001,.75,.7999,.8,1.]
GRID=np.array([(x/64,y/64) for y in range(65) for x in range(65)])
CHILD=np.array([(x/12,y/12) for y in range(13) for x in range(13)])

@lru_cache(maxsize=5)
def parent_keys(view):
    boxes={}
    for family in {family for _,family,_ in material_sources(view)}:
        with Image.open(SOURCE/(PREFIX[view]+family+'.png')) as im:boxes[family]=np.array(im.getchannel('A').getbbox())/1024
    samples,correction,centers,_=head_sampling_correction(view,boxes,GRID)
    out=[]
    for turn in TURNS:
        parent=np.array([reg.register_point(tuple(p),view,turn,'head') for p in GRID])
        if abs(turn-reg.VIEW_ANGLES[view])<=.300001 and turn!=reg.VIEW_ANGLES[view]:
            target=np.array([reg.register_point(tuple(p),view,turn,'head') for p in centers])
            parent+=correction@(target-samples@parent)
        out.append(parent)
    return out

@lru_cache(maxsize=1)
def small_builder():
    keys=[SimpleNamespace(value=t,deformer_offsets={},mesh_offsets={}) for t in TURNS]
    builder=SimpleNamespace(nodes={},meshes={},parts=[],layers=[],layer_bounds={},params={'ParamHeadTurn':SimpleNamespace(keyforms=keys)})
    for family,views in ORNAMENT_BEADS.items():
        for view in views:
            name=PREFIX[view]+family;parent='View_head_'+view;child='Sway_'+name
            with Image.open(SOURCE/(name+'.png')) as im:box=np.array(im.getchannel('A').getbbox())/1024
            low=np.maximum(0,box[:2]-2/1024);high=np.minimum(1,box[2:]+2/1024)
            vertices=np.array([low+np.array([x,y])/16*(high-low) for y in range(17) for x in range(17)])
            builder.nodes[parent]=SimpleNamespace(id=parent,parent='Head',grid_vertices=GRID,grid_rows=65)
            builder.nodes[child]=SimpleNamespace(id=child,parent=parent,grid_vertices=CHILD.tolist(),grid_rows=13)
            builder.parts.append(SimpleNamespace(id=name,parent_deformer=child))
            builder.meshes[name]=SimpleNamespace(vertices=vertices)
            builder.layers.append({'id':name,'family':family,'view':view,'zone':'head'})
            builder.layer_bounds[name]=box
            for key,grid in zip(keys,parent_keys(view)):key.deformer_offsets[parent]=grid-GRID
    refine_ornament_material_meshes(builder)
    return builder

def test_scalp_and_near_bead_addresses_are_original_painted_pixels():
    for view,point in SCALP_PARTINGS.items():
        with Image.open(SOURCE/(PREFIX[view]+'hair_front.png')) as im:assert im.getpixel(tuple(map(int,point)))[3]>128
    for family,views in ORNAMENT_BEADS.items():
        for view,points in views.items():
            with Image.open(SOURCE/(PREFIX[view]+family+'.png')) as im:
                for point in points:assert im.getpixel(tuple(map(int,point)))[3]>128
    assert ornament_targets('ornament_l','left_mid',-.25) is None
    assert ornament_targets('ornament_r','right_mid',.25) is None


def test_individual_beads_remeasure_from_original_color_rois():
    fixture=json.loads((Path(__file__).parent/'fixtures/original_head_material_controls12.json').read_text(encoding='utf-8'))
    for relative,expected in fixture['sourceHashes'].items():
        assert hashlib.sha256((SOURCE.parent/relative).read_bytes()).hexdigest()==expected
    for item in fixture['measurementDetail']:
        path=SOURCE/(item['layer']+'.png')
        x0,y0,x1,y1=item['sourceRoi']
        with Image.open(path) as im:rgba=np.array(im.convert('RGBA'),dtype=float)[y0:y1,x0:x1]
        r,g,b,a=np.moveaxis(rgba,-1,0)
        mask=(r>155)&(r>1.15*g)&(b>.9*g)&(b>100)&(a>128)
        y,x=np.nonzero(mask)
        assert len(x)==item['selectedPixelCount']
        point=[round(float(x.mean()+x0+.5),2),round(float(y.mean()+y0+.5),2)]
        assert point==item['pointPixelCenter']

@pytest.mark.parametrize('family,view',[(f,v) for f,views in ORNAMENT_BEADS.items() for v in views])
def test_existing_ornament_head_keys_preserve_root_endpoints_and_continuous_native_beads(family,view):
    builder=small_builder();name=PREFIX[view]+family;source=np.array(builder.meshes[name].vertices)
    keys=builder.params['ParamHeadTurn'].keyforms;parent=parent_keys(view)
    beads=np.array(ORNAMENT_BEADS[family][view])/1024
    low,high=source.min(axis=0),source.max(axis=0)
    samples=[]
    for point in beads:
        indices,weights=triangle_lattice_weights((point-low)/(high-low),17)
        row=np.zeros(len(source));row[indices]=weights;samples.append(row)
    samples=np.array(samples)
    assert name not in keys[TURNS.index(reg.VIEW_ANGLES[view])].mesh_offsets
    for i,(a,b) in enumerate(zip(TURNS,TURNS[1:])):
        if max(abs(a-reg.VIEW_ANGLES[view]),abs(b-reg.VIEW_ANGLES[view]))>.300001:continue
        for f in (0.,.23,.57,1.):
            turn=a+(b-a)*f;target=ornament_targets(family,view,turn)
            if target is None:continue
            offsets=[np.asarray(keys[j].mesh_offsets.get(name,np.zeros_like(source))) for j in (i,i+1)]
            local=source+offsets[0]*(1-f)+offsets[1]*f
            child=warp_points(CHILD,parent[i]*(1-f)+parent[i+1]*f,65)
            actual=warp_points(local,child,13)
            assert np.linalg.norm(samples@actual-target,axis=1).max()*384<.12
            root=source[:,1]<=builder.layer_bounds[name][1]+20/1024
            np.testing.assert_allclose(local[root],source[root],atol=1e-12)
            mesh=actual.reshape(17,17,2)
            for u,v in ((mesh[:-1,1:]-mesh[:-1,:-1],mesh[1:,:-1]-mesh[:-1,:-1]),
                        (mesh[1:,1:]-mesh[:-1,1:],mesh[1:,:-1]-mesh[:-1,1:])):
                assert np.min(u[:,:,0]*v[:,:,1]-u[:,:,1]*v[:,:,0])>0
