"""Painted cloth correspondence tests; no complete builder, Core or Qt."""
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
import sys

import numpy as np
from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools/authoring'))
from v51_pose import DrapeSections
from build_rig import MapleBuilder, grid_mesh
from climb_refinement import (_apply_drape_transfer, transfer_refinement, DRAPE_BLEND,
                              DRAPE_UNFOLD_END, DRAPE_EXCHANGE_END)
from climb_skirt_transfer import NativeClothParents, motion_settings
from maple_motions import build_motion,TRANSITIONS,V5_CLEAN_TRANSITIONS
from diagnose_rig import interpolate_keys
from pet_core import ANIMATIONS
from native_warp_sampling import warp_points

ART = ROOT/'assets/authoring/source/layers'


@pytest.mark.parametrize('side', ['l','r'])
def test_actual_painted_cross_sections_match_and_invert(side):
    profiles = []
    for suffix in ('_climb_drape_v2',''):
        with Image.open(ART/f'profile_{side}_skirt{suffix}.png') as image:
            profiles.append(DrapeSections(np.asarray(image.getchannel('A'))))
    new, old = profiles
    ys = np.linspace(new.pin+.001, new.hem-.001, 101)
    left, right = new.edges(ys)
    painted = np.asarray([(x,y) for y,l,r in zip(ys,left,right) for x in np.linspace(l,r,9)])
    mapped = new.map(painted,old)
    np.testing.assert_allclose(old.map(mapped,new),painted,atol=1e-12,rtol=0)
    new_left,new_right = old.edges(mapped[:,1])
    np.testing.assert_allclose(mapped[::9,0],new_left[::9],atol=1e-12,rtol=0)
    np.testing.assert_allclose(mapped[8::9,0],new_right[8::9],atol=1e-12,rtol=0)
    # This is a substantial unfold of the painted knee, not an identity field
    # in transparent canvas. The texture and waist material stay unchanged.
    assert np.max(np.linalg.norm(mapped-painted,axis=1)) > .04
    waist = np.asarray([(x,y) for x in np.linspace(.3,.75,9) for y in (.516,.535,new.pin)])
    np.testing.assert_array_equal(new.map(waist,old),waist)
    np.testing.assert_array_equal(old.map(waist,new),waist)


@pytest.mark.parametrize('side', ['l','r'])
def test_painted_rows_stay_ordered_during_unfold_and_transparent_edges_do_not_explode(side):
    profiles=[]
    for suffix in ('_climb_drape_v2',''):
        with Image.open(ART/f'profile_{side}_skirt{suffix}.png') as image:
            profiles.append(DrapeSections(np.asarray(image.getchannel('A'))))
    new,old=profiles
    ys=np.linspace(new.pin+.002,new.hem-.002,61)
    points=np.asarray([(x,y) for y in ys for x in np.linspace(.25,.82,61)])
    target=new.map(points,old)
    assert np.isfinite(target).all()
    assert np.max(np.abs(target-points)) < .3
    for progress in np.linspace(0,1,21):
        grid=(points+(target-points)*progress).reshape(61,61,2)
        assert np.min(np.diff(grid[:,:,0],axis=1)) > 0
        assert np.min(np.diff(grid[:,:,1],axis=0)) > 0


@pytest.fixture(scope='module')
def gated_cloth():
    b=SimpleNamespace(nodes={},params={},layers=[],parts=[],asset_root=ART,
                      width=1024,height=1024)
    b.warp=lambda *args:MapleBuilder.warp(b,*args)
    b.parameter=lambda *args,**kwargs:MapleBuilder.parameter(b,*args,**kwargs)
    b.opacity=lambda *args:MapleBuilder.opacity(b,*args)
    for name in ('ParamPoseClimb','ParamHeadTurn','ParamBodyTurn','ParamPoseSwing',
                 'ParamClimbHandoff','ParamClimbRefine','ParamClimbActive','ParamClimbDirection',
                 'ParamRibbonLX'):
        keys=np.linspace(-1,1,21) if name=='ParamPoseClimb' else [-1,0,1] if name in (
            'ParamHeadTurn','ParamBodyTurn','ParamClimbDirection') else [0,1]
        MapleBuilder.parameter(b,name,low=min(keys),high=max(keys),keys=keys)
    b.warp('body',None,17)
    MapleBuilder.deform(b,'ParamPoseClimb','body',lambda p,v:
        (p[0]+.02*v*np.sin(np.pi*p[1]),p[1]+.04*abs(v)*p[1]*(1-p[1])))
    for side,view in [('l','left'),('r','right')]:
        for suffix,family in [('skirt','skirt'),('costume_underlay_skirt','costume_underlay_skirt'),
                              ('skirt_climb_drape','skirt')]:
            name=f'profile_{side}_{suffix}'
            is_new=suffix.endswith('climb_drape')
            layer=dict(id=name,family=family,view=view,
                       file=f'profile_{side}_skirt'+('_climb_drape_v2' if is_new else '')+'.png')
            if is_new:layer.update(pose='climb_drape',climb_replaces=f'profile_{side}_skirt')
            b.layers.append(layer)
            parent=b.warp('parent_'+name,'body',25 if is_new else 17)
            if not is_new:
                MapleBuilder.deform(b,'ParamClimbRefine',parent,lambda p,v:
                    (p[0],p[1]-.02*v*max(0,p[1]-.54)))
            b.parts.append(SimpleNamespace(id=name,parent_deformer=parent))
    b.layer_by_id={layer['id']:layer for layer in b.layers}
    before=deepcopy(b)
    _apply_drape_transfer(b)
    return before,b


def test_only_cloth_leaf_nodes_and_handoff_gate_are_added(gated_cloth):
    before,b=gated_cloth
    added=set(b.nodes)-set(before.nodes)
    assert len(added)==2
    assert {name:node for name,node in b.nodes.items() if name not in added}==before.nodes
    assert set(b.params)==set(before.params)|{DRAPE_BLEND}
    for name,parameter in b.params.items():
        if name==DRAPE_BLEND:
            continue
        clean=deepcopy(parameter)
        for key in clean.keyforms:
            for field in ('deformer_offsets','deformer_offset_weights'):
                setattr(key,field,{n:v for n,v in getattr(key,field).items() if n not in added})
        assert clean==before.params[name]
    # The receiving surfaces retain their exact geometry and parent.
    for part,old in zip(b.parts,before.parts):
        if not part.id.endswith('climb_drape'):
            assert part.parent_deformer==old.parent_deformer
    for key in b.params['ParamClimbHandoff'].keyforms:
        assert all(key.deformer_offset_weights[n]==key.value for n in added)
    for key in b.params['ParamPoseClimb'].keyforms:
        for name in added:
            source=np.asarray(b.nodes[name].grid_vertices)
            delta=np.asarray(key.deformer_offsets[name])
            np.testing.assert_array_equal(delta[source[:,1]<=555/1024],0)
            if key.value==0:
                np.testing.assert_array_equal(delta,0)


def test_native_child_sampling_preserves_every_pose_outside_handoff(gated_cloth):
    before,b=gated_cloth
    native=NativeClothParents(b)
    points=np.asarray([(x,y) for y in np.linspace(.52,.9,13) for x in np.linspace(.34,.75,13)])
    for value in np.linspace(-1,1,17):
        settings={'ParamPoseClimb':value,'ParamClimbHandoff':0}
        cache={}
        for part,original in zip(b.parts,before.parts):
            node=b.nodes[part.parent_deformer]
            parent=b.nodes[original.parent_deformer]
            actual=warp_points(points,native.grid(node.id,settings,cache),node.grid_rows)
            expected=warp_points(points,native.grid(parent.id,settings,cache),parent.grid_rows)
            np.testing.assert_allclose(actual,expected,atol=1e-13,rtol=0)


def test_entire_lower_lattice_has_one_positive_affine_field_without_alpha_cutoffs(gated_cloth):
    _,b=gated_cloth
    for key in b.params['ParamPoseClimb'].keyforms:
        for name,delta in key.deformer_offsets.items():
            if not name.startswith('DrapeTransfer_'):
                continue
            source=np.asarray(b.nodes[name].grid_vertices)
            delta=np.asarray(delta)
            below=source[:,1]>555/1024
            coefficients=delta[below]/(source[below,1,None]-555/1024)
            np.testing.assert_allclose(coefficients,np.broadcast_to(coefficients[0],coefficients.shape),
                                       rtol=0,atol=1e-13)
            # Every intermediate gate retains a positive determinant. The
            # trailing hem is neither shortened nor cut off in empty cells.
            assert coefficients[:,1].min()>=-1e-13
            assert coefficients[:,1].max()<=.12+1e-13
            assert np.abs(coefficients[:,0]).max()<=.35+1e-13


def test_painted_triangles_keep_orientation_during_partial_handoff(gated_cloth):
    _,b=gated_cloth
    native=NativeClothParents(b)
    materials={}
    for part in b.parts:
        with Image.open(ART/b.layer_by_id[part.id]['file']) as image:
            alpha=np.asarray(image.getchannel('A'))
            bounds=np.asarray(image.getchannel('A').getbbox())/1024
        mesh=grid_mesh(part.id,bounds,17,17)
        uv=np.asarray(mesh.uvs);tri=np.asarray(mesh.triangles)
        pixel=np.clip((uv[tri].mean(axis=1)*1024).astype(int),0,1023)
        materials[part.id]=(uv,tri,alpha[pixel[:,1],pixel[:,0]]>=128)
    for sign in (-1,1):
        for pose in (.95,.88,.78,.70,.62,.54):
            seconds=(1-pose)*1.8
            refine=transfer_refinement(seconds)
            motion=build_motion('climb_to_top_left',TRANSITIONS['climb_to_top_left'],
                                {n:p.default for n,p in b.params.items()})
            blend=motion_settings(motion,seconds)[DRAPE_BLEND]
            for gate in (.25,.5,1.):
                settings={'ParamPoseClimb':sign*pose,'ParamClimbRefine':refine,
                          'ParamClimbHandoff':gate,'ParamBodyTurn':sign,'ParamHeadTurn':sign,DRAPE_BLEND:blend}
                cache={}
                for part in b.parts:
                    if b.layer_by_id[part.id]['view'] != ('left' if sign<0 else 'right'):
                        continue
                    is_new=part.id.endswith('climb_drape')
                    if (blend if is_new else 1-blend)<=.01:
                        continue
                    node=b.nodes[part.parent_deformer];uv,tri,painted=materials[part.id]
                    points=warp_points(uv,native.grid(node.id,settings,cache),node.grid_rows)[tri]
                    a,c=points[:,1]-points[:,0],points[:,2]-points[:,0]
                    area=a[:,0]*c[:,1]-a[:,1]*c[:,0]
                    assert area[painted].min()>0,(part.id,pose,gate,float(area[painted].min()))


@pytest.mark.parametrize('side',['left','right'])
def test_material_exchange_waits_for_unfold_without_changing_support_lanes(gated_cloth,side):
    _,b=gated_cloth
    defaults={n:p.default for n,p in b.params.items()}
    old_defaults={n:v for n,v in defaults.items() if n!=DRAPE_BLEND}
    for state,spec in [('climb_'+side,ANIMATIONS['climb_'+side]),
                       ('climb_to_top_'+side,TRANSITIONS['climb_to_top_'+side])]:
        new=build_motion(state,spec,defaults)
        old=build_motion(state,spec,old_defaults)
        assert [c for c in new['Curves'] if c['Id']!=DRAPE_BLEND]==old['Curves']
        assert motion_settings(new,0)[DRAPE_BLEND]==1
        if state.startswith('climb_to_top'):
            assert motion_settings(new,DRAPE_UNFOLD_END)[DRAPE_BLEND]==1
            assert motion_settings(new,DRAPE_EXCHANGE_END)[DRAPE_BLEND]==pytest.approx(0,abs=1e-12)
            assert motion_settings(new,1.8)[DRAPE_BLEND]==0
        else:
            assert motion_settings(new,1)[DRAPE_BLEND]==1


@pytest.mark.parametrize('side',['left','right'])
def test_wall_clean_take_loop_and_stow_keep_climbing_material(gated_cloth,side):
    _,b=gated_cloth
    defaults={n:p.default for n,p in b.params.items()}
    defaults.update(ParamCleanBrushR=0.,ParamCleanBrushL=0.)
    for suffix in ('','_enter','_exit'):
        state='clean_climb_'+side+suffix
        spec=V5_CLEAN_TRANSITIONS[state] if suffix else ANIMATIONS[state]
        motion=build_motion(state,spec,defaults)
        for time in np.linspace(0,motion['Meta']['Duration'],11):
            assert motion_settings(motion,float(time))[DRAPE_BLEND]==pytest.approx(1,abs=1e-12,rel=0)


@pytest.mark.parametrize('state',['idle','drag_left','drag_right','fall_float','land',
                                  'clean_ground','clean_top','clean_ground_enter','clean_ground_exit',
                                  'clean_top_enter','clean_top_exit'])
def test_unattached_and_nonwall_states_restore_receiving_material(gated_cloth,state):
    _,b=gated_cloth
    defaults={n:p.default for n,p in b.params.items()}
    defaults.update(ParamCleanBrushR=0.,ParamCleanBrushL=0.)
    spec=V5_CLEAN_TRANSITIONS[state] if state in V5_CLEAN_TRANSITIONS else ANIMATIONS[state]
    motion=build_motion(state,spec,defaults)
    for time in np.linspace(0,motion['Meta']['Duration'],11):
        assert motion_settings(motion,float(time))[DRAPE_BLEND]==0


def test_receiving_surface_stays_opaque_during_material_exchange(gated_cloth):
    _,b=gated_cloth
    for value in (0,.1,.5,.9,.9999,.99999,1):
        opacity={name:sum(key.opacity_overrides[name]*weight for key,weight in
                         interpolate_keys(b.params[DRAPE_BLEND],value)) for name in
            ('profile_l_skirt_climb_drape','profile_l_skirt','profile_l_costume_underlay_skirt')}
        new=opacity['profile_l_skirt_climb_drape'];old=opacity['profile_l_skirt']
        assert new==pytest.approx(value)
        assert old==opacity['profile_l_costume_underlay_skirt']
        assert new+(1-new)*old>=.9999
        if value==1:assert old==0
        if value==0:assert old==1
