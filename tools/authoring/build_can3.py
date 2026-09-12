"""Serialize Maple's own motion3 curves as an editable Cubism CAN3 project.

This is an independently generated animation graph, using the class/field
layout observed in Live2D's official Haru CAN3 as a format reference only:
https://cubism.live2d.com/sample-data/bin/haru/haru_ja.zip
No sample artwork, model, UUID, or choreography is copied. The shared root
parameter-group UUID is Cubism's well-known root identifier.

This emits an authoring project, NEVER an executable MOC3. Structural checks
cannot establish Editor compatibility; opening/saving it in Editor is required.
The 100 fps editing grid preserves all Maple cycle durations exactly and rounds
intermediate key times by at most 5 ms. Existing runtime motion3 files are read
only. Cubic control times are rescaled within their quantized endpoint interval.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent / "vendor"))
sys.path.insert(0, str(ROOT))
from image2live2d.backends.live2d.cmo3.caff import CaffEntry, pack_caff, COMPRESS_FAST
try:
    from .native_caff import read_project
except ImportError:
    from native_caff import read_project
from pet_core import ANIMATIONS
from maple_motions import TRANSITIONS, V5_CLEAN_TRANSITIONS

FPS = 100
ROOT_GROUP = "e9fe6eff-953b-4ce2-be7c-4a7c3913686b"
NS = uuid.UUID("47f56d7f-e8bb-489d-80b9-57e306ae176a")

# Public serialized Java class names, not implementation or sample data.
IMPORTS = """
com.live2d.cubism.CETargetVersion$Animation
com.live2d.cubism.doc.animation.CAnimation
com.live2d.cubism.doc.animation.CSceneSource
com.live2d.cubism.doc.animation.formAnimation.FormAnimationSet
com.live2d.cubism.doc.animation.movie.core.CMvMovieInfo
com.live2d.cubism.doc.animation.movie.effect.CMvEffect_EyeBlink
com.live2d.cubism.doc.animation.movie.effect.CMvEffect_LipSync
com.live2d.cubism.doc.animation.movie.effect.CMvEffect_Live2DParameter
com.live2d.cubism.doc.animation.movie.effect.CMvEffect_Live2DPartsVisible
com.live2d.cubism.doc.animation.movie.effect.CMvEffect_VisualDefault
com.live2d.cubism.doc.animation.movie.effect.CMvParameter_Group
com.live2d.cubism.doc.animation.movie.effect.CSoundHandler
com.live2d.cubism.doc.animation.movie.effect.CVisualHandler
com.live2d.cubism.doc.animation.movie.effect.ICMvEffect
com.live2d.cubism.doc.animation.movie.effect.attr.CMvAttrF
com.live2d.cubism.doc.animation.movie.effect.attr.CMvAttrI
com.live2d.cubism.doc.animation.movie.effect.attr.CMvAttrPt
com.live2d.cubism.doc.animation.movie.effect.attr.ICMvAttr
com.live2d.cubism.doc.animation.movie.effect.attr.ICMvAttr$AdaptType
com.live2d.cubism.doc.animation.movie.effect.attr.value.ACValueSequence
com.live2d.cubism.doc.animation.movie.effect.attr.value.CBezierCtrlPt
com.live2d.cubism.doc.animation.movie.effect.attr.value.CBezierPt
com.live2d.cubism.doc.animation.movie.effect.attr.value.CCurveType
com.live2d.cubism.doc.animation.movie.effect.attr.value.CFrameIndexType
com.live2d.cubism.doc.animation.movie.effect.attr.value.CIntSequence
com.live2d.cubism.doc.animation.movie.effect.attr.value.CMutableSequence
com.live2d.cubism.doc.animation.movie.effect.attr.value.CSeqPt
com.live2d.cubism.doc.animation.movie.effect.attr.xy.CPtTNS
com.live2d.cubism.doc.animation.movie.effect.attr.xy.CXY_TNSSequence
com.live2d.cubism.doc.animation.movie.res.ACResourceEntry
com.live2d.cubism.doc.animation.movie.res.CResourceData
com.live2d.cubism.doc.animation.movie.res.CResourceGroup
com.live2d.cubism.doc.animation.movie.res.CResourceManager
com.live2d.cubism.doc.animation.movie.track.CMvEffectManager
com.live2d.cubism.doc.animation.movie.track.CMvTrack_Group_Source
com.live2d.cubism.doc.animation.movie.track.CMvTrack_Live2DModel_Source
com.live2d.cubism.doc.animation.movie.track.ICMvTrack_Linked
com.live2d.cubism.doc.animation.movie.track.ICMvTrack_Source
com.live2d.cubism.doc.animation.movie.track.resource.ACResource_File
com.live2d.cubism.doc.animation.movie.track.resource.CResource_Linked_Model
com.live2d.cubism.doc.model.deformer.CTrackSourceSet
com.live2d.cubism.doc.model.id.CAttrId
com.live2d.cubism.doc.model.id.CEffectId
com.live2d.cubism.doc.model.id.CMvParameterGroupId
com.live2d.cubism.doc.model.options.edition.EditorEdition
com.live2d.cubism.doc.modeling.ui.viewer.sceneBlending.viewerData_SceneBlending.ASceneBlendingData
com.live2d.cubism.doc.modeling.ui.viewer.sceneBlending.viewerData_SceneBlending.CSceneBlendingSettingsSource
com.live2d.cubism.doc.modeling.ui.viewer.sceneBlending.viewerData_SceneBlending.PlaylistData
com.live2d.cubism.doc.modeling.ui.viewer.sceneBlending.viewerData_SceneBlending.PlaylistItemData
com.live2d.graphics.CImageCanvas
com.live2d.graphics3d.type.GRectF
com.live2d.graphics3d.type.GVector2
com.live2d.type.CColor
com.live2d.type.CParameterGroupGuid
com.live2d.type.CParameterGuid
com.live2d.type.CPlaylistGuid
com.live2d.type.CResourceGroupGuid
com.live2d.type.CResourceGuid
com.live2d.type.CSceneBlendingSettingsGuid
com.live2d.type.CSceneGuid
com.live2d.type.CTrackGuid
""".strip().splitlines()


def field(parent, tag, name=None, value=None, **attrs):
    if name is not None:
        attrs["xs.n"] = name
    child = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if value is not None:
        child.text = str(value).lower() if isinstance(value, bool) else str(value)
    return child


def findfield(parent, name):
    return next(e for e in parent if e.get("xs.n") == name)


@dataclass
class Parameter:
    id: str
    guid: str
    minimum: float
    maximum: float
    default: float
    group: str


def model_parameters(model: Path) -> dict[str, Parameter]:
    xml = next(e.content for e in read_project(model) if e.path == "main.xml")
    root = ET.fromstring(xml)
    objects = {e.get("xs.id"): e for e in root.iter() if e.get("xs.id")}

    def resolve(element):
        return objects[element.get("xs.ref")] if element.get("xs.ref") else element

    result = {}
    for source in root.iter("CParameterSource"):
        if not len(source):
            continue
        pid = resolve(findfield(source, "id")).get("idstr")
        guid = resolve(findfield(source, "guid")).get("uuid")
        group = resolve(findfield(source, "parentGroupGuid")).get("uuid")
        if not pid or not guid or pid in result:
            raise ValueError("Missing/duplicate model parameter identity")
        result[pid] = Parameter(pid, guid, float(findfield(source, "minValue").text),
                                float(findfield(source, "maxValue").text),
                                float(findfield(source, "defaultValue").text), group)
    if not result:
        raise ValueError("Model contains no editable parameters")
    if any(p.group != ROOT_GROUP for p in result.values()):
        raise ValueError("This Maple serializer currently requires the root parameter group")
    return result


def decode_curve(segments, fps=FPS):
    """Motion3 cubic/linear segments -> editable keyframes, retaining controls."""
    first = (float(segments[0]), float(segments[1]))
    # SMOOTH is Cubism's automatic interpolation mode: visiting a scene may
    # recalculate these handles. BEZIER keeps the authored controls, including
    # straight segments represented by their collinear one-third controls.
    # The scene's default type still governs newly inserted keys, not these.
    keys = [{"point": first, "prev": first, "next": first, "type": "BEZIER"}]
    i = 2
    while i < len(segments):
        kind = segments[i]
        i += 1
        if kind == 1:
            ca, cb, end = tuple(segments[i:i+2]), tuple(segments[i+2:i+4]), tuple(segments[i+4:i+6])
            i += 6
        elif kind == 0:
            start = keys[-1]["point"]
            end = tuple(segments[i:i+2])
            i += 2
            ca = tuple(a + (b-a)/3 for a, b in zip(start, end))
            cb = tuple(a + 2*(b-a)/3 for a, b in zip(start, end))
        else:
            raise ValueError(f"Unsupported Maple segment {kind}; do not silently alter curves")
        keys[-1]["next"] = ca
        keys.append({"point": end, "prev": cb, "next": end, "type": "BEZIER"})
    frames = [round(k["point"][0] * fps) for k in keys]
    if any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError("Timeline quantization collapses keyframes")
    output = []
    for i, key in enumerate(keys):
        point = (frames[i], key["point"][1])
        controls = {}
        for which, neighbor in [("prev", i-1), ("next", i+1)]:
            control = key[which]
            if neighbor < 0 or neighbor >= len(keys):
                controls[which] = point
            else:
                t0, t1 = key["point"][0], keys[neighbor]["point"][0]
                fraction = (control[0]-t0)/(t1-t0)
                controls[which] = (frames[i] + fraction*(frames[neighbor]-frames[i]), control[1])
        output.append({"point": point, **controls, "type": key["type"]})
    return output


class Project:
    def __init__(self):
        self.root = ET.Element("root", fileFormatVersion="400050002")
        self.shared = ET.SubElement(self.root, "shared")
        self.objects = {}

    def obj(self, tag):
        reference = f"#{len(self.objects)}"
        element = field(self.shared, tag, **{"xs.id": reference})
        self.objects[reference] = element
        return element

    def ref(self, parent, obj, name=None):
        return field(parent, obj.tag, name, **{"xs.ref": obj.get("xs.id")})

    def guid(self, tag, key):
        obj = self.obj(tag)
        obj.set("uuid", str(uuid.uuid5(NS, key)))
        obj.set("note", key)
        return obj

    def refs(self, parent, name, items, tag="carray_list", **attrs):
        array = field(parent, tag, name, count=len(items), **attrs)
        for item in items:
            self.ref(array, item)
        return array

    def attr_base(self, obj, track, identifier, parameter=None):
        base = field(obj, "ICMvAttr", "super")
        field(base, "b", "isShyMode", False)
        field(base, "CAttrId", "id", idstr=identifier)
        field(base, "s", "name", parameter.id if parameter else identifier)
        if parameter:
            field(base, "CParameterGuid", "guid", uuid=parameter.guid, note=parameter.id)
        else:
            field(base, "null", "guid")
        field(base, "b", "isActive", True)
        adapt = field(base, "AdaptType", "adaptType")
        field(adapt, "s", "text", "Absolute" if parameter else "Relative")
        options = field(base, "hash_map", "optionParam", count=int(parameter is not None), keyType="string")
        if parameter:
            field(options, "s", "KEY_PARAM_ID", f"live2dParam:{parameter.id}")
        self.ref(base, track, "track")

    def float_attr(self, track, identifier, keys, value=0., parameter=None, low="-Infinity", high="Infinity"):
        attr = self.obj("CMvAttrF")
        self.attr_base(attr, track, identifier, parameter)
        sequence = field(attr, "CMutableSequence", "valueData")
        base = field(sequence, "ACValueSequence", "super")
        values = [k[c][1] for k in keys for c in ("point", "prev", "next")] or [value]
        field(base, "d", "curMin", min(values))
        field(base, "d", "curMax", max(values))
        field(base, "i", "posStart", 0)
        field(base, "int-array", "keyPts2", " ".join(str(k["point"][0]) for k in keys), count=len(keys))
        field(base, "i", "keyMin", keys[0]["point"][0] if keys else 2147483647)
        field(base, "i", "keyMax", keys[-1]["point"][0] if keys else -2147483648)
        field(base, "d", "lastValue", keys[0]["point"][1] if keys else value)
        field(base, "i", "lastPos", 0)
        self.ref(base, attr, "attr")
        field(base, "d", "baseValue", parameter.default if parameter else value)
        points = field(sequence, "array", "points", count=len(keys), type="CBezierPt")
        for key in keys:
            pt = field(points, "CBezierPt")
            anchor = field(pt, "CSeqPt", "anchor")
            field(anchor, "b", "isCorner", False)
            field(anchor, "i", "pos", key["point"][0])
            field(anchor, "d", "doubleValue", key["point"][1])
            for which in ("next", "prev"):
                control = field(pt, "CBezierCtrlPt", which)
                field(control, "f", "posF", key[which][0])
                field(control, "i", "pos", 0)
                field(control, "d", "doubleValue", key[which][1])
                field(control, "b", "isPosOptimized", False)
        curves = field(sequence, "carray_list", "curveTypes", count=len(keys))
        for key in keys:
            field(curves, "CCurveType", v=key["type"])
        field(attr, "d", "rangeMin", parameter.minimum if parameter else low)
        field(attr, "d", "rangeMax", parameter.maximum if parameter else high)
        field(attr, "b", "isRepeat", False)
        field(attr, "d", "repeatMin", "-1.7976931348623157E308")
        field(attr, "d", "repeatMax", "1.7976931348623157E308")
        field(attr, "null", "linked_keyFormsForObject")
        return attr

    def point_attr(self, track, identifier, x, y):
        attr = self.obj("CMvAttrPt")
        self.attr_base(attr, track, identifier)
        seq = field(attr, "CXY_TNSSequence", "valueDataXY")
        points = field(seq, "array", "points", count=1, type="CPtTNS")
        point = field(points, "CPtTNS")
        for name in ("super", "value"):
            vector = field(point, "GVector2", name)
            field(vector, "f", "x", x)
            field(vector, "f", "y", y)
            if name == "super":
                field(point, "b", "isCorner", False)
        field(point, "i", "pos", 0)
        vector = field(seq, "GVector2", "basePt")
        field(vector, "f", "x", 0.)
        field(vector, "f", "y", 0.)
        field(seq, "null", "attr")
        return attr

    def integer_attr(self, track):
        attr = self.obj("CMvAttrI")
        self.attr_base(attr, track, "frameStep")
        seq = field(attr, "CIntSequence", "valueData")
        base = field(seq, "ACValueSequence", "super")
        for tag, name, value in [("d", "curMin", 0), ("d", "curMax", 0), ("i", "posStart", 0)]:
            field(base, tag, name, value)
        field(base, "int-array", "keyPts2", count=0)
        for tag, name, value in [("i", "keyMin", 2147483647), ("i", "keyMax", -2147483648),
                                 ("d", "lastValue", "NaN"), ("i", "lastPos", -1)]:
            field(base, tag, name, value)
        self.ref(base, attr, "attr")
        field(base, "d", "baseValue", 1.)
        field(seq, "array", "points", count=0, type="CSeqPt")
        field(attr, "i", "rangeMin", 0)
        field(attr, "i", "rangeMax", 100)
        return attr

    def effect(self, tag, identifier, track, attrs, active=True, deletable=False):
        obj = self.obj(tag)
        base = field(obj, "ICMvEffect", "super")
        field(base, "CEffectId", "id", idstr=identifier)
        field(base, "b", "isActive", active)
        field(base, "b", "canDelete", deletable)
        self.refs(base, "attrList", list(attrs.values()), tag="array", type="ICMvAttr")
        mapping = field(base, "hash_map", "attrMap", count=len(attrs))
        for identifier, attr in attrs.items():
            entry = field(mapping, "entry")
            field(entry, "CAttrId", "key", idstr=identifier)
            self.ref(entry, attr, "value")
        self.ref(base, track, "track")
        return obj

    def visual(self, track):
        attrs = {"xy": self.point_attr(track, "xy", 512., 512.)}
        for name, value in [("scalex", 100.), ("scaley", 100.), ("rotate", 0.), ("shear", 0.)]:
            attrs[name] = self.float_attr(track, name, [], value)
        attrs["anchor"] = self.point_attr(track, "anchor", 512., 512.)
        attrs["opacity"] = self.float_attr(track, "opacity", [], 100., low=0., high=100.)
        attrs["frameStep"] = self.integer_attr(track)
        attrs["artPathWidth"] = self.float_attr(track, "artPathWidth", [], 100.)
        effect = self.effect("CMvEffect_VisualDefault", "VisualDefault", track, attrs)
        for name, key in [("attrXY", "xy"), ("attrScaleX", "scalex"), ("attrScaleY", "scaley"),
                          ("attrRotate", "rotate"), ("attrAnchorXY", "anchor"), ("attrShear", "shear"),
                          ("attrOpacity", "opacity"), ("attrFrameStep", "frameStep"), ("attrArtPathWidth", "artPathWidth")]:
            self.ref(effect, attrs[key], name)
        return effect

    def track_base(self, parent, obj, scene, guid, frames, parent_guid=None, effects=()):
        base = field(parent, "ICMvTrack_Source", "super")
        field(base, "s", "name", "Maple" if parent_guid is not None else "Root")
        field(base, "b", "isUserRenamed", False)
        self.ref(base, guid, "guid")
        for name, value in [("start", 0), ("internalOffset", 0), ("duration", frames)]:
            field(base, "i", name, value)
        for name, value in [("editable", True), ("visible", True), ("mute", False), ("isGuide", False)]:
            field(base, "b", name, value)
        for tag, name in [("CVisualHandler", "visualHandler"), ("CSoundHandler", "soundHandler")]:
            self.ref(field(base, tag, name), obj, "track")
        field(base, "null", "soundEffect")
        if effects:
            self.ref(base, effects[-1], "visualEffect")
        else:
            field(base, "null", "visualEffect")
        self.refs(field(base, "CMvEffectManager", "effectManager"), "effectList", effects, tag="array", type="ICMvEffect")
        if parent_guid is not None:
            self.ref(base, parent_guid, "parentGuid")
        else:
            field(base, "null", "parentGuid")
        self.ref(base, scene, "_sceneSource")
        field(base, "hash_map", "userData", count=0, keyType="string")
        field(base, "null", "keys")

    def bounds(self, parent):
        bounds = field(parent, "GRectF", "bounds")
        for name, value in [("x", 0.), ("y", 0.), ("width", 1024.), ("height", 1024.)]:
            field(bounds, "f", name, value)

    def scene(self, animation, resource_guid, state, motion, parameters):
        scene = self.obj("CSceneSource")
        root_track = self.obj("CMvTrack_Group_Source")
        track = self.obj("CMvTrack_Live2DModel_Source")
        guid = self.guid("CSceneGuid", "Maple scene " + state)
        root_guid = self.guid("CTrackGuid", "Maple root " + state)
        track_guid = self.guid("CTrackGuid", "Maple model " + state)
        duration = motion["Meta"]["Duration"]
        end_frame = round(duration * FPS)
        if abs(end_frame/FPS-duration) > 1e-8:
            raise ValueError("Duration does not fit the selected editing frame rate")
        frames = end_frame + 1  # Frame zero plus the exact final keyframe.
        field(scene, "s", "sceneName", state)
        canvas = field(scene, "CImageCanvas", "canvas")
        field(canvas, "i", "pixelWidth", 1024)
        field(canvas, "i", "pixelHeight", 1024)
        field(canvas, "CColor", "background")
        self.ref(scene, guid, "guid")
        field(scene, "s", "tag", (ANIMATIONS.get(state) or TRANSITIONS.get(state) or V5_CLEAN_TRANSITIONS[state]).playback)
        sources = field(scene, "CTrackSourceSet", "trackSourceSet")
        self.refs(sources, "_sources", [root_track, track])
        self.ref(scene, root_track, "rootTrack")
        movie = field(scene, "CMvMovieInfo", "movieInfo")
        for tag, name, value in [("i", "width", 1024), ("i", "height", 1024), ("i", "duration", frames),
                                 ("d", "fps", float(FPS)), ("i", "workspaceStart", 0), ("i", "workspaceEnd", frames)]:
            field(movie, tag, name, value)
        field(movie, "CColor", "background")
        field(movie, "i", "fadeInMSec", round(motion.get("FadeInTime", .28)*1000))
        field(movie, "i", "fadeOutMSec", round(motion.get("FadeOutTime", .22)*1000))
        field(movie, "b", "isBezierRestricted", True)
        field(movie, "b", "isLoopMotion", motion["Meta"]["Loop"])
        field(movie, "i", "startFrame", 0)
        field(movie, "CFrameIndexType", "frameIndexType", v="ZERO_INDEX")
        self.ref(scene, animation, "_animation")
        field(scene, "hash_map", "marker", count=0, keyType="string")
        field(scene, "CCurveType", "defaultParameterCurveType", v="SMOOTH")
        field(scene, "CCurveType", "defaultPartCurveType", v="STEP")
        field(scene, "b", "fixAspect", True)
        field(scene, "Animation", "targetVersion", v="FOR_UNITY_SDK")

        curves = {c["Id"]: c for c in motion["Curves"] if c["Target"] == "Parameter"}
        if set(curves) != set(parameters):
            raise ValueError(f"{state} motion and model parameter sets differ")
        param_attrs = {}
        for pid, parameter in parameters.items():
            identifier = f"live2dParam_{pid}"
            keys = decode_curve(curves[pid]["Segments"])
            if keys[-1]["point"][0] != end_frame:
                raise ValueError(f"{state}/{pid} does not reach the exact cycle end")
            param_attrs[identifier] = self.float_attr(track, identifier, keys, parameter=parameter)
        param_effect = self.effect("CMvEffect_Live2DParameter", "Effects:Live2DParam", track, param_attrs)
        groups = field(param_effect, "carray_list", "parameterGroupList", count=1)
        group = field(groups, "CMvParameter_Group")
        field(group, "CParameterGroupGuid", "guid", uuid=ROOT_GROUP, note="Root Parameter Group")
        field(group, "b", "isShyMode", False)
        field(group, "CMvParameterGroupId", "id", idstr="ParamGroupRoot")
        field(group, "null", "attrFrameStep")
        parts_effect = self.effect("CMvEffect_Live2DPartsVisible", "live2DPartsOpacity_", track, {})
        blink_attrs = {name: self.float_attr(track, name, [], value) for name, value in [("eyeOpen", 1.), ("effectLevel", 100.)]}
        blink_effect = self.effect("CMvEffect_EyeBlink", "Effects:EyeBlink", track, blink_attrs, active=False, deletable=True)
        targets = field(blink_effect, "carray_list", "effectParameterAttrIds", count=2)
        for pid in ["ParamEyeLOpen", "ParamEyeROpen"]:
            field(targets, "CAttrId", idstr=f"live2dParam_{pid}")
        field(blink_effect, "b", "invert", False)
        field(blink_effect, "b", "relative", True)
        lipsync_attrs = {name: self.float_attr(track, name, [], value) for name, value in
                         [("soundLevel", 0.), ("lipSyncScale", 1.), ("lipSyncBase", 0.), ("lipSyncLevel", 100.)]}
        lip_effect = self.effect("CMvEffect_LipSync", "Effects:LipSync", track, lipsync_attrs, active=False, deletable=True)
        targets = field(lip_effect, "carray_list", "effectParameterAttrIds", count=1)
        field(targets, "CAttrId", idstr="live2dParam_ParamMouthOpenY")
        field(lip_effect, "null", "syncTrackGuid")
        field(lip_effect, "b", "isInvert", False)
        field(lip_effect, "b", "isRelative", True)
        visual = self.visual(track)
        effects = [blink_effect, lip_effect, param_effect, parts_effect, visual]
        self.track_base(root_track, root_track, scene, root_guid, frames)
        self.refs(root_track, "_childTrackGuids", [track_guid])
        self.bounds(root_track)
        linked = field(track, "ICMvTrack_Linked", "super")
        self.track_base(linked, track, scene, track_guid, frames, root_guid, effects)
        self.ref(linked, resource_guid, "_resourceGuid")
        for name, effect in [("keyParamEffect", param_effect), ("partsVisibleEffect", parts_effect),
                             ("lipSyncEffect", lip_effect), ("eyeBlinkEffect", blink_effect)]:
            self.ref(track, effect, name)
        field(track, "null", "formEditEffect")
        form = field(track, "FormAnimationSet", "formAnimationSet")
        field(form, "hash_map", "formMapOnGlobal", count=0, keyType="string")
        field(form, "hash_map", "formMapOnLocal", count=0, keyType="string")
        self.ref(form, track, "trackSource")
        self.bounds(track)
        return scene, guid

    def serialize(self):
        # xs.idx is the zero-based first-encounter traversal index of all XML
        # nodes, matching the native serializer, rather than shared-object order.
        for index, element in enumerate(self.root.iter()):
            if element.get("xs.id"):
                element.set("xs.idx", str(index))
        ET.indent(self.root, space="")
        versions = ["CSceneSource:3", "CAnimation:4", "CMvParameter_Group:1", "SerializeFormatVersion:2",
                    "CMvEffect_VisualDefault:1", "CMvMovieInfo:3", "CBezierCtrlPt:2"]
        prefix = '<?xml version="1.0" encoding="UTF-8"?>\n'
        prefix += "".join(f"<?version {version}?>\n" for version in versions)
        prefix += "".join(f"<?import {name}?>\n" for name in IMPORTS)
        return prefix.encode("utf8") + ET.tostring(self.root, encoding="utf-8", xml_declaration=False)


def create_project(model, output, motions_dir):
    model, output, motions_dir = Path(model).resolve(), Path(output).resolve(), Path(motions_dir).resolve()
    parameters = model_parameters(model)
    animations={**ANIMATIONS,**TRANSITIONS} if "ParamTransitionProgress" in parameters else ANIMATIONS
    if 'ParamCleanFanL' in parameters:
        animations={**animations,**V5_CLEAN_TRANSITIONS}
    project = Project()
    animation = project.obj("CAnimation")
    manager = project.obj("CResourceManager")
    root_guid = project.guid("CResourceGroupGuid", "Maple Assets")
    resource_guid = project.guid("CResourceGuid", "Maple editable model")
    resource_data = project.obj("CResourceData")
    scenes, scene_guids, motion_hashes = [], [], {}
    for state in animations:
        path = motions_dir / f"{state}.motion3.json"
        motion_hashes[state] = hashlib.sha256(path.read_bytes()).hexdigest()
        scene, guid = project.scene(animation, resource_guid, state, json.loads(path.read_text(encoding="utf8")), parameters)
        scenes.append(scene)
        scene_guids.append(guid)

    field(animation, "s", "name", f"Maple - {len(animations)} editable motions")
    field(animation, "file", "file", str(output))
    project.refs(animation, "_scenes", scenes)
    project.ref(animation, scenes[0], "currentScene")
    project.ref(animation, manager, "resourceManager")
    field(field(animation, "EditorEdition", "editorEdition"), "i", "edition", 15)
    blending = field(animation, "CSceneBlendingSettingsSource", "sceneBlendingSettings")
    field(blending, "CSceneBlendingSettingsGuid", "guid", uuid=str(uuid.uuid5(NS, "Maple scene blending")))
    playlists = field(blending, "carray_list", "playlists", count=1)
    playlist = field(playlists, "PlaylistData")
    field(playlist, "CPlaylistGuid", "guid", uuid=str(uuid.uuid5(NS, "Maple playlist")))
    field(playlist, "s", "name", "All Maple motions")
    items = field(playlist, "carray_list", "list", count=len(scenes))
    for guid in scene_guids:
        item = field(items, "PlaylistItemData")
        field(item, "ASceneBlendingData", "super")
        project.ref(item, guid, "guid")
    field(blending, "carray_list", "sceneGroups", count=0)
    field(animation, "b", "hideAbsolutePathIfLinkError", False)
    field(animation, "Animation", "targetVersion", v="FOR_UNITY_SDK")
    resource_group = field(manager, "CResourceGroup", "rootGroup")
    base = field(resource_group, "ACResourceEntry", "super")
    field(base, "null", "parentGuid")
    project.ref(base, manager, "_resourceManager")
    project.ref(resource_group, root_guid, "guid")
    project.refs(resource_group, "_childGuids", [resource_guid])
    field(resource_group, "s", "name", "Maple Assets")
    project.refs(manager, "_resourceRefList", [resource_data])
    mapping = field(manager, "hash_map", "resourceGuidMap", count=1)
    entry = field(mapping, "entry")
    project.ref(entry, resource_guid, "key")
    project.ref(entry, resource_data, "value")
    field(manager, "carray_list", "_resourceGroupList", count=0)
    field(manager, "hash_map", "resourceGroupGuidMap", count=0, keyType="string")
    project.ref(manager, scenes[0], "_sceneSource")
    base = field(resource_data, "ACResourceEntry", "super")
    project.ref(base, root_guid, "parentGuid")
    project.ref(base, manager, "_resourceManager")
    field(resource_data, "null", "customName")
    linked = field(resource_data, "CResource_Linked_Model", "resourceRef")
    resource = field(linked, "ACResource_File", "super")
    # Native CAN3 stores a full source path. Regenerate after relocating the
    # sibling pair to keep its link unambiguous; no guessed relative-path rules.
    field(resource, "file", "srcFile", str(model))
    project.ref(resource, resource_guid, "guid")
    field(resource, "s", "name", model.name)
    project.ref(field(project.root, "main"), animation)
    xml = project.serialize()
    validate_xml(xml, parameters)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(pack_caff([CaffEntry("main.xml", xml, tag="main_xml", compress=COMPRESS_FAST)], key=42))
    audit = {"status": "structurally_checked_pending_native_Editor_open_and_save", "project": str(output),
             "model": str(model), "modelSha256": hashlib.sha256(model.read_bytes()).hexdigest(),
             "projectSha256": hashlib.sha256(output.read_bytes()).hexdigest(), "sceneCount": len(scenes),
             "parameterCountPerScene": len(parameters), "fps": FPS, "maxKeyTimeQuantizationSeconds": .5/FPS,
             "durationConvention": "duration_frames = round(motion_seconds * fps) + 1; endpoint key included",
             "runtimeMotionsModified": False, "motionSha256": motion_hashes,
             "formatReference": "https://cubism.live2d.com/sample-data/bin/haru/haru_ja.zip",
             "sampleCharacterOrMotionIncluded": False}
    output.with_suffix(".can3.audit.json").write_text(json.dumps(audit, indent=2), encoding="utf8")
    return audit


def validate_xml(xml, parameters):
    root = ET.fromstring(xml)
    objects = {e.get("xs.id"): e for e in root.find("shared")}
    if None in objects or len(objects) != len(root.find("shared")):
        raise ValueError("Duplicate/missing shared object identity")
    for element in root.iter():
        reference = element.get("xs.ref")
        if reference and (reference not in objects or objects[reference].tag != element.tag):
            raise ValueError(f"Dangling or incorrectly typed reference {reference}")
        if element.tag in ("array", "carray_list", "hash_map") and int(element.get("count")) != len(element):
            raise ValueError("Array/map count mismatch")
    scenes = [e for e in objects.values() if e.tag == "CSceneSource"]
    expected=list(ANIMATIONS)+list(TRANSITIONS) if "ParamTransitionProgress" in parameters else list(ANIMATIONS)
    if 'ParamCleanFanL' in parameters:
        expected+=list(V5_CLEAN_TRANSITIONS)
    if [findfield(s, "sceneName").text for s in scenes] != expected:
        raise ValueError("Incorrect Maple scene coverage/order")
    effects = [e for e in objects.values() if e.tag == "CMvEffect_Live2DParameter"]
    for effect in effects:
        attrs = findfield(findfield(effect, "super"), "attrList")
        if len(attrs) != len(parameters):
            raise ValueError("Missing editable parameter lanes")
        seen = set()
        for ref in attrs:
            attr = objects[ref.get("xs.ref")]
            base = findfield(attr, "super")
            pid = findfield(base, "id").get("idstr").removeprefix("live2dParam_")
            if pid in seen or findfield(base, "guid").get("uuid") != parameters[pid].guid:
                raise ValueError("Parameter GUID mismatch")
            seen.add(pid)
        if seen != set(parameters):
            raise ValueError("Parameter lane coverage mismatch")


def relink_project(existing: Path, model: Path, output: Path | None = None):
    """Relink a moved single-model CAN3, preserving user-edited animation.

    The first use creates a sibling backup before an in-place replacement. A
    separate output is preferable when the animation is open in Editor. This
    only accepts matching parameter identities and never remaps user curves.
    """
    existing, model = Path(existing).resolve(), Path(model).resolve()
    output = Path(output).resolve() if output else existing
    params = model_parameters(model)
    entries = read_project(existing)
    main = next(entry for entry in entries if entry.path == "main.xml")
    xml = main.content.decode("utf8")
    tree = ET.fromstring(xml)
    objects = {e.get("xs.id"): e for e in tree.iter() if e.get("xs.id")}
    resources = [e for e in tree.iter("CResource_Linked_Model") if len(e)]
    if len(resources) != 1:
        raise ValueError("Relinking supports exactly one model resource")
    for attr in tree.iter("CMvAttrF"):
        if not len(attr):
            continue
        base = findfield(attr, "super")
        identifier = findfield(base, "id")
        identifier = objects[identifier.get("xs.ref")] if identifier.get("xs.ref") else identifier
        pid = identifier.get("idstr", "").removeprefix("live2dParam_")
        if not identifier.get("idstr", "").startswith("live2dParam_"):
            continue
        guid = findfield(base, "guid")
        guid = objects[guid.get("xs.ref")] if guid.get("xs.ref") else guid
        if pid not in params or guid.get("uuid") != params[pid].guid:
            raise ValueError(f"Moved model parameter identity differs: {pid}")
    resource = findfield(resources[0], "super")
    findfield(resource, "srcFile").text = str(model)
    findfield(resource, "name").text = model.name
    animations = [e for e in tree.iter("CAnimation") if len(e)]
    if len(animations) != 1:
        raise ValueError("Ambiguous animation root")
    findfield(animations[0], "file").text = str(output)
    prefix = xml[:xml.index("<root")]
    new_xml = prefix.encode("utf8") + ET.tostring(tree, encoding="utf-8", xml_declaration=False)
    replaced = [CaffEntry(e.path, new_xml if e.path == "main.xml" else e.content,
                          tag=e.tag, obfuscated=e.obfuscated, compress=e.compress) for e in entries]
    packed = pack_caff(replaced, key=42)
    if output == existing:
        backup = existing.with_suffix(".before-relink.can3")
        if not backup.exists():
            backup.write_bytes(existing.read_bytes())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(packed)
    return {"project": str(output), "model": str(model), "editedAnimationPreserved": True,
            "status": "relinked_pending_native_Editor_open"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=ROOT / "assets/authoring/revisions/v3/Maple.cmo3")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--relink", type=Path, help="Move an existing CAN3 link without regenerating edited curves")
    parser.add_argument("--motions", type=Path, default=ROOT / "assets/live2d/Maple/motions")
    args = parser.parse_args()
    if args.relink:
        result = relink_project(args.relink, args.model, args.output)
    else:
        result = create_project(args.model, args.output or args.model.with_suffix(".can3"), args.motions)
    print(json.dumps(result, indent=2))
