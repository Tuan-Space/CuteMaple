"""Reopen and recompute refined Core evidence; never start Core, a GUI or a helper.

This is a numerical release gate, not visual or desktop playback acceptance.
Legacy releases do not require the supplemental report.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from tools.native_model_contract import (REFINED_MOTION_REVISION, declared_native_parameters,
                                         declared_native_drawables, free_motion_contacts)
from tools.qa_model_inputs import digest, recheck_input_hashes, verify_native_uv_evidence, audit_atlas_files

CHECKS = {'sleepGround', 'climbHandoff', 'happySingleCycle', 'freeSleeveContacts'}
SYNTHETIC_FLAGS = ('synthetic', 'syntheticTestFixture', 'mock', 'fixture', 'unitTest', 'diagnosticOnly')


def _require(condition, message):
    if not condition:
        raise ValueError('Refined motion evidence: '+message)


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _inside(folder, name):
    _require(isinstance(name, str) and bool(name), 'invalid resource reference')
    result = (folder/name).resolve(strict=True)
    _require(result.is_relative_to(folder.resolve()) and result.is_file(), 'resource escapes its directory')
    return result


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _same(actual, expected):
    """Compare recomputed data, permitting only floating-point roundoff, not new thresholds."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(_same(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected))
    if _finite(expected):
        return _finite(actual) and abs(actual-expected) <= 1e-10
    return actual == expected


def _recompute_checks(samples, targets, metadata, manifest, manifest_path, atlas, bound):
    """Use original paint and real native positions, not the report's measured booleans."""
    import numpy as np
    from PIL import Image
    from tools import verify_refined_motion as verifier

    regions = {name: r for r in atlas['regions'] for name in r.get('drawables', [r['name']])}
    layers = {layer['id']: layer for layer in manifest['layers']}
    _require(len(layers) == len(manifest['layers']), 'duplicate source layer IDs')

    def layer_image(name):
        path = _inside(manifest_path.parent, layers[name]['file'])
        bound(path)
        return path

    def packed_pixels(region):
        page = Path(atlas['atlases'][region['page']]['path']).resolve(strict=True)
        bound(page)
        x, y, width, height = region['destination']
        with Image.open(page) as image:
            return np.asarray(image.convert('RGBA').crop((x, y, x+width, y+height)))

    bindings = {}
    for name in (*verifier.FEET, 'skirt', 'skirt_sleep'):
        source, region = layer_image(name), regions[name]
        _require(samples['topology'][name]['textureIndex'] == region['page'], 'painted source atlas page differs')
        with Image.open(source) as image:
            crop = np.asarray(image.convert('RGBA').crop(region['crop']))
        _require(np.array_equal(crop, packed_pixels(region)), 'source paint differs from its verified packed crop: '+name)
        points = verifier.painted_points(source, region, atlas['atlasSize'])
        bindings[name] = verifier.bind_samples(samples['topology'][name], points)

    samples['sourceVertexPairing'] = {}
    for side in ('l', 'r'):
        first = f'leg_{side}_climb_handoff'
        _require(digest(layer_image(f'leg_{side}_climb')) == digest(layer_image(f'leg_{side}_swing')),
                 'handoff source artwork differs')
        for last in (verifier.climb_leg_drawables(metadata)[side], f'leg_{side}_swing'):
            ra, rb = regions[first], regions[last]
            auv = np.asarray(samples['topology'][first]['textureUvs']).reshape(-1, 2)*atlas['atlasSize']+ra['translationToCanvas']
            buv = np.asarray(samples['topology'][last]['textureUvs']).reshape(-1, 2)*atlas['atlasSize']+rb['translationToCanvas']
            distances = np.linalg.norm(auv[:, None]-buv[None, :], axis=2)
            mapping = distances.argmin(axis=1)
            _require(len(set(mapping)) == len(auv) and distances[np.arange(len(auv)), mapping].max() <= .001,
                     'handoff source UV correspondence differs')
            ta = np.asarray(samples['topology'][first]['triangles']).reshape(-1, 3)
            tb = np.asarray(samples['topology'][last]['triangles']).reshape(-1, 3)
            _require(sorted(map(tuple, np.sort(mapping[ta], axis=1))) == sorted(map(tuple, np.sort(tb, axis=1))),
                     'handoff source triangles differ')
            _require(ra['sourceSize'] == rb['sourceSize'] and ra['crop'] == rb['crop']
                     and np.array_equal(packed_pixels(ra), packed_pixels(rb)), 'handoff packed material differs')
            samples['sourceVertexPairing'][first+'|'+last] = mapping
    return {'sleepGround': verifier.inspect_sleep(samples, targets, metadata, bindings),
            'climbHandoff': verifier.inspect_handoff(samples, targets, metadata),
            'happySingleCycle': verifier.inspect_happy(samples, targets),
            'freeSleeveContacts': verifier.inspect_free(samples, targets, metadata, regions, np.asarray(atlas['atlasSize']))}


def verify_refined_motion_evidence(bundle: Path, report_path: Path | None, *, native_uv=None) -> dict | None:
    """Return a reproducible receipt, or fail; all original inputs must remain available."""
    bundle = Path(bundle).resolve(strict=True)
    folder = bundle/'assets/live2d/Maple'
    model_path = folder/'Maple.model3.json'
    settings = _json(model_path)
    metadata_path = _inside(folder, settings['CuteMaple']['Metadata'])
    metadata = _json(metadata_path)
    if metadata.get('refinement', {}).get('motionRevision') != REFINED_MOTION_REVISION:
        _require(report_path is None, 'supplement was supplied for a different model revision')
        return None
    _require(report_path is not None, 'this motion revision requires --refined-motion-report')
    report_path = Path(report_path).resolve(strict=True)
    data = report_path.read_bytes()
    report_hash = hashlib.sha256(data).hexdigest()
    report = json.loads(data)
    _require(not any(report.get(key) for key in SYNTHETIC_FLAGS), 'synthetic/diagnostic evidence is not release evidence')
    _require(report.get('schemaVersion') == 1 and report.get('tool') == 'refined-native-motion-audit'
             and report.get('passed') is True and report.get('errors') == []
             and report.get('nativeCoreSamples') is True and report.get('sourcesUnchanged') is True,
             'missing successful actual Core report')
    _require(report.get('visualAccepted') is False and report.get('productionPlaybackVerified') is False,
             'numeric evidence must not claim visual or desktop playback acceptance')
    hashes = report.get('sourceHashesAtStart')
    _require(isinstance(hashes, dict) and hashes and hashes == report.get('sourceHashesAtEnd'), 'input hashes changed or are missing')
    recheck_input_hashes(hashes)
    final_hashes = dict(hashes)

    def bound(path):
        path = Path(path).resolve(strict=True)
        _require(hashes.get(str(path)) == digest(path), 'unbound or changed input: '+str(path))
        return path

    paths = report.get('inputReferences', {})
    _require(set(paths) == {'model', 'metadata', 'moc', 'sourceManifest', 'atlasAudit', 'nativeUvAudit'}, 'missing explicit input references')
    paths = {key: bound(path) for key, path in paths.items()}
    original = paths['model'].parent
    _require(digest(paths['model']) == digest(model_path), 'model settings differ from the package')
    _require(paths['metadata'] == _inside(original, settings['CuteMaple']['Metadata'])
             and digest(paths['metadata']) == digest(metadata_path), 'metadata differs from the package')
    moc = _inside(folder, settings['FileReferences']['Moc'])
    _require(paths['moc'] == _inside(original, settings['FileReferences']['Moc']) and digest(paths['moc']) == digest(moc),
             'MOC3 differs from the package')
    parameter_ids = declared_native_parameters(metadata['refinement'])
    drawable_ids = declared_native_drawables(metadata['refinement'])
    _require(parameter_ids and drawable_ids, 'missing complete authored identity')
    free_motion_contacts(metadata['refinement'])
    uv = verify_native_uv_evidence(paths['nativeUvAudit'], moc, paths['atlasAudit'], digest(moc),
                                  expected_mesh_count=len(drawable_ids), expected_drawable_ids=drawable_ids)
    _require(uv == report.get('nativeUvEvidence') and (native_uv is None or uv == native_uv),
             'supplement and main native QA use different UV evidence')
    _require(all(hashes.get(name) == value for name, value in uv['inputHashesAtStart'].items()), 'incomplete authored/atlas/UV inputs')
    atlas = _json(paths['atlasAudit'])
    pages = report.get('atlasCoordinateMapping', [])
    textures = settings['FileReferences']['Textures']
    _require(len(pages) == len(textures) == len(atlas['atlases']), 'incomplete atlas pages')
    for texture, page, claimed in zip(textures, atlas['atlases'], pages):
        actual = bound(_inside(original, texture)); packed = bound(page['path'])
        _require(digest(actual) == digest(_inside(folder, texture)), 'package texture differs from sampled texture')
        measured = audit_atlas_files(actual, packed, hashes[str(actual)], page['sha256'])
        _require(measured == claimed and measured['coordinateMappingVerified'] is True, 'atlas coordinate mapping differs')
    motions = {}
    packaged = {str(model_path.relative_to(bundle)): digest(model_path), str(metadata_path.relative_to(bundle)): digest(metadata_path),
                str(moc.relative_to(bundle)): digest(moc)}
    for name, entries in settings['FileReferences']['Motions'].items():
        _require(len(entries) == 1, 'ambiguous motion group: '+name)
        path = bound(_inside(original, entries[0]['File']))
        runtime_path = _inside(folder, entries[0]['File'])
        _require(digest(path) == digest(runtime_path), 'package motion differs: '+name)
        motions[name] = _json(path)
        packaged[str(runtime_path.relative_to(bundle))] = digest(runtime_path)
    for texture in textures:
        p = _inside(folder, texture); packaged[str(p.relative_to(bundle))] = digest(p)

    from tools import verify_refined_motion as verifier
    bound(Path(verifier.__file__)); bound(verifier.CORE)
    request_path = report_path.parent/'sampling-request.json'
    sample_path = Path(report['nativeSamplePath']).resolve(strict=True)
    _require(sample_path == report_path.parent/'native-samples.json', 'raw samples are not the original sibling output')
    for path, expected in ((request_path, report.get('samplingRequestSha256')), (sample_path, report.get('nativeSampleSha256'))):
        _require(digest(path) == expected, 'raw request/sample hash differs')
        final_hashes[str(path)] = expected
    request, samples = _json(request_path), _json(sample_path)
    frames = verifier.sampling_plan(motions, metadata)
    selected = sorted({*verifier.FEET, 'skirt', 'skirt_sleep', 'face_base'} |
                      {name for name in drawable_ids if re.search(r'(^|_)leg_[lr](?:_|$)', name)} |
                      {name for frame in frames for name in frame['geometryNames']})
    expected_request = {'moc': str(paths['moc']), 'core': str(verifier.CORE), 'parameterIds': sorted(parameter_ids),
                        'drawableIds': sorted(drawable_ids), 'selected': selected, 'frames': frames, 'output': str(sample_path)}
    _require(request == expected_request, 'sampling request omits or changes actual authored poses/fades')
    _require(samples.get('nativeCoreSamples') is True and not any(samples.get(k) for k in SYNTHETIC_FLAGS), 'raw samples are not actual Core evidence')
    core_hashes = {request['moc']: hashes[request['moc']], request['core']: hashes[request['core']]}
    _require(samples.get('sourceHashesAtStart') == core_hashes == samples.get('sourceHashesAtEnd'), 'raw Core source identity differs')
    geometries = [_json(name) for name in uv['inputHashesAtStart'] if Path(name).suffix.lower() == '.json']
    geometries = [g for g in geometries if g.get('tool') == 'official-cubism-native-geometry-audit']
    _require(len(geometries) == 1, 'missing bound native topology audit')
    geometry = geometries[0]
    _require(samples.get('canvasInfo') == geometry['canvasInfo'], 'native canvas differs from the verified MOC')
    by_id = {d['id']: d for d in geometry['drawables']}
    _require(set(samples.get('topology', {})) == set(selected), 'raw native topology set is incomplete')
    for name, topology in samples['topology'].items():
        actual = by_id[name]
        flipped_uv = [1-v if i % 2 else v for i, v in enumerate(actual['textureUvs'])]
        _require(topology == {'textureIndex': actual['textureIndex'], 'textureUvs': flipped_uv, 'triangles': actual['indices']},
                 'sampled native topology differs from verified MOC: '+name)
    _require(len(samples.get('frames', [])) == len(frames) == report.get('sampleCount')
             and report.get('topologyCount') == len(selected), 'raw sample count differs')
    for plan, frame in zip(frames, samples['frames']):
        _require(frame.get('id') == plan['id'] and set(frame.get('parameters', {})) == parameter_ids,
                 'raw frame identity/parameter inventory differs')
        _require(all(_finite(v) for v in frame['parameters'].values()) and all(
            abs(frame['parameters'][name]-value) <= 1e-5 for name, value in plan['parameters'].items()), 'actual Core parameter target differs')
        _require(set(frame.get('drawables', {})) == set(selected), 'raw frame mesh set differs')
        for name, item in frame['drawables'].items():
            _require(_finite(item.get('opacity')) and -.000001 <= item['opacity'] <= 1.000001, 'invalid native opacity')
            if name in plan['geometryNames']:
                values = item.get('positions', [])
                _require(len(values) == len(samples['topology'][name]['textureUvs']) and all(_finite(v) for v in values),
                         'missing/nonfinite native positions')
    recomputed = _recompute_checks(samples, {f['id']: f for f in frames}, metadata,
                                   _json(paths['sourceManifest']), paths['sourceManifest'], atlas, bound)
    _require(set(report.get('checks', {})) == CHECKS == set(recomputed), 'all four numeric checks are required')
    for name, measured in recomputed.items():
        claimed = report['checks'][name]
        _require(isinstance(claimed, dict) and claimed.get('passed') is True and claimed.get('errors') == []
                 and measured.get('passed') is True and measured.get('errors') == [] and _same(claimed, measured),
                 'recomputed check failed or differs: '+name)
    final_hashes[str(report_path)] = report_hash
    recheck_input_hashes(final_hashes)
    _require(all(digest(bundle/name) == value for name, value in packaged.items()), 'packaged inputs changed during recheck')
    return {'verified': True, 'reportPath': str(report_path), 'reportSha256': report_hash,
            'motionRevision': REFINED_MOTION_REVISION, 'checksRecomputed': sorted(CHECKS),
            'nativeCoreSamples': True, 'visualAccepted': False, 'productionPlaybackVerified': False,
            'sampleCount': len(frames), 'nativeUvAuditSha256': uv['sha256'],
            'matchedPackagedInputs': packaged, 'inputSha256': final_hashes}
