"""Final authoring keys and a tiny serialized parent/mesh opacity fixture."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import numpy as np
from PIL import Image
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/authoring'))
from build_v4 import original_view_coverage,view_weight,TURN_KEYS,PAINT_ORDER,layer_draw_order
from head_view_exchange import apply_head_view_exchange,natural_far_family,grouped_head_base_orders
from build_rig import Parameter,Keyform,Part,Texture,Deformer,DeformerType,Meta,Rig,grid_mesh,corrected_project
from image2live2d.backends.live2d.cmo3 import unpack_caff

MANIFEST=ROOT/'assets/authoring/revisions/v5-contour-fan-20260910/layers.json'
LAYERS=json.loads(MANIFEST.read_text(encoding='utf-8'))['layers']
CHANGED={'ribbon_l','ribbon_r','profile_l_ribbon_l','profile_r_ribbon_r','ornament_l','ornament_r'}

def fixture_builder(layers=LAYERS):
    head=Parameter(id='ParamHeadTurn',min=-1,max=1,keyforms=[Keyform(value=t,
        deformer_opacity_overrides={'View_head_'+v:view_weight(v,t) for v in PAINT_ORDER}) for t in TURN_KEYS])
    params={head.id:head}
    # Existing independent detail geometry/opacity bindings must remain byte
    # equivalent. These axes must never gain a second HeadTurn factor.
    for name in ('ParamEyeLOpen','ParamEyeROpen','ParamEyeBallX','ParamEyeBallY','ParamMouthOpenY'):
        params[name]=Parameter(id=name,min=-1,max=1,keyforms=[Keyform(value=t,
            mesh_offsets={'pupil_l':[(t*.01,0)]},opacity_overrides={'eye_closed_l':.5-t*.5}) for t in (-1.,0.,1.)])
    parts=[SimpleNamespace(id=l['id'],draw_order=layer_draw_order(l)) for l in layers]
    return SimpleNamespace(layers=layers,parts=parts,params=params,meshes={'sentinel':{'vertices':[(0.,0.)],'uvs':[(0.,0.)]}})

def apply(builder):apply_head_view_exchange(builder,original_view_coverage,view_weight,PAINT_ORDER)

def final_opacity(builder,layer,turn):
    keys=builder.params['ParamHeadTurn'].keyforms
    parent=np.interp(turn,TURN_KEYS,[key.deformer_opacity_overrides['View_head_'+layer['view']] for key in keys])
    mesh=np.interp(turn,TURN_KEYS,[key.opacity_overrides.get(layer['id'],1) for key in keys])
    return parent*mesh

def test_final_base_orders_keep_detail_body_back_layers_and_within_view_order():
    b=fixture_builder();before={p.id:p.draw_order for p in b.parts};apply(b);after={p.id:p.draw_order for p in b.parts}
    changed={name for name in before if before[name]!=after[name]}
    base={l['id'] for l in LAYERS if l.get('zone')=='head' and l['role'] in ('face_base','hair_front','hair_side')}
    assert len(base)==22 and sorted(after[n] for n in base)==list(range(180,202))
    assert changed<=base
    for name in before.keys()-base:assert after[name]==before[name]
    for view in PAINT_ORDER:
        ids=sorted((l['id'] for l in LAYERS if l['id'] in base and l['view']==view),key=before.get)
        assert [after[n] for n in ids]==sorted(after[n] for n in ids)
    assert max(after[n] for n in base)<220
    assert after['glasses']==245

def test_only_six_far_lower_mesh_axes_change_and_detail_bindings_remain_exact():
    b=fixture_builder();original=copy.deepcopy(b);apply(b)
    head=b.params['ParamHeadTurn'];old=original.params['ParamHeadTurn']
    assert {name for k in head.keyforms for name in k.opacity_overrides}==CHANGED
    for name in b.params.keys()-{'ParamHeadTurn'}:assert b.params[name].model_dump()==original.params[name].model_dump()
    for key,before in zip(head.keyforms,old.keyforms):
        actual=key.model_dump();actual['opacity_overrides']={}
        assert actual==before.model_dump()
    assert b.meshes==original.meshes
    for layer in LAYERS:
        view=layer.get('view')
        if layer.get('zone')=='head' and view in PAINT_ORDER:
            assert final_opacity(b,layer,{'front':0,'left':-1,'left_mid':-.5,'right':1,'right_mid':.5}[view])==1

@pytest.mark.parametrize('first,last',[(-.3,-.2),(-.8,-.7),(.2,.3),(.7,.8)])
@pytest.mark.parametrize('reverse',[False,True])
def test_real_key_products_follow_far_coverage_in_every_exchange_and_direction(first,last,reverse):
    b=fixture_builder();apply(b)
    points=sorted({float(x) for a,z in zip(TURN_KEYS,TURN_KEYS[1:]) if first<=a and z<=last for x in np.linspace(a,z,33)})
    if reverse:points.reverse()
    for layer in LAYERS:
        if layer.get('zone')!='head' or layer.get('view') not in PAINT_ORDER:continue
        view=layer['view']
        for turn in points:
            if natural_far_family(layer.get('family'),turn):
                expected=np.interp(turn,TURN_KEYS,[original_view_coverage(t).get(view,0) for t in TURN_KEYS])
            else:
                expected=np.interp(turn,TURN_KEYS,[view_weight(view,t) for t in TURN_KEYS])
            assert abs(final_opacity(b,layer,turn)-expected)<7.6e-7

def test_hidden_guard_factor_cannot_flash_between_nearly_invisible_endpoints():
    b=fixture_builder();apply(b);keys=b.params['ParamHeadTurn'].keyforms
    layer=next(l for l in LAYERS if l['id']=='ornament_l')
    for turn in np.linspace(-.3,-.2999,101):assert final_opacity(b,layer,turn)<=3.0e-6
    hidden=keys[TURN_KEYS.index(-.3)]
    assert hidden.opacity_overrides['ornament_l']==0
    # A superficially harmless factor1 at the hidden parent endpoint makes
    # both endpoint products tiny but creates the previously missed .25 flash.
    hidden.opacity_overrides['ornament_l']=1
    assert final_opacity(b,layer,-.29995)>.249

def test_final_key_opacity_is_exact_and_source_endpoints_do_not_fade():
    b=fixture_builder();apply(b)
    for layer in LAYERS:
        if layer.get('zone')!='head' or layer.get('view') not in PAINT_ORDER:continue
        for turn in TURN_KEYS:
            expected=(original_view_coverage(turn).get(layer['view'],0) if natural_far_family(layer.get('family'),turn)
                      else view_weight(layer['view'],turn))
            assert final_opacity(b,layer,turn)==pytest.approx(expected,abs=1e-15)

def test_head_exchange_rejects_competing_animated_base_order():
    b=fixture_builder();b.params['ParamHeadTurn'].keyforms[0].draw_order_overrides['hair_front']=209
    with pytest.raises(ValueError,match='Animated head base'):apply(b)

def test_three_mesh_native_serializer_keeps_parent_opacity_mesh_factors_and_texture_coordinates(tmp_path):
    # A four-vertex, three-mesh fixture exercises the actual writer, not a full
    # character builder and not Core. No fixture image is used by the product.
    names=('ornament_l','ornament_r','eye_l')
    layers=[l for l in LAYERS if l['id'] in names]
    b=fixture_builder();apply(b)
    b.params={'ParamHeadTurn':b.params['ParamHeadTurn']}
    for key in b.params['ParamHeadTurn'].keyforms:
        key.opacity_overrides={name:value for name,value in key.opacity_overrides.items() if name in names}
    image=tmp_path/'fixture.png';Image.new('RGBA',(8,8),(160,100,80,255)).save(image)
    grid=[(0.,0.),(1.,0.),(0.,1.),(1.,1.)]
    nodes=[Deformer(id='View_head_'+view,type=DeformerType.warp,grid_rows=2,grid_cols=2,grid_vertices=grid) for view in PAINT_ORDER]
    orders={p.id:p.draw_order for p in b.parts}
    parts=[Part(id=l['id'],semantic_role=l['role'],texture_id='fixture',draw_order=orders[l['id']],parent_deformer='View_head_front') for l in layers]
    meshes=[grid_mesh(l['id'],(.2,.2,.8,.8),2,2) for l in layers]
    model=Rig(meta=Meta(name='HeadExchangeFixture'),textures=[Texture(id='fixture',path='fixture.png',width=8,height=8)],parts=parts,meshes=meshes,deformers=nodes,parameters=list(b.params.values()))
    original_meshes=[mesh.model_dump() for mesh in meshes]
    entries=unpack_caff(corrected_project(model,tmp_path))
    assert [mesh.model_dump() for mesh in meshes]==original_meshes
    root=ET.fromstring(next(e.content for e in entries if e.path=='main.xml'))
    sources={e.find('./ACDrawableSource/CDrawableId').get('idstr'):e for e in root.iter('CArtMeshSource') if e.get('xs.id')}
    far_values={float(e.text) for e in sources['ornament_l'].findall('.//ACDrawableForm/f[@xs.n="opacity"]')}
    assert {0.,.5,1.}<=far_values
    # Near right ornament is intentionally far on the opposite turn, but
    # the eye never receives any material-retirement forms or alpha factors.
    eye_values={float(e.text) for e in sources['eye_l'].findall('.//ACDrawableForm/f[@xs.n="opacity"]')}
    assert eye_values=={1.}
    for source in sources.values():
        assert list(map(float,source.find('./float-array[@xs.n="positions"]').text.split()))==pytest.approx([1.6,1.6,6.4,1.6,1.6,6.4,6.4,6.4])
        name=source.find('./ACDrawableSource/CDrawableId').get('idstr')
        for form in source.iter('CArtMeshForm'):
            assert int(form.find('./ACDrawableForm/i[@xs.n="drawOrder"]').text)==orders[name]
