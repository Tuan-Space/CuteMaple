"""Measured material contacts for the editable climb rig; no runtime fitting.

The calibration contains fourteen original phase keys from a genuine Editor
export. A final shared affine arm field commutes with native lattice sampling:
coincident cuff/elbow material stays coincident, including the registered fan.
The actual painted palm follows the common wall and nominal host rise. The
shoulder and its transverse line are fixed, giving the minimum rank-one change.
Every later Editor export still needs native contact and visual verification.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from PIL import Image

CALIBRATION = Path(__file__).with_name('climb_contact_native08.json')


def painted_sole_depth(builder, original, ankle_y):
    """Use opaque source pixel centers, excluding transparent mesh padding."""
    path = builder.asset_root / builder.layer_by_id[original]['file']
    with Image.open(path) as image:
        alpha = image.convert('RGBA').getchannel('A')
        bounds = alpha.point(lambda value: 255 if value > 8 else 0).getbbox()
        if not bounds or image.size != (builder.width, builder.height):
            raise ValueError(f'{original}: missing painted sole or source dimensions differ')
        depth = (bounds[3]-.5)/image.height-float(ankle_y)
    if not 0 < depth < .15:
        raise ValueError(f'{original}: painted sole is not below its source ankle')
    return depth


def minimum_affine(shoulder, palm, target):
    """Pin the measured shoulder; translate the palm without a thin triangle."""
    axis = [palm[i]-shoulder[i] for i in range(2)]
    length2 = sum(value*value for value in axis)
    if length2 < .01:
        raise ValueError('Contact calibration has an insufficient shoulder-to-palm span')
    delta = [target[i]-palm[i] for i in range(2)]
    normal = [value/length2 for value in axis]
    matrix = [[float(i == j)+delta[i]*normal[j] for j in range(2)] for i in range(2)]
    shift = [-delta[i]*sum(normal[j]*shoulder[j] for j in range(2)) for i in range(2)]
    determinant = matrix[0][0]*matrix[1][1]-matrix[0][1]*matrix[1][0]
    deformation = math.sqrt(sum((matrix[i][j]-float(i == j))**2 for i in range(2) for j in range(2)))
    if not all(math.isfinite(value) for row in matrix for value in row) or not .8 < determinant < 1.25 or deformation > .2:
        raise ValueError('Contact affine field would excessively distort the sleeve')
    return matrix, shift


def affine_point(point, transform):
    matrix, shift = transform
    return tuple(sum(matrix[i][j]*point[j] for j in range(2))+shift[i] for i in range(2))


def load_calibration(path=CALIBRATION):
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    from climb_refinement import PHASE_KEYS, RISE, WALL_X, MIRROR_X
    if (document.get('schemaVersion') != 1 or document['phaseKeys'] != PHASE_KEYS or
            document['risePerCycle'] != RISE or document['wallX'] != WALL_X or document['mirrorX'] != MIRROR_X):
        raise ValueError('Native contact calibration does not match the authored climb contract')
    if set(document['tracks']) != {'left_l', 'left_r', 'right_l', 'right_r'}:
        raise ValueError('Contact calibration requires independent anatomical near and far arms')
    for track in document['tracks'].values():
        if [key['phase'] for key in track['keys']] != PHASE_KEYS:
            raise ValueError('Contact calibration must use exactly the original fourteen phase keys')
    return document


def contact_transform(track, phase, direction, calibration):
    from climb_refinement import climb_travel, hand_reach
    key = next((key for key in track['keys'] if abs(key['phase']-phase) < 1e-8), None)
    if key is None:
        raise ValueError('Uncalibrated authored phase key')
    target = (calibration['wallX'] if direction > 0 else calibration['mirrorX']-calibration['wallX'],
              track['keys'][0]['palm'][1]+calibration['risePerCycle']*(climb_travel(phase)-hand_reach(phase, track['role'] == 'near')))
    return minimum_affine(key['shoulder'], key['palm'], target)


def contact_pose_weight(pose, direction, role):
    """The far palm relinquishes its wall correction before first rope grab.

    PoseClimb supplies both direction and transfer time, retaining three axes
    per node. Its existing path includes the exact .32-second first-grip key.
    The near arm still follows the ordinary Refine fade until wall release.
    """
    from climb_refinement import smoothstep
    if direction*pose <= 0:
        return 0.
    if role == 'near':
        return 1.
    if role != 'far':
        raise ValueError('Unknown anatomical contact role')
    time = max(0., (1-abs(pose))*1.8)
    return 1-smoothstep(time/.32)


def _branch_root(builder, part):
    node, visited = part.parent_deformer, set()
    while builder.nodes[node].parent != builder.scene:
        if node in visited or builder.nodes[node].parent is None:
            raise ValueError(f'{part.id}: active arm does not descend from the scene')
        visited.add(node)
        node = builder.nodes[node].parent
    return node


def apply_climb_contact_refinement(builder):
    """Run after finish_free_arm_refinement; retain all existing mesh keyforms."""
    calibration = load_calibration()
    for name, expected in calibration['sourceLayers'].items():
        path = builder.asset_root / builder.layer_by_id[name]['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
            raise ValueError(f'{name}: artwork differs from measured contact calibration')
    parts = {part.id: part for part in builder.parts}
    branches = {side: {_branch_root(builder, parts[f'sleeve_{side}_{segment}'])
                       for segment in ('upper', 'lower')} for side in ('l', 'r')}
    if branches['l'] & branches['r']:
        raise ValueError('Independent anatomical arms unexpectedly share a correction branch')
    for side in ('l', 'r'):
        parent = builder.scene
        for direction in (-1, 1):
            label = 'right' if direction > 0 else 'left'
            track = calibration['tracks'][label+'_'+side]
            node = builder.warp(f'ClimbMaterialContact_{label}_{side}', parent, 2)
            builder.deform('ParamClimbPhase', node,
                lambda p, v, tr=track, d=direction: affine_point(p, contact_transform(tr, v, d, calibration)))
            for key in builder.params['ParamPoseClimb'].keyforms:
                key.deformer_offset_weights[node] = contact_pose_weight(key.value, direction, track['role'])
            for key in builder.params['ParamClimbRefine'].keyforms:
                key.deformer_offset_weights[node] = key.value
            parent = node
        for branch in branches[side]:
            builder.nodes[branch].parent = parent
    builder.climb_contact_calibration = calibration


def add_climb_contact_metadata(metadata, builder):
    """Author source materials; the verified installer resolves native atlas UV."""
    calibration = builder.climb_contact_calibration
    contract = metadata['locomotion']['climb']
    contract['contactCalibration'] = {'schemaVersion': 1,
        'nativeMocSha256': calibration['provenance']['mocSha256'],
        'calibrationSha256': hashlib.sha256(CALIBRATION.read_bytes()).hexdigest(),
        'wallX': calibration['wallX'], 'mirrorX': calibration['mirrorX'],
        'nativeReexportVerificationRequired': True}
    for direction in ('left', 'right'):
        contacts = {}
        for side in ('l', 'r'):
            track = calibration['tracks'][direction+'_'+side]
            contacts[track['role']+'Grip'] = {'drawable': f'hand_{side}_support', 'sourceUv': track['sourceUv']}
        for state in (f'climb_{direction}', f'climb_to_top_{direction}', f'clean_climb_{direction}',
                      f'clean_climb_{direction}_enter', f'clean_climb_{direction}_exit'):
            anchors = metadata['states'].setdefault(state, {}).setdefault('anchors', {})
            anchors.update(deepcopy(contacts))
            if not state.startswith('climb_to_top_'):
                anchors[direction] = deepcopy(contacts['nearGrip'])
