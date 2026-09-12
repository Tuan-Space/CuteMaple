"""Register painted sleeping/standing soles to one continuous contact plane.

Only four private foot branches receive standard translation deformers. The two
standing shoes also stop inheriting their private .55 sleep compression: that
cloth fold shortened a painted shoe and opened a gap when its sole was planted.
Source pixels, mesh/UV data, the head, torso and shared ancestors stay untouched.
The parameter zero pose is identity, including currently invisible foot layers.
This is an authoring correction; genuine Editor export/native QA is still needed.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from diagnose_rig import interpolate_keys, warp_points

FOOT_BRANCHES = {
    'leg_l': 'SleepFold_leg_l', 'leg_r': 'SleepFold_leg_r',
    'leg_l_sleep': 'Sleep_Knee_l', 'leg_r_sleep': 'Sleep_Knee_r',
}
EXCHANGE = (.82, .92)


def _smooth(value):
    value = min(1., max(0., float(value)))
    return value * value * (3 - 2 * value)


def _table(points, value):
    value = min(points[-1][0], max(points[0][0], float(value)))
    for (a, x), (b, y) in zip(points, points[1:]):
        if value <= b:
            return x + (y - x) * (value - a) / (b - a)
    return points[-1][1]


def painted_samples(builder, name):
    """Original alpha contours, without color keying or changing any image.

    One source-pixel contour sampling and opaque lattice vertices avoid treating
    the transparent mesh bounding-box corners as actual shoe/cloth contacts.
    """
    layer = builder.layer_by_id[name]
    with Image.open(Path(builder.asset_root) / layer['file']) as image:
        alpha = np.asarray(image.getchannel('A'))
    contours, _ = cv2.findContours((alpha > 8).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise ValueError(f'{name}: missing painted alpha')
    points = np.concatenate([contour[:, 0, :] for contour in contours]).astype(float) + .5
    size = np.array([builder.width, builder.height], dtype=float)
    mesh = builder.meshes[name]
    uv = np.asarray(mesh.uvs, dtype=float)
    if not np.allclose(uv, np.asarray(mesh.vertices), atol=1e-12):
        raise ValueError(f'{name}: contact source coordinates require a source-aligned static mesh')
    pixels = np.clip(np.floor(uv * size).astype(int), [0, 0], size.astype(int) - 1)
    points = np.concatenate([points / size, uv[alpha[pixels[:, 1], pixels[:, 0]] > 8]])
    return points


class ContactEvaluator:
    """Small selected-branch IR evaluator; no texture raster or full-rig copies."""
    def __init__(self, builder):
        self.builder = builder
        self.parts = {part.id: part for part in builder.parts}
        self.samples = {}
        self.grid_cache = {}

    def samples_for(self, name):
        if name not in self.samples:
            self.samples[name] = painted_samples(self.builder, name)
        return self.samples[name]

    def _grid(self, name, settings):
        key = name, tuple(sorted(settings.items()))
        if key in self.grid_cache:
            return self.grid_cache[key]
        node = self.builder.nodes[name]
        base = np.asarray(node.grid_vertices, dtype=float)
        points = base.copy()
        offset_weight = 1.
        for parameter in self.builder.params.values():
            selection = interpolate_keys(parameter, settings.get(parameter.id, parameter.default))
            multiplier = None
            for form, weight in selection:
                if name in form.deformer_offsets:
                    points += np.asarray(form.deformer_offsets[name]) * weight
                if name in form.deformer_offset_weights:
                    multiplier = (multiplier or 0.) + form.deformer_offset_weights[name] * weight
            if multiplier is not None:
                offset_weight *= multiplier
        result = base + (points - base) * offset_weight
        self.grid_cache[key] = result
        return result

    def points(self, name, value, *, breath=0., injected_after=None, dy=0., points=None):
        settings = {'ParamPoseSleep': float(value), 'ParamBreath': float(breath)}
        points = (self.samples_for(name) if points is None else np.asarray(points)).copy()
        for parameter in self.builder.params.values():
            for form, weight in interpolate_keys(parameter, settings.get(parameter.id, parameter.default)):
                if weight and name in form.mesh_offsets and np.any(form.mesh_offsets[name]):
                    raise ValueError(f'{name}: unsupported nonzero mesh-axis offsets during sole calibration')
        parent = self.parts[name].parent_deformer
        visited = set()
        while parent:
            if parent in visited:
                raise ValueError('Cyclic sleep contact branch')
            visited.add(parent)
            node = self.builder.nodes[parent]
            points = warp_points(points, self._grid(parent, settings), node.grid_rows, node.grid_cols)
            if parent == injected_after:
                points[:, 1] += dy
            parent = node.parent
        return points

    def sole(self, name, value, **kwargs):
        return float(self.points(name, value, **kwargs)[:, 1].max())


def sleep_ground_anchor(builder):
    """Return the renderer's shared parameter-point anchor contract."""
    if not hasattr(builder, 'sleep_contact_refinement'):
        raise ValueError('Apply sleep contact refinement before requesting its anchor')
    return deepcopy(builder.sleep_contact_refinement['groundAnchor'])


def apply_sleep_contact_refinement(builder):
    if hasattr(builder, 'sleep_contact_refinement'):
        return builder.sleep_contact_refinement
    for name, branch in FOOT_BRANCHES.items():
        if name not in builder.meshes or branch not in builder.nodes:
            raise ValueError(f'Sleep contact refinement requires {name}/{branch}')
        if sum(part.parent_deformer == branch for part in builder.parts) != 1:
            raise ValueError(f'{branch} is not a private foot branch')
    # The standing source layers contain shoes plus a short trouser cuff, not
    # a full leg. Preserve their painted size while the skirt crouches over
    # them; translating an already squashed shoe left a visible ankle gap.
    # These two private nodes have no other parameter/part consumers.
    for form in builder.params['ParamPoseSleep'].keyforms:
        for name in ('leg_l', 'leg_r'):
            branch = FOOT_BRANCHES[name]
            form.deformer_offsets[branch] = [(0., 0.) for _ in builder.nodes[branch].grid_vertices]
    evaluator = ContactEvaluator(builder)
    keys = [float(form.value) for form in builder.params['ParamPoseSleep'].keyforms]
    if keys[0] != 0 or keys[-1] != 1 or .82 not in keys or .92 not in keys:
        raise ValueError('Sleep contact needs the existing zero/one and exchange keyforms')
    neutral = {name: evaluator.sole(name, 0) for name in ('leg_l', 'leg_r')}
    ground_zero = max(neutral.values())
    # At full sitting the painted skirt hem and soles meet the same floor.
    # Hold the early stand/crouch contact stable; the final sitting settlement
    # is small and smooth, not the hidden skirt mesh's entire .20 translation.
    ground_one = max(ground_zero, evaluator.sole('skirt_sleep', 1))
    ground = [(value, ground_zero + (ground_one - ground_zero) * _smooth((value - .82) / .18))
              for value in keys]
    corrections = {}
    before = {}
    for name, branch in FOOT_BRANCHES.items():
        entries = []
        before[name] = []
        for value in keys:
            actual = evaluator.sole(name, value)
            before[name].append([value, actual])
            wanted = _table(ground, value)
            if name in neutral:
                # Preserve the original subpixel L/R sole difference at rest;
                # both feet meet the plane by the start of the painted exchange.
                wanted -= (ground_zero - neutral[name]) * max(0., 1 - value / .82)
            delta = wanted - actual
            if name.endswith('_sleep'):
                delta *= _smooth(value / .7)  # invisible early preparation; zero is identity
            if value == 0:
                delta = 0.
            if abs(delta) > 1e-12:
                displaced = evaluator.sole(name, value, injected_after=branch, dy=.001)
                slope = (displaced - actual) / .001
                if not .8 < slope < 1.2:
                    raise ValueError(f'{name}: unexpected outer contact-chain response {slope}')
                delta /= slope
            entries.append((value, delta))
        corrections[name] = entries
    for name, entries in corrections.items():
        branch = builder.nodes[FOOT_BRANCHES[name]]
        node = builder.warp('SleepContact_' + name, branch.parent, 5)
        branch.parent = node
        builder.deform('ParamPoseSleep', node,
                       lambda point, value, table=entries: (point[0], point[1] + _table(table, value)))
    # Native Cubism will export these four ordinary uniform-translation warps.
    # Keep this audit explicit about source sampling versus genuine native QA.
    anchor = {'point': [.508, ground_zero], 'parameter': 'ParamPoseSleep',
              'points': [[value, .508, y] for value, y in ground]}
    result = {'kind': 'painted-sole-contact-registration', 'nativeEditorVerified': False,
              'parameter': 'ParamPoseSleep', 'groundAnchor': anchor,
              'exchange': list(EXCHANGE), 'footDrawables': list(FOOT_BRANCHES),
              'sourceAlphaSampling': 'Original alpha>8 contours and opaque lattice vertices; approx one source pixel',
              'beforePaintedSoles': before, 'localTranslations': corrections,
              'newDeformers': ['SleepContact_' + name for name in FOOT_BRANCHES],
              'standingShoeCompressionRemoved': ['SleepFold_leg_l', 'SleepFold_leg_r'],
              'newParameters': [], 'restOffsetsZero': all(table[0][1] == 0 for table in corrections.values())}
    after = ContactEvaluator(builder)
    samples = []
    worst = 0.
    for value in sorted(set(keys + list(np.linspace(.82, 1, 19)))):
        y = _table(ground, value)
        soles = {name: after.sole(name, value) for name in FOOT_BRANCHES}
        active = ('leg_l', 'leg_r') if value < .82 else (
            tuple(FOOT_BRANCHES) if value < .92 else ('leg_l_sleep', 'leg_r_sleep'))
        error = max(abs(soles[name] - y) for name in active)
        worst = max(worst, error)
        samples.append({'pose': value, 'ground': y, 'soles': soles, 'activeMaximumError': error,
                        'sleepHem': after.sole('skirt_sleep', value)})
    result['samples'] = samples
    result['maximumActiveSoleError'] = worst
    if worst > .0015:
        raise ValueError(f'Sleep sole registration exceeds 1.5/1024 canvas: {worst}')
    if any(row['pose'] >= .82 and row['sleepHem'] > row['ground'] + .001 for row in samples):
        raise ValueError('Sleeping cloth would cross its registered ground')
    builder.sleep_contact_refinement = result
    return result
