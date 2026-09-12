"""Exact authored/native parameter identity for the legacy and refined v5 rigs."""
REFINED_MOTION_REVISION = 'v5-motion-refined-20260909'
BRUSH_PARAMETERS = {'ParamCleanContext', 'ParamCleanPoseL', 'ParamCleanPoseR',
                    'ParamCleanBrushL', 'ParamCleanBrushR', 'ParamCleanStroke'}
POLISH_PARAMETERS = BRUSH_PARAMETERS | {'ParamFreeArmBrace', 'ParamClimbDrapeBlend'}
BRUSH_DRAWABLES = {f'clean_brush_{side}' for side in ('l', 'r')} | {
    f'clean_hand_{side}_{part}' for side in ('l', 'r') for part in ('palm', 'fingers')} | {
    f'clean_sleeve_{side}_lower' for side in ('l', 'r')} | {
    'clean_ground_brush_r', 'clean_ground_hand_r_palm', 'clean_ground_hand_r_fingers',
    'clean_ground_hand_r_relaxed'}
CLEAN_CONTEXTS = {'clean_ground': 0, 'clean_top': 2, 'clean_climb_left': -1, 'clean_climb_right': 1}


def physics_output_parameters(definition: dict, native_parameters) -> list[str]:
    """Actual physics destinations; support/control channels must remain final-pose checks."""
    import re
    if definition.get('Version') != 3 or not isinstance(definition.get('PhysicsSettings'), list):
        raise ValueError('Invalid physics output declaration')
    names = set()
    for setting in definition['PhysicsSettings']:
        if not isinstance(setting.get('Output'), list):
            raise ValueError('Invalid physics output list')
        for output in setting['Output']:
            destination = output.get('Destination', {})
            name = destination.get('Id')
            if destination.get('Target') != 'Parameter' or not isinstance(name, str) or name not in native_parameters:
                raise ValueError('Physics output does not name an actual native parameter')
            if re.match(r'^Param(?:Pose|Climb|Clean|FreeArm|Hand|ArmPose|ArmSupport|Transition|Swing|HeadTurn|BodyTurn|(?:Arm|Leg)[LR][AB]$)', name):
                raise ValueError(f'Physics may not replace support/control endpoint verification: {name}')
            names.add(name)
    return sorted(names)


def physics_resource_contract(model_directory, settings: dict, native_parameters) -> dict:
    """Reopen the model's own physics file; report-declared exceptions are insufficient."""
    import hashlib
    import json
    from pathlib import Path
    root = Path(model_directory).resolve(strict=True)
    path = (root / settings['FileReferences']['Physics']).resolve(strict=True)
    relative = path.relative_to(root).as_posix()
    data = path.read_bytes()
    return {'resource': 'assets/live2d/Maple/'+relative, 'sha256': hashlib.sha256(data).hexdigest(),
            'parameters': physics_output_parameters(json.loads(data), native_parameters)}


def motion_polish_enabled(refinement: dict) -> bool:
    version = refinement.get('motionPolishVersion', 0)
    if type(version) is not int or version not in (0, 1):
        raise ValueError('Unsupported motionPolishVersion')
    return version == 1


def brush_contract(metadata: dict) -> dict | None:
    """Require painted material anchors, including every take/return segment."""
    import math
    refinement = metadata.get('refinement', {})
    if not motion_polish_enabled(refinement):
        return None
    tool = refinement.get('cleaningTool', {})
    forearms = {side: {'drawable': f'clean_sleeve_{side}_lower', 'replaces': f'sleeve_{side}_lower',
                       'visibilityParameter': 'ParamCleanBrush'+side.upper(), 'drawOrder': 158} for side in ('l', 'r')}
    if (tool.get('type') != 'brush' or set(tool.get('parameters', [])) != BRUSH_PARAMETERS
            or tool.get('contexts') != {'-1': 'left', '0': 'ground', '1': 'right', '2': 'top'}
            or tool.get('forearms') != forearms
            or tool.get('rigidGrip') is not True or tool.get('legacyFanUsed') is not False):
        raise ValueError('Missing motion-polish painted brush contract')
    result = {}
    for state, context in CLEAN_CONTEXTS.items():
        side = 'l' if context == -1 else 'r'
        expected = {'workSide': side, 'supportSide': None if context == 0 else ('r' if side == 'l' else 'l'),
                    'context': context, 'forearmDrawable': forearms[side]['drawable']}
        prefix = 'clean_ground_' if context == 0 else 'clean_'
        if context == 0:
            original = free_motion_contacts(refinement)
            expected.update(forearmDrawable=original[side]['cuff'], forearmKind='whole',
                            cuffSourceUv=original[side]['sourceUv'], armPoseMode=0,
                            relaxedHandDrawable='clean_ground_hand_r_relaxed')
        definition = metadata.get('states', {}).get(state, {})
        if definition.get('cleaningTool') != expected:
            raise ValueError(f'Invalid brush work/support hand: {state}')
        anchors = definition.get('anchors', {})
        for suffix in ('', '_enter', '_exit'):
            segment = metadata.get('states', {}).get(state+suffix, {})
            if segment.get('cleaningTool') != expected:
                raise ValueError(f'Invalid brush work/support hand: {state+suffix}')
            actual = segment.get('anchors', {})
            for key, drawable in [('brushTip', prefix+'brush_'+side), ('brushGrip', prefix+'brush_'+side),
                                  ('freeHand', prefix+'hand_'+side+'_fingers')]:
                anchor = actual.get(key, {})
                coordinate = 'sourceUv' if 'sourceUv' in anchor else 'atlasUv'
                uv = anchor.get(coordinate, [])
                if (anchor.get('drawable') != drawable or set(anchor) != {'drawable', coordinate}
                        or not isinstance(uv, list) or len(uv) != 2
                        or not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in uv)
                        or anchor != anchors.get(key)):
                    raise ValueError(f'Invalid brush material anchor: {state+suffix}/{key}')
            if 'fanTip' in actual:
                raise ValueError(f'Brush cleanup still declares a fan tip: {state+suffix}')
        if anchors['brushTip'] == anchors['brushGrip']:
            raise ValueError(f'Brush bristles and handle use the same material point: {state}')
        if ('sourceUv' in anchors['brushGrip'] and
                anchors['brushGrip']['sourceUv'] != anchors['freeHand'].get('sourceUv')):
            raise ValueError(f'Brush/finger source grip registration differs: {state}')
        result[state] = {**expected, 'anchors': {key: anchors[key] for key in ('brushTip', 'brushGrip', 'freeHand')},
                         'palmDrawable': prefix+'hand_'+side+'_palm',
                         'fingersDrawable': prefix+'hand_'+side+'_fingers'}
    return result


def climb_drape_exchange(refinement: dict) -> dict | None:
    if not motion_polish_enabled(refinement):
        return None
    expected = {'parameter': 'ParamClimbDrapeBlend', 'default': 0, 'unfoldEndSeconds': .4,
                'exchangeEndSeconds': .52, 'blend': 'opaque-receiving-surface-normal-over',
                'receivingOpacityGuard': .9999}
    if refinement.get('climbDrapeExchange') != expected:
        raise ValueError('Missing independent climbing drape material exchange contract')
    return expected


def climb_materials(refinement: dict) -> dict | None:
    if not motion_polish_enabled(refinement):
        return None
    climb_drape_exchange(refinement)
    expected = {direction: {'drawable': f'profile_{side}_skirt_climb_drape',
                            'replaces': f'profile_{side}_skirt',
                            'underlay': f'profile_{side}_costume_underlay_skirt'}
                for direction, side in [('left', 'l'), ('right', 'r')]}
    if refinement.get('climbMaterials') != expected:
        raise ValueError('Missing motion-polish climbing material replacement contract')
    return expected


def free_motion_contacts(refinement: dict) -> dict | None:
    """Refined pickup/fall keep the original sleeve and hand artwork visible."""
    contract = refinement.get('freeContacts')
    if contract is None:
        if refinement.get('motionRevision') == REFINED_MOTION_REVISION:
            raise ValueError('Refined motion is missing the free sleeve contact contract')
        return None
    contacts = contract.get('contacts', {})
    expected = {side: {'hand': 'hand_'+side, 'cuff': 'arm_'+side, 'sourceUv': uv}
                for side, uv in [('l', [.495, .518]), ('r', [.526, .518])]}
    if (refinement.get('motionRevision') != REFINED_MOTION_REVISION
            or contract.get('parameter') != 'ParamFreeArmActive'
            or contract.get('poseParameter') != 'ParamFreeArmPose'
            or contract.get('armPoseMode') != 0
            or contract.get('samePaintedSleevesThroughLanding') is not True
            or contacts != expected):
        raise ValueError('Invalid original sleeve free-motion contact contract')
    return {side: {'cuff': item['cuff'], 'hands': [item['hand']], 'sourceUv': item['sourceUv']}
            for side, item in contacts.items()}


def declared_native_parameters(refinement: dict) -> set[str] | None:
    polished = motion_polish_enabled(refinement)
    revision = refinement.get('motionRevision')
    names = refinement.get('nativeParameterIds')
    if revision is None and names is None and not polished:
        return None  # The preserved original v5 contract contains 67 axes.
    if (revision != REFINED_MOTION_REVISION or not isinstance(names, list)
            or not 70 <= len(names) <= 100
            or any(not isinstance(name, str) or not name.startswith('Param') for name in names)
            or len(set(names)) != len(names)
            or not {'ParamFreeArmActive', 'ParamFreeArmPose', 'ParamClimbRefine'}.issubset(names)
            or (polished and not POLISH_PARAMETERS.issubset(names))
            or not set(refinement.get('requiredParameters', [])).issubset(names)):
        raise ValueError('Invalid refined v5 authored native parameter inventory')
    return set(names)


def declared_native_drawables(refinement: dict) -> set[str] | None:
    polished = motion_polish_enabled(refinement)
    materials = climb_materials(refinement)
    revision, names = refinement.get('motionRevision'), refinement.get('nativeDrawableIds')
    if revision is None and names is None and not polished:
        return None
    if (revision != REFINED_MOTION_REVISION or not isinstance(names, list)
            or not 143 <= len(names) <= 200
            or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names)
            or (polished and not BRUSH_DRAWABLES.issubset(names))
            or (materials and not {name for value in materials.values() for name in value.values()}.issubset(names))
            or not {'face_base', 'torso', 'leg_l_climb_handoff', 'leg_r_climb_handoff'}.issubset(names)):
        raise ValueError('Invalid refined v5 authored drawable inventory')
    return set(names)
