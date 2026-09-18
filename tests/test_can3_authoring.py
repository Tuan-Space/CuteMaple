"""Editable-project integrity and curve conversion; no Editor or hardware use."""
from pathlib import Path
import hashlib
import json
import sys
import uuid
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/authoring"))
from build_can3 import (FPS, ROOT_GROUP, CaffEntry, create_project, decode_curve,
                        field, findfield, model_parameters, pack_caff, validate_xml, relink_project,
                        Project)
from native_caff import read_project
from tools.authoring.animation_specs import ANIMATIONS
from maple_motions import build_motion, TRANSITIONS, V5_CLEAN_TRANSITIONS

# Explicit synthetic parameter contracts keep this serializer test independent
# of whichever native model revision is currently installed for the desktop.
BASE_PARAMETER_IDS = (
    "ParamAngleX", "ParamBodyAngleX", "ParamAngleY", "ParamBodyAngleY",
    "ParamAngleZ", "ParamBodyAngleZ", "ParamEyeLOpen", "ParamEyeLSmile",
    "ParamBrowLY", "ParamBrowLForm", "ParamArmLA", "ParamArmLB",
    "ParamLegLA", "ParamLegLB", "ParamEyeROpen", "ParamEyeRSmile",
    "ParamBrowRY", "ParamBrowRForm", "ParamArmRA", "ParamArmRB",
    "ParamLegRA", "ParamLegRB", "ParamEyeBallX", "ParamEyeBallY",
    "ParamMouthForm", "ParamHairFront", "ParamHairSide", "ParamHairBack",
    "ParamSkirt", "ParamMouthOpenY", "ParamCheek", "ParamBreath",
    "ParamTyping", "ParamTypingPulse", "ParamListening", "ParamDizzy",
    "ParamGlasses", "ParamFan", "ParamMaple", "ParamPoseSleep",
    "ParamPoseSwing", "ParamPoseTop", "ParamCrouch", "ParamBounce",
    "ParamSwingVisible", "ParamCleaning", "ParamPoseClimb", "ParamSwing",
)
V4_PARAMETER_IDS = (
    "ParamHeadTurn", "ParamBodyTurn", "ParamHandLShape", "ParamSleeveLHang",
    "ParamHandRShape", "ParamSleeveRHang", "ParamTransitionProgress", "ParamArmPoseMode",
)


def test_cubic_controls_keep_values_and_relative_time_after_frame_quantization():
    keys = decode_curve([0, 0, 1, .0486667, 0, .0973333, 4, .146, 4])
    assert keys[0]["point"] == (0, 0)
    assert keys[1]["point"] == (15, 4)
    assert keys[0]["next"][0] == pytest.approx(5, abs=1e-5)
    assert keys[1]["prev"][0] == pytest.approx(10, abs=1e-5)
    assert keys[0]["next"][1] == 0
    assert keys[1]["prev"][1] == 4
    assert abs(keys[1]["point"][0]/FPS-.146) <= .005


def test_unsupported_or_collapsed_keys_are_rejected():
    with pytest.raises(ValueError, match="Unsupported"):
        decode_curve([0, 0, 2, 1, 1])
    with pytest.raises(ValueError, match="collapses"):
        decode_curve([0, 0, 0, .001, 1])


def test_authored_handles_use_explicit_bezier_instead_of_editor_auto_smooth():
    # These asymmetric handles are deliberately not a smooth tangent through
    # their shared key. Automatic smoothing visibly changes this choreography.
    # Include a following straight segment to check that mixed curves keep the
    # preceding incoming handle and use explicit collinear outgoing controls.
    keys = decode_curve([0, 0, 1, .2, 1.75, .8, -.25, 1, 1,
                         0, 2, 3])
    project = Project()
    track = project.obj("CMvTrack_Live2DModel_Source")
    attr = project.float_attr(track, "test", keys)
    sequence = findfield(attr, "valueData")
    assert [e.get("v") for e in findfield(sequence, "curveTypes")] == ["BEZIER"] * 3
    points = findfield(sequence, "points")
    actual = []
    for point in points:
        actual.append({side: (
            float(findfield(findfield(point, side), "posF").text),
            float(findfield(findfield(point, side), "doubleValue").text),
        ) for side in ("prev", "next")})
    assert actual[0]["next"] == pytest.approx((20, 1.75))
    assert actual[1]["prev"] == pytest.approx((80, -.25))
    assert actual[1]["next"] == pytest.approx((100 + 100/3, 1 + 2/3))
    assert actual[2]["prev"] == pytest.approx((100 + 200/3, 1 + 4/3))


@pytest.fixture
def editable_model(tmp_path):
    # A small source-only CAFF fixture with fresh identities; no image needed
    # to test that animation links use source GUIDs rather than guessed IDs.
    source = ET.Element("root")
    parameter_set = field(source, "CParameterSourceSet")
    assert len(BASE_PARAMETER_IDS) == len(set(BASE_PARAMETER_IDS)) == 48
    for pid in BASE_PARAMETER_IDS:
        parameter = field(parameter_set, "CParameterSource")
        field(parameter, "CParameterId", "id", idstr=pid)
        field(parameter, "CParameterGuid", "guid", uuid=str(uuid.uuid4()))
        field(parameter, "CParameterGroupGuid", "parentGroupGuid", uuid=ROOT_GROUP)
        for name, value in [("minValue", -100), ("maxValue", 100), ("defaultValue", 0)]:
            field(parameter, "f", name, value)
    model = tmp_path / "Maple.cmo3"
    model.write_bytes(pack_caff([CaffEntry("main.xml", ET.tostring(source))]))
    return model


@pytest.fixture
def base_motions(tmp_path):
    motions = tmp_path / "base-motions"
    motions.mkdir()
    assert len(ANIMATIONS) == 21
    for state, spec in ANIMATIONS.items():
        motion = build_motion(state, spec, {pid: 0 for pid in BASE_PARAMETER_IDS})
        (motions / f"{state}.motion3.json").write_text(json.dumps(motion), encoding="utf8")
    return motions


def test_generated_caff_covers_all_scenes_and_uses_exact_source_guids(editable_model, base_motions):
    output = editable_model.with_suffix(".can3")
    motions = base_motions
    original = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in motions.glob("*.motion3.json")}
    model_bytes = editable_model.read_bytes()
    audit = create_project(editable_model, output, motions)
    assert audit["sceneCount"] == 21
    assert audit["parameterCountPerScene"] == 48
    assert "pending_native" in audit["status"]
    assert editable_model.read_bytes() == model_bytes
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in motions.glob("*.motion3.json")} == original
    entries = read_project(output)
    assert [entry.path for entry in entries] == ["main.xml"]
    xml = entries[0].content
    assert b"haru" not in xml.lower()
    parameters = model_parameters(editable_model)
    validate_xml(xml, parameters)
    tree = ET.fromstring(xml)
    scenes = [e for e in tree.find("shared") if e.tag == "CSceneSource"]
    objects = {e.get("xs.id"): e for e in tree.find("shared")}
    for scene in scenes:
        state = findfield(scene, "sceneName").text
        movie = findfield(scene, "movieInfo")
        seconds = sum(ANIMATIONS[state].delays_ms)/1000
        assert (int(findfield(movie, "duration").text)-1)/FPS == pytest.approx(seconds)
        assert int(findfield(movie, "workspaceEnd").text) == int(findfield(movie, "duration").text)
        tracks = findfield(findfield(scene, "trackSourceSet"), "_sources")
        track = objects[tracks[1].get("xs.ref")]
        effect = objects[findfield(track, "keyParamEffect").get("xs.ref")]
        for ref in findfield(findfield(effect, "super"), "attrList"):
            attr = objects[ref.get("xs.ref")]
            seq = findfield(attr, "valueData")
            keys = findfield(seq, "points")
            assert {e.get("v") for e in findfield(seq, "curveTypes")} == {"BEZIER"}
            assert int(findfield(findfield(keys[0], "anchor"), "pos").text) == 0
            assert int(findfield(findfield(keys[-1], "anchor"), "pos").text) == round(seconds*FPS)
    files = list(tree.iter("file"))
    assert {p.text for p in files} == {str(editable_model), str(output)}


def test_relink_preserves_user_edited_keys_and_makes_backup(editable_model, base_motions, tmp_path):
    animation = editable_model.with_suffix(".can3")
    create_project(editable_model, animation, base_motions)
    xml = read_project(animation)[0].content.decode("utf8")
    tree = ET.fromstring(xml)
    first_value = tree.find(".//CBezierPt/CSeqPt/d")
    first_value.text = "12.34567"  # A deliberate user edit.
    edited = xml[:xml.index("<root")].encode() + ET.tostring(tree, encoding="utf-8", xml_declaration=False)
    animation.write_bytes(pack_caff([CaffEntry("main.xml", edited)]))
    previous = animation.read_bytes()
    new_model = tmp_path / "moved" / "Maple.cmo3"
    new_model.parent.mkdir()
    new_model.write_bytes(editable_model.read_bytes())
    result = relink_project(animation, new_model)
    assert result["editedAnimationPreserved"]
    assert animation.with_suffix(".before-relink.can3").read_bytes() == previous
    after = ET.fromstring(read_project(animation)[0].content)
    assert after.find(".//CBezierPt/CSeqPt/d").text == "12.34567"
    assert str(new_model) in {e.text for e in after.iter("file")}


def test_v4_can3_includes_editable_internal_transition_scenes(editable_model,tmp_path):
    tree=ET.fromstring(read_project(editable_model)[0].content)
    pset=tree.find("CParameterSourceSet")
    assert len(V4_PARAMETER_IDS) == 8
    assert not set(BASE_PARAMETER_IDS).intersection(V4_PARAMETER_IDS)
    for pid in V4_PARAMETER_IDS:
        p=field(pset,"CParameterSource")
        field(p,"CParameterId","id",idstr=pid)
        field(p,"CParameterGuid","guid",uuid=str(uuid.uuid4()))
        field(p,"CParameterGroupGuid","parentGroupGuid",uuid=ROOT_GROUP)
        for name,value in [("minValue",-1),("maxValue",1),("defaultValue",0)]:
            field(p,"f",name,value)
    editable_model.write_bytes(pack_caff([CaffEntry("main.xml",ET.tostring(tree))]))
    parameters=model_parameters(editable_model)
    assert len(parameters) == 56
    assert set(parameters) == set(BASE_PARAMETER_IDS + V4_PARAMETER_IDS)
    assert len({p.guid for p in parameters.values()}) == 56
    motion_folder=tmp_path/"v4-motions";motion_folder.mkdir()
    for state,spec in {**ANIMATIONS,**TRANSITIONS}.items():
        (motion_folder/f"{state}.motion3.json").write_text(json.dumps(build_motion(state,spec,{p:0 for p in parameters})))
    audit=create_project(editable_model,tmp_path/"v4.can3",motion_folder)
    assert audit["sceneCount"]==23
    assert audit["parameterCountPerScene"]==56
    validate_xml(read_project(tmp_path/"v4.can3")[0].content,parameters)


def test_v5_can3_keeps_all_take_fan_and_return_fan_scenes(editable_model,tmp_path):
    tree=ET.fromstring(read_project(editable_model)[0].content)
    pset=tree.find('CParameterSourceSet')
    added=V4_PARAMETER_IDS+tuple('ParamRibbon'+s+a for s in ('L','R') for a in ('X','Y'))+(
        'ParamCleanFanL','ParamCleanFanR','ParamCleaningSweep','ParamCleanGround',
        'ParamClimbPhase','ParamClimbActive','ParamClimbDirection')
    for pid in added:
        p=field(pset,'CParameterSource');field(p,'CParameterId','id',idstr=pid)
        field(p,'CParameterGuid','guid',uuid=str(uuid.uuid4()))
        field(p,'CParameterGroupGuid','parentGroupGuid',uuid=ROOT_GROUP)
        for name,value in [('minValue',-100),('maxValue',100),('defaultValue',0)]:field(p,'f',name,value)
    editable_model.write_bytes(pack_caff([CaffEntry('main.xml',ET.tostring(tree))]))
    parameters=model_parameters(editable_model)
    motions=tmp_path/'v5-motions';motions.mkdir()
    for state,spec in {**ANIMATIONS,**TRANSITIONS,**V5_CLEAN_TRANSITIONS}.items():
        (motions/f'{state}.motion3.json').write_text(json.dumps(build_motion(state,spec,{p:0 for p in parameters})),encoding='utf8')
    output=tmp_path/'v5.can3';audit=create_project(editable_model,output,motions)
    assert audit['sceneCount']==31
    assert audit['parameterCountPerScene']==67
    xml=read_project(output)[0].content;validate_xml(xml,parameters)
    scenes=[e for e in ET.fromstring(xml).find('shared') if e.tag=='CSceneSource']
    internal=[s for s in scenes if findfield(s,'sceneName').text in V5_CLEAN_TRANSITIONS]
    assert len(internal)==8
    for scene in internal:
        name=findfield(scene,'sceneName').text
        assert findfield(scene,'tag').text=='one_shot'
        duration=int(findfield(findfield(scene,'movieInfo'),'duration').text)-1
        assert duration==70 if name.endswith('_enter') else duration==65
