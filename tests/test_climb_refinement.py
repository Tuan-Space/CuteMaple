"""Editable IR regressions: old transfer compatibility and actual new support."""
import copy
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/authoring'))
from build_v5 import V5Builder, PHASE_KEYS, RISE as LEGACY_RISE, climb_travel
from climb_refinement import (CONTROL, HANDOFF, DRAPE_BLEND, WALL_X, MIRROR_X, RISE, apply_climb_refinement,
    body_shift, climb_metadata, refined_arm_joints, original_arm_joints,
    refined_leg_joints, _leg_mapping, hand_reach, skirt_fold_point, transfer_refinement,
    AIRBORNE_RETRACTION)
from diagnose_rig import DiagnosticRenderer, motion_parameters


def renderer(rig):
    r = DiagnosticRenderer.__new__(DiagnosticRenderer)
    r.rig = rig
    r.meshes = {m.part_id: m for m in rig.meshes}
    r.nodes = {d.id: d for d in rig.deformers}
    return r


def point(r, pose, name, uv):
    mesh = r.meshes[name]
    uvs = np.asarray(mesh.uvs)
    for ids in mesh.triangles:
        a, b, c = uvs[list(ids)]
        try:
            weights = np.linalg.solve(np.array([b-a, c-a]).T, np.asarray(uv)-a)
        except np.linalg.LinAlgError:
            continue
        if min(weights) >= -1e-8 and sum(weights) <= 1+1e-8:
            vertices = pose[name][0]
            return vertices[ids[0]]*(1-sum(weights)) + vertices[ids[1]]*weights[0] + vertices[ids[2]]*weights[1]
    raise AssertionError((name, 'contact is outside source triangles', uv))


@pytest.fixture(scope='module')
def rigs():
    folder = ROOT/'assets/authoring/source'
    if not (folder/'layers.json').exists():
        pytest.skip('The frozen v5 authoring input is absent')
    b = V5Builder(json.loads((folder/'layers.json').read_text(encoding='utf8')), folder)
    # After integration the production builder calls this module itself.
    # Disable ONLY that hook while constructing the historical comparison.
    import build_v5
    brush_hook = build_v5.brush_refinement.apply_brush_refinement
    def omit_brush(builder):
        # This before/after fixture intentionally predates brush geometry.
        # Remove only brush axes whose keyforms were omitted with the hook.
        for name in tuple(builder.params):
            if name in {'ParamCleanBrushL','ParamCleanBrushR','ParamCleanContext','ParamCleanPoseL','ParamCleanPoseR','ParamCleanStroke'}:
                del builder.params[name]
    build_v5.brush_refinement.apply_brush_refinement = omit_brush
    hook = getattr(build_v5, 'apply_climb_refinement', None)
    contact_hook = getattr(build_v5, 'apply_climb_contact_refinement', None)
    if hook is not None:
        build_v5.apply_climb_refinement = lambda builder: None
    if contact_hook is not None:
        build_v5.apply_climb_contact_refinement = lambda builder: None
    try:
        original = copy.deepcopy(b.build())
    finally:
        build_v5.brush_refinement.apply_brush_refinement = brush_hook
        if hook is not None:
            build_v5.apply_climb_refinement = hook
        if contact_hook is not None:
            build_v5.apply_climb_contact_refinement = contact_hook
    apply_climb_refinement(b)
    refined = original.model_copy(update={'deformers': list(b.nodes.values()), 'parameters': list(b.params.values()),
                                         'parts': b.parts, 'meshes': list(b.meshes.values())})
    return folder, b, renderer(original), renderer(refined)


def test_registration_is_explicit_and_nonduplicating(rigs):
    _, b, old, new = rigs
    assert set(p.id for p in new.rig.parameters) - set(p.id for p in old.rig.parameters) == {CONTROL, HANDOFF, DRAPE_BLEND}
    assert b.params[CONTROL].default == 0
    assert [m for m in new.rig.meshes if m.part_id in old.meshes] == old.rig.meshes
    assert len(new.rig.meshes) == len(old.rig.meshes)+4
    with pytest.raises(ValueError, match='already'):
        apply_climb_refinement(b)


@pytest.mark.parametrize('state', ['idle', 'sleep_loop', 'climb_to_top_left', 'climb_to_top_right',
                                   'clean_ground', 'clean_climb_left', 'clean_top', 'drag_right'])
def test_disabled_refinement_preserves_existing_entire_pose(rigs, state):
    folder, _, old, new = rigs
    for phase in (0, .18, .32/1.8, .85/1.8, .65, 1):
        params = motion_parameters(folder/'runtime/motions'/f'{state}.motion3.json', phase)
        # Current motion inputs opt into refinement; explicitly disable all
        # refinement axes for this backwards-compatibility comparison.
        params.update({CONTROL: 0, HANDOFF: 0, DRAPE_BLEND: 0})
        before, after = old.evaluate(params), new.evaluate(params)
        for name in before:
            np.testing.assert_allclose(after[name][0], before[name][0], atol=1e-10, rtol=0,
                                       err_msg=f'{state}/{phase}/{name}')
            assert after[name][1] == before[name][1]


@pytest.mark.parametrize('direction', ['left', 'right'])
def test_refined_actual_ir_cuffs_and_support_palms_still_meet(rigs, direction):
    folder, _, _, r = rigs
    for phase in sorted(set(PHASE_KEYS+[.125, .375, .625, .875])):
        params = motion_parameters(folder/'runtime/motions'/f'climb_{direction}.motion3.json', phase)
        pose = r.evaluate({**params, CONTROL: 1})
        for side, hand_uv, cuff_uv in [('l', (.495, .518), (.274, .390)),
                                       ('r', (.526, .518), (.728, .390))]:
            gap = np.linalg.norm(point(r, pose, f'hand_{side}_support', hand_uv) -
                                 point(r, pose, f'sleeve_{side}_lower', cuff_uv))
            assert gap < .012, (direction, phase, side, gap)


@pytest.mark.parametrize('direction', ['left', 'right'])
def test_refined_actual_ir_palms_remain_planted_across_cycle_wrap(rigs, direction):
    folder, _, _, r = rigs
    for side, uv in [('l', (.495, .518)), ('r', (.526, .518))]:
        near = (side == 'l') == (direction == 'right')
        for start, end in ([(.86, 1.09), (.36, .59)] if near else [(.86, 1.59), (.01, .59)]):
            positions = []
            for time in np.linspace(start, end, 8):
                cycle, phase = int(time), time % 1
                params = motion_parameters(folder/'runtime/motions'/f'climb_{direction}.motion3.json', phase)
                p = point(r, r.evaluate({**params, CONTROL: 1}), f'hand_{side}_support', uv).copy()
                p[1] -= RISE*(cycle+climb_travel(phase))
                positions.append(p)
            assert np.ptp(np.array(positions), axis=0).max() < .008


def test_articulated_arm_keeps_bone_lengths_and_new_world_support_while_shoulder_transfers_weight():
    for direction in (-1, 1):
        for side in ('l', 'r'):
            for phase in PHASE_KEYS:
                old = np.asarray(original_arm_joints(side, direction, phase))
                new = np.asarray(refined_arm_joints(side, direction, phase))
                near = (side == 'l') == (direction > 0)
                expected_delta = [0, (RISE-LEGACY_RISE)*(climb_travel(phase)-hand_reach(phase, near))]
                np.testing.assert_allclose(new[2]-old[2], expected_delta, atol=1e-12)
                np.testing.assert_allclose(new[0]-old[0], body_shift(phase, direction), atol=1e-12)
                old_lengths = np.linalg.norm(np.diff(old, axis=0), axis=1)
                new_lengths = np.linalg.norm(np.diff(new, axis=0), axis=1)
                np.testing.assert_allclose(new_lengths, old_lengths, rtol=.001)


def test_feet_face_wall_and_remain_on_it_in_their_actual_support_windows():
    source = ((.48, .612), (.469, .690), (.456, .778))
    depth = .038
    sole = (source[2][0], source[2][1]+depth)
    for direction in (-1, 1):
        for side in ('l', 'r'):
            near = (side == 'l') == (direction > 0)
            windows = [(0, .60), (.85, 1)] if near else [(0, .10), (.35, 1)]
            for low, high in windows:
                world = []
                for phase in np.linspace(low, high, 8):
                    joints = refined_leg_joints(side, direction, phase, depth)
                    p = _leg_mapping(sole, source, joints, direction)
                    world.append((p[0], p[1]-RISE*climb_travel(phase)))
                    assert abs(p[0]-(WALL_X if direction > 0 else MIRROR_X-WALL_X)) < 1e-10
                assert np.ptp(np.asarray(world), axis=0).max() < 1e-10


def test_new_leg_cycle_has_real_joint_angle_changes_without_length_stretch():
    for direction in (-1, 1):
        for side in ('l', 'r'):
            angles = []
            for phase in np.linspace(0, 1, 41):
                h, k, a = np.asarray(refined_leg_joints(side, direction, phase, .038))
                lengths = np.linalg.norm([k-h, a-k], axis=1)
                np.testing.assert_allclose(lengths, [.108, .112], rtol=.001)
                angles.append(np.dot(h-k, a-k)/np.prod(lengths))
            assert np.ptp(angles) > .2


def test_recovering_foot_withdraws_clearly_without_changing_planted_height_or_cycle_entry():
    depth = .038
    for direction in (-1, 1):
        for side in ('l', 'r'):
            near = (side == 'l') == (direction > 0)
            start, finish = (.60, .85) if near else (.10, .35)
            wall = WALL_X if direction > 0 else MIRROR_X-WALL_X
            ankle = refined_leg_joints(side, direction, (start+finish)/2, depth)[2]
            assert direction*(wall-ankle[0])-depth == pytest.approx(AIRBORNE_RETRACTION)
            assert AIRBORNE_RETRACTION == .065
            for phase, reach in ((start, 0), (finish, 1)):
                ankle = refined_leg_joints(side, direction, phase, depth)[2]
                assert direction*(wall-ankle[0]) == pytest.approx(depth)
                assert ankle[1] == pytest.approx((.795 if near else .855)+RISE*(climb_travel(phase)-reach))
            np.testing.assert_allclose(refined_leg_joints(side, direction, 0, depth),
                                       refined_leg_joints(side, direction, 1, depth), atol=1e-12)


def test_support_plane_alignment_translates_the_whole_leg_without_stretch_or_vertical_change(monkeypatch):
    import climb_refinement as refinement
    assert refinement.WALL_X == .719 and refinement.HIP_X == .557
    for direction in (-1, 1):
        for side in ('l', 'r'):
            for phase in PHASE_KEYS:
                current = np.asarray(refined_leg_joints(side, direction, phase, .038))
                with monkeypatch.context() as patch:
                    patch.setattr(refinement, 'WALL_X', .687)
                    patch.setattr(refinement, 'HIP_X', .525)
                    historical_plane = np.asarray(refined_leg_joints(side, direction, phase, .038))
                np.testing.assert_allclose(current-historical_plane,
                    np.tile([direction*.032, 0.], (3, 1)), atol=1e-12, rtol=0)


def test_metadata_keeps_model_and_host_rise_synchronized():
    contract = climb_metadata()
    assert contract['cycleDuration'] == 1.0
    assert contract['risePerCycle'] == RISE
    assert RISE == .12 and LEGACY_RISE == .16
    assert contract['phaseTravel'] == [[p, climb_travel(p)] for p in PHASE_KEYS]
    assert contract['refinementParameter'] == CONTROL


@pytest.mark.parametrize('direction', ['left', 'right'])
def test_painted_leg_material_does_not_penetrate_wall(rigs, direction):
    from PIL import Image
    folder, builder, _, r = rigs
    materials = {}
    for side in ('l','r'):
        mesh = r.meshes[f'leg_{side}_climb_refined']
        uv = np.asarray(mesh.uvs)
        with Image.open(folder/builder.layer_by_id[f'leg_{side}_climb']['file']) as image:
            yy,xx = np.where(np.asarray(image.getchannel('A')) > 8)
            samples = np.column_stack((xx+.5,yy+.5))/image.size
        # Every painted source pixel center, including the actual shoe edge,
        # maps through its real source triangle. Transparent mesh padding is
        # deliberately excluded from the physical contact surface.
        grid = (samples-uv[0])/((uv[-1]-uv[0])/16)
        ij = np.clip(np.floor(grid).astype(int),0,15)
        tx,ty = (grid-ij).T
        a = ij[:,1]*17+ij[:,0]
        first = tx+ty <= 1
        ids = np.where(first[:,None],np.column_stack((a,a+1,a+17)),np.column_stack((a+1,a+18,a+17)))
        weights = np.where(first[:,None],np.column_stack((1-tx-ty,tx,ty)),np.column_stack((1-ty,tx+ty-1,1-tx)))
        materials[side] = ids,weights
    for phase in sorted(set(PHASE_KEYS+[.125, .375, .625, .875])):
        params = motion_parameters(folder/'runtime/motions'/f'climb_{direction}.motion3.json', phase)
        pose = r.evaluate({**params, CONTROL: 1})
        for side in ('l', 'r'):
            ids,weights = materials[side]
            x = np.sum(pose[f'leg_{side}_climb_refined'][0][ids,0]*weights,axis=1)
            assert x.max() <= WALL_X+1e-4 if direction == 'right' else x.min() >= MIRROR_X-WALL_X-1e-4


@pytest.mark.parametrize('direction', ['left', 'right'])
def test_actual_sleeve_contacts_follow_the_existing_wall_to_rope_transfer_during_fade(rigs, direction):
    folder, _, _, r = rigs
    for time in np.linspace(0, .86, 24):
        params = motion_parameters(folder/'runtime/motions'/f'climb_to_top_{direction}.motion3.json', time/1.8)
        params[CONTROL] = transfer_refinement(time)
        params[HANDOFF] = 1
        pose = r.evaluate(params)
        for side, uv, cuff in [('l', (.495, .518), (.274, .390)), ('r', (.526, .518), (.728, .390))]:
            hand = f'hand_{side}_grip_palm' if params[f'ParamHand{side.upper()}Shape'] > .5 else f'hand_{side}_support'
            gap = np.linalg.norm(point(r, pose, hand, uv)-point(r, pose, f'sleeve_{side}_lower', cuff))
            assert gap < .012, (direction, time, hand, gap)


def test_skirt_preserves_waist_and_forms_curved_local_fold_without_inverting():
    for direction in (-1, 1):
        for phase in PHASE_KEYS:
            for x in (.38, .45, .52, .60, .67):
                for y in (.52, .54, .56):
                    np.testing.assert_allclose(skirt_fold_point((x,y), phase, direction), [x,y])
                ys = [skirt_fold_point((x,y), phase, direction)[1] for y in np.linspace(.56, .9, 50)]
                assert np.diff(ys).min() > 0
            # Sample the complete mirrored front/rear span, including the
            # translated knee crest instead of clipping the left-facing crest.
            hem_x = [x if direction > 0 else MIRROR_X-x for x in np.linspace(.32,.75,31)]
            hem = np.array([skirt_fold_point((x,.83),phase,direction)[1] for x in hem_x])
            assert np.abs(np.diff(hem, n=2)).max() > .001
            # The rear hem hangs at its source length; only the knee crest rises.
            assert 0 <= .83-hem.max() < .002
            assert .08 < .83-hem.min() < .16


def test_front_fold_tracks_the_knee_smoothly_without_raising_the_rear_train():
    # Two knee-height crossings previously switched the single fold center.
    # Sample their neighborhoods densely; a sub-frame change cannot jump the
    # cloth or turn the entire lower panel into a raised shelf.
    x_values = np.linspace(.35, .71, 73)
    for middle in (.24, .727):
        phases = np.linspace(middle-.02, middle+.02, 81)
        rows = np.array([[skirt_fold_point((x,.83),p,1) for x in x_values] for p in phases])
        assert np.abs(np.diff(rows, axis=0)).max() < .002
        assert np.abs(rows[:, :5, 1]-.83).max() < .0001
    crests = []
    for phase in (.10, .35, .60, .85):
        hem = [skirt_fold_point((x,.83),phase,1)[1] for x in x_values]
        crests.append(x_values[int(np.argmin(hem))])
        for x, y in ((.4,.83), (.6,.7), (.66,.83)):
            right = skirt_fold_point((x,y),phase,1)
            left = skirt_fold_point((MIRROR_X-x,y),phase,-1)
            np.testing.assert_allclose(left, (MIRROR_X-right[0],right[1]), atol=1e-12)
    assert max(crests)-min(crests) > .018


@pytest.fixture(scope='module')
def cloth_rigs():
    """The real profile/body parent chain, with only ten small skirt layers."""
    from build_rig import Rig
    from climb_refinement import _apply_climb_body_and_skirt
    from unittest.mock import patch
    from contextlib import ExitStack
    import build_v5
    folder = ROOT/'assets/authoring/source'
    manifest = json.loads((folder/'layers.json').read_text(encoding='utf8'))
    manifest['layers'] = [layer for layer in manifest['layers']
                          if layer.get('role') == 'clothing' and layer.get('has_seated_variant')
                          and layer.get('pose', 'rest') == 'rest']
    builder = V5Builder(manifest, folder)
    # These unrelated late hooks require actual arms/legs. The production
    # layer loader still creates every real profile, fabric and body parent.
    with ExitStack() as stack:
        stack.enter_context(patch.object(build_v5.brush_refinement,'apply_brush_refinement',lambda builder:None))
        for name in ('apply_free_arm_refinement','apply_climb_refinement',
                     'finish_free_arm_refinement','apply_sleep_contact_refinement',
                     'apply_climb_contact_refinement'):
            stack.enter_context(patch.object(build_v5,name,lambda builder:None))
        builder._load_layers(*builder._hierarchy())
    builder.parameter(CONTROL, 0, 1, keys=[0, .001, 1])
    _apply_climb_body_and_skirt(builder)
    rig = Rig(meta={'name':'Actual skirt parent-chain test'}, textures=builder.textures,
              parts=builder.parts, meshes=list(builder.meshes.values()),
              deformers=list(builder.nodes.values()), parameters=list(builder.params.values()))
    # A comparison with just this new cloth field disabled keeps the same
    # profile, crouch, phase body shift and disabled legacy skirt compression.
    phase = builder.params['ParamClimbPhase'].model_copy(deep=True)
    for key in phase.keyforms:
        key.deformer_offsets = {name:value for name,value in key.deformer_offsets.items()
                                if not name.startswith('RefinedClimbSkirt_')}
    unfolded = rig.model_copy(update={'parameters':[phase if p.id == phase.id else p for p in rig.parameters]})
    return folder, builder, renderer(unfolded), renderer(rig)


@pytest.mark.parametrize('direction', [-1, 1])
def test_painted_front_panel_lifts_after_actual_profile_and_body_parents(cloth_rigs, direction):
    from PIL import Image
    folder, builder, unfolded, refined = cloth_rigs
    side = 'r' if direction > 0 else 'l'
    name = f'profile_{side}_skirt'
    front = (.535,.84) if direction > 0 else (.450,.84)
    rear = (.390,.85) if direction > 0 else (.625,.85)
    waist = (.510,.518) if direction > 0 else (.470,.518)
    with Image.open(folder/builder.layer_by_id[name]['file']) as image:
        for uv in (front,rear,waist):
            assert image.getpixel(tuple(int(v*1024) for v in uv))[3] >= 128
    lifts = []
    for phase in (0,.135,.25,.35,.60,.70,.85,1):
        params = {CONTROL:1,'ParamClimbActive':1,'ParamClimbDirection':direction,
                  'ParamPoseClimb':direction,'ParamClimbPhase':phase,'ParamBodyTurn':direction}
        base, pose = unfolded.evaluate(params), refined.evaluate(params)
        delta = point(refined,pose,name,front)-point(unfolded,base,name,front)
        lifts.append(-delta[1])
        # This is painted embroidery after its real parent chain. The former
        # empty-canvas fold only lifted the right sample by roughly 1 pixel.
        assert .07 < -delta[1] < .19, (direction,phase,delta)
        assert 0 <= direction*delta[0] < .035
        np.testing.assert_allclose(point(refined,pose,name,rear),point(unfolded,base,name,rear),atol=.002,rtol=0)
        np.testing.assert_allclose(point(refined,pose,name,waist),point(unfolded,base,name,waist),atol=.001,rtol=0)
        # Different source mesh bounds cannot make the opacity underlay use
        # a different cloth field and expose a second, rigid front hem.
        underlay=f'profile_{side}_costume_underlay_skirt'
        np.testing.assert_allclose(point(refined,pose,name,front),point(refined,pose,underlay,front),atol=.003,rtol=0)
    assert max(lifts)-min(lifts) > .01


@pytest.mark.parametrize('direction', [-1, 1])
def test_gathered_skirt_covers_hip_roots_through_the_complete_actual_chain(cloth_rigs, direction):
    from PIL import Image
    folder, builder, _, refined = cloth_rigs
    side = 'r' if direction > 0 else 'l'
    name = f'profile_{side}_skirt'
    mesh = refined.meshes[name]
    triangles = np.asarray(mesh.triangles)
    texture = np.asarray(mesh.uvs)[triangles]
    with Image.open(folder/builder.layer_by_id[name]['file']) as image:
        alpha = np.asarray(image.getchannel('A'))
    for phase in np.linspace(0,1,101):
        params = {CONTROL:1,'ParamClimbActive':1,'ParamClimbDirection':direction,
                  'ParamPoseClimb':direction,'ParamClimbPhase':phase,'ParamBodyTurn':direction}
        positions = refined.evaluate(params)[name][0][triangles]
        origins=positions[:,0]
        matrices=np.stack((positions[:,1]-origins,positions[:,2]-origins),axis=2)
        inverses=np.linalg.inv(matrices)
        for leg in ('l','r'):
            hip=np.array(refined_leg_joints(leg, direction,phase,.038)[0])
            for offset in ((0,0),(-.004,0),(.004,0),(0,-.004),(0,.004)):
                weights=np.einsum('nij,nj->ni',inverses,hip+offset-origins)
                bary=np.column_stack((1-weights.sum(axis=1),weights))
                inside=bary.min(axis=1)>=-1e-8
                uv=np.einsum('ni,nij->nj',bary[inside],texture[inside])
                pixels=np.clip((uv*1024).astype(int),0,1023)
                assert len(pixels) and alpha[pixels[:,1],pixels[:,0]].max() >= 128, (direction,phase,leg,offset)


@pytest.mark.parametrize('direction', ['left', 'right'])
def test_actual_sole_material_point_stays_planted_not_only_mesh_bounding_box(rigs, direction):
    folder, _, _, r = rigs
    layers = {x['id']: x for x in json.loads((folder/'layers.json').read_text(encoding='utf8'))['layers']}
    for side in ('l', 'r'):
        name = f'leg_{side}_climb_refined'
        uv = (layers[f'leg_{side}_climb']['ankle'][0], max(p[1] for p in r.meshes[name].uvs)-.002)
        near = side == 'l'
        for low, high in ([(0,.60),(.85,1)] if near else [(0,.10),(.35,1)]):
            points = []
            for phase in np.linspace(low,high,9):
                params = motion_parameters(folder/'runtime/motions'/f'climb_{direction}.motion3.json', phase)
                p = point(r,r.evaluate({**params,CONTROL:1}),name,uv).copy()
                p[1] -= RISE*climb_travel(phase)
                points.append(p)
            assert np.ptp(np.asarray(points),axis=0).max() < .003


@pytest.mark.parametrize('direction', ['left', 'right'])
def test_transfer_has_one_opaque_leg_pair_and_pixel_identical_receiving_pose(rigs, direction):
    folder, _, _, r = rigs
    new_ids = {f'leg_{s}_climb_handoff' for s in ('l','r')}
    for time in np.linspace(0,1.8,19):
        params = motion_parameters(folder/'runtime/motions'/f'climb_to_top_{direction}.motion3.json', time/1.8)
        poses = r.evaluate({**params, CONTROL: transfer_refinement(time), HANDOFF:1})
        visible = {p.id for p in r.rig.parts if p.semantic_role.value.startswith('leg_') and poses[p.id][1]>.001}
        assert visible == new_ids
        assert all(poses[name][1] == 1 for name in new_ids)
        if time == 0:
            reference = r.evaluate({**params, CONTROL:1, HANDOFF:0})
            for side in ('l','r'):
                np.testing.assert_allclose(poses[f'leg_{side}_climb_handoff'][0],
                                           reference[f'leg_{side}_climb_refined'][0], atol=1e-10, rtol=0)
        if time == 1.8:
            reference = r.evaluate({**params, CONTROL:0, HANDOFF:0})
            for side in ('l','r'):
                np.testing.assert_allclose(poses[f'leg_{side}_climb_handoff'][0],
                                           reference[f'leg_{side}_swing'][0], atol=1e-10, rtol=0)
    for side in ('l','r'):
        assert r.meshes[f'leg_{side}_climb_handoff'].uvs == r.meshes[f'leg_{side}_swing'].uvs
        assert r.meshes[f'leg_{side}_climb_handoff'].triangles == r.meshes[f'leg_{side}_swing'].triangles


def test_refinement_disables_only_legacy_skirt_compression(rigs):
    _, builder, _, _ = rigs
    branches = [name for name in builder.nodes if name.startswith('ClimbFold_')]
    assert branches
    for key in builder.params[CONTROL].keyforms:
        assert all(key.deformer_offset_weights[name] == 1-key.value for name in branches)
    # The old authored field remains present for the Refine=0 baseline.
    active = next(key for key in builder.params['ParamClimbActive'].keyforms if key.value == 1)
    assert all(any(delta != (0., 0.) for delta in active.deformer_offsets[name]) for name in branches)


def test_new_climb_legs_use_only_native_linear_mesh_and_mirror_at_handoff_entry(rigs):
    _, builder, _, r = rigs
    mirror = builder.nodes['RefinedClimbLegMirror']
    assert (mirror.grid_rows, mirror.grid_cols) == (2, 2)
    assert mirror.parent == builder.scene
    contract = climb_metadata()
    assert contract['refinedLegRoles'] == {'leg_l_climb_refined': 'near', 'leg_r_climb_refined': 'far'}
    for side in ('l', 'r'):
        name = f'leg_{side}_climb_refined'
        part = next(p for p in r.rig.parts if p.id == name)
        assert part.parent_deformer == mirror.id
        original = r.meshes[f'leg_{side}_climb']
        assert r.meshes[name].uvs == original.uvs and r.meshes[name].triangles == original.triangles
        owners = {p.id for p in builder.params.values() if any(name in k.mesh_offsets for k in p.keyforms)}
        assert owners == {'ParamClimbPhase'}
        # These endpoints cannot hide a DiagnosticRenderer nonlinear-warp bake:
        # the exact same linear vertex values go to the original native mesh.
        phase0 = next(k for k in builder.params['ParamClimbPhase'].keyforms if k.value == 0)
        raw = np.asarray(r.meshes[name].vertices)+phase0.mesh_offsets[name]
        for direction in (-1, 1):
            key = next(k for k in builder.params['ParamPoseClimb'].keyforms if k.value == direction)
            target = np.asarray(r.meshes[f'leg_{side}_climb_handoff'].vertices)+key.mesh_offsets[f'leg_{side}_climb_handoff']
            expected = raw.copy()
            if direction < 0:
                expected[:, 0] = MIRROR_X-expected[:, 0]
            np.testing.assert_allclose(target, expected, atol=1e-12, rtol=0)


def test_refined_leg_visibility_and_airborne_ankle_release(rigs):
    folder, _, _, r = rigs
    for direction in ('left', 'right'):
        samples = []
        for phase in [0, .10, .25, .35, .60, .70, .85, 1]:
            params = motion_parameters(folder/'runtime/motions'/f'climb_{direction}.motion3.json', phase)
            pose = r.evaluate({**params, CONTROL: 1, HANDOFF: 0})
            actual = {p.id for p in r.rig.parts if p.semantic_role.value.startswith('leg_') and pose[p.id][1] > .001}
            assert actual == set(climb_metadata()['refinedLegDrawables'])
            samples.append(pose)
        # A planted shoe retains its angle, while the released one articulates.
        layer = json.loads((folder/'layers.json').read_text())['layers']
        layer = next(x for x in layer if x['id'] == 'leg_l_climb')
        ankle = layer['ankle']
        a = (ankle[0]-.003, ankle[1]+.03)
        b = (ankle[0]+.003, ankle[1]+.03)
        vectors = [point(r, pose, 'leg_l_climb_refined', b)-point(r, pose, 'leg_l_climb_refined', a) for pose in samples]
        np.testing.assert_allclose(vectors[0], vectors[-1], atol=1e-12, rtol=0)
        assert abs(vectors[0][0]*vectors[5][1]-vectors[0][1]*vectors[5][0]) > 1e-7


@pytest.mark.parametrize('source,phase,target', [
    ('climb_left', 0., 'drag_right'), ('climb_left', .35, 'drag_right'),
    ('climb_left', .85, 'drag_right'), ('climb_right', .1, 'idle'),
    ('climb_right', .6, 'idle'), ('climb_to_top_left', 0., 'drag_left'),
    ('climb_to_top_left', .35, 'drag_left'), ('climb_to_top_right', .65, 'drag_right'),
    ('climb_to_top_right', .9, 'drag_right')])
def test_actual_motion_fades_never_resurrect_a_third_leg_family(rigs, source, phase, target, record_property):
    _, _, _, r = rigs
    motions = ROOT/'assets/live2d/Maple/motions'
    before = motion_parameters(motions/(source+'.motion3.json'), phase)
    after = motion_parameters(motions/(target+'.motion3.json'), 0.)
    assert before[CONTROL] >= 0 and after[CONTROL] == 0
    legacy_max = 0.
    refined_during_handoff_max = 0.
    for mix in [*np.linspace(0, 1, 21), .999, .9995, .9999, .99999]:
        values = {p.id: before.get(p.id, p.default)*(1-mix)+after.get(p.id, p.default)*mix for p in r.rig.parameters}
        poses = r.evaluate(values)
        legacy = max(poses[f'leg_{side}_climb'][1] for side in ('l', 'r'))
        legacy_max = max(legacy_max, legacy)
        families = set()
        for name, (_, opacity) in poses.items():
            if not name.startswith(('leg_l', 'leg_r')) or opacity <= .01:
                continue
            family = ('handoff' if name.endswith('_handoff') else
                      'refined' if name.endswith('_climb_refined') else
                      'legacy-climb' if name.endswith('_climb') else 'standing')
            families.add(family)
        assert len(families) <= 2, (source, phase, target, mix, families)
        assert 'legacy-climb' not in families
        if values[HANDOFF] >= .001:
            refined = max(poses[f'leg_{side}_climb_refined'][1] for side in ('l','r'))
            refined_during_handoff_max = max(refined_during_handoff_max, refined)
            assert refined == 0
        assert all(np.isfinite(vertices).all() for name, (vertices, _) in poses.items() if name.startswith('leg_'))
    assert legacy_max < .001
    record_property('maximumLegacyClimbOpacity', legacy_max)
    record_property('maximumRefinedLegOpacityWhileHandoffSelected', refined_during_handoff_max)
