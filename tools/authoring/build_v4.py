"""Maple v4: articulated parts, local profile art and fixed swing suspension.

Inputs are registered, independently editable full-canvas PNG layers. Profile
art is separated by semantic part; no complete character image is faded in.
An actual Cubism Editor export and native visual review remain required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image

from build_rig import (ROOT, MapleBuilder, Part, SemanticRole, Texture,
                       PhysicsRig, Rig, clamp, rotate, grid_mesh, audit_rig,
                       corrected_project, metadata_for, physics3)
from maple_motions import build_motion, motion_references, TRANSITIONS, TRANSITION_EVENTS
from pet_core import ANIMATIONS
from view_registration import register_point, validate_registration
from neck_registration import neck_point
from head_material_registration import (HEAD_HAIR_SWAY_GRID, refine_head_warp_sampling,
                                        refine_ornament_material_meshes)
from head_view_exchange import apply_head_view_exchange
from v51_pose import full_sleeve_point, expand_climb_material_layers

_POSITIVE_TURNS=[.20,.2001,.25,.2999,.30,.5,.70,.7001,.75,.7999,.80,1.]
TURN_KEYS = [-v for v in reversed(_POSITIVE_TURNS)]+[0.]+_POSITIVE_TURNS
VIEW_ANGLES={"left":-1.,"left_mid":-.5,"front":0.,"right_mid":.5,"right":1.}
PAINT_ORDER={"front":0,"right":1,"left":2,"right_mid":3,"left_mid":4}


def layer_draw_order(layer):
    """Normal-over material exchange must not depend on export tie ordering."""
    return layer["draw_order"] + PAINT_ORDER.get(layer.get("view"), 0)


def smoothstep(value):
    value=clamp(value)
    return value*value*(3-2*value)


def original_view_coverage(turn, available=None):
    # Geometry moves throughout the turn. Only the locally painted surface
    # changes in a narrow interval after features share common landmarks.
    available=available or set(VIEW_ANGLES)
    side="left" if turn<0 else "right"
    sequence=["front"]+([side+"_mid"] if side+"_mid" in available else [])+[side]
    boundaries=[(abs(VIEW_ANGLES[a])+abs(VIEW_ANGLES[b]))/2 for a,b in zip(sequence,sequence[1:])]
    changes=[smoothstep((abs(turn)-bound+.05)/.10) for bound in boundaries]
    weights=[1-changes[0]]
    weights += [a*(1-b) for a,b in zip(changes,changes[1:])]
    weights += [changes[-1]]
    return dict(zip(sequence,weights))


def view_weight(view, turn, available=None):
    coverage=original_view_coverage(turn,available)
    active=[name for name,weight in coverage.items() if weight>1e-12]
    if len(active)==2:
        # Native normal-over compositing of .5/.5 is only .75 opaque. In a
        # local material exchange the lower painted surface remains opaque
        # while the upper one blends; its contribution is naturally attenuated
        # by the upper alpha. Endpoint guard keys retire the covered surface
        # only once the new one is opaque, without a broad transparency dip.
        coverage[min(active,key=PAINT_ORDER.get)]=1.
    return coverage.get(view,0.)


def hanging_rope_point(point, side, swing, *, length=.75, center=.508):
    """Two fixed upper endpoints; the rigid seat moves as one pendulum."""
    top_x=.355 if side=="l" else .657
    bottom=(.383 if side=="l" else .633,length)
    angle=math.radians(8*swing)
    moved=rotate(bottom,(center,0),math.degrees(angle))
    fraction=point[1]/length
    return (point[0]+(moved[0]-bottom[0])*fraction,
            point[1]+(moved[1]-bottom[1])*fraction)


def palm_on_rope(side, swing, y=.50):
    top_x=.355 if side=="l" else .657
    bottom_x=.383 if side=="l" else .633
    source=(top_x+(bottom_x-top_x)*y/.75,y)
    return hanging_rope_point(source,side,swing)


def swing_arm_joints(builder, side):
    """One canonical supported reference for sleeve and free/cleanup authors."""
    return (builder._joints(side)[0], (.409 if side=='l' else .601,.485),
            palm_on_rope(side,0))


def two_bone_elbow(shoulder, wrist, lengths, bend):
    dx,dy=wrist[0]-shoulder[0],wrist[1]-shoulder[1]
    distance=math.hypot(dx,dy)
    if distance<1e-8:
        return shoulder
    a,b=lengths
    d=min(max(distance,abs(a-b)+1e-5),a+b-1e-5)
    along=(a*a-b*b+d*d)/(2*d)
    normal=math.sqrt(max(0.,a*a-along*along))*bend
    ux,uy=dx/distance,dy/distance
    return shoulder[0]+along*ux-normal*uy,shoulder[1]+along*uy+normal*ux


def segment_transform(point, source_a, source_b, target_a, target_b):
    dx,dy=source_b[0]-source_a[0],source_b[1]-source_a[1]
    length2=dx*dx+dy*dy
    along=((point[0]-source_a[0])*dx+(point[1]-source_a[1])*dy)/length2
    across=(-(point[0]-source_a[0])*dy+(point[1]-source_a[1])*dx)/math.sqrt(length2)
    tx,ty=target_b[0]-target_a[0],target_b[1]-target_a[1]
    length=math.hypot(tx,ty)
    return target_a[0]+along*tx-across*ty/length,target_a[1]+along*ty+across*tx/length


class V4Builder(MapleBuilder):
    # V4's independent hand mapping needs the original segment-local fields.
    # V5 opts into the rounded shared field and finishes its hand registration
    # against the actual sleeve lattice in finish_free_arm_refinement.
    rounded_supported_sleeves = False

    def __init__(self,manifest,asset_root):
        expand_climb_material_layers(manifest)
        super().__init__(manifest,asset_root)
        self.layer_bounds={}
        self.family_views={}
        for layer in self.layers:
            path=self.asset_root/layer["file"]
            with Image.open(path) as im:
                box=im.getchannel("A").getbbox()
            if box is None:
                raise ValueError(f"Empty layer {layer['id']}")
            self.layer_bounds[layer['id']]=tuple(value/(self.width if i%2==0 else self.height)
                                                for i,value in enumerate(box))
            if layer.get("family"):
                key=(layer["family"],layer.get("view","front"),layer.get("pose","rest"))
                self.family_views[key]=layer["id"]

    def _registered_point(self,point,view,turn,zone):
        return register_point(point,view,turn,zone)

    def _parameters(self):
        super()._parameters()
        for name in ["HeadTurn","BodyTurn"]:
            self.parameter("Param"+name,keys=TURN_KEYS)
        for side in ("L","R"):
            self.parameter(f"ParamHand{side}Shape",keys=[-1.,0.,1.])
            self.parameter(f"ParamSleeve{side}Hang")
        self.parameter("ParamTransitionProgress",0,1,keys=[0,.25,.5,.75,1])
        self.parameter("ParamArmPoseMode",0,1,keys=[0,.025,.05,1])
        path_keys=sorted(set([0.,-1.,1.]+[sign*(1-t/1.8) for sign in (-1,1) for t in (0,.16,.32,.55,.85,1.05,1.25,1.45,1.65,1.8)]))
        self.parameter("ParamPoseClimb",keys=path_keys)
        self.parameter("ParamPoseSwing",0,1,keys=[0,.5,.82,.8201,.87,.9199,.92,1])

    def _hierarchy(self):
        root=self.warp("Rig_Root")
        scene,chest,head=super()._hierarchy()
        self.nodes[scene].parent=root
        transition=self.warp("Body_Transition",scene)
        self.nodes["Body_SeatedSleep"].parent=transition
        self.deform("ParamTransitionProgress",transition,
                    lambda p,v:(p[0],p[1]-.006*math.sin(math.pi*v)))
        # The two fixed endpoints belong to Rig_Root. The seated body and
        # board still move together beneath them in Scene_Swing.
        self.scene,self.chest,self.head=scene,chest,head
        return root,chest,head

    def _joints(self,side):
        defaults={"l":{"shoulder":[.434,.390],"elbow":[.439,.465],"wrist":[.495,.518]},
                  "r":{"shoulder":[.568,.390],"elbow":[.572,.463],"wrist":[.526,.518]}}
        configured=self.manifest.get("joints",{}).get(side,defaults[side])
        return tuple(configured["shoulder"]),tuple(configured["elbow"]),tuple(configured["wrist"])

    @staticmethod
    def _source_joints(side):
        """Texture-space bones are horizontal, independent of the rest pose."""
        return (((.434,.390),(.354,.390),(.274,.390)) if side=="l" else
                ((.568,.390),(.648,.390),(.728,.390)))

    def _arm(self,side):
        upper_side=side.upper()
        name=f"Arm_{upper_side}"
        if name+"_Wrist" in self.nodes:
            return {segment:name+"_"+suffix for segment,suffix in
                    [("upper","SwingUpper"),("lower","SwingLower"),("hand","HandShape") ]}
        shoulder,elbow,wrist=self._joints(side)
        sign=-1 if side=="l" else 1
        turn=self.warp(name+"_Turn",self.chest)
        self.deform("ParamBodyTurn",turn,lambda p,v:
                    (shoulder[0]+(p[0]-shoulder[0])*(1-.28*abs(v))+.026*v,
                     p[1]+.012*abs(v)))
        # One chain per segment keeps the shoulder rooted, the elbow joint
        # distinct from the wrist and the hands attached to their forearms.
        pose=self.warp(name+"_Pose",turn,13)
        self.deform("ParamPoseClimb",pose,
                    lambda p,v:rotate(p,shoulder,-sign*22*abs(v)-52*v))
        self.deform("ParamPoseTop",pose,lambda p,v:rotate(p,shoulder,-sign*105*v))
        reaction=self.warp(name+"_Reaction",pose)
        self.deform("ParamTyping",reaction,lambda p,v:rotate(p,shoulder,sign*6*v))
        self.deform("ParamListening",reaction,lambda p,v:rotate(p,shoulder,-sign*(8 if side=="r" else 2)*v))
        self.deform("ParamTypingPulse",reaction,lambda p,v:(p[0],p[1]-.003*v*clamp((p[1]-shoulder[1])/.12)))
        upper=self.warp(name+"_Upper",reaction)
        self.deform(f"ParamArm{upper_side}A",upper,lambda p,v:rotate(p,shoulder,1.5*v))
        lower=self.warp(name+"_Lower",upper)
        self.deform(f"ParamArm{upper_side}B",lower,lambda p,v:rotate(p,elbow,2.0*v))
        hand=self.warp(name+"_Wrist",lower)
        # Rope contact correction is expressed before the shared pendulum
        # parent. At both ends and intermediate keys the palm follows the
        # authored rope, including its two independent fixed suspension ends.
        def correction(p,value):
            target=palm_on_rope(side,value)
            source=palm_on_rope(side,0)
            local=rotate(target,(.508,0),-8*value)
            return p[0]+local[0]-source[0],p[1]+local[1]-source[1]
        self.deform("ParamSwing",hand,correction)
        lengths=(math.dist(shoulder,elbow),math.dist(elbow,wrist))
        goal=palm_on_rope(side,0)
        target_elbow=two_bone_elbow(shoulder,goal,lengths,-sign)
        segments={}
        for segment,base,a,b,c,d in [
                ("upper",upper,shoulder,elbow,shoulder,target_elbow),
                ("lower",lower,elbow,wrist,target_elbow,goal),
                ("hand",hand,elbow,wrist,target_elbow,goal)]:
            suffix={"upper":"SwingUpper","lower":"SwingLower","hand":"SwingHand"}[segment]
            node=self.warp(name+"_"+suffix,base,13)
            def reshape(point,value,a=a,b=b,c=c,d=d):
                target_a=tuple(x+(y-x)*value for x,y in zip(a,c))
                target_b=tuple(x+(y-x)*value for x,y in zip(b,d))
                return segment_transform(point,a,b,target_a,target_b)
            self.deform("ParamPoseSwing",node,reshape)
            segments[segment]=node
        hand_shape=self.warp(name+"_HandShape",segments["hand"])
        self.deform(f"ParamHand{upper_side}Shape",hand_shape,lambda p,v:rotate(p,wrist,sign*4*v))
        segments["hand"]=hand_shape
        return segments

    def _view_binding(self,layer):
        family,view=layer.get("family"),layer.get("view")
        if not family or view not in VIEW_ANGLES:
            return
        name=layer["id"]
        param="ParamHeadTurn" if layer.get("zone")=="head" else "ParamBodyTurn"
        pose=layer.get("pose","rest")
        # Visibility is inherited from the common view warp. Keying every
        # child mesh would multiply all blink/gaze forms by all turn keys.

    def _view_parent(self,layer,parent):
        view,zone=layer.get("view"),layer.get("zone")
        if not layer.get("family") or view not in VIEW_ANGLES:
            return parent
        name=f"View_{zone}_{view}"
        if name not in self.nodes:
            self.warp(name,parent,65)
            self.deform("ParamHeadTurn" if zone=="head" else "ParamBodyTurn",name,
                        lambda point,value:self._registered_point(point,view,value,zone))
            if zone == 'head':
                refine_head_warp_sampling(self, name, view)
            for key in self.params["ParamHeadTurn" if zone=="head" else "ParamBodyTurn"].keyforms:
                key.deformer_opacity_overrides[name]=view_weight(view,key.value,{l.get('view') for l in self.layers})
        return name

    def _active_arm(self,side,segment):
        """Joint-to-joint cloth mapping with gravity-biased shared seams.

        The donor is split at its elbow. Unlike a rigid image rotation, this
        keeps sleeve fabric below the arm and the wrist shared by all hands.
        Its fixed suspension parent deliberately excludes gaze/breath warps.
        """
        name=f"ActiveArm_{side}_{segment}"
        if name in self.nodes:
            return name
        shoulder,elbow,wrist=self._joints(side)
        source_shoulder,source_elbow,source_wrist=self._source_joints(side)
        a,b=(source_shoulder,source_elbow) if segment=="upper" else (source_elbow,source_wrist)
        node=self.warp(name,self.scene,17)
        source_points=list(self.nodes[node].grid_vertices)
        def joints(climb=0.,top=0.):
            # One constrained transfer path. abs(climb) is the remaining
            # wall phase, so the two directions share exact swing endpoints.
            goal=palm_on_rope(side,0)
            swing=swing_arm_joints(self,side)
            if abs(climb)<1e-10:
                s,e,w=swing
            else:
                direction=1 if climb>0 else -1
                near=(side=="l" and direction>0) or (side=="r" and direction<0)
                sx=.429 if near else .487
                wx=.687 if near else .675
                ex=.539 if near else .552
                if direction<0:
                    sx=.553 if near else .516
                    wx,ex=1.016-wx,1.016-ex
                wall=((sx,.426 if near else .415),(ex,.46 if near else .51),
                      (wx,.344 if near else .446))
                t=(1-abs(climb))*1.8
                body=smoothstep((t-.32)/(1.65-.32))
                s=tuple(a+(b-a)*body for a,b in zip(wall[0],shoulder))
                if near:
                    # Keep the actual high wall hand fixed until release;
                    # route it outside/below the face before the second grip.
                    route=[(.0,wall[2]),(.85,wall[2]),(1.12,(wall[2][0],.56)),
                           (1.45,(goal[0],.54)),(1.65,goal),(1.8,goal)]
                else:
                    first_grip=palm_on_rope(side,0,y=.43)
                    route=[(0.,wall[2]),(.32,first_grip),(.85,first_grip),
                           (1.65,goal),(1.8,goal)]
                for (ta,wa),(tb,wb) in zip(route,route[1:]):
                    if t<=tb+1e-10:
                        f=smoothstep((t-ta)/(tb-ta))
                        w=tuple(a+(b-a)*f for a,b in zip(wa,wb))
                        break
                # Elbow remains between shoulder and wrist, with a stable
                # downward bend; never add a second complete pose delta.
                target_e=((s[0]+w[0])/2, max(s[1],w[1])+.025)
                move=smoothstep(t/(.32 if not near else 1.65))
                e=tuple(a+(b-a)*move for a,b in zip(wall[1],target_e))
                settle=smoothstep((t-1.45)/.20)
                e=tuple(a+(b-a)*settle for a,b in zip(e,swing[1]))
            if top and side=="r":
                e=(e[0]+.060*top,e[1]-.12*top)
                w=(w[0]+.110*top,w[1]-.24*top)
            return s,e,w
        def mapping(p,js,axis_js=None,roundness=0.):
            s,e,w=js
            if segment=="hand":
                return p[0]+w[0]-wrist[0],p[1]+w[1]-wrist[1]
            if self.rounded_supported_sleeves:
                return full_sleeve_point(p,self._source_joints(side),js,
                                         roundness=roundness,axis_joints=axis_js)
            c,d=(s,e) if segment=="upper" else (e,w)
            u=(p[0]-a[0])/(b[0]-a[0])
            drop=p[1]-(a[1]+u*(b[1]-a[1]))
            bs,be,bw=axis_js or js
            def drape(first,last):
                dx,dy=last[0]-first[0],last[1]-first[1]
                length=max(1e-8,math.hypot(dx,dy))
                nx,ny=-dy/length,dx/length
                if ny<0:
                    nx,ny=-nx,-ny
                return nx*.7,.3+ny*.7
            upper_axis,lower_axis=drape(bs,be),drape(be,bw)
            elbow_axis=tuple((x+y)/2 for x,y in zip(upper_axis,lower_axis))
            ca,da=(upper_axis,elbow_axis) if segment=="upper" else (elbow_axis,lower_axis)
            axis=tuple(x+(y-x)*u for x,y in zip(ca,da))
            return c[0]+u*(d[0]-c[0])+axis[0]*drop,c[1]+u*(d[1]-c[1])+axis[1]*drop
        # Register the uncompressed canonical texture once into the rest
        # pose. Every parameter contributes only its displacement from that
        # same rest geometry; adding three complete registrations would
        # otherwise triple the source-to-rest displacement.
        rest_joints=(shoulder,elbow,wrist)
        baseline=[mapping(point,rest_joints) for point in source_points]
        self.nodes[node].grid_vertices=baseline
        for key in self.params["ParamPoseClimb"].keyforms:
            key.deformer_offsets[node]=[
                (target[0]-base[0],target[1]-base[1])
                for point,base in zip(source_points,baseline)
                for target in [mapping(point,joints(climb=key.value),
                    roundness=1-smoothstep(abs(key.value)/.30))]]
        for key in self.params["ParamPoseTop"].keyforms:
            key.deformer_offsets[node]=[
                (target[0]-base[0],target[1]-base[1])
                for point in source_points
                for base in [mapping(point,joints(),roundness=1)]
                for target in [mapping(point,joints(top=key.value),joints(),roundness=1)]]
        for key in self.params["ParamArmPoseMode"].keyforms:
            key.deformer_offset_weights[node]=key.value
        # Small gait motions move the same endpoint on both sleeve and hand.
        movement=self.warp(name+"_Movement",self.scene,13)
        self.nodes[node].parent=movement
        self.deform(f"ParamArm{side.upper()}A",movement,lambda p,v:(p[0],p[1]+.0012*v))
        self.deform(f"ParamArm{side.upper()}B",movement,lambda p,v:(p[0]+.0005*v,p[1]-.0008*v))
        correction=self.warp(name+"_RopeCorrection",self.scene,
                             2 if self.rounded_supported_sleeves else 13)
        self.nodes[movement].parent=correction
        if not self.rounded_supported_sleeves:
            def correct(p,value):
                target=palm_on_rope(side,value)
                source=palm_on_rope(side,0)
                local=rotate(target,(.508,0),-8*value)
                swing_elbow_x=swing_arm_joints(self,side)[1][0]
                weight=(p[0]-shoulder[0])/(swing_elbow_x-shoulder[0]) if segment=="upper" else 1.
                return p[0]+(local[0]-source[0])*weight,p[1]+(local[1]-source[1])*weight
            self.deform("ParamSwing",correction,correct)
            return node
        # The old upper-only X weight agreed with the lower translation at
        # one elbow point, but separated the descending cloth seam by several
        # pixels during a swing. Use ONE affine field on the complete arm.
        # Fit its endpoints to the actual source-lattice registration rather
        # than nominal joint coordinates; affine prewarping then preserves
        # the original native wrist displacement exactly on both pieces.
        from native_warp_sampling import warp_points
        reference_grid=[full_sleeve_point(p,self._source_joints(side),
                        swing_arm_joints(self,side),roundness=1) for p in source_points]
        fixed,grip=warp_points([source_shoulder,source_wrist],reference_grid,17)
        axis=grip-fixed
        axis_length2=float(axis@axis)
        if axis_length2<1e-10:
            raise ValueError('Supported sleeve shoulder and wrist coincide')
        def correct(p,value):
            target=palm_on_rope(side,value)
            source=palm_on_rope(side,0)
            local=rotate(target,(.508,0),-8*value)
            weight=((p[0]-fixed[0])*axis[0]+(p[1]-fixed[1])*axis[1])/axis_length2
            return p[0]+(local[0]-source[0])*weight,p[1]+(local[1]-source[1])*weight
        self.deform("ParamSwing",correction,correct)
        return node

    def _hand_binding(self,layer):
        shape=layer.get("hand_shape")
        if shape is None:
            return
        side=layer.get("side","l").upper()
        desired={"rest":0,"support":-1,"grip":1}[shape]
        self.opacity(f"ParamHand{side}Shape",layer["id"],lambda value:max(0,1-abs(value-desired)))

    def _arm_visibility(self,layer):
        variant=layer.get("arm_variant")
        if variant is None and layer["role"].startswith("hand_"):
            variant="active"
        if variant:
            self.opacity("ParamArmPoseMode",layer["id"],
                         (lambda value:1-smoothstep(value/.05)) if variant=="rest" else (lambda value:smoothstep(value/.05)))

    def _pose_binding(self,layer):
        pose=layer.get("pose","rest")
        if pose=="swing":
            self.opacity("ParamPoseSwing",layer["id"],lambda v:smoothstep((v-.82)/.10))
        elif layer.get("has_seated_variant"):
            self.opacity("ParamPoseSwing",layer["id"],lambda v:1. if v<.92 else 0.)

    def _neck_binding(self,layer):
        """Two standard mesh axes share a single weighted neck registration.

        Static vertices must stay in source space: Cubism recalculates texture
        UVs from CArtMeshSource.positions, even in TEXTURE_ATLAS mode. Put the
        neutral registration into the head axis's ordinary keyform offsets;
        every rendered form still equals N(head,0)+N(0,body)-N(0,0).
        """
        name,view=layer["id"],layer["view"]
        mesh=self.meshes[name]
        source=list(mesh.vertices)
        base=[neck_point(p,view,0,0) for p in source]
        for param,index in [("ParamHeadTurn",0),("ParamBodyTurn",1)]:
            for key in self.params[param].keyforms:
                values=[0.,0.];values[index]=key.value
                key.mesh_offsets[name]=[(q[0]-b[0],q[1]-b[1])
                    for p,b in zip(source,source if index==0 else base)
                    for q in [neck_point(p,view,*values)]]
                if index==1:
                    key.opacity_overrides[name]=view_weight(view,key.value)

    def _standing_leg(self,layer,parent):
        side=layer["role"][-1].upper()
        name=f"StandingLeg_{side}_{layer.get('view','front')}"
        if name+"_Knee" in self.nodes:
            return name+"_Knee"
        hip=(.470 if side=="L" else .548,.57)
        knee=(hip[0],.75)
        upper=self.warp(name+"_Hip",parent,13)
        self.deform(f"ParamLeg{side}A",upper,lambda p,v:rotate(p,hip,v))
        lower=self.warp(name+"_Knee",upper,13)
        self.deform(f"ParamLeg{side}B",lower,lambda p,v:rotate(p,knee,v*1.1*clamp((p[1]-.75)/.15)))
        self.deform("ParamPoseClimb",lower,lambda p,v:(p[0]+.016*v,p[1]-.025*abs(v)))
        return lower

    def _load_layers(self,root,chest,head):
        for layer in sorted(self.layers,key=lambda item:item["draw_order"]):
            name,role=layer["id"],layer["role"]
            head_parent=self._view_parent(layer,head) if layer.get("zone")=="head" else head
            body_parent=self._view_parent(layer,chest) if layer.get("zone")=="body" else chest
            box=self.layer_bounds[name]
            bounds=tuple(max(0.,v-2/self.width) if i<2 else min(1.,v+2/self.width) for i,v in enumerate(box))
            if layer.get("arm_variant")=="active" and layer.get("segment")=="lower":
                wrist=self._source_joints(layer["side"])[2]
                bounds=(min(bounds[0],wrist[0]-2/self.width),min(bounds[1],wrist[1]-2/self.height),
                        max(bounds[2],wrist[0]+2/self.width),max(bounds[3],wrist[1]+2/self.height))
            small=role.startswith(("eye","pupil","mouth","blush"))
            self.meshes[name]=grid_mesh(name,bounds,9 if small else 17,9 if small else 17)
            self.textures.append(Texture(id="Texture_"+name,path=layer["file"],width=self.width,height=self.height))
            if name in ("rope_l","rope_r"):
                parent=root
                side=name[-1]
                self.deform("ParamSwing",name,lambda p,v,side=side:hanging_rope_point(p,side,v),mesh=True)
            elif name=="seat":
                parent=self.scene
            elif layer.get("pose")=="swing":
                parent="Body_Transition"
                if role.startswith("leg_"):
                    side=layer["side"].upper()
                    knee=tuple(layer["pivot"])
                    hip=tuple(layer["hip"])
                    upper=self.warp("SeatedLeg_"+side+"_Hip",parent,13)
                    self.deform(f"ParamLeg{side}A",upper,lambda p,v,hip=hip:rotate(p,hip,v*.8))
                    parent=self.warp("SeatedLeg_"+side+"_Knee",upper,13)
                    self.deform(f"ParamLeg{side}B",parent,lambda p,v,knee=knee:rotate(p,knee,(v-6)*.8*clamp((p[1]-knee[1])/.1)))
            elif role.startswith(("arm_","hand_")):
                side=layer.get("side",role[-1])
                segment=layer.get("segment","hand" if role.startswith("hand_") else "lower")
                parent=(self._active_arm(side,segment) if layer.get("arm_variant")=="active" or
                        role.startswith("hand_")
                        else self._arm(side)[segment])
                if layer.get("cloth"):
                    cloth=self.warp("Sleeve_"+name,parent,13)
                    pivot=self.pivot(layer,self._joints(side)[1])
                    shoulder,elbow,wrist=(self._source_joints(side) if layer.get("arm_variant")=="active"
                                          else self._joints(side))
                    a,b=(shoulder,elbow) if segment=="upper" else (elbow,wrist)
                    def cloth_sway(point,value,pivot=pivot,a=a,b=b):
                        if layer.get("arm_variant")=="active":
                            u=(point[0]-a[0])/(b[0]-a[0])
                            drop=point[1]-(a[1]+u*(b[1]-a[1]))
                            weight=clamp(drop/.16)
                            # Preserve the common vertical source elbow
                            # boundary. Separate rotations around the two
                            # bone roots pull the same cut edge apart.
                            return point[0],point[1]+.008*value*weight
                        else:
                            weight=clamp((point[1]-pivot[1]+.02)/.18)
                        return rotate(point,pivot,3*value*weight)
                    self.deform(f"ParamSleeve{side.upper()}Hang",cloth,
                                cloth_sway)
                    parent=cloth
            elif role=="neck":
                parent=chest
                self._neck_binding(layer)
            elif role.startswith("leg_"):
                parent=self._standing_leg(layer,body_parent)
            elif layer.get("zone")=="head" or role.startswith("hair_"):
                parent=head_parent
                if role.startswith("hair_"):
                    size = HEAD_HAIR_SWAY_GRID if role == 'hair_front' and layer.get('zone') == 'head' else 13
                    parent=self.warp("Sway_"+name,head_parent,size)
                    pivot=self.pivot(layer,(.508,.22))
                    driver={"hair_front":"ParamHairFront","hair_side":"ParamHairSide","hair_back":"ParamHairBack"}[role]
                    self.deform(driver,parent,lambda p,v,pivot=pivot:rotate(p,pivot,2.2*v*clamp((p[1]-pivot[1]+.04)/.4)))
            elif role in ("torso","neck","clothing"):
                parent=body_parent if layer.get("zone")!="head" else head_parent
                if role=="clothing":
                    parent=self.warp("Fabric_"+name,body_parent)
                    self.deform("ParamSkirt",parent,lambda p,v:(p[0]+.009*v*clamp((p[1]-.54)/.4)**2,p[1]))
            elif name in ("fan","maple","microphone","mic"):
                parent=self._arm("r")["hand"]
            else:
                parent=head_parent
            self.parts.append(Part(id=name,semantic_role=SemanticRole(role),texture_id="Texture_"+name,
                                   draw_order=layer_draw_order(layer),parent_deformer=parent,opacity=1,
                                   clip_to=layer.get("clip_to",[]),invert_clipping=layer.get("invert_clipping",False)))
            self._facial_binding(layer)
            self._view_binding(layer)
            self._hand_binding(layer)
            self._arm_visibility(layer)
            self._pose_binding(layer)
            if layer.get("occlusion_side"):
                side=layer["occlusion_side"]
                for key in self.params["ParamBodyTurn"].keyforms:
                    near=(side=="l" and key.value>0) or (side=="r" and key.value<0)
                    key.draw_order_overrides[name]=int(layer.get("near_order",160) if near else
                                                        layer.get("far_order",75) if key.value else layer["draw_order"])
            prop={"glasses":"ParamGlasses","fan":"ParamFan","maple":"ParamMaple",
                  "rope_l":"ParamSwingVisible","rope_r":"ParamSwingVisible","seat":"ParamSwingVisible"}.get(name)
            if prop:
                self.opacity(prop,name,lambda v:v)
            elif role=="blush":
                self.opacity("ParamCheek",name,lambda v:v)

    def build(self):
        self._load_layers(*self._hierarchy())
        refine_ornament_material_meshes(self)
        apply_head_view_exchange(self, original_view_coverage, view_weight, PAINT_ORDER)
        physics=[PhysicsRig(id="Physics_"+suffix,driver_param="ParamAngleX",extra_drivers=["ParamAngleZ"],
                            output_param="ParamHair"+suffix,drag=drag,length=length)
                 for suffix,drag,length in [("Front",.3,.55),("Side",.25,.8),("Back",.2,1.1)]]
        physics.append(PhysicsRig(id="Physics_Skirt",driver_param="ParamBodyAngleX",extra_drivers=["ParamLegLA"],
                                  output_param="ParamSkirt",drag=.4,length=.6))
        for side in ("L","R"):
            physics.append(PhysicsRig(id="Physics_Sleeve"+side,driver_param=f"ParamArm{side}A",
                                      extra_drivers=["ParamSwing"],output_param=f"ParamSleeve{side}Hang",drag=.35,length=.5))
        rig=Rig(meta={"name":"Maple","source_image":self.manifest.get("source")},textures=self.textures,
                parts=self.parts,meshes=list(self.meshes.values()),deformers=list(self.nodes.values()),
                parameters=list(self.params.values()),physics=physics)
        audit_rig(rig)
        return rig


def metadata_v4(rig,manifest):
    metadata=metadata_for(rig,manifest)
    metadata["authoringSource"]="../../authoring/revisions/v4/Maple.cmo3"
    metadata["modelVersion"]="v4"
    metadata["anchors"].update({"gripLeft":{"drawable":"hand_l_relaxed"},"gripRight":{"drawable":"hand_r_relaxed"},
                                "left":{"drawable":"hand_l_relaxed"},"right":{"drawable":"hand_r_relaxed"},
                                "hangerLeft":{"drawable":"rope_l","edge":"top"},
                                "hangerRight":{"drawable":"rope_r","edge":"top"},
                                "hang":{"point":[.508,0]},"top":{"point":[.508,0]}})
    metadata["transitions"]={state:{"duration":1.8,"playback":"one_shot","to":"swing_cycle",
                                    "events":[{"time":t,"name":name} for t,name in TRANSITION_EVENTS]}
                             for state in TRANSITIONS}
    metadata["capabilities"]["internalMotions"]=list(TRANSITIONS)
    metadata["capabilities"]["independentProfileTurn"]=True
    metadata["capabilities"]["fixedRopeSuspension"]=True
    # The near raised support palm is the outermost wall contact. Both the
    # public climb and internal transfer expose the identical live edge so
    # the host captures one continuous world point before changing support.
    for side,hand in [("left","r"),("right","l")]:
        anchor={"drawable":f"hand_{hand}_support","edge":side}
        grip="gripLeft" if side=="left" else "gripRight"
        for state in [f"climb_{side}",f"clean_climb_{side}",f"climb_to_top_{side}"]:
            metadata["states"].setdefault(state,{}).setdefault("anchors",{}).update({side:anchor,grip:anchor})
    metadata["refinement"]={
        "version":4,"publicMotionCount":21,"internalMotionCount":2,
        "requiredParameters":["ParamHeadTurn","ParamBodyTurn","ParamHandLShape","ParamHandRShape",
                              "ParamTransitionProgress","ParamArmPoseMode","ParamPoseSwing","ParamSwing","ParamSwingVisible"],
        "neck":{"materialParameter":"ParamBodyTurn","geometryParameters":["ParamHeadTurn","ParamBodyTurn"],
                "views":{"front":"neck","left":"profile_l_neck","right":"profile_r_neck",
                         "leftMid":"mid_l_neck","rightMid":"mid_r_neck"}},
        "turn":{"range":[-1,1],"endpointAngleDegrees":78,"gazeSeparate":True,
                "materialBlendTurnWidth":.10,"materialBlend":"opaque-lower-surface-normal-over",
                "materialDrawOrderRanks":PAINT_ORDER,
                "front":["face_base","torso"],"left":["profile_l_face_base","profile_l_torso"],
                "right":["profile_r_face_base","profile_r_torso"],
                "leftMid":["mid_l_face_base","mid_l_torso"],"rightMid":["mid_r_face_base","mid_r_torso"]},
        "hands":{side:{"parameter":f"ParamHand{side.upper()}Shape","rest":[f"hand_{side}",f"hand_{side}_relaxed"],
                        "support":[f"hand_{side}_support"],
                        "grip":[f"hand_{side}_grip_palm",f"hand_{side}_grip_fingers"],
                        "values":{"support":-1,"rest":0,"grip":1},
                        "gripPartition":"complementary pixels, one hand"} for side in ("l","r")},
        "seated":{"parameter":"ParamPoseSwing","standing":["skirt","costume_underlay_skirt","leg_l","leg_r"],
                  "seated":["skirt_swing","leg_l_swing","leg_r_swing"],"torsoContinuous":True},
        "suspension":{"ropes":["rope_l","rope_r"],"seat":"seat","fixedTopY":0,
                      "gripDrawOrder":{"palm":152,"rope":154,"fingers":166}},
        "occlusion":{"parts":["arm_l","arm_r"],"near":158,"far":68},
        "armPoseMode":{"parameter":"ParamArmPoseMode","rest":0,"active":1},
        "contacts":{side:{"sourceUv":list(wrist),"canvasSize":[1024,1024],
                          "handSourceUv":list(wrist),
                          "cuffSourceUv":list(V4Builder._source_joints(side)[2]),
                          "cuff":f"sleeve_{side}_lower",
                          "hands":[f"hand_{side}_relaxed",f"hand_{side}_support",f"hand_{side}_grip_palm"],
                          "atlasUvRequiresPackingPlacement":True}
                    for side,wrist in [("l",(.495,.518)),("r",(.526,.518))]},
        "nativeEditorExportRequired":True,"visualAcceptanceRequired":True}
    return metadata


def build_v4(manifest_path,output=None,runtime=None):
    manifest_path=Path(manifest_path).resolve()
    manifest=json.loads(manifest_path.read_text(encoding="utf8"))
    if manifest.get("version")!=4:
        raise ValueError("Explicit v4 registered-layer manifest required")
    builder=V4Builder(manifest,manifest_path.parent)
    rig=builder.build()
    output=Path(output or manifest_path.parent).resolve()
    runtime=Path(runtime or output/"runtime").resolve()
    if output==ROOT/"assets/authoring" or output==ROOT/"assets/authoring/revisions/v3":
        raise ValueError("v4 may not replace preserved source projects")
    output.mkdir(parents=True,exist_ok=True)
    (runtime/"motions").mkdir(parents=True,exist_ok=True)
    (output/"Maple.rig.json").write_text(rig.model_dump_json(indent=2),encoding="utf8")
    project=corrected_project(rig,manifest_path.parent)
    (output/"Maple.cmo3").write_bytes(project)
    motions={**ANIMATIONS,**TRANSITIONS}
    defaults={p.id:p.default for p in rig.parameters}
    for state,spec in motions.items():
        (runtime/"motions"/f"{state}.motion3.json").write_text(json.dumps(build_motion(state,spec,defaults),indent=2),encoding="utf8")
    (runtime/"Maple.pet.json").write_text(json.dumps(metadata_v4(rig,manifest),ensure_ascii=False,indent=2),encoding="utf8")
    (runtime/"Maple.physics3.json").write_text(json.dumps(physics3(rig),indent=2),encoding="utf8")
    fragment={"FileReferences":{"Motions":motion_references(motions),"Physics":"Maple.physics3.json"},
              "Groups":[{"Target":"Parameter","Name":"EyeBlink","Ids":["ParamEyeLOpen","ParamEyeROpen"]}],
              "CuteMaple":{"Metadata":"Maple.pet.json"}}
    (output/"Maple.model3-fragment.json").write_text(json.dumps(fragment,indent=2),encoding="utf8")
    report={"status":"requires_native_Editor_export_and_visual_acceptance","version":4,
            "layers":len(rig.parts),"parameters":len(rig.parameters),"deformers":len(rig.deformers),
            "motionGroups":list(motions),"projectSha256":hashlib.sha256(project).hexdigest(),
            "geometryAxes":audit_rig(rig),"runtimeStaging":str(runtime)}
    report["viewRegistration"]=validate_registration()
    report["warpControlPoints"]={node.id:node.grid_rows*node.grid_cols for node in rig.deformers}
    report["warpControlPointTotal"]=sum(report["warpControlPoints"].values())
    report["performanceAcceptance"]="Native Core frame time and RSS must be measured after Editor export; dense shared silhouette warps are provisional."
    (output/"Maple.build-audit.json").write_text(json.dumps(report,indent=2),encoding="utf8")
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest",nargs="?",type=Path,default=ROOT/"assets/authoring/revisions/v4/layers.json")
    parser.add_argument("--output",type=Path)
    parser.add_argument("--runtime",type=Path)
    args=parser.parse_args()
    print(json.dumps(build_v4(args.manifest,args.output,args.runtime),indent=2))
