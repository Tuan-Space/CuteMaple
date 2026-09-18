"""Source-pixel contact and world-support regressions for the v5 rig."""
import json
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/authoring'))
from build_v5 import V5Builder, PHASE_KEYS, climb_travel
from climb_refinement import RISE
from diagnose_rig import DiagnosticRenderer, motion_parameters
from maple_motions import build_motion
from tools.authoring.animation_specs import ANIMATIONS


@pytest.fixture(scope='module')
def source():
    folder=ROOT/'assets/authoring/source'
    if not (folder/'layers.json').exists():
        pytest.skip('v5 source layers are not present')
    builder=V5Builder(json.loads((folder/'layers.json').read_text(encoding='utf8')),folder)
    # The IR renderer cannot reproduce Cubism's native lattice interpolation.
    # Native contact calibration has its own exported-sample regression suite;
    # keep this numerical IR fixture before that native-only correction.
    import build_v5
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(build_v5, 'apply_climb_contact_refinement', lambda b: None)
        rig=builder.build()
    renderer=DiagnosticRenderer.__new__(DiagnosticRenderer)
    renderer.rig=rig
    renderer.meshes={m.part_id:m for m in rig.meshes}
    renderer.nodes={d.id:d for d in rig.deformers}
    return folder,renderer


def point(renderer,poses,name,uv):
    vertices,_=poses[name]
    mesh=renderer.meshes[name]
    uvs=np.asarray(mesh.uvs)
    for ids in mesh.triangles:
        a,b,c=uvs[list(ids)]
        basis=np.array([b-a,c-a]).T
        try: weights=np.linalg.solve(basis,np.asarray(uv)-a)
        except np.linalg.LinAlgError: continue
        if min(weights)>=-1e-8 and sum(weights)<=1+1e-8:
            return vertices[ids[0]]*(1-sum(weights))+vertices[ids[1]]*weights[0]+vertices[ids[2]]*weights[1]
    raise AssertionError(f'{name}: contact source point outside mesh')


def test_rest_hands_follow_visible_sleeves_through_sleep_drag_and_fall(source):
    folder,r=source
    for state in ('idle','sleep_enter','sleep_loop','sleep_exit','drag_left','drag_right','fall_float','happy'):
        for phase in (0,.25,.5,.75,1):
            params=motion_parameters(folder/'runtime/motions'/f'{state}.motion3.json',phase)
            pose=r.evaluate(params)
            for side,uv in [('l',(.495,.518)),('r',(.526,.518))]:
                gap=np.linalg.norm(point(r,pose,'hand_'+side,uv)-point(r,pose,'arm_'+side,uv))
                assert gap<.012,(state,phase,side,float(gap))


@pytest.mark.parametrize('direction',('left','right'))
def test_active_cuff_and_palm_share_source_wrist(source,direction):
    folder,r=source
    for phase in PHASE_KEYS:
        params=motion_parameters(folder/'runtime/motions'/f'climb_{direction}.motion3.json',phase)
        pose=r.evaluate(params)
        for side,huv,cuv in [('l',(.495,.518),(.274,.390)),('r',(.526,.518),(.728,.390))]:
            gap=np.linalg.norm(point(r,pose,f'hand_{side}_support',huv)-point(r,pose,f'sleeve_{side}_lower',cuv))
            assert gap<.012,(direction,phase,side,float(gap))


@pytest.mark.parametrize('direction',('left','right'))
def test_real_planted_hand_stays_at_world_support_across_two_cycles(source,direction):
    folder,r=source
    for side,uv in [('l',(.495,.518)),('r',(.526,.518))]:
        near=(side=='l')==(direction=='right')
        intervals=[(.86,1.09),(.36,.59)] if near else [(.86,1.59),(.01,.59)]
        for start,end in intervals:
            points=[]
            for time in np.linspace(start,end,9):
                cycle=int(time);phase=time-cycle
                params=motion_parameters(folder/'runtime/motions'/f'climb_{direction}.motion3.json',phase)
                pose=r.evaluate(params)
                p=point(r,pose,f'hand_{side}_support',uv).copy()
                p[1]-=RISE*(cycle+climb_travel(phase))
                points.append(p)
            assert np.ptp(np.array(points),axis=0).max()<.008,(direction,side,start,end,points)


def test_original_ribbon_transfer_does_not_color_key_white_pattern(source):
    folder,_=source
    # Check the shipped pixels directly, without an old generated audit file.
    from PIL import Image
    manifest=json.loads((folder/'layers.json').read_text(encoding='utf8'))
    original=np.asarray(Image.open(ROOT/manifest['source']).convert('RGBA'))
    for side in ('l','r'):
        layer=next(item for item in manifest['layers'] if item['id']=='ribbon_'+side)
        pixels=np.asarray(Image.open(folder/layer['file']).convert('RGBA'))
        painted=pixels[:,:,3]>0
        assert painted.sum()>40000
        assert (painted & (pixels[:,:,:3].min(axis=2)>200)).sum()>4000
        assert np.array_equal(pixels[painted],original[painted])


def test_support_wrist_is_independent_of_runtime_breath_and_head_effects(source):
    folder,r=source
    for direction in ('left','right'):
        params=motion_parameters(folder/'runtime/motions'/f'climb_{direction}.motion3.json',.5)
        base=r.evaluate(params)
        for breath in (0,.5,1):
            probe=r.evaluate({**params,'ParamBreath':breath,'ParamAngleX':12,
                'ParamAngleY':-8.5,'ParamAngleZ':2.5,'ParamTyping':1,'ParamListening':1})
            for side,uv in [('l',(.495,.518)),('r',(.526,.518))]:
                np.testing.assert_allclose(point(r,base,f'hand_{side}_support',uv),
                                           point(r,probe,f'hand_{side}_support',uv),atol=1e-10)


def test_native_03_failure_parameters_keep_actual_cuffs_at_hands(source):
    _,r=source
    fixture=json.loads((ROOT/'tests/fixtures/v5-native-contact-03.json').read_text(encoding='utf8'))
    for label,params in fixture['samples'].items():
        poses=r.evaluate(params)
        for side,huv,cuv in [('l',(.495,.518),(.274,.390)),('r',(.526,.518),(.728,.390))]:
            hand=f'hand_{side}_grip_palm' if params['ParamHand'+side.upper()+'Shape']>.5 else f'hand_{side}_support'
            gap=np.linalg.norm(point(r,poses,hand,huv)-point(r,poses,f'sleeve_{side}_lower',cuv))
            assert gap<.012,(label,side,float(gap))


def test_all_native_04_captures_and_probes_preserve_visible_wrist_contacts(source):
    _,r=source
    fixture=json.loads((ROOT/'tests/fixtures/v5-native-contact-04.json').read_text(encoding='utf8'))
    assert len(fixture['samples'])>=100
    for label,sample in fixture['samples'].items():
        poses=r.evaluate(sample['parameters'])
        for hand in sample['nativeVisibleHands']:
            name=hand['id']
            # Complementary fingers do not contain the wrist pixel. The
            # paired palm does and is sampled whenever the grip is visible.
            if name.endswith('_fingers'):continue
            side=name.split('_')[1]
            huv=(.495,.518) if side=='l' else (.526,.518)
            rest=name==f'hand_{side}'
            cuff=f'arm_{side}' if rest else f'sleeve_{side}_lower'
            cuv=huv if rest else ((.274,.390) if side=='l' else (.728,.390))
            gap=np.linalg.norm(point(r,poses,name,huv)-point(r,poses,cuff,cuv))
            assert gap<.012,(label,name,cuff,float(gap))


def test_active_palms_use_the_visible_cuff_native_hierarchy_through_transfer(source):
    _,r=source
    parameter=next(p for p in r.rig.parameters if p.id=='ParamPoseClimb')
    parents={part.id:part.parent_deformer for part in r.rig.parts}
    for side,hand_uv,cuff_uv in [('l',(.495,.518),(.274,.390)),('r',(.526,.518),(.728,.390))]:
        cuff=f'sleeve_{side}_lower'
        hands=[f'hand_{side}_{shape}' for shape in ('relaxed','support','grip_palm')]
        for hand in hands:
            # The registered material point must enter the same native parent,
            # not two nominally equal nonlinear warp stacks.
            assert r.nodes[parents[hand]].parent == parents[cuff]
            assert any(np.linalg.norm(np.asarray(uv)-hand_uv)<1e-10 for uv in r.meshes[hand].uvs)
        assert any(np.linalg.norm(np.asarray(uv)-cuff_uv)<1e-10 for uv in r.meshes[cuff].uvs)
        for key in parameter.keyforms:
            poses=r.evaluate({'ParamPoseClimb':key.value,'ParamArmPoseMode':1,
                              'ParamClimbRefine':1,'ParamClimbActive':1})
            for hand in hands:
                assert np.linalg.norm(point(r,poses,hand,hand_uv)-point(r,poses,cuff,cuff_uv))<1e-7


@pytest.mark.parametrize('state',('clean_ground','clean_top','clean_climb_left','clean_climb_right'))
def test_fan_sweep_cannot_change_cuff_shoulder_compensation(source,state):
    folder,r=source
    params=motion_parameters(folder/'runtime/motions'/f'{state}.motion3.json',0)
    for sweep in np.linspace(-1,1,9):
        pose=r.evaluate({**params,'ParamCleaningSweep':float(sweep)})
        for side,huv,cuv in [('l',(.495,.518),(.274,.390)),('r',(.526,.518),(.728,.390))]:
            shape=params['ParamHand'+side.upper()+'Shape']
            hand=f'hand_{side}_grip_palm' if shape>.5 else f'hand_{side}_support'
            gap=np.linalg.norm(point(r,pose,hand,huv)-point(r,pose,f'sleeve_{side}_lower',cuv))
            assert gap<.012,(state,float(sweep),side,float(gap))


def test_pickup_fall_and_landing_keep_the_same_visible_sleeves_and_hands(source,tmp_path):
    _,r=source
    defaults={p.id:p.default for p in r.rig.parameters}
    rest=r.evaluate(defaults)
    for state in ('drag_left','drag_right','fall_float','land'):
        path=tmp_path/(state+'.motion3.json')
        path.write_text(json.dumps(build_motion(state,ANIMATIONS[state],defaults)),encoding='utf8')
        for phase in (0,.2,.5,.8,1):
            params=motion_parameters(path,phase);pose=r.evaluate(params)
            assert params['ParamArmPoseMode']==0
            assert params['ParamFreeArmActive']==pytest.approx(1, abs=1e-12)
            for side,huv in [('l',(.495,.518)),('r',(.526,.518))]:
                name=f'hand_{side}'
                assert pose[name][1]>.99 and pose[f'arm_{side}'][1]>.99
                assert pose[f'hand_{side}_relaxed'][1]<.01
                assert all(pose[f'sleeve_{side}_{segment}'][1]<.01 for segment in ('upper','lower'))
                gap=np.linalg.norm(point(r,pose,name,huv)-point(r,pose,f'arm_{side}',huv))
                assert gap<.012,(state,phase,side,float(gap))
                if state!='land':
                    displacement=np.linalg.norm(point(r,pose,name,huv)-point(r,rest,f'hand_{side}',huv))
                    # The v5.1 wrist is intentionally closer to the waist.
                    # Require visibly different placement (about 8 px at the
                    # 384 px review size) while bounding outward travel; the
                    # former >.04 floor encouraged the unwanted wide pose.
                    assert .02<displacement<.12,(state,side,float(displacement))
        if state=='land':
            assert params['ParamArmPoseMode']==0
            for side in ('l','r'):
                assert pose[f'hand_{side}'][1]>.99
                assert pose[f'hand_{side}_relaxed'][1]<.01


def test_landing_returns_original_wrists_without_artwork_exchange_and_preserves_face(source,tmp_path):
    _, r = source
    defaults = {p.id: p.default for p in r.rig.parameters}
    path = tmp_path/'land.motion3.json'
    path.write_text(json.dumps(build_motion('land', ANIMATIONS['land'], defaults)), encoding='utf8')
    for phase in (.81, .85, .88, .94, .98, 1):
        params = motion_parameters(path, phase)
        # The v5.1 brace releases first; the original wrists settle at .85.
        # The .81 sample must retain contact while the hands are still moving.
        settled_phase = .85 if 'ParamFreeArmBrace' in params else .78
        if 'ParamFreeArmBrace' in params:
            assert params['ParamFreeArmBrace'] == 0
        if phase >= settled_phase:
            assert params['ParamFreeArmPose'] == 0
        else:
            assert 0 < params['ParamFreeArmPose'] < 1
        pose = r.evaluate(params)
        for side, uv in [('l', (.495, .518)), ('r', (.526, .518))]:
            assert pose[f'hand_{side}_relaxed'][1]<.01
            assert np.linalg.norm(point(r, pose, f'arm_{side}', uv)
                                  - point(r, pose, f'hand_{side}', uv)) < .004
            if phase >= settled_phase:
                neutral_arm = r.evaluate({**params, 'ParamFreeArmActive': 0})[f'arm_{side}'][0]
                np.testing.assert_allclose(pose[f'arm_{side}'][0], neutral_arm, atol=1e-6)
    standing = r.evaluate(defaults)['face_base'][0]
    crouched = r.evaluate({**defaults, 'ParamCrouch': .6})['face_base'][0]
    # A landing must not squash the face while bending the lower body.
    np.testing.assert_allclose(np.ptp(standing, axis=0), np.ptp(crouched, axis=0), atol=.0001)


def test_happy_has_one_settle_and_no_body_bounce(source,tmp_path):
    _, r = source
    defaults = {p.id: p.default for p in r.rig.parameters}
    motion = build_motion('happy', ANIMATIONS['happy'], defaults)
    assert not motion['Meta']['Loop']
    path = tmp_path/'happy.motion3.json'
    path.write_text(json.dumps(motion), encoding='utf8')
    tilts = []
    for phase in np.linspace(0, 1, 81):
        params = motion_parameters(path, float(phase))
        assert params['ParamBounce'] == 0
        tilts.append(params['ParamAngleZ'])
    assert min(tilts) < -2 and tilts[0] == tilts[-1] == 0
    differences = np.diff(tilts)
    moving = differences[abs(differences) > 1e-8]
    assert sum(np.sign(moving[1:]) != np.sign(moving[:-1])) == 1
