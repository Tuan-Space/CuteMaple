"""Pure native material-point measurements shared by capture and release validation."""
import math
from tools.native_model_contract import brush_contract, free_motion_contacts, climb_materials, climb_drape_exchange, physics_output_parameters


def _scale(snapshot):
    info = snapshot['canvasInfo']
    values = [info[key] for key in ('pixelsPerUnit', 'width', 'height')]
    if not all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in values):
        raise ValueError('Invalid native canvas scale')
    return values[0]/max(values[1:])


def _visible(snapshot, name, shown=True):
    item = {item['id']: item for item in snapshot['drawables']}[name]
    opacity = item.get('opacity')
    if (type(opacity) not in (int, float) or not math.isfinite(opacity)
            or (opacity < .95 if shown else opacity > .05)):
        raise ValueError(f'Unexpected material visibility: {name}')


def material_point(report, snapshot, anchor):
    if 'sourceUv' in anchor:
        return source_point(report, snapshot, anchor['drawable'], anchor['sourceUv'])
    # Installed atlasUv uses original Core V; diagnostic textureUvs are top-down.
    name, uv = anchor['drawable'], anchor['atlasUv']
    regions = {name: region for region in report['atlasAudit']['regions']
               for name in region.get('drawables', [region.get('name')])}
    item = {item['id']: item for item in snapshot['drawables']}[name]
    if report.get('atlasCoordinateMappingVerified') is not True or item['textureIndex'] != regions[name]['page']:
        raise ValueError('Unverified installed material point')
    return native_uv_point(item, [uv[0], 1-uv[1]])


def _contact(report, snapshot, side, hand, definition):
    _visible(snapshot, hand)
    _visible(snapshot, definition['cuff'])
    return _contact_geometry(report, snapshot, side, hand, definition)


def _contact_geometry(report, snapshot, side, hand, definition):
    # Callers establish visibility first; transition crossfades can show two
    # corresponding material pairs below full opacity.
    cuff = source_point(report, snapshot, definition['cuff'], definition.get('cuffSourceUv', definition['sourceUv']))
    wrist = source_point(report, snapshot, hand, definition.get('handSourceUv', definition['sourceUv']))
    gap = math.dist(cuff, wrist)*_scale(snapshot)
    if not math.isfinite(gap) or gap > .012:
        raise ValueError(f'Native hand/cuff contact separates: {side}/{hand} ({gap})')
    return {'cuff': cuff, 'wrist': wrist, 'distance': gap}


def _forearm_order(actual, side, definition):
    order = actual[definition['drawable']]['drawOrder']
    if (type(order) not in (int, float) or not math.isfinite(order) or abs(order-definition['drawOrder']) > .01
            or not max(actual['ribbon_'+s]['drawOrder'] for s in ('l', 'r')) < order < actual[f'clean_hand_{side}_palm']['drawOrder']):
        raise ValueError(f'Working brush forearm remains behind ribbons or covers the fist: {side}')


def brush_contacts(report, definition):
    """Select the declared painted cuffs, not the hidden active donor geometry."""
    return (free_motion_contacts(report['v5Contract']) if definition['context'] == 0
            else report['contactContract'])


def brush_forearm(report, snapshot, side, working, context=None):
    """Check the painted forearm actually shown, retaining the original support sleeve."""
    if context == 0:
        definition = free_motion_contacts(report['v5Contract'])[side]
        _visible(snapshot, definition['cuff'])
        for part in (f'sleeve_{side}_upper', f'sleeve_{side}_lower', f'clean_sleeve_{side}_lower'):
            _visible(snapshot, part, False)
        for hand in set(report['contactContract'][side]['hands'])-set(definition['hands']):
            _visible(snapshot, hand, False)
        mode = snapshot['parameters'].get('ParamArmPoseMode')
        if type(mode) not in (int, float) or not math.isfinite(mode) or abs(mode) > .03:
            raise ValueError('Ground cleanup must retain original hanging sleeves')
        actual = {item['id']: item for item in snapshot['drawables']}
        return {'drawable': definition['cuff'], 'originalOpacity': actual[definition['cuff']]['opacity'],
                'foregroundOpacity': actual[f'clean_sleeve_{side}_lower']['opacity'],
                'originalDrawOrder': actual[definition['cuff']]['drawOrder'], 'forearmKind': 'whole'}
    definition = report['v5Contract']['cleaningTool']['forearms'][side]
    foreground, original = definition['drawable'], definition['replaces']
    if original != report['contactContract'][side]['cuff']:
        raise ValueError(f'Brush forearm replaces a different native cuff: {side}')
    _visible(snapshot, foreground, working)
    _visible(snapshot, original, not working)
    actual = {item['id']: item for item in snapshot['drawables']}
    value = snapshot['parameters'][definition['visibilityParameter']]
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value-int(working)) > .03:
        raise ValueError(f'Brush forearm visibility does not use its authored parameter: {side}')
    if working:
        _forearm_order(actual, side, definition)
    return {'drawable': foreground if working else original, 'foregroundOpacity': actual[foreground]['opacity'],
            'originalOpacity': actual[original]['opacity'], 'foregroundDrawOrder': actual[foreground]['drawOrder']}


def measure_brush_sweep(report):
    definitions = brush_contract({'refinement': report['v5Contract'], 'states': report['stateContracts']})
    if definitions is None:
        raise ValueError('Brush measurements require motionPolishVersion=1')
    rows = {}
    for state, definition in definitions.items():
        side, anchors = definition['workSide'], definition['anchors']
        other = 'r' if side == 'l' else 'l'
        contacts = brush_contacts(report, definition)
        palm, fingers, brush = definition['palmDrawable'], definition['fingersDrawable'], anchors['brushGrip']['drawable']
        samples, tips, supports = [], [], []
        for index, phase in [(0, 'before'), (0, 'after'), (1, 'before'), (1, 'after')]:
            snapshot = report['probes'][f'v51_brush_{state}_{index}'][phase]
            factor = _scale(snapshot)
            for name in (brush, palm, fingers):
                _visible(snapshot, name)
            for name in (f'clean_brush_{other}', 'clean_fan_l', 'clean_fan_r'):
                _visible(snapshot, name, False)
            if definition['context'] == 0:
                for name in (f'clean_brush_{side}', f'clean_hand_{side}_palm', f'clean_hand_{side}_fingers',
                             definition['relaxedHandDrawable']):
                    _visible(snapshot, name, False)
            else:
                for name in ('clean_ground_brush_r', 'clean_ground_hand_r_palm', 'clean_ground_hand_r_fingers',
                             'clean_ground_hand_r_relaxed'):
                    _visible(snapshot, name, False)
            for name in contacts[side]['hands']:
                _visible(snapshot, name, False)
            actual = {item['id']: item for item in snapshot['drawables']}
            order = [actual[name]['drawOrder'] for name in (palm, brush, fingers)]
            if not order[0] < order[1] < order[2]:
                raise ValueError(f'Brush handle does not pass behind gripping fingers: {state}')
            points = {key: [v*factor for v in material_point(report, snapshot, anchor)] for key, anchor in anchors.items()}
            gap = math.dist(points['brushGrip'], points['freeHand'])
            if not math.isfinite(gap) or gap > .012:
                raise ValueError(f'Painted brush handle separates from fingers: {state}')
            forearm = brush_forearm(report, snapshot, side, True, definition['context'])
            other_forearm = brush_forearm(report, snapshot, other, False, definition['context'])
            working_definition = {**contacts[side], 'cuff': forearm['drawable']}
            working = _contact(report, snapshot, side, palm, working_definition)
            supporting = contacts[other]
            shown = [item['id'] for item in snapshot['drawables']
                     if item['id'] in supporting['hands'] and item.get('opacity', 0) >= .95]
            if len(shown) != 1:
                raise ValueError(f'Ambiguous non-working hand: {state}')
            contact = _contact(report, snapshot, other, shown[0], supporting)
            support = [v*factor for v in contact['wrist']]
            samples.append({'points': points, 'gripGap': gap, 'workingCuff': working, 'nonWorkingCuff': contact,
                            'nonWorkingHand': shown[0], 'nonWorkingPoint': support,
                            'workingForearm': forearm, 'nonWorkingForearm': other_forearm})
            tips.append(points['brushTip']); supports.append(support)
        sweep = max(math.dist(a, b) for a in tips for b in tips)
        slip = max(math.dist(a, b) for a in supports for b in supports)
        lengths = [math.dist(row['points']['brushTip'], row['points']['brushGrip']) for row in samples]
        if sweep < .01 or slip > .008 or min(lengths) <= 0 or max(lengths)/min(lengths) > 1.03:
            raise ValueError(f'Brush sweep, rigid shape or non-working hand drift failed: {state}')
        rows[state] = {'samples': samples, 'nativeBristleDistance': sweep, 'nonWorkingHandMovement': slip,
                       'supportSide': definition['supportSide'], 'gripLimit': .012, 'supportLimit': .008,
                       'rigidLengthRatio': max(lengths)/min(lengths)}
    return rows


def _ground_brush_transition(report, sample, work, key):
    """Original ground cloth stays opaque while the matching hand/tool exchanges."""
    actual = {item['id']: item for item in sample['drawables']}
    definitions = brush_contacts(report, work)
    for name in ('clean_brush_l', 'clean_brush_r', 'clean_hand_l_palm', 'clean_hand_l_fingers',
                 'clean_hand_r_palm', 'clean_hand_r_fingers'):
        _visible(sample, name, False)
    side_rows = {}
    for side, definition in definitions.items():
        forearm = brush_forearm(report, sample, side, side == work['workSide'], 0)
        value = sample['parameters']['ParamCleanBrush'+side.upper()]
        if (type(value) not in (int, float) or not math.isfinite(value) or not -.03 <= value <= 1.03
                or (side != work['workSide'] and abs(value) > .03)):
            raise ValueError(f'Unexpected ground brush visibility parameter: {key}/{side}')
        value = min(1., max(0., value))
        hand = definition['hands'][0]
        expected = {definition['cuff']: 1., hand: 1-value}
        if side == work['workSide']:
            shape = sample['parameters']['ParamHand'+side.upper()+'Shape']
            if type(shape) not in (int, float) or not math.isfinite(shape) or not -1.03 <= shape <= 1.03:
                raise ValueError(f'Invalid ground relaxed hand exchange: {key}/{side}')
            shape = min(1., abs(shape))
            expected[hand] = (1-shape)*(1-value)
            expected[work['relaxedHandDrawable']] = shape*(1-value)
            expected.update({name: value for name in (work['palmDrawable'], work['fingersDrawable'],
                                                       work['anchors']['brushGrip']['drawable'])})
        for name, target in expected.items():
            opacity = actual[name]['opacity']
            if type(opacity) not in (int, float) or not math.isfinite(opacity) or abs(opacity-target) > .05:
                raise ValueError(f'Ground hand/tool is not complementary on its original sleeve: {key}/{name}')
        contacts = {}
        if actual[hand]['opacity'] > .05:
            contacts[hand] = _contact_geometry(report, sample, side, hand, definition)
        if side == work['workSide'] and actual[work['relaxedHandDrawable']]['opacity'] > .05:
            relaxed = work['relaxedHandDrawable']
            contacts[relaxed] = _contact_geometry(report, sample, side, relaxed, definition)
            if actual[relaxed]['drawOrder'] <= actual[definition['cuff']]['drawOrder']:
                raise ValueError(f'Ground relaxed hand is hidden behind its sleeve: {key}')
        if side == work['workSide'] and value > .05:
            palm = work['palmDrawable']
            contacts[palm] = _contact_geometry(report, sample, side, palm, definition)
            order = [actual[name]['drawOrder'] for name in (definition['cuff'], palm,
                     work['anchors']['brushGrip']['drawable'], work['fingersDrawable'])]
            if not all(a < b for a, b in zip(order, order[1:])):
                raise ValueError(f'Ground brush grip covers the sleeve or fingers: {key}')
            grip, finger = [material_point(report, sample, work['anchors'][name])
                            for name in ('brushGrip', 'freeHand')]
            if math.dist(grip, finger)*_scale(sample) > .012:
                raise ValueError(f'Ground transition brush separates from fingers: {key}')
        side_rows[side] = {'visibilityParameter': value, 'forearm': forearm,
                           'opacities': {name: actual[name]['opacity'] for name in expected}, 'contacts': contacts}
    return side_rows


def measure_brush_transitions(report):
    """Measure existing native take/stow start, interior and end captures."""
    definitions = brush_contract({'refinement': report['v5Contract'], 'states': report['stateContracts']})
    if definitions is None:
        raise ValueError('Brush transitions require motionPolishVersion=1')
    rows = {}
    for public, work in definitions.items():
        for suffix in ('enter', 'exit'):
            name = public+'_'+suffix
            binding = report['motionBindings'][name]
            for phase, key in [('start', name+'-start'), ('interior', name), ('end', name+'-end')]:
                sample = report['captures'][key]
                play = sample['playback']
                if (sample['generation'] != binding['generation'] or play['name'] != name
                        or play['token'] != binding['token'] or play.get('evaluated') is not True
                        or type(play.get('nativeUpdateCount')) is not int or play['nativeUpdateCount'] <= 0):
                    raise ValueError(f'Stale native brush transition: {key}')
                if work['context'] == 0:
                    side_rows = _ground_brush_transition(report, sample, work, key)
                    rows.setdefault(name, {})[phase] = {'captureKey': key, 'binding': binding, 'sides': side_rows}
                    continue
                for hidden in ('clean_ground_brush_r', 'clean_ground_hand_r_palm', 'clean_ground_hand_r_fingers',
                               'clean_ground_hand_r_relaxed'):
                    _visible(sample, hidden, False)
                actual = {item['id']: item for item in sample['drawables']}
                mode = sample['parameters']['ParamArmPoseMode']
                if type(mode) not in (int, float) or not math.isfinite(mode) or not -.03 <= mode <= 1.03:
                    raise ValueError(f'Invalid brush transition active sleeve gate: {key}')
                # Authored ArmPoseMode has 0/.025/.05/1 keys with 0/.5/1/1
                # visibility. Preserve that gate before multiplying CleanBrush.
                active = min(1., max(0., mode/.05))
                side_rows = {}
                for side in ('l', 'r'):
                    forearm = report['v5Contract']['cleaningTool']['forearms'][side]
                    original, foreground = forearm['replaces'], forearm['drawable']
                    definition = report['contactContract'][side]
                    if original != definition['cuff']:
                        raise ValueError(f'Brush transition replaces a different cuff: {key}/{side}')
                    value = sample['parameters'][forearm['visibilityParameter']]
                    if (type(value) not in (int, float) or not math.isfinite(value) or not -.03 <= value <= 1.03
                            or (side != work['workSide'] and abs(value) > .03)):
                        raise ValueError(f'Unexpected brush transition visibility parameter: {key}/{side}')
                    value = min(1., max(0., value))
                    expected = {f'sleeve_{side}_upper': active, original: active*(1-value), foreground: active*value}
                    for part, target in expected.items():
                        opacity = actual[part]['opacity']
                        if type(opacity) not in (int, float) or not math.isfinite(opacity) or abs(opacity-target) > .05:
                            raise ValueError(f'Brush transition forearms are not complementary: {key}/{part}')
                    contacts = {}
                    for cuff, hands in [(original, definition['hands']), (foreground, [f'clean_hand_{side}_palm'])]:
                        shown = [hand for hand in hands if actual[hand]['opacity'] > .05]
                        visible = actual[cuff]['opacity'] > .05
                        if bool(shown) != visible:
                            raise ValueError(f'Visible transition hand lacks its matching sleeve: {key}/{cuff}')
                        if visible and cuff == foreground:
                            _forearm_order(actual, side, forearm)
                        for hand in shown:
                            contacts[hand] = _contact_geometry(report, sample, side, hand, {**definition, 'cuff': cuff})
                    side_rows[side] = {'visibilityParameter': value,
                        'opacities': {part: actual[part]['opacity'] for part in expected}, 'contacts': contacts}
                rows.setdefault(name, {})[phase] = {'captureKey': key, 'binding': binding, 'sides': side_rows}
    return rows


def measure_free_brace(report):
    definitions = free_motion_contacts(report['v5Contract'])
    if not definitions:
        raise ValueError('Brace requires original painted sleeve contacts')
    rows = {}
    for index in range(4):
        name = f'v51_free_recovery_{index}'
        rows[name] = {phase: {side: _contact(report, report['probes'][name][phase], side,
                                            definition['hands'][0], definition)
                             for side, definition in definitions.items()} for phase in ('before', 'after')}
    for state, target in [('fall_float', 1), ('land-start', 1)]:
        parameters = report['captures'][state]['parameters']
        if any(type(parameters.get(name)) not in (int, float) or not math.isfinite(parameters[name])
               or abs(parameters[name]-target) > .03
               for name in ('ParamFreeArmActive', 'ParamFreeArmPose', 'ParamFreeArmBrace')):
            raise ValueError(f'Brace fall/landing endpoint did not recover: {state}')
    end = report['captures']['land-end']
    binding, event, play = report['motionBindings']['land'], report['finishedEvents']['land'], end['playback']
    if (binding.get('name') != 'land' or end['generation'] != binding['generation']
            or play.get('name') != 'land' or play.get('token') != binding['token']
            or play.get('evaluated') is not True or type(play.get('nativeUpdateCount')) is not int
            or play['nativeUpdateCount'] < event.get('nativeUpdateCount', 1)
            or event.get('type') != 'finished' or event.get('cycle') != 1
            or any(event.get(key) != binding.get(key) for key in ('name', 'generation', 'token'))):
        raise ValueError('Landing endpoint is not bound to its actual native completion')
    targets = report['motionEndpoints']['land']['end']
    expected = {'ParamFreeArmActive': 1, 'ParamFreeArmPose': 0, 'ParamFreeArmBrace': 0, 'ParamArmPoseMode': 0}
    for name, target in expected.items():
        authored, actual = targets.get(name), end['parameters'].get(name)
        if (type(authored) not in (int, float) or not math.isfinite(authored) or abs(authored-target) > 1e-6
                or type(actual) not in (int, float) or not math.isfinite(actual) or abs(actual-authored) > .03):
            raise ValueError(f'Landing authored/rest endpoint did not recover: {name}')
    recovery = {side: _contact(report, end, side, definition['hands'][0], definition)
                for side, definition in definitions.items()}
    travel = {}
    for side in ('l', 'r'):
        points = [rows[f'v51_free_recovery_{index}'][phase][side]['wrist']
                  for index in (0, 1) for phase in ('before', 'after')]
        distance = max(math.dist(a, b) for a in points for b in points)*_scale(report['probes']['v51_free_recovery_0']['before'])
        if distance <= 1e-6:
            raise ValueError(f'Brace has no actual native wrist response: {side}')
        travel[side] = distance
    return {'samples': rows, 'braceWristTravel': travel,
            'landingEnd': {'captureKey': 'land-end', 'binding': binding,
                           'targets': expected, 'contacts': recovery}}


def measure_climb_materials(report):
    definitions = climb_materials(report['v5Contract'])
    if definitions is None:
        raise ValueError('Climbing replacements require motionPolishVersion=1')
    exchange = climb_drape_exchange(report['v5Contract'])
    rows = {}
    for name, case in report['climbProbeContract'].items():
        material = definitions[case['direction']]
        for phase in ('before', 'after'):
            snapshot = report['probes'][name][phase]
            value = snapshot['parameters'].get(exchange['parameter'])
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value-1) > .03:
                raise ValueError(f'Climb material probe did not use its independent drape gate: {name}/{phase}')
            _visible(snapshot, material['drawable'])
            _visible(snapshot, material['replaces'], False)
            _visible(snapshot, material['underlay'], False)
        rows[name] = dict(material)
    if len(rows) != 16:
        raise ValueError('Missing complete climbing material phase samples')
    return rows


def continuous_swing_targets(snapshot):
    """Evaluate the documented runtime owner instead of expecting authored zeroes."""
    play = snapshot['playback']
    phase, amplitude = play['swingPhase'], play['swingAmplitude']
    if (play.get('swingActive') != 1 or not all(type(v) in (int, float) and math.isfinite(v) for v in (phase, amplitude))
            or not 0 <= amplitude <= 1.01 or play.get('swingPettingWeight') != 0):
        raise ValueError('Missing current continuous swing phase evidence')
    value = amplitude*math.sin(phase)
    return {'ParamSwing': value, 'ParamAngleZ': -3*value, 'ParamLegLA': 4*value, 'ParamLegRA': 4*value}


def authored_physics_outputs(report, snapshot, expected):
    """Strict authoring values for every file-declared physics destination."""
    declaration = report['physicsOutputs']
    resource, digest, names = (declaration[key] for key in ('resource', 'sha256', 'parameters'))
    if (not isinstance(resource, str) or not resource.startswith('assets/live2d/Maple/')
            or not isinstance(digest, str) or len(digest) != 64
            or any(report[key].get(resource) != digest for key in
                   ('resourceHashesAtStart', 'resourceHashesLoaded', 'resourceHashesAtEnd'))):
        raise ValueError('Physics output declaration is not bound to the actual loaded resource')
    # Reuse contract validation to reject unknown/native-control substitutions.
    checked = physics_output_parameters({'Version': 3, 'PhysicsSettings': [{'Output': [
        {'Destination': {'Target': 'Parameter', 'Id': name}} for name in names]}]}, expected)
    values = snapshot.get('motionPhysicsValues')
    if checked != names or not isinstance(values, dict) or set(values) != set(names):
        raise ValueError('Missing or extra motion-before-physics parameter evidence')
    rows = {}
    for name in names:
        actual, final, target = values[name], snapshot['parameters'].get(name), expected[name]
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (actual, final, target))
                or abs(actual-target) > .03):
            raise ValueError(f'Authored physics endpoint differs before physics: {name}')
        rows[name] = {'motionValue': actual, 'physicsValue': final, 'target': target, 'error': abs(actual-target)}
    return rows


def cleanup_endpoint_parameters(report, name, phase):
    snapshot = report['captures'][name+'-'+phase]
    expected = report['motionEndpoints'][name][phase]
    polished = report.get('v5Contract', {}).get('motionPolishVersion') == 1
    physics = authored_physics_outputs(report, snapshot, expected) if polished else {}
    runtime = continuous_swing_targets(snapshot) if polished and name.startswith('clean_top') else {}
    differences = {}
    for parameter, target in expected.items():
        if parameter in ('ParamEyeLOpen', 'ParamEyeROpen', 'ParamBreath') or (not polished and parameter.startswith('ParamHair')):
            continue
        target = runtime.get(parameter, target)
        actual = physics[parameter]['motionValue'] if parameter in physics else snapshot['parameters'].get(parameter)
        if type(actual) not in (int, float) or not math.isfinite(actual) or abs(actual-target) > .03:
            raise ValueError(f'Cleanup endpoint mismatch: {name}/{phase}/{parameter}={actual}, expected {target}')
        differences[parameter] = abs(actual-target)
    return {'maximumParameterError': max(differences.values()), 'parametersCompared': len(differences),
            **({'physicsOutputs': physics} if polished else {}),
            **({'continuousSwingTargets': runtime} if runtime else {})}

def native_uv_point(drawable: dict, point: list[float]) -> tuple[float, float]:
    """Sample the real exported mesh at a texture point, independent of vertex order."""
    positions, uvs, triangles = (drawable.get(key, []) for key in ('positions', 'textureUvs', 'triangles'))
    if len(positions) != len(uvs) or len(uvs) < 6 or len(uvs) % 2 or len(triangles) % 3:
        raise ValueError('Missing native contact topology')
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in [*positions, *uvs, *point]):
        raise ValueError('Nonfinite native contact geometry')
    for offset in range(0, len(triangles), 3):
        indices = triangles[offset:offset+3]
        if not all(isinstance(index, int) and 0 <= index < len(uvs)//2 for index in indices):
            raise ValueError('Invalid native contact triangle')
        a, b, c = [(uvs[2*index], uvs[2*index+1]) for index in indices]
        dx, dy = point[0]-a[0], point[1]-a[1]
        bx, by, cx, cy = b[0]-a[0], b[1]-a[1], c[0]-a[0], c[1]-a[1]
        determinant = bx*cy-by*cx
        if abs(determinant) < 1e-14:
            continue
        wb, wc = (dx*cy-dy*cx)/determinant, (bx*dy-by*dx)/determinant
        weights = (1-wb-wc, wb, wc)
        if min(weights) >= -1e-5:
            return tuple(sum(weight*positions[2*index+axis] for index, weight in zip(indices, weights)) for axis in (0, 1))
    raise ValueError('Authored wrist lies outside native mesh UV triangles')


def source_point(report: dict, snapshot: dict, name: str, uv: list[float]) -> tuple[float, float]:
    """Resolve a canonical painted point through the verified atlas into native geometry."""
    audit = report['atlasAudit']
    if report.get('atlasCoordinateMappingVerified') is not True:
        raise ValueError('Atlas coordinate mapping is not verified')
    regions = {name: region for region in audit['regions'] for name in region.get('drawables', [region.get('name')])}
    region = regions[name]
    items = {item['id']: item for item in snapshot['drawables']}
    if items[name].get('textureIndex') != region['page']:
        raise ValueError(f'Native texture page differs for {name}')
    point = [(uv[axis]*region['sourceSize'][axis]-region['translationToCanvas'][axis])/audit['atlasSize'][axis]
             for axis in (0, 1)]
    return native_uv_point(items[name], point)

