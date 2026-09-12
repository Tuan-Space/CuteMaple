"""Check handoff sequencing and support invariants, independently of model art."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'authoring'))
from maple_motions import build_motion, TransitionSpec, V5_CLEAN_TRANSITIONS
from diagnose_rig import motion_parameters


@pytest.fixture
def sample(tmp_path):
    idle = json.loads((ROOT / 'assets/live2d/Maple/motions/idle.motion3.json').read_text())
    defaults = {curve['Id']: curve['Segments'][1] for curve in idle['Curves']}
    defaults.update({name: 0 for name in ('ParamFreeArmBrace', 'ParamCleanContext',
        'ParamCleanPoseL', 'ParamCleanPoseR', 'ParamCleanBrushL', 'ParamCleanBrushR', 'ParamCleanStroke',
        'ParamClimbDrapeBlend')})

    def at(state, phase):
        spec = V5_CLEAN_TRANSITIONS.get(state, TransitionSpec((1000,), 'one_shot' if state == 'land' else 'loop'))
        motion = build_motion(state, spec, defaults)
        path = tmp_path / (state + '.motion3.json')
        path.write_text(json.dumps(motion))
        return motion_parameters(path, phase), motion
    return at


@pytest.mark.parametrize('state,work,support,context', [
    ('clean_ground', 'R', 'L', 0), ('clean_top', 'R', 'L', 2),
    ('clean_climb_left', 'L', 'R', -1), ('clean_climb_right', 'R', 'L', 1)])
def test_brush_appears_only_after_arm_reaches_ready_pose_and_stows_before_return(sample, state, work, support, context):
    for phase in (0, .2, .5, .78):
        pose, _ = sample(state + '_enter', phase)
        assert pose['ParamCleanBrush' + work] == 0
        assert pose['ParamCleanContext'] == pytest.approx(context, abs=1e-12)
        assert pose['ParamClimbDrapeBlend'] == pytest.approx(int(context in (-1, 1)), abs=1e-12)
    ready, _ = sample(state + '_enter', .9)
    assert ready['ParamCleanPose' + work] == 1
    assert ready['ParamCleanBrush' + work] > 0
    for phase in (0, .15, .34):
        pose, _ = sample(state + '_exit', phase)
        assert pose['ParamCleanPose' + work] == pytest.approx(1, abs=1e-12)
    stowed, _ = sample(state + '_exit', .5)
    assert stowed['ParamCleanBrush' + work] == 0
    assert 0 < stowed['ParamCleanPose' + work] < 1
    for phase in (0, .16, .35, .62, .85, 1):
        pose, motion = sample(state, phase)
        assert pose['ParamCleanBrush' + support] == 0
        assert pose['ParamCleanFanL'] == pose['ParamCleanFanR'] == 0
        if state != 'clean_ground':
            assert pose['ParamCleanPose' + support] == 0
            assert pose['ParamHand' + support + 'Shape'] == pytest.approx(1 if state == 'clean_top' else -1, abs=1e-12)
        assert motion['UserData'] == [{'Time': .62, 'Value': 'clean_sweep'}]
    initial, _ = sample(state, 0)
    end, _ = sample(state, 1)
    assert initial == end


def test_ground_cleanup_preserves_original_sleeves_and_relaxed_hand_through_take_and_stow(sample):
    for state in ('clean_ground_enter', 'clean_ground', 'clean_ground_exit'):
        for phase in (0., .05, .15, .3, .5, .7, .85, 1.):
            pose, unused = sample(state, phase)
            assert pose['ParamArmPoseMode'] == 0
            assert pose['ParamArmSupportBlend'] == 0
            assert pose['ParamHandLShape'] == 0
            if pose['ParamCleanPoseR'] > .01:
                assert pose['ParamHandRShape'] == pytest.approx(1, abs=1e-12)
    for state, phase in (('clean_ground_enter', 0), ('clean_ground_exit', 1)):
        pose, unused = sample(state, phase)
        assert pose['ParamCleanPoseR'] == pose['ParamHandRShape'] == 0


def test_landing_releases_elbow_brace_before_returning_hands_to_rest(sample):
    falling, _ = sample('fall_float', .5)
    landing, _ = sample('land', 0)
    for name in ('ParamFreeArmActive', 'ParamFreeArmPose', 'ParamFreeArmBrace'):
        assert falling[name] == landing[name] == 1
    early, _ = sample('land', .35)
    assert early['ParamFreeArmPose'] == 1
    assert 0 < early['ParamFreeArmBrace'] < 1
    middle, _ = sample('land', .5)
    assert middle['ParamFreeArmPose'] == 1 and middle['ParamFreeArmBrace'] == 0
    late, _ = sample('land', .8)
    assert 0 < late['ParamFreeArmPose'] < 1 and late['ParamFreeArmBrace'] == 0
    end, _ = sample('land', 1)
    assert end['ParamFreeArmPose'] == end['ParamFreeArmBrace'] == 0
