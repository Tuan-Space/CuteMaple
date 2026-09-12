"""Editable v5 pose rig. Runtime files are staged, never installed here.

All output geometry is standard Cubism mesh/deformer keyforms. This tool
cannot export MOC3; the final project must be opened and exported by Editor.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
from build_v4 import (V4Builder, metadata_v4, smoothstep, palm_on_rope,
                      ROOT, Rig, audit_rig, corrected_project, physics3)
from build_rig import clamp, rotate
from maple_motions import build_motion, motion_references, TRANSITIONS, V5_CLEAN_TRANSITIONS
from tools.authoring.animation_specs import ANIMATIONS
from free_arm_refinement import (apply_free_arm_refinement, finish_free_arm_refinement,
                                 finish_fan_material_refinement)
from climb_refinement import apply_climb_refinement, climb_metadata
from sleep_contact_refinement import apply_sleep_contact_refinement, sleep_ground_anchor
from climb_contact_refinement import apply_climb_contact_refinement, add_climb_contact_metadata
import brush_refinement

PHASE_KEYS=[0,.10,.18,.25,.35,.40,.50,.55,.60,.70,.78,.85,.90,1.]
TRAVEL_KNOTS=[(0,0),(.18,.08),(.40,.40),(.55,.48),(.78,.82),(1,1)]
RISE=.16


def linear_table(points,value):
    for (a,x),(b,y) in zip(points,points[1:]):
        if value<=b:
            return x+(y-x)*clamp((value-a)/(b-a))
    return points[-1][1]


def climb_travel(phase):
    return linear_table(TRAVEL_KNOTS,phase)


def hand_reach(phase,near):
    a,b=(.10,.35) if near else (.60,.85)
    return smoothstep((phase-a)/(b-a))


def climb_hand_y(phase,near):
    return RISE*(climb_travel(phase)-hand_reach(phase,near))


def supported_arm_axes(side,climb,shoulder):
    """The existing transfer's shoulder/wrist until its wall-release marker.

    ClimbActive reaches zero at .85 seconds. Freeze this compensation field
    there so unused later keyforms cannot divide by an arm crossing its own
    shoulder; the ordinary v4 arm path still continues to its rope endpoint.
    """
    direction=1 if climb>=0 else -1
    near=(side=='l')==(direction>0)
    sx=(.429 if near else .487) if direction>0 else (.553 if near else .516)
    wx=(.687 if near else .675) if direction>0 else (.329 if near else .341)
    wall_s=(sx,.426 if near else .415);wall_w=(wx,.344 if near else .446)
    t=min(.85,(1-abs(climb))*1.8)
    body=smoothstep((t-.32)/(1.65-.32))
    s=tuple(a+(b-a)*body for a,b in zip(wall_s,shoulder))
    if near:w=wall_w
    else:
        goal=palm_on_rope(side,0,y=.43)
        f=smoothstep(t/.32)
        w=tuple(a+(b-a)*f for a,b in zip(wall_w,goal))
    return s,w


def leg_registration(point,source,target):
    """Two rigid length-preserving cloth axes sharing one actual knee.

    The source is a complete painted trouser leg. The transverse coordinate
    retains its width; only the longitudinal coordinate follows joint length.
    """
    hip,knee,ankle=source
    split=(point[1]-hip[1])/(knee[1]-hip[1])
    a,b,c,d=(hip,knee,target[0],target[1]) if split<1 else (knee,ankle,target[1],target[2])
    dx,dy=b[0]-a[0],b[1]-a[1]
    length=math.hypot(dx,dy)
    along=((point[0]-a[0])*dx+(point[1]-a[1])*dy)/(length*length)
    across=(-(point[0]-a[0])*dy+(point[1]-a[1])*dx)/length
    tx,ty=d[0]-c[0],d[1]-c[1];tl=max(1e-8,math.hypot(tx,ty))
    return c[0]+along*tx-across*ty/tl,c[1]+along*ty+across*tx/tl


class V5Builder(V4Builder):
    rounded_supported_sleeves = True

    def _parameters(self):
        super()._parameters()
        for side in ('L','R'):
            for axis in ('X','Y'):
                self.parameter('ParamRibbon'+side+axis)
            self.parameter('ParamCleanFan'+side,0,1)
        self.parameter('ParamCleaningSweep',keys=[-1,-.5,0,.5,1])
        self.parameter('ParamCleanGround',0,1)
        self.parameter('ParamClimbPhase',0,1,keys=PHASE_KEYS)
        self.parameter('ParamClimbActive',0,1)
        self.parameter('ParamClimbDirection',keys=[-1,0,1])
        self.parameter('ParamPoseSleep',0,1,keys=[0,.35,.70,.82,.8201,.87,.9199,.92,1])
        if brush_refinement.enabled(self.manifest):
            brush_refinement.add_parameters(self)

    def _hierarchy(self):
        result=super()._hierarchy()
        # A rigid upper-body descent preserves head/face and embroidery scale.
        # The lower body has separate folds and real knee joints below.
        self.deform('ParamPoseSleep','Body_SeatedSleep',lambda p,v:(p[0],p[1]+.20*v))
        self.sleep_head=self.warp('Sleep_Head_Nod',self.head,13)
        self.deform('ParamPoseSleep',self.sleep_head,
                    lambda p,v:rotate(p,(.5107,.365),-5*v))
        climb=self.warp('Climb_Body_Lower','Body_SeatedSwing',17)
        self.nodes['Body_Weight'].parent=climb
        self.deform('ParamClimbActive',climb,lambda p,v:
                    (p[0],p[1]+.085*v*(1-clamp((p[1]-.54)/.4))))
        return result

    def _active_arm(self,side,segment):
        name=f'ActiveArm_{side}_{segment}'
        if name in self.nodes:
            return name
        name=super()._active_arm(side,segment)
        parent=self.nodes[name].parent
        support=self.warp(f'ClimbShoulder_{side}_{segment}',parent,17)
        self.nodes[name].parent=support
        # Torso crouches by 0.085 canvas while planted palms keep their world
        # points. Blend that descent over the actual arm, without scaling head.
        def shoulder_drop(p,v):
            # The first transfer palm reaches a rope before ClimbActive is
            # released. Its zero-weight position must follow that same
            # PoseClimb keyform, rather than remaining at the old wall x.
            s,w=supported_arm_axes(side,v,self._joints(side)[0])
            sx,wx=s[0],w[0]
            # Keep this field affine through the complete mesh, including
            # cloth beyond the wrist. Clamping at the wrist splits triangles
            # across a derivative discontinuity: their interpolated cuff then
            # moves even though the authored wrist itself has weight zero.
            weight=0 if segment=='hand' else 1-(p[0]-sx)/(wx-sx)
            return p[0],p[1]+.085*weight
        self.deform('ParamPoseClimb',support,shoulder_drop)
        for key in self.params['ParamClimbActive'].keyforms:
            key.deformer_offset_weights[support]=key.value
        parent=support
        # This parent operates after the canonical cloth has been mapped to
        # the actual articulated arm. Both cuffs and palms share its endpoint.
        for direction in (-1,1):
            node=self.warp(f'ClimbCycle_{direction}_{side}_{segment}',parent,17)
            self.nodes[name].parent=node
            near=(side=='l')==(direction>0)
            sx=(.429 if near else .487) if direction>0 else (.553 if near else .516)
            wx=(.687 if near else .675) if direction>0 else (.329 if near else .341)
            def shift(p,v,near=near,sx=sx,wx=wx):
                amount=climb_hand_y(v,near)
                weight=1. if segment=='hand' else (p[0]-sx)/(wx-sx)
                return p[0],p[1]+amount*weight
            self.deform('ParamClimbPhase',node,shift)
            for key in self.params['ParamClimbDirection'].keyforms:
                key.deformer_offset_weights[node]=clamp(direction*key.value)
            for key in self.params['ParamClimbActive'].keyforms:
                key.deformer_offset_weights[node]=key.value
            parent=node
        # Ground fan cleanup uses the same active sleeve and palm rather than
        # moving a prop independently from a resting folded hand.
        # Apply shared fan translations after the shoulder/cycle fields. If
        # applied before them, moving the cuff in x changes its shoulder
        # weight while the corresponding palm still has constant weight.
        ground=self.warp(f'CleanGround_{side}_{segment}',self.nodes[support].parent,13)
        goal=palm_on_rope(side,0)
        target=(.444,.545) if side=='l' else (.650,.565)
        scale=0.5 if segment=='upper' else 1.
        self.deform('ParamCleanGround',ground,lambda p,v:
                    (p[0]+(target[0]-goal[0])*v*scale,p[1]+(target[1]-goal[1])*v*scale))
        sweep=self.warp(f'FanSweep_{side}_{segment}',ground,13)
        self.nodes[support].parent=sweep
        factor=.4 if segment=='upper' else 1.
        self.deform('ParamCleaningSweep',sweep,lambda p,v:
                    (p[0]+.038*v*factor,p[1]-.016*math.sin(v*math.pi/2)*factor))
        for key in self.params['ParamCleanFan'+side.upper()].keyforms:
            key.deformer_offset_weights[sweep]=key.value
        return name

    def _pose_binding(self,layer):
        pose=layer.get('pose','rest')
        if pose=='sleep':
            self.opacity('ParamPoseSleep',layer['id'],lambda v:smoothstep((v-.82)/.10))
        elif pose=='climb':
            self.opacity('ParamClimbActive',layer['id'],lambda v:v)
        else:
            super()._pose_binding(layer)
            if layer.get('has_seated_variant'):
                self.opacity('ParamPoseSleep',layer['id'],lambda v:1. if v<.92 else 0.)
                if layer['role'].startswith('leg_'):
                    self.opacity('ParamClimbActive',layer['id'],lambda v:1-v)

    def _load_layers(self,root,chest,head):
        super()._load_layers(root,chest,head)
        for part in self.parts:
            layer=self.layer_by_id[part.id]
            name,role=part.id,layer['role']
            pose=layer.get('pose','rest')
            if role.startswith('hand_') and layer.get('arm_variant')=='rest':
                part.parent_deformer=self._arm(layer['side'])['hand']
            if pose=='sleep':
                part.parent_deformer='Body_SeatedSleep'
                if role.startswith('leg_'):
                    side=layer['side']
                    node=self.warp('Sleep_Knee_'+side,'Body_SeatedSleep',17)
                    part.parent_deformer=node
                    source=(tuple(layer['hip']),tuple(layer['pivot']),tuple(layer['ankle']))
                    target=(((.493,.55),(.433,.637),(.484,.735)) if side=='l' else
                            ((.529,.55),(.586,.637),(.540,.735)))
                    self.deform('ParamPoseSleep',node,lambda p,v,s=source,t=target:
                        tuple(a+(b-a)*v for a,b in zip(p,leg_registration(p,s,t))))
            elif pose=='climb':
                side=layer['side']
                parent=self.warp('ClimbLeg_'+side,'Body_Transition',17)
                part.parent_deformer=parent
                source=(tuple(layer['hip']),tuple(layer['pivot']),tuple(layer['ankle']))
                # A true bent trouser leg replaces the old independently
                # rotating halves of a standing shoe. Hip remains under skirt.
                def bend(p,v,s=source,side=side):
                    near=(side=='l')==(v>0)
                    target=((.520 if near else .535,.650),(.638 if near else .590,.720),
                            (.648 if near else .620,.857 if near else .899))
                    if v<0:
                        target=tuple((1.016-x,y) for x,y in target)
                    q=leg_registration(p,s,target)
                    return tuple(a+(b-a)*abs(v) for a,b in zip(p,q))
                self.deform('ParamClimbDirection',parent,bend)
                gait=self.warp('ClimbLegCycle_'+side,'Body_Transition',17)
                self.nodes[parent].parent=gait
                self.deform('ParamClimbPhase',gait,lambda p,v,s=side:
                    (p[0],p[1]+climb_hand_y(v,s=='r')*.65))
            if layer.get('has_seated_variant'):
                node=self.warp('SleepFold_'+name,part.parent_deformer,17)
                part.parent_deformer=node
                self.deform('ParamPoseSleep',node,lambda p,v:
                    (p[0],p[1]-.55*v*max(0,p[1]-.54)))
                if role=='clothing':
                    climb=self.warp('ClimbFold_'+name,node,17)
                    part.parent_deformer=climb
                    self.deform('ParamClimbActive',climb,lambda p,v:
                        (p[0],p[1]-.19*v*max(0,p[1]-.54)))
            if 'ribbon_' in name:
                side='L' if name.endswith('_l') else 'R'
                node=self.warp('Wind_'+name,part.parent_deformer,17)
                part.parent_deformer=node
                top=self.layer_bounds[name][1]
                length=max(.1,self.layer_bounds[name][3]-top)
                self.deform('ParamRibbon'+side+'X',node,lambda p,v,t=top,l=length:
                    (p[0]+.072*v*clamp((p[1]-t)/l)**1.4,p[1]))
                self.deform('ParamRibbon'+side+'Y',node,lambda p,v,t=top,l=length:
                    (p[0],p[1]+.080*v*clamp((p[1]-t)/l)**1.5))
                sleep=self.warp('SleepRibbon_'+name,part.parent_deformer,17)
                part.parent_deformer=sleep
                # The long tail settles laterally on the floor, rather than
                # following the torso below the canvas during sitting.
                sign=-1 if side=='L' else 1
                self.deform('ParamPoseSleep',sleep,lambda p,v,s=sign:
                    (p[0]+s*.045*v*clamp((p[1]-.65)/.3),
                     p[1]-.21*v*clamp((p[1]-.60)/.32)))
            if layer.get('zone')=='head' and 'ribbon_' not in name:
                # Swap only the existing head-root reference, so all standard
                # eye/head view geometry remains unchanged in neutral.
                parent=part.parent_deformer
                if parent==head:
                    part.parent_deformer=self.sleep_head
                elif parent and self.nodes[parent].parent==head:
                    self.nodes[parent].parent=self.sleep_head
            if layer.get('clean_fan'):
                side=layer['side']
                part.parent_deformer=self._active_arm(side,'hand')
                self.opacity('ParamCleanFan'+side.upper(),name,lambda v:v)
                shaft=tuple(layer['shaftSourceUv'])
                def present(p,v,pivot=shaft,side=side):
                    q=rotate(p,pivot,(1-v)*(65 if side=='l' else -65))
                    return pivot[0]+(q[0]-pivot[0])*(.15+.85*v),pivot[1]+(q[1]-pivot[1])*(.25+.75*v)
                self.deform('ParamCleanFan'+side.upper(),name,present,mesh=True)
            if role=='neck':
                # Share the head's standard gaze only along the upper seam.
                # The lower collar edge stays on the torso's actual chain.
                previous=part.parent_deformer
                sleep_neck=self.warp('SleepNeck_'+name,previous,17)
                part.parent_deformer=sleep_neck;previous=sleep_neck
                def neck_nod(p,v):
                    q=rotate(p,(.5107,.365),-5*v)
                    w=1-smoothstep((p[1]-.350)/.052)
                    return p[0]+(q[0]-p[0])*w,p[1]+(q[1]-p[1])*w
                self.deform('ParamPoseSleep',sleep_neck,neck_nod)
                for suffix in ('X','Y','Z'):
                    node=self.warp('NeckGaze_'+suffix+'_'+name,previous,17)
                    part.parent_deformer=node;previous=node
                    def gaze(p,v,s=suffix):
                        weight=1-smoothstep((p[1]-.350)/.052)
                        if s=='X':
                            q=(p[0]+.018*v/30*math.exp(-((p[1]-.29)/.23)**2)-.04*abs(v)/30*(p[0]-.51),p[1]+.002*v/30*(p[0]-.51))
                        elif s=='Y':
                            q=(p[0],p[1]+.011*v/30*math.exp(-((p[1]-.3)/.2)**2))
                        else:
                            q=rotate(p,(.5107,.365),.4*v)
                        return p[0]+(q[0]-p[0])*weight,p[1]+(q[1]-p[1])*weight
                    self.deform('ParamAngle'+suffix,node,gaze)

        apply_free_arm_refinement(self)
        apply_climb_refinement(self)
        finish_free_arm_refinement(self)
        apply_climb_contact_refinement(self)
        apply_sleep_contact_refinement(self)
        finish_fan_material_refinement(self)
        brush_refinement.apply_brush_refinement(self)


def metadata_v5(rig,manifest,builder):
    result=metadata_v4(rig,manifest)
    result['modelVersion']='v5'
    result['authoringSource']='../../authoring/revisions/v5/Maple.cmo3'
    refine=result['refinement'];refine['version']=5
    refine['requiredParameters'] += ['ParamRibbonLX','ParamRibbonLY','ParamRibbonRX','ParamRibbonRY',
                                     'ParamCleanFanL','ParamCleanFanR','ParamCleaningSweep','ParamClimbPhase','ParamClimbActive',
                                     'ParamClimbDirection','ParamCleanGround',
                                     'ParamFreeArmActive','ParamFreeArmPose','ParamArmSupportBlend',
                                     'ParamClimbRefine','ParamClimbHandoff']
    if 'ParamFreeArmBrace' in builder.params and 'ParamFreeArmBrace' not in refine['requiredParameters']:
        refine['requiredParameters'].append('ParamFreeArmBrace')
    refine['motionRevision']='v5-motion-refined-20260909'
    refine['nativeParameterIds']=[p.id for p in rig.parameters]
    refine['nativeDrawableIds']=[p.id for p in rig.parts]
    if manifest.get('brushRefinementVersion') == 1:
        drawable_ids = set(refine['nativeDrawableIds'])
        refine['pettingHeadDrawables'] = [layer['id'] for layer in builder.layers
            if layer.get('family') in {'face_base', 'hair_front'} and layer['id'] in drawable_ids]
    climb_materials = {}
    for layer in builder.layers:
        if layer.get('pose') == 'climb_drape':
            view = layer.get('view')
            underlay = next(item['id'] for item in builder.layers
                            if item.get('family') == 'costume_underlay_skirt' and item.get('view') == view)
            climb_materials[view] = {'drawable': layer['id'],
                                    'replaces': layer['climb_replaces'], 'underlay': underlay}
    if climb_materials:
        refine['climbMaterials'] = climb_materials
        if 'ParamClimbDrapeBlend' in builder.params:
            refine['requiredParameters'].append('ParamClimbDrapeBlend')
            refine['climbDrapeExchange'] = {
                'parameter': 'ParamClimbDrapeBlend', 'default': 0,
                'unfoldEndSeconds': .4, 'exchangeEndSeconds': .52,
                'blend': 'opaque-receiving-surface-normal-over',
                'receivingOpacityGuard': .9999}
    result['locomotion']={'climb':climb_metadata()}
    refine['ribbons']={'parameters':['ParamRibbonLX','ParamRibbonLY','ParamRibbonRX','ParamRibbonRY'],
        'range':[-1,1],'positiveAxes':'canvas right/down','rootPinned':True,'originalAlphaPreserved':True}
    refine['sleep']={'parameter':'ParamPoseSleep','pose':'curled ground sitting',
        'seated':['skirt_sleep','leg_l_sleep','leg_r_sleep'],'upperBodyScale':[1,1]}
    refine['restContacts']={
        'l':{'hand':'hand_l','cuff':'arm_l','sourceUv':[.495,.518]},
        'r':{'hand':'hand_r','cuff':'arm_r','sourceUv':[.526,.518]}}
    refine['freeContacts']={
        'parameter':'ParamFreeArmActive','poseParameter':'ParamFreeArmPose','armPoseMode':0,
        'samePaintedSleevesThroughLanding':True,'contacts':refine['restContacts']}
    refine['sleep']['groundContact']=builder.sleep_contact_refinement
    for state in ('idle','sleep_enter','sleep_loop','sleep_exit'):
        result['states'].setdefault(state,{}).setdefault('anchors',{})['ground']=sleep_ground_anchor(builder)
    for state in ('clean_ground','clean_top','clean_climb_left','clean_climb_right'):
        side='l' if state=='clean_climb_left' else 'r'
        anchors=result['states'].setdefault(state,{}).setdefault('anchors',{})
        anchors.update(fanTip={'drawable':'clean_fan_'+side,'edge':'top'},
                       freeHand={'drawable':'hand_'+side+'_grip_palm'})
        for suffix,duration in [('enter',.7),('exit',.65)]:
            internal=state+'_'+suffix
            result['states'][internal]={'anchors':dict(anchors)}
            result['transitions'][internal]={'duration':duration,'playback':'one_shot',
                'to':state if suffix=='enter' else 'previous_support_state','events':[]}
    result['capabilities']['internalMotions']=list(TRANSITIONS)+list(V5_CLEAN_TRANSITIONS)
    refine['internalMotionCount']=10
    add_climb_contact_metadata(result,builder)
    brush_refinement.add_metadata(result,builder)
    return result


def build_v5(manifest_path=None,*,write_project=True):
    manifest_path=Path(manifest_path or ROOT/'assets/authoring/revisions/v5/layers.json').resolve()
    revisions=(ROOT/'assets/authoring/revisions').resolve()
    if not manifest_path.is_relative_to(revisions) or not manifest_path.relative_to(revisions).parts[0].startswith('v5'):
        raise ValueError('v5 must use its separate revision directory')
    manifest=json.loads(manifest_path.read_text(encoding='utf8'))
    if manifest.get('version')!=5:
        raise ValueError('Explicit v5 manifest required')
    builder=V5Builder(manifest,manifest_path.parent)
    rig=builder.build()
    output=manifest_path.parent;runtime=output/'runtime'
    (runtime/'motions').mkdir(parents=True,exist_ok=True)
    (output/'Maple.rig.json').write_text(rig.model_dump_json(indent=2),encoding='utf8')
    if write_project:
        (output/'Maple.cmo3').write_bytes(corrected_project(rig,output))
    defaults={p.id:p.default for p in rig.parameters}
    motions={**ANIMATIONS,**TRANSITIONS,**V5_CLEAN_TRANSITIONS}
    for state,spec in motions.items():
        (runtime/'motions'/f'{state}.motion3.json').write_text(json.dumps(build_motion(state,spec,defaults),indent=2),encoding='utf8')
    metadata=metadata_v5(rig,manifest,builder)
    metadata['authoringSource']='../../'+(output/'Maple.cmo3').relative_to(ROOT/'assets').as_posix()
    (runtime/'Maple.pet.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf8')
    (runtime/'Maple.physics3.json').write_text(json.dumps(physics3(rig),indent=2),encoding='utf8')
    fragment={'FileReferences':{'Motions':motion_references(motions),'Physics':'Maple.physics3.json'},
              'Groups':[{'Target':'Parameter','Name':'EyeBlink','Ids':['ParamEyeLOpen','ParamEyeROpen']}],
              'CuteMaple':{'Metadata':'Maple.pet.json'}}
    (output/'Maple.model3-fragment.json').write_text(json.dumps(fragment,indent=2),encoding='utf8')
    report={'version':5,'status':'offline candidate, native Editor and visual validation pending',
            'parts':len(rig.parts),'parameters':len(rig.parameters),'deformers':len(rig.deformers),
            'geometryAxes':audit_rig(rig),'inputSha256':{layer['file']:hashlib.sha256((output/layer['file']).read_bytes()).hexdigest() for layer in builder.layers}}
    (output/'Maple.build-audit.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',nargs='?',type=Path)
    parser.add_argument('--ir-only',action='store_true')
    args=parser.parse_args()
    report=build_v5(args.manifest,write_project=not args.ir_only)
    print({key:value for key,value in report.items() if key not in ('geometryAxes','inputSha256')})
