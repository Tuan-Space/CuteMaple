"""Build Maple's editable Cubism project from registered source-art layers.

This tool reads PNGs without changing their pixels. It emits the auditable IRR,
an editable CAFF .cmo3, continuous motion3 clips, physics, and model metadata.
It NEVER emits a .moc3: that binary must be exported by Live2D Cubism Editor.

The third-party writer is used only for project serialization. Geometry,
hierarchy, parameter bindings and choreography are authored here. Its known
six-mesh-axis cap is checked before serialization, and its hard-coded draw
orders are corrected afterward. Editor import and visual export verification
remain a separate required step; a successful build is not that verification.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent / "vendor"))
sys.path.insert(0, str(ROOT))
from image2live2d.irr.schema import (Deformer, DeformerType, Keyform, Mesh, Meta,
                                     Parameter, Part, PhysicsRig, Rig, SemanticRole, Texture)
from image2live2d.backends.live2d.cmo3 import pack_caff, rig_to_cmo3, unpack_caff
from image2live2d.backends.live2d.physics3 import physics3
from pet_core import ANIMATIONS
try:
    from .maple_motions import build_motion, motion_references
except ImportError:
    from maple_motions import build_motion, motion_references


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def rotate(point, pivot, angle):
    x, y = point[0] - pivot[0], point[1] - pivot[1]
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return pivot[0] + c*x - s*y, pivot[1] + s*x + c*y


def lattice(bounds=(0., 0., 1., 1.), cols=9, rows=9):
    left, top, right, bottom = bounds
    return [(left + (right-left)*x/(cols-1), top + (bottom-top)*y/(rows-1))
            for y in range(rows) for x in range(cols)]


def grid_mesh(part_id, bounds, cols=13, rows=13):
    vertices = lattice(bounds, cols, rows)
    triangles = []
    for y in range(rows-1):
        for x in range(cols-1):
            a = y*cols+x
            triangles.extend([(a, a+1, a+cols), (a+1, a+cols+1, a+cols)])
    return Mesh(part_id=part_id, vertices=vertices, uvs=vertices, triangles=triangles)


class MapleBuilder:
    def __init__(self, manifest, asset_root):
        self.manifest, self.asset_root = manifest, Path(asset_root).resolve()
        self.width, self.height = int(manifest["width"]), int(manifest["height"])
        self.layers = manifest["layers"]
        if not self.layers or len({l["id"] for l in self.layers}) != len(self.layers):
            raise ValueError("Layer ids must be unique and the manifest nonempty")
        self.params: dict[str, Parameter] = {}
        self.nodes: dict[str, Deformer] = {}
        self.meshes: dict[str, Mesh] = {}
        self.parts: list[Part] = []
        self.textures: list[Texture] = []
        self.layer_by_id = {layer["id"]: layer for layer in self.layers}
        self._parameters()

    def parameter(self, name, low=-1., high=1., default=0., keys=None):
        keys = keys if keys is not None else sorted(set([low, default, high]))
        self.params[name] = Parameter(id=name, min=low, max=high, default=default,
                                      keyforms=[Keyform(value=v) for v in keys])

    def _parameters(self):
        for suffix in ("X", "Y", "Z"):
            self.parameter("ParamAngle"+suffix, -30, 30)
            self.parameter("ParamBodyAngle"+suffix, -10, 10)
        for side in ("L", "R"):
            self.parameter(f"ParamEye{side}Open", 0, 1, 1, [0, .25, 1])
            self.parameter(f"ParamEye{side}Smile", 0, 1)
            self.parameter(f"ParamBrow{side}Y")
            self.parameter(f"ParamBrow{side}Form")
            for limb in ("Arm", "Leg"):
                for joint in ("A", "B"):
                    self.parameter(f"Param{limb}{side}{joint}", -10, 10)
        for name in ("EyeBallX", "EyeBallY", "MouthForm", "HairFront", "HairSide", "HairBack", "Skirt"):
            self.parameter("Param"+name)
        for name in ("MouthOpenY", "Cheek", "Breath", "Typing", "TypingPulse", "Listening",
                     "Dizzy", "Glasses", "Fan", "Maple", "PoseSleep", "PoseSwing", "PoseTop",
                     "Crouch", "Bounce", "SwingVisible", "Cleaning"):
            self.parameter("Param"+name, 0, 1)
        self.parameter("ParamPoseClimb")
        self.parameter("ParamSwing")
        # Intermediate shoulder poses preserve shape during larger rotations:
        # linear interpolation between only 0 and 145 degrees would shrink arms.
        self.parameter("ParamPoseTop",0,1,keys=[0,.25,.5,.75,1])
        self.parameter("ParamPoseSwing",0,1,keys=[0,.5,1])
        self.parameter("ParamPoseClimb",keys=[-1,-.5,0,.5,1])

    def warp(self, name, parent=None, grid=9):
        self.nodes[name] = Deformer(id=name, type=DeformerType.warp, parent=parent,
                                   grid_cols=grid, grid_rows=grid, grid_vertices=lattice(cols=grid, rows=grid))
        return name

    def deform(self, param, target, transform, *, mesh=False):
        points = self.meshes[target].vertices if mesh else self.nodes[target].grid_vertices
        for key in self.params[param].keyforms:
            destination = key.mesh_offsets if mesh else key.deformer_offsets
            destination[target] = [(q[0]-p[0], q[1]-p[1])
                                   for p in points for q in [transform(p, key.value)]]

    def opacity(self, param, target, fn):
        for key in self.params[param].keyforms:
            key.opacity_overrides[target] = clamp(fn(key.value))

    def pivot(self, layer, fallback):
        value = layer.get("pivot", fallback)
        # Existing manifest uses normalized pivots; accept pixel pivots as well.
        return (float(value[0])/self.width, float(value[1])/self.height) if max(map(abs, value)) > 1 else tuple(value)

    def _hierarchy(self):
        scene = self.warp("Scene_Swing")
        self.deform("ParamSwing", scene, lambda p, v: rotate(p, (.5078, 0), v*8))
        sleep = self.warp("Body_SeatedSleep", scene)
        self.deform("ParamPoseSleep", sleep,
                    lambda p, v: (p[0]+v*(p[0]-.508)*.06,
                                  p[1]+v*.23*(1-clamp((p[1]-.48)/.46))))
        sit = self.warp("Body_SeatedSwing", sleep)
        self.deform("ParamPoseSwing", sit,
                    lambda p, v: (p[0], p[1]-.14*v*clamp((p[1]-.51)/.44)))
        posture = self.warp("Body_Weight", sit)
        self.deform("ParamCrouch", posture,
                    lambda p, v: (p[0]+(p[0]-.508)*.04*v, .934+(p[1]-.934)*(1-.10*v)))
        self.deform("ParamBounce", posture, lambda p,v: (p[0], p[1]-.045*v))
        self.deform("ParamPoseTop", posture, lambda p,v: (p[0], .5+(p[1]-.5)*(1-.06*v)))
        lean = self.warp("Body_TurnLean", posture)
        self.deform("ParamBodyAngleX", lean,
                    lambda p,v: (p[0]+.0012*v*(1-clamp((p[1]-.38)/.58)), p[1]))
        self.deform("ParamBodyAngleY", lean,
                    lambda p,v: (p[0], .89+(p[1]-.89)*(1-.0015*v)))
        self.deform("ParamBodyAngleZ", lean, lambda p,v: rotate(p, (.508,.89), .6*v))
        chest = self.warp("Body_Breath", lean)
        self.deform("ParamBreath", chest,
                    lambda p,v: (p[0]+(p[0]-.508)*.015*v*math.exp(-((p[1]-.44)/.20)**2),
                                  p[1]-.004*v*(1-clamp((p[1]-.42)/.25))))
        head = self.warp("Head_Turn", chest, 11)
        self.deform("ParamAngleX", head,
                    lambda p,v: (p[0]+.018*v/30*math.exp(-((p[1]-.29)/.23)**2)
                                  -.04*abs(v)/30*(p[0]-.51), p[1]+.002*v/30*(p[0]-.51)))
        self.deform("ParamAngleY", head,
                    lambda p,v: (p[0], p[1]+.011*v/30*math.exp(-((p[1]-.3)/.2)**2)))
        self.deform("ParamAngleZ", head, lambda p,v: rotate(p, (.5107,.365), .4*v))
        expression = self.warp("Head_Reactions", head)
        self.deform("ParamTyping", expression, lambda p,v: rotate(p, (.5107,.365), -2*v))
        self.deform("ParamListening", expression, lambda p,v: rotate(p, (.5107,.365), 3*v))
        self.deform("ParamDizzy", expression, lambda p,v: rotate(p, (.5107,.365), -6*v))
        return scene, chest, expression

    def _limb_parent(self, layer, chest):
        role, name = layer["role"], layer["id"]
        side = "L" if role.endswith("_l") else "R"
        is_arm = role.startswith(("arm", "hand"))
        limb = "Arm" if is_arm else "Leg"
        base_name = f"{limb}_{side}_Pose"
        if base_name in self.nodes:
            return f"{limb}_{side}_Lower"
        pivot_layer = self.layer_by_id.get(f"{'arm' if is_arm else 'leg'}_{side.lower()}", layer)
        pivot = self.pivot(pivot_layer, (.44 if side=="L" else .57, .39 if is_arm else .85))
        sign = -1 if side == "L" else 1
        pose = self.warp(base_name, chest)
        if is_arm:
            self.deform("ParamPoseClimb", pose,
                        lambda p,v: rotate(p,pivot,-sign*35*abs(v)-75*v))
            self.deform("ParamPoseTop", pose, lambda p,v: rotate(p, pivot, -sign*145*v))
            self.deform("ParamPoseSwing", pose, lambda p,v: rotate(p, pivot, -sign*55*v))
            react = self.warp(f"{limb}_{side}_Reaction", pose)
            self.deform("ParamTyping", react, lambda p,v: rotate(p, pivot, sign*9*v))
            self.deform("ParamListening", react,
                        lambda p,v: rotate(p, pivot, -sign*(13 if side=="R" else 3)*v))
            self.deform("ParamTypingPulse", react,
                        lambda p,v: (p[0], p[1]-.004*v*clamp((p[1]-pivot[1])/.1)))
        else:
            self.deform("ParamPoseClimb", pose,
                        lambda p,v: (p[0]+.022*v, p[1]-.026*abs(v)))
            react = pose
        upper = self.warp(f"{limb}_{side}_Upper", react)
        self.deform(f"Param{limb}{side}A", upper,
                    lambda p,v: rotate(p, pivot, v*(1.8 if is_arm else 1.2)))
        lower = self.warp(f"{limb}_{side}_Lower", upper, 13)
        lower_pivot = self.pivot(self.layer_by_id.get(f"hand_{side.lower()}", layer),
                                 (pivot[0], pivot[1]+.09)) if is_arm else (pivot[0], pivot[1]+.045)
        self.deform(f"Param{limb}{side}B", lower,
                    lambda p,v: rotate(p, lower_pivot,
                                       v*(2.5 if is_arm else 1.3)*clamp((p[1]-pivot[1])/(.14 if is_arm else .07))))
        return lower

    def _load_layers(self, scene, chest, head):
        for layer in sorted(self.layers, key=lambda l: l["draw_order"]):
            name, role = layer["id"], layer["role"]
            if not name or any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in name):
                raise ValueError(f"Use stable ASCII drawable ids: {name!r}")
            path = (self.asset_root/layer["file"]).resolve()
            if not path.is_relative_to(self.asset_root):
                raise ValueError("Texture path escapes authoring folder")
            with Image.open(path) as im:
                if im.mode != "RGBA" or im.size != (self.width, self.height):
                    raise ValueError(f"{name} must be full-canvas RGBA")
                box = im.getchannel("A").getbbox()
            if box is None:
                raise ValueError(f"Empty layer: {name}")
            bounds = (max(0,box[0]-2)/self.width, max(0,box[1]-2)/self.height,
                      min(self.width,box[2]+2)/self.width, min(self.height,box[3]+2)/self.height)
            small = role.startswith(("eye", "pupil", "mouth", "blush"))
            self.meshes[name] = grid_mesh(name, bounds, 9 if small else 17, 9 if small else 17)
            self.textures.append(Texture(id=f"Texture_{name}", path=layer["file"],
                                         width=self.width, height=self.height))
            if role.startswith(("arm_", "hand_", "leg_")):
                parent = self._limb_parent(layer, chest)
            elif role in ("torso", "neck", "clothing"):
                parent = chest
                if role == "clothing":
                    parent = self.warp("Fabric_"+name, chest)
                    pv = self.pivot(layer, (.508,.54))
                    self.deform("ParamSkirt", parent,
                                lambda p,v,pv=pv: (p[0]+.014*v*clamp((p[1]-pv[1])/.4)**2,p[1]))
                    self.deform("ParamLegLA", parent,
                                lambda p,v: (p[0]+.0006*v*clamp((p[1]-.62)/.28),p[1]))
                    self.deform("ParamLegRA", parent,
                                lambda p,v: (p[0]+.0006*v*clamp((p[1]-.62)/.28),p[1]))
            elif role.startswith("hair_"):
                parent = self.warp("Sway_"+name, head, 13)
                pv = self.pivot(layer, (.508,.22))
                param = {"hair_front":"ParamHairFront", "hair_side":"ParamHairSide", "hair_back":"ParamHairBack"}[role]
                self.deform(param, parent,
                            lambda p,v,pv=pv: rotate(p, pv, 3*v*clamp((p[1]-pv[1]+.04)/.35)))
            elif role == "accessory":
                if name == "swing":
                    parent = scene
                elif name in ("fan", "maple", "microphone", "mic"):
                    arm = self.layer_by_id.get("arm_r")
                    parent = self._limb_parent(arm, chest) if arm else chest
                elif name == "tablet":
                    parent = chest
                else:
                    parent = head
            elif role in ("background", "other"):
                parent = scene
            else:
                parent = head
            # Hidden-at-rest layers need opacity keyforms with base opacity 1:
            # the vendor multiplies each keyed opacity by Part.opacity.
            self.parts.append(Part(id=name, semantic_role=SemanticRole(role), texture_id=f"Texture_{name}",
                                   draw_order=int(layer["draw_order"]), parent_deformer=parent, opacity=1))
            self._facial_binding(layer)
            prop = {"glasses":"ParamGlasses", "fan":"ParamFan", "maple":"ParamMaple",
                    "swing":"ParamSwingVisible", "tablet":"ParamTyping",
                    "microphone":"ParamListening", "mic":"ParamListening"}.get(name)
            if prop:
                self.opacity(prop, name, lambda v: v)
                if float(layer.get("opacity", 1)) != 0:
                    raise ValueError(f"Accessory {name} is expected hidden at rest")
            elif role == "blush":
                self.opacity("ParamCheek", name, lambda v:v)
            elif float(layer.get("opacity", 1)) == 0 and not role.startswith("eye_closed_"):
                raise ValueError(f"Hidden layer {name} lacks a visibility binding")

    def _facial_binding(self, layer):
        name, role = layer["id"], layer["role"]
        points = self.meshes[name].vertices
        pv = self.pivot(layer, (sum(p[0] for p in points)/len(points), sum(p[1] for p in points)/len(points)))
        side = "L" if role.endswith("_l") else "R"
        if role.startswith(("eye_", "eye_white_", "pupil_")) and not role.startswith("eyebrow_"):
            closed = role.startswith("eye_closed_")
            self.deform(f"ParamEye{side}Open", name,
                        lambda p,v: (p[0],pv[1]+(p[1]-pv[1])*(.15+.85*v)) if not closed else p, mesh=True)
            self.opacity(f"ParamEye{side}Open", name,
                         (lambda v:1-clamp(v/.25)) if closed else (lambda v:clamp(v/.25)))
            self.deform(f"ParamEye{side}Smile", name,
                        lambda p,v: (p[0],pv[1]+(p[1]-pv[1])*(1-.18*v)
                                      -.002*v*(1-clamp(abs(p[0]-pv[0])/.03))),mesh=True)
        if role.startswith("pupil_"):
            self.deform("ParamEyeBallX", name, lambda p,v:(p[0]+.0048*v,p[1]),mesh=True)
            self.deform("ParamEyeBallY", name, lambda p,v:(p[0],p[1]-.0035*v),mesh=True)
            self.deform("ParamDizzy", name, lambda p,v:rotate(p,pv,12*v),mesh=True)
        if role.startswith("eyebrow_"):
            self.deform(f"ParamBrow{side}Y",name,lambda p,v:(p[0],p[1]-.004*v),mesh=True)
            self.deform(f"ParamBrow{side}Form",name,
                        lambda p,v:rotate(p,pv,(8 if side=="L" else -8)*v),mesh=True)
            self.deform("ParamCleaning",name,lambda p,v:(p[0],p[1]+.0015*v),mesh=True)
        if role == "mouth":
            self.deform("ParamMouthOpenY",name,
                        lambda p,v:(p[0],pv[1]+(p[1]-pv[1])*(1+1.6*v)),mesh=True)
            self.deform("ParamMouthForm",name,
                        lambda p,v:(pv[0]+(p[0]-pv[0])*(1+.10*v),
                                    p[1]-.002*v*clamp(abs(p[0]-pv[0])/.018)),mesh=True)

    def build(self):
        self._load_layers(*self._hierarchy())
        rigs = [PhysicsRig(id="Physics_"+suffix, driver_param="ParamAngleX", extra_drivers=["ParamAngleZ"],
                           output_param="ParamHair"+suffix, drag=drag, length=length)
                for suffix,drag,length in [("Front",.30,.55),("Side",.22,.8),("Back",.18,1.25)]]
        rigs.append(PhysicsRig(id="Physics_Skirt",driver_param="ParamBodyAngleX", extra_drivers=["ParamLegLA"],
                               output_param="ParamSkirt",drag=.35,length=.7))
        rig = Rig(meta=Meta(name="Maple",source_image=self.manifest.get("source")),
                  textures=self.textures,parts=self.parts,meshes=list(self.meshes.values()),
                  deformers=list(self.nodes.values()),parameters=list(self.params.values()),physics=rigs)
        audit_rig(rig)
        return rig


def audit_rig(rig):
    """Fail loudly on silent-cap risk, excessive grids, or absent driven controls."""
    geometry_axes = {}
    for mesh in rig.meshes:
        names = [p.id for p in rig.parameters if any(
            any(abs(x)+abs(y)>1e-10 for x,y in k.mesh_offsets.get(mesh.part_id,[])) for k in p.keyforms)]
        geometry_axes[mesh.part_id] = names
        if len(names)>6:
            raise ValueError(f"Writer would discard mesh axes: {mesh.part_id}: {names}")
    for node in rig.deformers:
        params = [p for p in rig.parameters if any(node.id in k.deformer_offsets or node.id in k.deformer_opacity_overrides or node.id in k.deformer_offset_weights for k in p.keyforms)]
        if len(params)>3:
            raise ValueError(f"Excessive warp keyform product on {node.id}")
    driven = {p.id for p in rig.parameters if any(k.mesh_offsets or k.deformer_offsets or k.opacity_overrides
                                               or k.draw_order_overrides or k.deformer_opacity_overrides or k.deformer_offset_weights for k in p.keyforms)}
    missing = {p.id for p in rig.parameters}-driven
    if missing:
        raise ValueError(f"Parameters without authored geometry/visibility: {sorted(missing)}")
    return geometry_axes


def corrected_project(rig, asset_root, child_coordinates="normalized"):
    """Correct serialization details without altering source textures.

    Warp children use normalized lattice coordinates. Static rotation
    children use canvas-pixel lengths relative to the rotation origin; the
    rotation origin itself uses its parent's coordinate system. This second
    convention is verified by the native nine-frame rotation probe, including
    a sheared parent (artifacts/native-v5-rotation-probe-12/measurement.json).
    ArtMesh source positions and GEditableMesh2.point stay in canvas pixels
    so Editor can reconstruct the original texture UVs.
    """
    entries = unpack_caff(rig_to_cmo3(rig, asset_root))
    result = []
    for entry in entries:
        if entry.path != "main.xml":
            result.append(entry)
            continue
        raw = entry.content.decode("utf-8")
        offset = raw.index("<root")
        prefix, tree = raw[:offset], ET.fromstring(raw[offset:])
        part_map = {p.id:p for p in rig.parts}
        node_map = {d.id:d for d in rig.deformers}
        for source in tree.iter("CArtMeshSource"):
            id_element = source.find("./ACDrawableSource/CDrawableId")
            if id_element is None:
                continue
            name = id_element.attrib["idstr"]
            # The writer now emits real per-keyform draw order. Do not replace
            # it by the neutral value: profile near/far limbs rely on it.
            if child_coordinates=="normalized" and part_map[name].parent_deformer:
                for form in source.iter("CArtMeshForm"):
                    for coords in form.iter("float-array"):
                        if coords.get("xs.n") == "positions":
                            _parent_form_coordinates(coords,node_map[part_map[name].parent_deformer],
                                                     rig.textures[0].width,rig.textures[0].height)
        if child_coordinates=="normalized":
            for source in tree.iter("CWarpDeformerSource"):
                id_element = source.find("./ACDeformerSource/CDeformerId")
                if id_element is None or not node_map[id_element.attrib["idstr"]].parent:
                    continue
                parent=node_map[node_map[id_element.attrib["idstr"]].parent]
                for coords in source.iter("float-array"):
                    if coords.get("xs.n")=="positions":
                        _parent_form_coordinates(coords,parent,rig.textures[0].width,rig.textures[0].height)
            for source in tree.iter("CRotationDeformerSource"):
                id_element=source.find("./ACDeformerSource/CDeformerId")
                if id_element is None or not node_map[id_element.attrib["idstr"]].parent:
                    continue
                parent=node_map[node_map[id_element.attrib["idstr"]].parent]
                for form in source.iter("CRotationDeformerForm"):
                    for axis,field in enumerate(("originX","originY")):
                        size=(rig.textures[0].width,rig.textures[0].height)[axis]
                        value=float(form.get(field))
                        value=(value-parent.pivot[axis]*size if parent.type is DeformerType.rotation
                               else value/size)
                        form.set(field,f"{value:.8f}")
        result.append(replace(entry,content=(prefix+ET.tostring(tree,encoding="unicode")).encode("utf-8")))
    return pack_caff(result,key=42)


def _normalize_coordinates(element,width,height):
    values = [float(v) for v in (element.text or "").split()]
    element.text = " ".join(f"{v/(width if i%2==0 else height):.8f}" for i,v in enumerate(values))


def _parent_form_coordinates(element,parent,width,height):
    """Convert a canvas-space form into its static parent's local frame."""
    if parent.type is not DeformerType.rotation:
        _normalize_coordinates(element,width,height)
        return
    values=[float(v) for v in (element.text or "").split()]
    element.text=" ".join(f"{v-parent.pivot[i%2]*(width if i%2==0 else height):.8f}"
                          for i,v in enumerate(values))


def metadata_for(rig, manifest):
    parts = {p.id for p in rig.parts}
    anchors = {"head":{"drawable":"face_base"}, "ground":{"point":[.508,.935]},
               "left":{"drawable":"hand_l"},"right":{"drawable":"hand_r"},
               "top":{"point":[.508,.034]}}
    for name,anchor in list(anchors.items()):
        if "drawable" in anchor and anchor["drawable"] not in parts:
            del anchors[name]
    # A semantic bottom edge survives Editor mesh vertex reordering. Each
    # state's foot moves with its actual deformer hierarchy.
    foot = next((m for m in rig.meshes if m.part_id=="leg_r"),None)
    if foot:
        anchors["ground"]={"drawable":foot.part_id,"edge":"bottom"}
    return {"version":1, "name":"美腻枫", "authoringSource":"../../authoring/Maple.cmo3",
            "anchors":anchors,
            "states":{s:{"disableBlink":True,"disableGaze":True}
                      for s in ("sleep_enter","sleep_loop")},
            "capabilities":{"geometryDeformation":True,"cursorGaze":True,"automaticBlink":True,
                            "hairPhysics":True,"continuousMotions":list(ANIMATIONS),
                            "semanticDurationsMs":{s:sum(a.delays_ms) for s,a in ANIMATIONS.items()},
                            "parameters":[p.id for p in rig.parameters]},
            "expressions":{
                "keyboard":[{"id":"ParamTyping","value":1,"blend":"Overwrite"}],
                "audio":[{"id":"ParamListening","value":1,"blend":"Overwrite"}],
                "dizzy":[{"id":"ParamDizzy","value":1,"blend":"Overwrite"}],
                "glasses":[{"id":"ParamGlasses","value":1,"blend":"Overwrite"}],
                "fan":[{"id":"ParamFan","value":1,"blend":"Overwrite"}],
                "maple":[{"id":"ParamMaple","value":1,"blend":"Overwrite"}],
                "happy":[{"id":"ParamCheek","value":.75,"blend":"Overwrite"},
                         {"id":"ParamMouthForm","value":1,"blend":"Overwrite"}]}}


def build(manifest_path, output=None, runtime=None, child_coordinates="normalized"):
    manifest_path=Path(manifest_path).resolve()
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    builder=MapleBuilder(manifest,manifest_path.parent)
    rig=builder.build()
    output=Path(output or manifest_path.parent)
    runtime=Path(runtime or ROOT/"assets/live2d/Maple")
    output.mkdir(parents=True,exist_ok=True)
    (runtime/"motions").mkdir(parents=True,exist_ok=True)
    defaults={p.id:p.default for p in rig.parameters}
    (output/"Maple.rig.json").write_text(rig.model_dump_json(indent=2),encoding="utf-8")
    project=corrected_project(rig,manifest_path.parent,child_coordinates)
    (output/"Maple.cmo3").write_bytes(project)
    for state,spec in ANIMATIONS.items():
        (runtime/"motions"/f"{state}.motion3.json").write_text(
            json.dumps(build_motion(state,spec,defaults),ensure_ascii=False,indent=2),encoding="utf-8")
    (runtime/"Maple.physics3.json").write_text(json.dumps(physics3(rig),indent=2),encoding="utf-8")
    (runtime/"Maple.pet.json").write_text(json.dumps(metadata_for(rig,manifest),ensure_ascii=False,indent=2),encoding="utf-8")
    # A fragment must be merged only after Editor export. It cannot masquerade
    # as a loadable model3 settings file before an actual moc3 exists.
    fragment={"FileReferences":{"Physics":"Maple.physics3.json","Motions":motion_references(ANIMATIONS)},
              "Groups":[{"Target":"Parameter","Name":"EyeBlink","Ids":["ParamEyeLOpen","ParamEyeROpen"]},
                        {"Target":"Parameter","Name":"LipSync","Ids":["ParamMouthOpenY"]}],
              "CuteMaple":{"Metadata":"Maple.pet.json"}}
    (output/"Maple.model3-fragment.json").write_text(json.dumps(fragment,indent=2),encoding="utf-8")
    report={"status":"requires-editor-export-and-visual-verification", "childCoordinates":child_coordinates,
            "layers":len(rig.parts),"deformers":len(rig.deformers),"parameters":len(rig.parameters),
            "motionGroups":len(ANIMATIONS),"meshGeometryAxes":audit_rig(rig),
            "sourceTextureSha256":{t.path:hashlib.sha256((manifest_path.parent/t.path).read_bytes()).hexdigest() for t in rig.textures},
            "projectSha256":hashlib.sha256(project).hexdigest()}
    (output/"Maple.build-audit.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest",nargs="?",type=Path,default=ROOT/"assets/authoring/layers.json")
    parser.add_argument("--output",type=Path)
    parser.add_argument("--runtime",type=Path)
    parser.add_argument("--child-coordinates",choices=("canvas","normalized"),default="normalized")
    args=parser.parse_args()
    result=build(args.manifest,args.output,args.runtime,args.child_coordinates)
    print(json.dumps({k:v for k,v in result.items() if k not in ("sourceTextureSha256","meshGeometryAxes")},indent=2))
