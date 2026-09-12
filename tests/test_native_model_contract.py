"""New model identity remains exact when authoring adds independent pose axes."""
import pytest

from tools.native_model_contract import REFINED_MOTION_REVISION, declared_native_parameters, free_motion_contacts
from tools.verify_maple_model import record_native_identity, V5_PARAMETERS, V5_DRAWABLES, ANIMATIONS, TRANSITIONS, V5_CLEAN_SEGMENTS


@pytest.mark.parametrize('mutation', ['none', 'renamed-axis', 'missing-axis', 'duplicate-declaration', 'unknown-revision'])
def test_refined_identity_checks_declared_set_not_just_a_larger_parameter_count(mutation):
    core = {'ParamHeadTurn', 'ParamBodyTurn', 'ParamHandLShape', 'ParamHandRShape',
            'ParamTransitionProgress', 'ParamArmPoseMode', 'ParamPoseSwing', 'ParamSwing', 'ParamSwingVisible'} | V5_PARAMETERS
    core |= {'ParamFreeArmActive', 'ParamFreeArmPose', 'ParamClimbRefine', 'ParamArmSupportBlend'}
    names = sorted(core | {f'ParamFixture{i}' for i in range(71-len(core))})
    states = [*ANIMATIONS, *TRANSITIONS, *V5_CLEAN_SEGMENTS]
    metadata = {'version': 5, 'requiredParameters': sorted(core), 'motionRevision': REFINED_MOTION_REVISION,
                'nativeParameterIds': names[:]}
    parameters = dict.fromkeys(names, 0)
    ids = sorted(V5_DRAWABLES | {'face_base', 'torso', 'leg_l_climb_handoff', 'leg_r_climb_handoff'})
    ids += [f'fixture_mesh_{i}' for i in range(143-len(ids))]
    metadata['nativeDrawableIds'] = sorted(ids)
    report = {'ready': True, 'actualNative': True, 'nativeStates': states, 'finished': dict.fromkeys(states, True),
              'captures': {'baseline': {'parameters': parameters, 'drawables': [{'id': name} for name in ids]}}}
    if mutation == 'renamed-axis':
        parameters['ParamUnexpected'] = parameters.pop('ParamFixture0')
    elif mutation == 'missing-axis': parameters.pop('ParamFreeArmPose')
    elif mutation == 'duplicate-declaration': metadata['nativeParameterIds'][-1] = names[0]
    elif mutation == 'unknown-revision': metadata['motionRevision'] = 'unknown'
    record_native_identity(report, metadata)
    assert report['modelRevision'] == ('v5' if mutation == 'none' else None)


def test_legacy_parameter_contract_remains_distinct():
    assert declared_native_parameters({'version': 5}) is None
    with pytest.raises(ValueError):
        declared_native_parameters({'version': 5, 'nativeParameterIds': []})


def test_free_motion_contract_rejects_hidden_donor_sleeves_and_changed_registration():
    metadata = {'motionRevision': REFINED_MOTION_REVISION, 'freeContacts': {
        'parameter': 'ParamFreeArmActive', 'poseParameter': 'ParamFreeArmPose', 'armPoseMode': 0,
        'samePaintedSleevesThroughLanding': True, 'contacts': {
            'l': {'hand': 'hand_l', 'cuff': 'arm_l', 'sourceUv': [.495, .518]},
            'r': {'hand': 'hand_r', 'cuff': 'arm_r', 'sourceUv': [.526, .518]}}}}
    assert free_motion_contacts(metadata)['l']['hands'] == ['hand_l']
    metadata['freeContacts']['contacts']['l']['cuff'] = 'sleeve_l_lower'
    with pytest.raises(ValueError, match='contact contract'):
        free_motion_contacts(metadata)
    with pytest.raises(ValueError, match='missing'):
        free_motion_contacts({'motionRevision': REFINED_MOTION_REVISION})
    assert free_motion_contacts({'version': 5}) is None
