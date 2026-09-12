"""Lightweight body-material checks; no model build, Core or Qt fixture.

Native-grid replay below checks source geometry only, never visual acceptance.
"""
import copy
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools/authoring'))
from v51_pose import (free_arm_joints, hanging_material_point, joint_delta_point,
                      sleeve_point, full_sleeve_point, expand_climb_material_layers)
from free_arm_refinement import original_sleeve_point
from native_warp_sampling import warp_points
from climb_refinement import (CYCLE_DURATION, RISE, climb_metadata,
                              drape_phase_point, _apply_climb_drape_materials,
                              climb_leg_width, leg_registration, _leg_mapping,
                              _handoff_map, refined_leg_joints, AIRBORNE_RETRACTION)

SOURCES = {'l': ((.434,.390),(.439,.465),(.495,.518)),
           'r': ((.568,.390),(.572,.463),(.526,.518))}
ART = ROOT/'assets/authoring/revisions/v5-head-coverage-20260910/layers'


@pytest.mark.parametrize('side', ['l','r'])
def test_free_and_braced_arm_rotate_real_bones_and_preserve_canonical(side):
    source = SOURCES[side]
    lengths = [math.dist(a,b) for a,b in zip(source,source[1:])]
    for pose in np.linspace(0,1,21):
        for brace in (0,.5,1):
            joints = free_arm_joints(source,side,pose,brace)
            assert joints[0] == source[0]
            assert [math.dist(a,b) for a,b in zip(joints,joints[1:])] == pytest.approx(lengths)
            if pose <= .05:
                assert np.asarray(joints) == pytest.approx(np.asarray(source))
    hanging = free_arm_joints(source,side,1)
    braced = free_arm_joints(source,side,1,1)
    # Upper arms descend, with the elbow close to the shoulder rather than
    # stretching a sleeve horizontally toward the old wide wrist target.
    assert hanging[1][1] > source[0][1]
    assert abs(hanging[1][0]-source[0][0]) < .03
    assert braced[2][1] < hanging[2][1]


@pytest.mark.parametrize('side', ['l','r'])
def test_original_hand_and_sleeve_share_complete_native_cuff_cell(side):
    builder = SimpleNamespace(_joints=lambda s:SOURCES[s])
    wrist = np.asarray(SOURCES[side][2])
    cell = np.floor(wrist*16)/16
    for pose in np.linspace(0,1,9):
        for delta in ((0,0),(1/16,0),(0,1/16),(1/16,1/16)):
            point = cell+delta
            cloth = original_sleeve_point(builder,side,point,pose,False)
            hand = original_sleeve_point(builder,side,point,pose,True)
            assert hand == cloth


@pytest.mark.parametrize('side', ['l','r'])
def test_long_hem_retains_gravity_length_without_wrist_extrapolation(side):
    source = SOURCES[side]
    target = free_arm_joints(source,side,1)
    x = source[2][0] + (-.12 if side=='l' else .12)
    points = [(x,.72),(x,.82)]
    mapped = [hanging_material_point(p,source,target,1) for p in points]
    assert np.subtract(mapped[1],mapped[0]) == pytest.approx((0,.10))
    braced = free_arm_joints(source,side,1,1)
    moved = [joint_delta_point(p,target,braced) for p in mapped]
    assert np.subtract(moved[1],moved[0]) == pytest.approx((0,.10))


@pytest.mark.parametrize('side', ['l','r'])
def test_original_painted_mesh_survives_native_triangle_lattice_sampling(side):
    alpha = np.asarray(Image.open(ART/f'arm_{side}.png').getchannel('A'))
    yy,xx = np.where(alpha>8)
    xs = np.linspace((xx.min()-2)/1024,(xx.max()+3)/1024,17)
    ys = np.linspace((yy.min()-2)/1024,(yy.max()+3)/1024,17)
    material = np.array([(x,y) for y in ys for x in xs])
    triangles = np.array([tri for y in range(16) for x in range(16) for a in [y*17+x]
                          for tri in ((a,a+1,a+17),(a+1,a+18,a+17))])
    center = material[triangles].mean(axis=1)
    painted = alpha[(center[:,1]*1024).astype(int),(center[:,0]*1024).astype(int)]>8
    grid = np.array([(x/16,y/16) for y in range(17) for x in range(17)])
    source = SOURCES[side]
    for pose in (0,.2,.4,.6,.8,1):
        target = free_arm_joints(source,side,pose)
        amount = max(0,(pose-.05)/.95)
        first = np.array([hanging_material_point(p,source,target,amount) for p in grid])
        for brace in (0,.5,1):
            hanging = free_arm_joints(source,side,1)
            braced = free_arm_joints(source,side,1,brace)
            outer = np.array([p+(np.asarray(joint_delta_point(p,hanging,braced))-p)*amount for p in grid])
            # Core prewarps a child lattice before sampling the mesh through
            # it. This is deliberately not just pointwise function composition.
            effective = warp_points(first,outer,17)
            actual = warp_points(material,effective,17)[triangles]
            a,b = actual[:,1]-actual[:,0],actual[:,2]-actual[:,0]
            areas = a[:,0]*b[:,1]-a[:,1]*b[:,0]
            assert np.all(areas[painted]>0), (side,pose,brace)


@pytest.mark.parametrize('side', ['l','r'])
def test_rounded_supported_sleeve_preserves_entire_elbow_seam_and_tangent(side):
    from build_v4 import V4Builder,swing_arm_joints
    builder = SimpleNamespace(_joints=lambda s:SOURCES[s])
    source = V4Builder._source_joints(side)
    target = swing_arm_joints(builder,side)
    sign = 1 if side=='l' else -1
    for drop in (0,.04,.10):
        point = np.array(source[1])+(0,drop)
        upper = sleeve_point(point,source,target,'upper',roundness=1)
        lower = sleeve_point(point,source,target,'lower',roundness=1)
        assert upper == pytest.approx(lower,abs=1e-12)
        epsilon = 1e-7
        before = sleeve_point(point+(sign*epsilon,0),source,target,'upper',roundness=1)
        after = sleeve_point(point-(sign*epsilon,0),source,target,'lower',roundness=1)
        assert (np.asarray(upper)-before)/(-sign*epsilon) == pytest.approx(
            (np.asarray(after)-lower)/(-sign*epsilon),abs=1e-5)
    for point,segment,index in ((source[0],'upper',0),(source[2],'lower',2)):
        assert sleeve_point(point,source,target,segment,roundness=1) == pytest.approx(target[index])


@pytest.mark.parametrize('side', ['l','r'])
def test_supported_parts_share_native_field_through_parent_lattice_resampling(side):
    from build_rig import MapleBuilder
    from build_v5 import V5Builder
    # Construct only the arm component; no layers, full model or exporter.
    builder = V5Builder.__new__(V5Builder)
    MapleBuilder.__init__(builder,{'width':1024,'height':1024,'layers':[{'id':'stub'}]},'.')
    builder.scene = builder.warp('Scene')
    names = [builder._active_arm(side,segment) for segment in ('upper','lower')]
    source = builder._source_joints(side)
    seam = [(source[1][0],source[1][1]+drop) for drop in (0,.01,.04,.10)]
    # A nonlinear 13-row ancestor reproduces the failure condition: two
    # seemingly equal cuts cease to match when their child grids differ.
    outer = [(x/12+.02*math.sin(math.pi*y/12),
              y/12+.035*math.sin(2*math.pi*x/12)) for y in range(13) for x in range(13)]
    for key in builder.params['ParamPoseClimb'].keyforms:
        fields = [np.asarray(builder.nodes[name].grid_vertices)+key.deformer_offsets[name] for name in names]
        endpoints = [warp_points(seam,warp_points(field,outer,13),17) for field in fields]
        assert endpoints[0] == pytest.approx(endpoints[1],abs=1e-12)


@pytest.mark.parametrize('side', ['l','r'])
def test_shared_full_field_keeps_shoulder_and_wrist_native_cells_unchanged(side):
    from build_v4 import V4Builder,swing_arm_joints
    builder = SimpleNamespace(_joints=lambda s:SOURCES[s])
    source = V4Builder._source_joints(side)
    target = swing_arm_joints(builder,side)
    for index,segment in ((0,'upper'),(2,'lower')):
        cell = np.floor(np.asarray(source[index])*16)/16
        for offset in ((0,0),(1/16,0),(0,1/16),(1/16,1/16)):
            point = cell+offset
            assert full_sleeve_point(point,source,target,roundness=1) == pytest.approx(
                sleeve_point(point,source,target,segment,roundness=1),abs=1e-12)


@pytest.mark.parametrize('side', ['l','r'])
def test_swing_correction_preserves_complete_native_seam_and_original_grip_travel(side):
    from build_rig import MapleBuilder,rotate
    from build_v4 import palm_on_rope
    from build_v5 import V5Builder
    builder=V5Builder.__new__(V5Builder)
    MapleBuilder.__init__(builder,{'width':1024,'height':1024,'layers':[{'id':'stub'}]},'.')
    builder.scene=builder.warp('Scene')
    names=[builder._active_arm(side,s) for s in ('upper','lower')]
    source=builder._source_joints(side)
    seam=[(source[1][0],source[1][1]+drop) for drop in (0,.01,.03,.07,.10,.14)]
    def evaluate(name,points,swing):
        settings={'ParamArmPoseMode':1,'ParamSwing':swing}
        cache={}
        def grid(node_name):
            if node_name in cache:
                return cache[node_name]
            node=builder.nodes[node_name]
            result=np.asarray(node.grid_vertices,dtype=float)
            delta=np.zeros_like(result);weight=1.
            for parameter in builder.params.values():
                key=next(k for k in parameter.keyforms if k.value==settings.get(parameter.id,parameter.default))
                delta+=np.asarray(key.deformer_offsets.get(node_name,np.zeros_like(result)))
                weight*=key.deformer_offset_weights.get(node_name,1.)
            result+=delta*weight
            if node.parent and node.parent!=builder.scene:
                parent=builder.nodes[node.parent]
                result=warp_points(result,grid(node.parent),parent.grid_rows,parent.grid_cols)
            cache[node_name]=result
            return result
        node=builder.nodes[name]
        return warp_points(points,grid(name),node.grid_rows,node.grid_cols)
    neutral_shoulder=evaluate(names[0],[source[0]],0)[0]
    neutral_grip=evaluate(names[1],[source[2]],0)[0]
    for swing in (-1,0,1):
        upper=evaluate(names[0],seam,swing)
        lower=evaluate(names[1],seam,swing)
        assert upper==pytest.approx(lower,abs=1e-12)
        assert evaluate(names[0],[source[0]],swing)[0]==pytest.approx(neutral_shoulder,abs=1e-12)
        expected=np.asarray(rotate(palm_on_rope(side,swing),(.508,0),-8*swing))-palm_on_rope(side,0)
        assert evaluate(names[1],[source[2]],swing)[0]-neutral_grip==pytest.approx(expected,abs=1e-12)


def test_climb_material_inventory_is_opt_in_idempotent_and_retains_original():
    layer = {'id':'profile_l_skirt','role':'clothing','family':'skirt','view':'left',
             'file':'original.png','draw_order':90,'has_seated_variant':True,
             'climbMaterialFile':'new.png'}
    manifest = {'version':5,'layers':[copy.deepcopy(layer)]}
    expand_climb_material_layers(manifest)
    expand_climb_material_layers(manifest)
    assert manifest['layers'][0] == layer
    assert len(manifest['layers']) == 2
    new = manifest['layers'][1]
    assert new['file']=='new.png' and new['pose']=='climb_drape'
    assert not new['has_seated_variant'] and new['climb_replaces']==layer['id']


def test_new_drape_retires_matching_underlay_without_affecting_other_views():
    layers = [dict(id='old',family='skirt',view='left'),
              dict(id='underlay',family='costume_underlay_skirt',view='left'),
              dict(id='other',family='costume_underlay_skirt',view='right'),
              dict(id='new',pose='climb_drape',view='left',climb_replaces='old',
                   file='profile_l_skirt.png')]
    opacity, fields = {},{}
    builder = SimpleNamespace(layers=layers,parts=[SimpleNamespace(id=l['id'],parent_deformer='body') for l in layers],
        asset_root=ART,width=1024,height=1024,params={name:SimpleNamespace(keyforms=[]) for name in
            ('ParamClimbDirection','ParamClimbRefine')},
        warp=lambda name,parent,size:name,
        opacity=lambda parameter,name,fn:opacity.setdefault(name,fn),
        deform=lambda parameter,name,fn:fields.setdefault(name,fn))
    _apply_climb_drape_materials(builder)
    for value in (0,.3,1):
        assert opacity['new'](value)+opacity['old'](value)==pytest.approx(1)
        assert opacity['old'](value)==opacity['underlay'](value)
    assert 'other' not in opacity
    assert len(fields)==1


@pytest.mark.parametrize('direction,view', [(-1,'left'),(1,'right')])
def test_painted_knee_fold_is_not_gathered_again_and_keeps_waist_train(direction,view):
    panel=(.3,.516,.75,.90)
    for phase in np.linspace(0,1,31):
        for point in ((.44,.529),(.56,.54),(.4,.565),(.34,.9),(.72,.9)):
            assert drape_phase_point(point,phase,direction,view,panel)==pytest.approx(point,abs=1e-10)
    for point in ((.38,.63),(.65,.69)):
        assert drape_phase_point(point,0,direction,view,panel)==pytest.approx(point)
        assert drape_phase_point(point,1,direction,view,panel)==pytest.approx(point)
    assert CYCLE_DURATION == climb_metadata()['cycleDuration'] == 1
    assert RISE == .12


@pytest.mark.parametrize('side',['l','r'])
def test_swing_original_shoulder_ring_is_gathered_without_moving_joints(side):
    from build_v4 import V4Builder,swing_arm_joints
    builder=SimpleNamespace(_joints=lambda s:SOURCES[s])
    source=V4Builder._source_joints(side)
    target=swing_arm_joints(builder,side)
    alpha=np.asarray(Image.open(ART/f'sleeve_{side}_upper.png').getchannel('A'))
    yy,xx=np.where(alpha>128)
    beyond=(xx/1024-source[0][0])*(1 if side=='l' else -1)>0
    cap=np.column_stack(((xx[beyond]+.5)/1024,(yy[beyond]+.5)/1024))
    mapped=np.asarray([full_sleeve_point(p,source,target,roundness=1) for p in cap])
    # The original flared opening projected roughly 4.8 px above S. A narrow
    # rounded cloth cap must now stay within 2.5 px, without changing the PNG.
    assert (target[0][1]-mapped[:,1].min())*384 < 2.5
    assert np.ptp(mapped[:,0])*384 < 5.6
    for point,index in zip(source,(0,1,2)):
        assert full_sleeve_point(point,source,target,roundness=1)==pytest.approx(target[index])


def test_climb_trouser_volume_is_transverse_and_tapers_before_the_shoe():
    source=((.48,.612),(.469,.690),(.456,.778))
    target=refined_leg_joints('l',1,.25,.038)
    for value in (.63,.66,.70,.72):
        point=(.48,value)
        scale=climb_leg_width(point,source)
        assert scale==pytest.approx(1.18)
        a,b=source[:2] if value<source[1][1] else source[1:]
        c,d=target[:2] if value<source[1][1] else target[1:]
        vector=np.subtract(b,a);unit=vector/np.linalg.norm(vector)
        along=np.dot(np.subtract(point,a),unit)/np.linalg.norm(vector)
        center=np.asarray(c)+along*np.subtract(d,c)
        old=np.asarray(leg_registration(point,source,target))
        new=np.asarray(leg_registration(point,source,target,scale))
        assert np.linalg.norm(new-center)==pytest.approx(1.18*np.linalg.norm(old-center))
    for y in np.linspace(source[2][1]-.01,.82,10):
        assert climb_leg_width((.48,y),source)==1


def test_climb_volume_keeps_shoe_pixels_and_handoff_shoe_rotation_unchanged(monkeypatch):
    import climb_refinement as module
    source=((.48,.612),(.469,.690),(.456,.778))
    points=[(x,y) for x in (.44,.456,.48) for y in (.77,.778,.79,.815)]
    for phase in (0,.225,.25,.5,.725,1):
        for direction in (-1,1):
            joints=refined_leg_joints('l',direction,phase,.038)
            actual=[_leg_mapping(p,source,joints,direction) for p in points]
            with monkeypatch.context() as patch:
                patch.setattr(module,'CLIMB_LEG_VOLUME',0)
                historical=[_leg_mapping(p,source,joints,direction) for p in points]
            assert np.asarray(actual)==pytest.approx(np.asarray(historical),abs=1e-12)
            for volume in (0,.5,1):
                got=[_handoff_map(p,source,joints,-65,direction,volume) for p in points]
                old=[_handoff_map(p,source,joints,-65,direction,0) for p in points]
                assert np.asarray(got)==pytest.approx(np.asarray(old),abs=1e-12)


def test_airborne_foot_gets_more_clearance_without_lengthening_leg(monkeypatch):
    import climb_refinement as module
    for side in ('l','r'):
        midpoint=.725 if side=='l' else .225
        after=np.asarray(refined_leg_joints(side,1,midpoint,.038))
        with monkeypatch.context() as patch:
            patch.setattr(module,'AIRBORNE_RETRACTION',.043)
            before=np.asarray(refined_leg_joints(side,1,midpoint,.038))
        assert (before[2,0]-after[2,0])*384==pytest.approx((.065-.043)*384)
        assert np.linalg.norm(np.diff(after,axis=0),axis=1)==pytest.approx([.108,.112])
        assert after[2,1]==before[2,1]
    assert AIRBORNE_RETRACTION==.065
