"""Bounded, genuine Cubism Core checks for the refined motion export.

This never reads an IR mesh as runtime geometry. It evaluates the exported
motion curves in the unmodified bundled Core, and maps original painted pixels
through a separately verified native atlas/UV audit. It is not GPU, physics,
native motion-manager, desktop-window or visual acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.qa_model_inputs import audit_atlas_files, digest, recheck_input_hashes, verify_native_uv_evidence
from tools.native_model_contract import declared_native_parameters, declared_native_drawables

CORE = ROOT / 'third_party/CubismSdkForWeb-5-r.5/Core/live2dcubismcore.min.js'
FEET = ('leg_l', 'leg_r', 'leg_l_sleep', 'leg_r_sleep')
SOLE_LIMIT = .0015
NODE_PROGRAM = r'''
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const request=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const bytes=fs.readFileSync(request.moc),code=fs.readFileSync(request.core);
globalThis.require=require;globalThis.__dirname=path.dirname(request.core);
console.log=(...a)=>process.stderr.write(a.join(' ')+'\n');
vm.runInThisContext(code.toString('utf8'));
setTimeout(()=>{let moc,model;try{
 const core=globalThis.Live2DCubismCore;core.Logging.csmSetLogFunction(()=>{});
 const buffer=bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);
 if(bytes.subarray(0,4).toString()!=='MOC3'||core.Moc.prototype.hasMocConsistency(buffer)!==1)throw Error('Official Core rejected MOC3');
 moc=core.Moc.fromArrayBuffer(buffer);model=core.Model.fromMoc(moc);if(!model)throw Error('No native model');
 const ids=Array.from(model.parameters.ids),drawIds=Array.from(model.drawables.ids);
 const equal=(a,b)=>JSON.stringify([...a].sort())===JSON.stringify([...b].sort());
 if(!equal(ids,request.parameterIds)||!equal(drawIds,request.drawableIds))throw Error('Native identity differs from declared refined export');
 const indices=new Map(ids.map((id,i)=>[id,i]));
 const selected=request.selected.map(id=>{const i=drawIds.indexOf(id);if(i<0)throw Error('Missing drawable '+id);return i;});
 const d=model.drawables;
 const topology=Object.fromEntries(selected.map(i=>[drawIds[i],{textureIndex:d.textureIndices[i],
  textureUvs:Array.from(d.vertexUvs[i],(v,j)=>j%2?1-v:v),triangles:Array.from(d.indices[i])}]));
 const frames=[];
 for(const target of request.frames){
  model.parameters.values.set(model.parameters.defaultValues);
  for(const [name,value]of Object.entries(target.parameters)){
   const i=indices.get(name);if(i===undefined||!Number.isFinite(value))throw Error('Invalid target '+name);
   if(value<model.parameters.minimumValues[i]-1e-5||value>model.parameters.maximumValues[i]+1e-5)throw Error('Target outside native range '+name);
   model.parameters.values[i]=Math.min(model.parameters.maximumValues[i],Math.max(model.parameters.minimumValues[i],value));
  }
  d.resetDynamicFlags();model.update();
  const geometry=new Set(target.geometryNames||request.selected);
  const drawables=Object.fromEntries(selected.map(i=>{if(!geometry.has(drawIds[i]))return [drawIds[i],{opacity:d.opacities[i]}];const p=Array.from(d.vertexPositions[i]);
   if(!p.every(Number.isFinite))throw Error('Nonfinite native geometry '+drawIds[i]);
   return [drawIds[i],{positions:p,opacity:d.opacities[i]}];}));
  frames.push({id:target.id,parameters:Object.fromEntries(ids.map((id,i)=>[id,model.parameters.values[i]])),drawables});
 }
 const sourceHashesAtEnd={[request.moc]:sha(fs.readFileSync(request.moc)),[request.core]:sha(fs.readFileSync(request.core))};
 if(sourceHashesAtEnd[request.moc]!==sha(bytes)||sourceHashesAtEnd[request.core]!==sha(code))throw Error('Native input changed');
 fs.writeFileSync(request.output,JSON.stringify({coreVersion:core.Version.csmGetVersion(),nativeCoreSamples:true,
  sourceHashesAtStart:{[request.moc]:sha(bytes),[request.core]:sha(code)},sourceHashesAtEnd,
  canvasInfo:{width:model.canvasinfo.CanvasWidth,height:model.canvasinfo.CanvasHeight,
   originX:model.canvasinfo.CanvasOriginX,originY:model.canvasinfo.CanvasOriginY,pixelsPerUnit:model.canvasinfo.PixelsPerUnit},topology,frames}),{flag:'wx'});
 process.stdout.write(JSON.stringify({frames:frames.length,drawables:selected.length})+'\n');
}catch(e){process.stderr.write(String(e.stack||e)+'\n');process.exitCode=1;}finally{model?.release();moc?._release();}},0);
'''


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def resource(folder, name):
    path = (folder / name).resolve(strict=True)
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ValueError('Model/authoring reference escapes its directory')
    return path


def curve_value(curve, seconds):
    """Only verified restricted-time Cubism cubic segments, never arbitrary eval."""
    data = curve['Segments']
    if len(data) < 9 or (len(data)-2) % 7 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in data):
        raise ValueError('Malformed restricted cubic curve')
    ta, va = data[:2]
    result = None
    for i in range(2, len(data), 7):
        kind, t1, v1, t2, v2, tb, vb = data[i:i+7]
        span = tb-ta
        if kind != 1 or span <= 0 or abs(t1-ta-span/3) > 2e-6 or abs(t2-tb+span/3) > 2e-6:
            raise ValueError('Unrestricted or malformed cubic time')
        if result is None and seconds <= tb:
            u = min(1., max(0., (seconds-ta)/span))
            result = (1-u)**3*va+3*(1-u)**2*u*v1+3*(1-u)*u*u*v2+u**3*vb
        ta, va = tb, vb
    return va if result is None else result


def motion_pose(motion, fraction):
    if motion['Meta'].get('AreBeziersRestricted') is not True or motion['Meta']['Duration'] <= 0:
        raise ValueError('Expected authored restricted motion')
    curves = [c for c in motion['Curves'] if c['Target'] == 'Parameter']
    if len({c['Id'] for c in curves}) != len(curves):
        raise ValueError('Duplicate motion parameter')
    return {c['Id']: curve_value(c, fraction*motion['Meta']['Duration']) for c in curves}


def ground_point(anchor, parameters):
    if anchor.get('parameter') != 'ParamPoseSleep' or 'drawable' in anchor:
        raise ValueError('Sleep requires its calibrated parameter-point ground anchor')
    points = np.asarray(anchor['points'], dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 2 or not np.isfinite(points).all():
        raise ValueError('Invalid ground point curve')
    if points[0, 0] != 0 or points[-1, 0] != 1 or np.any(np.diff(points[:, 0]) <= 0):
        raise ValueError('Ground parameter knots must increase from zero to one')
    value = parameters['ParamPoseSleep']
    return np.array([np.interp(value, points[:, 0], points[:, axis]) for axis in (1, 2)])


def canvas_positions(values, info):
    points = np.asarray(values, dtype=float).reshape(-1, 2)
    if len(points) < 3 or not np.isfinite(points).all():
        raise ValueError('Missing finite native vertices')
    return (np.array([info['originX'], info['originY']])+points*np.array([1, -1])*info['pixelsPerUnit'])/np.array([info['width'], info['height']])


def bind_samples(topology, points):
    """Exact native UV triangles; no nearest-vertex or bbox fallbacks."""
    uv = np.asarray(topology['textureUvs'], dtype=float).reshape(-1, 2)
    triangles = np.asarray(topology['triangles'], dtype=int).reshape(-1, 3)
    if not np.isfinite(uv).all() or np.any(triangles < 0) or np.any(triangles >= len(uv)):
        raise ValueError('Invalid native UV topology')
    ids = np.full((len(points), 3), -1, dtype=int)
    weights = np.zeros((len(points), 3))
    for triangle in triangles:
        a, b, c = uv[triangle]
        matrix = np.array([b-a, c-a]).T
        if abs(np.linalg.det(matrix)) < 1e-15:
            continue
        w = (points-a) @ np.linalg.inv(matrix).T
        valid = (ids[:, 0] < 0) & (w.min(axis=1) >= -1e-7) & (w.sum(axis=1) <= 1+1e-7)
        ids[valid] = triangle
        weights[valid] = np.column_stack((1-w[valid].sum(axis=1), w[valid]))
    if (ids < 0).any():
        raise ValueError(f'{int((ids[:, 0] < 0).sum())} source paint points lie outside native UV triangles')
    return ids, weights


def painted_points(path, region, atlas_size):
    with Image.open(path) as image:
        alpha = np.asarray(image.convert('RGBA'))[:, :, 3]
    if list(alpha.shape[::-1]) != region['sourceSize']:
        raise ValueError('Source PNG dimensions differ from verified atlas')
    contours, _ = cv2.findContours((alpha > 8).astype('uint8'), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise ValueError('Empty source alpha')
    pixels = np.concatenate([contour[:, 0] for contour in contours]).astype(float)+.5
    return (pixels-np.array(region['translationToCanvas']))/np.array(atlas_size)


def inspect_sleep(samples, targets, metadata, bindings):
    rows, errors = [], []
    info = samples['canvasInfo']
    anchors = [metadata['states'][state]['anchors']['ground'] for state in ('idle', 'sleep_enter', 'sleep_loop', 'sleep_exit')]
    if any(anchor != anchors[0] for anchor in anchors):
        raise ValueError('Sleep/idle use different ground anchors at the completion boundary')
    for frame in samples['frames']:
        plan = targets[frame['id']]
        if plan['group'] != 'sleep':
            continue
        ground = float(ground_point(anchors[0], frame['parameters'])[1])
        measured = {}
        for name in (*FEET, 'skirt', 'skirt_sleep'):
            item = frame['drawables'][name]
            if item['opacity'] <= .01:
                continue
            vertices = canvas_positions(item['positions'], info)
            ids, weights = bindings[name]
            sole = float((vertices[ids]*weights[..., None]).sum(axis=1)[:, 1].max())
            measured[name] = {'opacity': item['opacity'], 'paintedBottom': sole, 'groundDelta': sole-ground}
            if name in FEET and abs(sole-ground) > SOLE_LIMIT:
                errors.append(f"Native painted sole misses ground: {frame['id']}/{name}: {sole-ground:.7f}")
            if sole-ground > SOLE_LIMIT:
                errors.append(f"Native painted lower clothing crosses ground: {frame['id']}/{name}: {sole-ground:.7f}")
        if not any(name in FEET for name in measured):
            errors.append('No visible native foot: '+frame['id'])
        rows.append({'id': frame['id'], 'pose': frame['parameters']['ParamPoseSleep'],
                     'breath': frame['parameters']['ParamBreath'], 'ground': ground, 'paintedLayers': measured})
    if not rows:
        errors.append('Missing native sleep samples')
    return {'passed': not errors, 'errors': errors, 'limitCanvas': SOLE_LIMIT, 'samples': rows}


def vertex_pair_error(samples, first, a, last, b):
    """Compare matching original material points despite distinct atlas regions."""
    first_vertices = canvas_positions(first['drawables'][a]['positions'], samples['canvasInfo'])
    last_vertices = canvas_positions(last['drawables'][b]['positions'], samples['canvasInfo'])
    # Caller supplies native matching by verified original source UV below.
    mapping = samples['sourceVertexPairing'][a+'|'+b]
    return float(np.linalg.norm(first_vertices-last_vertices[mapping], axis=1).max())


def climb_leg_drawables(metadata):
    """Select the authored receiving leg pair, preserving earlier export evidence."""
    contract = (metadata or {}).get('locomotion', {}).get('climb', {})
    names = contract.get('refinedLegDrawables')
    expected = [f'leg_{side}_climb_refined' for side in ('l', 'r')]
    declared = set((metadata or {}).get('refinement', {}).get('nativeDrawableIds', []))
    if names is None:
        if declared.intersection(expected):
            raise ValueError('Native refined legs are missing their motion contract')
        return {side: f'leg_{side}_climb' for side in ('l', 'r')}
    if (names != expected or contract.get('refinedLegRoles') != dict(zip(expected, ('near', 'far')))
            or (declared and not set(expected).issubset(declared))):
        raise ValueError('Invalid refined near/far leg artwork contract')
    return dict(zip(('l', 'r'), expected))


def inspect_handoff(samples, targets, metadata):
    contract = metadata['locomotion']['climb']
    expected = {'leg_l_climb_handoff', 'leg_r_climb_handoff'}
    if set(contract.get('handoffDrawables', [])) != expected or contract.get('handoffParameter') != 'ParamClimbHandoff':
        raise ValueError('Missing single-pair climbing handoff contract')
    errors, rows, endpoints = [], [], []
    by_id = {frame['id']: frame for frame in samples['frames']}
    climb_legs = climb_leg_drawables(metadata)
    for name, pair in [('left-start', set(climb_legs.values())), ('right-start', set(climb_legs.values())),
                       ('swing-start', {'leg_l_swing', 'leg_r_swing'})]:
        opacities = {key: item['opacity'] for key, item in by_id[name]['drawables'].items()
                     if re.search(r'(^|_)leg_[lr](?:_|$)', key)}
        if {key for key, alpha in opacities.items() if alpha > .01} != pair or any(opacities[key] < .99 for key in pair):
            errors.append('Native handoff reference does not show its authored leg pair: '+name)
    for frame in samples['frames']:
        plan = targets[frame['id']]
        # Weight-zero pickup samples are still the unblended climbing pose.
        # Reuse their already sampled leg opacities to catch a legacy leg
        # resurfacing mid-cycle, without adding Core frames or geometry.
        source = plan.get('from', [])
        climbing_reference = (plan['group'] == 'free' and plan.get('fraction') == 0
                              and len(source) == 2 and source[0] in ('climb_left', 'climb_right'))
        if plan['group'] != 'handoff' and not climbing_reference:
            continue
        pair = set(climb_legs.values()) if climbing_reference else expected
        opacities = {name: item['opacity'] for name, item in frame['drawables'].items() if re.search(r'(^|_)leg_[lr](?:_|$)', name)}
        visible = {name for name, alpha in opacities.items() if alpha > .01}
        if visible != pair or any(opacities[name] < .99 for name in pair):
            pose = 'climbing cycle' if climbing_reference else 'transfer'
            errors.append(f'Native {pose} does not show exactly one opaque leg pair: '+frame['id'])
        rows.append({'id': frame['id'], 'opacities': opacities})
    for side in ('left', 'right'):
        for limb in ('l', 'r'):
            handoff = f'leg_{limb}_climb_handoff'
            comparisons = [('entry', by_id[f'{side}-start'], climb_legs[limb], by_id[f'{side}-handoff-000']),
                           ('exit', by_id['swing-start'], f'leg_{limb}_swing', by_id[f'{side}-handoff-060'])]
            for label, reference, original, frame in comparisons:
                error = vertex_pair_error(samples, frame, handoff, reference, original)
                endpoints.append({'side': side, 'limb': limb, 'endpoint': label, 'maximumMaterialVertexGap': error})
                if error > .001:
                    errors.append(f'Native handoff endpoint differs: {side}/{limb}/{label}: {error:.7f}')
    return {'passed': not errors, 'errors': errors, 'endpointLimitCanvas': .001, 'samples': rows, 'endpoints': endpoints}


def inspect_happy(samples, targets):
    frames = [f for f in samples['frames'] if targets[f['id']]['group'] == 'happy']
    errors, turns = [], {}
    for parameter in ('ParamAngleY', 'ParamAngleZ'):
        values = np.array([f['parameters'][parameter] for f in frames])
        delta = np.diff(values); signs = np.sign(delta[np.abs(delta) > 1e-5])
        count = int(np.count_nonzero(np.diff(signs)))
        turns[parameter] = count
        if count > 1:
            errors.append('Happy repeats authored head oscillations: '+parameter)
    for parameter in ('ParamBounce', 'ParamBodyAngleZ', 'ParamCrouch'):
        if max(abs(f['parameters'][parameter]) for f in frames) > 1e-5:
            errors.append('Happy contains unwanted body bounce: '+parameter)
    base = canvas_positions(frames[0]['drawables']['face_base']['positions'], samples['canvasInfo'])
    base -= base.mean(axis=0)
    maximum = 0.
    for frame in frames:
        face = canvas_positions(frame['drawables']['face_base']['positions'], samples['canvasInfo'])
        face -= face.mean(axis=0)
        singular = np.linalg.svd(np.linalg.lstsq(base, face, rcond=None)[0], compute_uv=False)
        maximum = max(maximum, float(np.abs(singular-1).max()))
    if maximum > .03:
        errors.append('Happy changes the face proportions beyond 3%')
    return {'passed': not errors, 'errors': errors, 'headDirectionChanges': turns,
            'maximumFaceAffineScaleDeviation': maximum, 'scope': 'One actual exported curve cycle in Core; no host repetition/physics/window claim.'}


def free_definitions(metadata):
    refinement = metadata['refinement']
    contract = refinement.get('freeContacts', {})
    if (contract.get('parameter') != 'ParamFreeArmActive' or contract.get('poseParameter') != 'ParamFreeArmPose'
            or contract.get('armPoseMode') != 0 or contract.get('samePaintedSleevesThroughLanding') is not True):
        raise ValueError('Missing same-painted-sleeve free-arm contract')
    definitions = []
    for side in ('l', 'r'):
        rest = contract['contacts'][side]
        definitions.append({'side': side, 'cuff': rest['cuff'], 'hand': rest['hand'],
                            'cuffUv': rest['sourceUv'], 'handUv': rest['sourceUv']})
        active = refinement['contacts'][side]
        for hand in active['hands']:
            definitions.append({'side': side, 'cuff': active['cuff'], 'hand': hand,
                                'cuffUv': active.get('cuffSourceUv', active.get('sourceUv')),
                                'handUv': active.get('handSourceUv', active.get('sourceUv'))})
    return definitions


def inspect_free(samples, targets, metadata, regions, atlas_size):
    definitions = free_definitions(metadata)
    bindings, rows, errors = {}, [], []
    for definition in definitions:
        for kind in ('cuff', 'hand'):
            name, source = definition[kind], definition[kind+'Uv']
            region = regions[name]
            if samples['topology'][name]['textureIndex'] != region['page']:
                raise ValueError('Free-contact native atlas page differs')
            uv = (np.asarray(source)*region['sourceSize']-region['translationToCanvas'])/atlas_size
            bindings[name] = bind_samples(samples['topology'][name], uv[None, :])
    for frame in samples['frames']:
        target = targets[frame['id']]
        if target['group'] != 'free':
            continue
        measured = []
        for definition in definitions:
            hand_opacity = frame['drawables'][definition['hand']]['opacity']
            cuff_opacity = frame['drawables'][definition['cuff']]['opacity']
            if hand_opacity <= .01:
                continue
            if cuff_opacity <= .01:
                errors.append(f"Visible native hand has no visible cuff: {frame['id']}/{definition['hand']}")
            points = []
            for kind in ('cuff', 'hand'):
                name = definition[kind]
                vertices = canvas_positions(frame['drawables'][name]['positions'], samples['canvasInfo'])
                ids, weights = bindings[name]
                points.append((vertices[ids]*weights[..., None]).sum(axis=1)[0])
            gap = float(np.linalg.norm(points[1]-points[0]))
            measured.append({'side': definition['side'], 'hand': definition['hand'], 'cuff': definition['cuff'],
                             'handOpacity': hand_opacity, 'cuffOpacity': cuff_opacity, 'gapCanvas': gap})
            if gap > .012:
                errors.append(f"Visible native free/fade hand separates: {frame['id']}/{definition['hand']}: {gap:.7f}")
        if {row['side'] for row in measured} != {'l', 'r'}:
            errors.append('Missing visible free/fade hand contact on one or both sides: '+frame['id'])
        if target.get('freeEndpoint'):
            if abs(frame['parameters']['ParamArmPoseMode']) > 1e-5:
                errors.append('Free motion selects a different sleeve artwork: '+frame['id'])
            original = metadata['refinement']['freeContacts']['contacts']
            original_names = {item[kind] for item in original.values() for kind in ('hand', 'cuff')}
            donor_names = {definition[kind] for definition in definitions for kind in ('hand', 'cuff')} - original_names
            if any(frame['drawables'][name]['opacity'] < .99 for name in original_names):
                errors.append('Free endpoint hides original hand or sleeve artwork: '+frame['id'])
            if any(frame['drawables'][name]['opacity'] > .01 for name in donor_names):
                errors.append('Free endpoint shows donor hand or sleeve artwork: '+frame['id'])
        rows.append({'id': frame['id'], 'contacts': measured})
    return {'passed': not errors, 'errors': errors, 'limitCanvas': .012, 'samples': rows,
            'scope': 'Actual Core geometry at exported poses and explicit fractional parameter blends, not motion-manager timing proof.'}


def sampling_plan(motions, metadata=None):
    frames = []
    def add(name, group, motion, phase, **overrides):
        frames.append({'id': name, 'group': group, 'motion': motion, 'fraction': phase,
                       'parameters': {**motion_pose(motions[motion], phase), **overrides}})
    for state in ('sleep_enter', 'sleep_exit'):
        for breath in (0, 1):
            for index in range(61):
                add(f'{state}-{breath}-{index:03}', 'sleep', state, index/60, ParamBreath=breath)
    for pose in (.82, .8201, .84, .87, .9199, .92, 1):
        for breath in (0, 1):
            add(f'sleep-pose-{pose}-{breath}', 'sleep', 'sleep_loop', 0, ParamPoseSleep=pose, ParamBreath=breath)
    for side in ('left', 'right'):
        add(side+'-start', 'reference', 'climb_'+side, 0)
        for index in range(61):
            add(f'{side}-handoff-{index:03}', 'handoff', 'climb_to_top_'+side, index/60)
    add('swing-start', 'reference', 'swing_cycle', 0)
    for index in range(61):
        add(f'happy-{index:03}', 'happy', 'happy', index/60)
    for frame in frames:
        group = frame['group']
        if group == 'sleep':
            frame['geometryNames'] = [*FEET, 'skirt', 'skirt_sleep']
        elif group == 'handoff':
            frame['geometryNames'] = ['leg_l_climb_handoff', 'leg_r_climb_handoff']
        elif group == 'happy':
            frame['geometryNames'] = ['face_base']
        else:
            frame['geometryNames'] = ([f'leg_{side}_swing' for side in ('l', 'r')]
                                      if frame['id'] == 'swing-start' else list(climb_leg_drawables(metadata).values()))
    if metadata is not None:
        definitions = free_definitions(metadata)
        names = sorted({definition[kind] for definition in definitions for kind in ('hand', 'cuff')})
        pairs = [('idle', 0, state, 0) for state in ('drag_left', 'drag_right', 'fall_float')]
        pairs += [('climb_'+side, phase, 'drag_'+side, 0) for side in ('left', 'right') for phase in (0, .35, .7)]
        pairs += [('climb_to_top_'+side, phase, 'drag_'+side, 0)
                  for side in ('left', 'right') for phase in (.18, .47)]
        pairs += [(state, .3, 'drag_left', 0) for state in ('talk', 'sleep_loop')]
        pairs += [('drag_left', .5, 'fall_float', 0), ('fall_float', .9, 'land', 0)]
        for case, (first, first_phase, second, second_phase) in enumerate(pairs):
            a, b = motion_pose(motions[first], first_phase), motion_pose(motions[second], second_phase)
            if a.keys() != b.keys():
                raise ValueError('Fractional fade requires the complete same parameter baseline')
            for weight in (0, .025, .05, .15, .4, .7, 1):
                frames.append({'id': f'free-fade-{case}-{weight}', 'group': 'free',
                    'geometryNames': names, 'from': [first, first_phase], 'to': [second, second_phase],
                    'fraction': weight, 'freeEndpoint': weight == 1,
                    'parameters': {name: a[name]+(b[name]-a[name])*weight for name in a}})
        for index in range(31):
            frames.append({'id': f'land-{index:03}', 'group': 'free', 'geometryNames': names,
                'motion': 'land', 'fraction': index/30, 'freeEndpoint': True,
                'parameters': motion_pose(motions['land'], index/30)})
    return frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-directory', type=Path, required=True)
    parser.add_argument('--source-manifest', type=Path, required=True)
    parser.add_argument('--atlas-audit', type=Path, required=True)
    parser.add_argument('--native-uv-audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    folder, output = options.model_directory.resolve(strict=True), options.output.resolve()
    if output.exists():
        parser.error('Use a new output directory; old evidence is preserved')
    output.mkdir(parents=True)
    report = {'schemaVersion': 1, 'tool': 'refined-native-motion-audit', 'passed': False, 'errors': [],
              'nativeCoreSamples': False, 'productionPlaybackVerified': False, 'visualAccepted': False,
              'scope': __doc__, 'sourceHashesAtStart': {}, 'sourceHashesAtEnd': {}}
    hashes = report['sourceHashesAtStart']
    def bind(path):
        path = Path(path).resolve(strict=True); hashes[str(path)] = digest(path); return path
    try:
        model_path = bind(folder / 'Maple.model3.json'); settings = read_json(model_path)
        metadata_path = bind(resource(folder, settings['CuteMaple']['Metadata'])); metadata = read_json(metadata_path)
        parameter_ids = declared_native_parameters(metadata['refinement'])
        drawable_ids = declared_native_drawables(metadata['refinement'])
        if not parameter_ids or not drawable_ids:
            raise ValueError('Explicit refined model identity is required')
        moc = bind(resource(folder, settings['FileReferences']['Moc']))
        atlas_path = bind(options.atlas_audit); atlas = read_json(atlas_path)
        uv_path = bind(options.native_uv_audit)
        uv = verify_native_uv_evidence(uv_path, moc, atlas_path, hashes[str(moc)],
                                      expected_mesh_count=len(drawable_ids), expected_drawable_ids=drawable_ids)
        hashes.update(uv['inputHashesAtStart']); report['nativeUvEvidence'] = uv
        report['atlasCoordinateMapping'] = []
        for texture, page in zip(settings['FileReferences']['Textures'], atlas['atlases'], strict=True):
            actual, expected = bind(resource(folder, texture)), bind(page['path'])
            result = audit_atlas_files(actual, expected, hashes[str(actual)], page['sha256'])
            report['atlasCoordinateMapping'].append(result)
            if not result['coordinateMappingVerified']:
                raise ValueError('Runtime atlas pixels do not match verified source coordinates')
        manifest_path = bind(options.source_manifest); manifest = read_json(manifest_path)
        report['inputReferences'] = {'model': str(model_path), 'metadata': str(metadata_path), 'moc': str(moc),
            'sourceManifest': str(manifest_path), 'atlasAudit': str(atlas_path), 'nativeUvAudit': str(uv_path)}
        layers = {layer['id']: layer for layer in manifest['layers']}
        motions = {}
        for name in settings['FileReferences']['Motions']:
            path = bind(resource(folder, settings['FileReferences']['Motions'][name][0]['File']))
            motions[name] = read_json(path)
        frames = sampling_plan(motions, metadata); targets = {f['id']: f for f in frames}
        selected = sorted({*FEET, 'skirt', 'skirt_sleep', 'face_base'} |
                          {name for name in drawable_ids if re.search(r'(^|_)leg_[lr](?:_|$)', name)} |
                          {name for frame in frames for name in frame['geometryNames']})
        bind(CORE); bind(Path(__file__))
        request = {'moc': str(moc), 'core': str(CORE), 'parameterIds': sorted(parameter_ids),
                   'drawableIds': sorted(drawable_ids), 'selected': selected, 'frames': frames,
                   'output': str(output / 'native-samples.json')}
        request_path = output / 'sampling-request.json'
        request_path.write_text(json.dumps(request), encoding='utf-8')
        child = subprocess.run(['node', '-e', NODE_PROGRAM, str(request_path)], cwd=ROOT,
                               capture_output=True, text=True, encoding='utf-8', timeout=90,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        (output / 'core-process.json').write_text(json.dumps({'exitCode': child.returncode,
            'stdout': child.stdout, 'stderr': child.stderr}, indent=2), encoding='utf-8')
        if child.returncode:
            raise ValueError('Native Core sampling failed; see core-process.json')
        samples = read_json(output / 'native-samples.json')
        report['nativeSamplePath'] = str(output / 'native-samples.json')
        report['nativeSampleSha256'] = digest(output / 'native-samples.json')
        report['samplingRequestSha256'] = digest(request_path)
        if samples['sourceHashesAtStart'] != samples['sourceHashesAtEnd']:
            raise ValueError('Native sampling inputs changed')
        report['nativeCoreSamples'] = True
        regions = {name: r for r in atlas['regions'] for name in r.get('drawables', [r['name']])}
        bindings = {}
        for name in (*FEET, 'skirt', 'skirt_sleep'):
            source = bind(resource(manifest_path.parent, layers[name]['file']))
            region = regions[name]
            if samples['topology'][name]['textureIndex'] != region['page']:
                raise ValueError('Native texture page differs for '+name)
            # Validate the actual layer alpha against the audited atlas crop,
            # instead of assuming an identically named external PNG is its art.
            with Image.open(source) as source_image, Image.open(atlas['atlases'][region['page']]['path']) as page_image:
                crop = source_image.convert('RGBA').crop(region['crop'])
                x, y, width, height = region['destination']
                packed = page_image.convert('RGBA').crop((x, y, x+width, y+height))
                if not np.array_equal(np.asarray(crop), np.asarray(packed)):
                    raise ValueError('Source PNG differs from the verified packed crop: '+name)
            points = painted_points(source, region, atlas['atlasSize'])
            bindings[name] = bind_samples(samples['topology'][name], points)
        samples['sourceVertexPairing'] = {}
        for side in ('l', 'r'):
            a = f'leg_{side}_climb_handoff'
            # The two receiving poses must carry the same actual artwork,
            # not merely similarly shaped UVs and coincident joint points.
            climb_source = bind(resource(manifest_path.parent, layers[f'leg_{side}_climb']['file']))
            swing_source = bind(resource(manifest_path.parent, layers[f'leg_{side}_swing']['file']))
            if hashes[str(climb_source)] != hashes[str(swing_source)]:
                raise ValueError('Handoff leg artwork differs between climb and swing')
            for b in (climb_leg_drawables(metadata)[side], f'leg_{side}_swing'):
                def source_uv(name):
                    r = regions[name]
                    return np.asarray(samples['topology'][name]['textureUvs']).reshape(-1, 2)*atlas['atlasSize']+r['translationToCanvas']
                auv, buv = source_uv(a), source_uv(b)
                distance = np.linalg.norm(auv[:, None]-buv[None, :], axis=2)
                mapping = distance.argmin(axis=1)
                if len(set(mapping)) != len(auv) or distance[np.arange(len(auv)), mapping].max() > .001:
                    raise ValueError('Handoff leg source UV correspondence differs')
                ta = np.asarray(samples['topology'][a]['triangles']).reshape(-1, 3)
                tb = np.asarray(samples['topology'][b]['triangles']).reshape(-1, 3)
                if sorted(map(tuple, np.sort(mapping[ta], axis=1))) != sorted(map(tuple, np.sort(tb, axis=1))):
                    raise ValueError('Handoff material triangle connectivity differs')
                ra, rb = regions[a], regions[b]
                if ra['sourceSize'] != rb['sourceSize'] or ra['crop'] != rb['crop']:
                    raise ValueError('Handoff source image crop differs')
                with Image.open(atlas['atlases'][ra['page']]['path']) as pa, Image.open(atlas['atlases'][rb['page']]['path']) as pb:
                    def crop_region(image, region):
                        x, y, width, height = region['destination']
                        return np.asarray(image.convert('RGBA').crop((x, y, x+width, y+height)))
                    if not np.array_equal(crop_region(pa, ra), crop_region(pb, rb)):
                        raise ValueError('Handoff packed painted leg pixels differ')
                samples['sourceVertexPairing'][a+'|'+b] = mapping
        report['checks'] = {'sleepGround': inspect_sleep(samples, targets, metadata, bindings),
                            'climbHandoff': inspect_handoff(samples, targets, metadata),
                            'happySingleCycle': inspect_happy(samples, targets),
                            'freeSleeveContacts': inspect_free(samples, targets, metadata, regions, np.asarray(atlas['atlasSize']))}
        report['sampleCount'] = len(samples['frames']); report['topologyCount'] = len(samples['topology'])
        for check in report['checks'].values():
            report['errors'].extend(check['errors'])
    except Exception as error:
        report['errors'].append(f'{type(error).__name__}: {error}')
    try:
        report['sourceHashesAtEnd'] = recheck_input_hashes(hashes)
    except Exception as error:
        report['errors'].append(str(error))
    report['sourcesUnchanged'] = bool(hashes) and hashes == report['sourceHashesAtEnd']
    report['passed'] = report['nativeCoreSamples'] and report['sourcesUnchanged'] and not report['errors']
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'report': str(output / 'report.json'), 'errors': report['errors'][:12]}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
