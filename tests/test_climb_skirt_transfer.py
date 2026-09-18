"""Cloth-only handoff regressions; no Core/Qt or complete character build."""
from copy import deepcopy
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools/authoring'))
from build_rig import grid_mesh, lattice
from build_v5 import V5Builder
from climb_refinement import _apply_climb_body_and_skirt, _handoff_map, refined_leg_joints
from climb_skirt_transfer import (apply_climb_skirt_transfer, gathered_transfer_points,
                                  NativeClothParents, motion_settings)
from native_warp_sampling import warp_points
from maple_motions import build_motion, TRANSITIONS


def oriented_areas(points, rows):
    q = np.asarray(points).reshape(rows, rows, 2)
    a, b, c, d = q[:-1, :-1], q[:-1, 1:], q[1:, :-1], q[1:, 1:]
    def cross(x,y): return x[...,0]*y[...,1]-x[...,1]*y[...,0]
    return np.concatenate((cross(b-a,c-a).ravel(), cross(d-b,c-b).ravel()))


@pytest.mark.parametrize('direction', [-1, 1])
def test_local_gather_tracks_knees_pins_waist_and_keeps_hem_cover(direction):
    p = np.asarray(lattice(cols=33, rows=33))
    joints = [((.5,.63),(.5+direction*.1,.72),(.5+direction*.07,.81)),
              ((.53,.63),(.53+direction*.07,.75),(.53+direction*.05,.84))]
    settings = {'ParamClimbRefine':0., 'ParamPoseSwing':.44}
    q = gathered_transfer_points(p,p,(.3,.52,.72,.91),joints,direction,settings)
    np.testing.assert_array_equal(q[p[:,1]<=.575], p[p[:,1]<=.575])
    assert np.isfinite(q).all()
    assert oriented_areas(q,33).min() > 0
    # There is a knee crease even when the ankle constrains the hem lift.
    interior = (p[:,1]>.65)&(p[:,1]<.8)
    assert np.max(np.abs(q[interior]-p[interior])) > .015
    moved = [[tuple(np.asarray(j)+(0.,-.03)) for j in leg] for leg in joints]
    other = gathered_transfer_points(p,p,(.3,.52,.72,.91),moved,direction,settings)
    assert np.max(np.abs(other-q)) > .004
    for endpoint in ({'ParamClimbRefine':1., 'ParamPoseSwing':0.},
                     {'ParamClimbRefine':0., 'ParamPoseSwing':1.}):
        np.testing.assert_array_equal(gathered_transfer_points(p,p,(.3,.52,.72,.91),joints,direction,endpoint),p)


@pytest.fixture(scope='module')
def cloth_builder():
    """Actual ten PNG clothing parent chains; small synthetic joint tracks.

    The unrelated arm/head/leg finalizers require complete character inventory,
    and are not part of this deliberately reduced cloth fixture. Production
    full-builder tests retain their hooks. Joint tracks exercise the existing
    material-anchor contract, not a replacement native leg measurement.
    """
    import build_v5
    folder = ROOT/'assets/authoring/source'
    manifest = json.loads((folder/'layers.json').read_text('utf-8'))
    originals = {l['id']:l for l in manifest['layers']}
    manifest['layers'] = [l for l in manifest['layers'] if l.get('role')=='clothing'
        and l.get('has_seated_variant') and l.get('pose','rest')=='rest']
    b = V5Builder(manifest,folder)
    with ExitStack() as stack:
        stack.enter_context(patch.object(build_v5.brush_refinement,'apply_brush_refinement',lambda builder:None))
        for name in ('apply_free_arm_refinement','apply_climb_refinement','finish_free_arm_refinement',
                     'apply_sleep_contact_refinement','apply_climb_contact_refinement'):
            stack.enter_context(patch.object(build_v5,name,lambda builder:None))
        b._load_layers(*b._hierarchy())
    b.parameter('ParamClimbRefine',0,1,keys=[0,.001,1])
    b.parameter('ParamClimbHandoff',0,1,keys=[0,.001,1])
    _apply_climb_body_and_skirt(b)
    for side in ('l','r'):
        name=f'leg_{side}_climb_handoff'
        layer=deepcopy(originals[f'leg_{side}_climb'])
        layer['id']=name
        b.layer_by_id[name]=layer
        mesh=grid_mesh(name,(0,0,1,1),9,9)
        b.meshes[name]=mesh
        source=[tuple(layer[k]) for k in ('hip','pivot','ankle')]
        for key in b.params['ParamPoseClimb'].keyforms:
            direction=1 if key.value>=0 else -1
            start=refined_leg_joints(side,direction,0,.038)
            end=((.48 if side=='l' else .54,.64),(.45 if side=='l' else .57,.74),(.46 if side=='l' else .56,.81))
            u=1-abs(key.value)
            joints=np.asarray(start)*(1-u)+np.asarray(end)*u
            positions=np.asarray([_handoff_map(p,source,joints,-90*direction*(1-u)) for p in mesh.vertices])
            key.mesh_offsets[name]=[tuple(q-p) for p,q in zip(mesh.vertices,positions)]
    return b


def test_actual_cloth_chains_only_add_leaf_nodes_and_preserve_other_fields(cloth_builder):
    b=cloth_builder
    before_nodes=deepcopy(b.nodes)
    before_meshes=deepcopy(b.meshes)
    before_params=deepcopy(b.params)
    before_parts=deepcopy(b.parts)
    apply_climb_skirt_transfer(b)
    new=set(b.nodes)-set(before_nodes)
    assert len(new)==10
    assert b.meshes==before_meshes
    assert {name:node for name,node in b.nodes.items() if name not in new}==before_nodes
    for old,new_part in zip(before_parts,b.parts):
        assert new_part.model_copy(update={'parent_deformer':old.parent_deformer})==old
        assert new_part.parent_deformer in new
        assert b.nodes[new_part.parent_deformer].parent==old.parent_deformer
    for pid, parameter in b.params.items():
        stripped=parameter.model_copy(deep=True)
        for key in stripped.keyforms:
            key.deformer_offsets={n:v for n,v in key.deformer_offsets.items() if n not in new}
            key.deformer_offset_weights={n:v for n,v in key.deformer_offset_weights.items() if n not in new}
        assert stripped==before_params[pid]
    for key in b.params['ParamPoseClimb'].keyforms:
        for name in new:
            points=np.asarray(b.nodes[name].grid_vertices)+key.deformer_offsets[name]
            assert np.isfinite(points).all()
            assert oriented_areas(points,17).min()>0, (name,key.value)
            if abs(key.value) in (0,1):
                np.testing.assert_array_equal(key.deformer_offsets[name],np.zeros((289,2)))
            source=np.asarray(b.nodes[name].grid_vertices)
            np.testing.assert_array_equal(points[source[:,1]<=.575],source[source[:,1]<=.575])
    assert set(b.params)==set(before_params)
    keys=b.params['ParamPoseClimb'].keyforms
    for first,last in zip(keys,keys[1:]):
        for fraction in (.25,.5,.75):
            for name in new:
                source=np.asarray(b.nodes[name].grid_vertices)
                delta=(1-fraction)*np.asarray(first.deformer_offsets[name])+fraction*np.asarray(last.deformer_offsets[name])
                assert oriented_areas(source+delta,17).min()>0,(name,first.value,last.value,fraction)
    # A same-density identity child reproduces its parent lattice exactly;
    # this protects ordinary climb/sleep and canonical handoff endpoints.
    sample=np.array([[.43,.545],[.61,.62],[.4,.82]])
    for part in b.parts:
        node=b.nodes[part.parent_deformer]
        parent=b.nodes[node.parent]
        assert (node.grid_rows,node.grid_cols)==(parent.grid_rows,parent.grid_cols)==(17,17)
        inherited=np.asarray(parent.grid_vertices)+(.01,-.02)
        child=warp_points(node.grid_vertices,inherited,17)
        np.testing.assert_allclose(warp_points(sample,child,17),warp_points(sample,inherited,17),atol=1e-14,rtol=0)
    with pytest.raises(ValueError,match='already'):
        apply_climb_skirt_transfer(b)


@pytest.mark.parametrize('direction', [-1,1])
def test_existing_motion_samples_have_real_refinement_gap(cloth_builder,direction):
    b=cloth_builder
    name='climb_to_top_'+('right' if direction>0 else 'left')
    motion=build_motion(name,TRANSITIONS[name],{n:p.default for n,p in b.params.items()})
    for t in (.85,1.05,1.25):
        values=motion_settings(motion,t)
        assert values['ParamClimbRefine']==0
        assert values['ParamPoseSwing']<.82
