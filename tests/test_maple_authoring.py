"""Structural and motion-contract tests; genuine Cubism visual QA is separate."""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from PIL import Image
import pytest

ROOT=Path(__file__).resolve().parents[1]
pytest.importorskip("pydantic", reason="Optional editable-model authoring dependency")
sys.path.insert(0,str(ROOT/"tools/authoring"))
from build_rig import MapleBuilder, audit_rig, corrected_project, metadata_for
from maple_motions import build_motion
from tools.authoring.animation_specs import ANIMATIONS
from image2live2d.backends.live2d.cmo3 import unpack_caff


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    folder=tmp_path_factory.mktemp("maple-art-fixture")
    names=[("swing","accessory"),("torso","torso"),("skirt","clothing"),
           ("hair_front","hair_front"),("ribbon_l","hair_back"),("ornament_l","hair_side"),
           ("face_base","face_base"),("mouth","mouth"),("blush","blush"),
           ("glasses","accessory"),("fan","accessory"),("maple","accessory")]
    for side in ("l","r"):
        for role in ("arm","hand","leg","eye","eye_closed","pupil","eyebrow"):
            names.append((f"{role}_{side}",f"{role}_{side}"))
    layers=[]
    for index,(name,role) in enumerate(names):
        # Tiny synthetic fixtures are never copied into model output assets.
        Image.new("RGBA",(32,32),(index*7%255,100,180,255)).save(folder/f"{name}.png")
        hidden=name in ("swing","glasses","fan","maple","blush") or name.startswith("eye_closed")
        layers.append({"id":name,"role":role,"file":f"{name}.png","draw_order":50+index*5,
                       "opacity":0 if hidden else 1,"pivot":[.5,.4]})
    manifest={"width":32,"height":32,"layers":layers}
    return MapleBuilder(manifest,folder).build(),folder,manifest


def test_every_parameter_retained_and_no_writer_cap(rig):
    model,_,_=rig
    axes=audit_rig(model)
    assert max(map(len,axes.values()))<=6
    assert {"ParamTyping","ParamListening","ParamEyeLOpen","ParamEyeBallX","ParamPoseSleep"} <= {p.id for p in model.parameters}
    for node in model.deformers:
        affecting=[p for p in model.parameters if any(node.id in k.deformer_offsets for k in p.keyforms)]
        assert len(affecting)<=3
        assert all(len(k.deformer_offsets[node.id])==len(node.grid_vertices)
                   for p in affecting for k in p.keyforms)


def test_neutral_geometry_and_visibility(rig):
    model,_,_=rig
    visible={p.id:p.opacity for p in model.parts}
    for parameter in model.parameters:
        key=next(k for k in parameter.keyforms if k.value==parameter.default)
        for offsets in [*key.mesh_offsets.values(),*key.deformer_offsets.values()]:
            assert all(abs(x)+abs(y)<1e-8 for x,y in offsets)
        for name,value in key.opacity_overrides.items():
            visible[name]*=value
    for name in ("swing","glasses","fan","maple","blush","eye_closed_l","eye_closed_r"):
        assert visible[name]==0
    for name in ("face_base","eye_l","eye_r","pupil_l","pupil_r"):
        assert visible[name]==1


def test_typing_listening_and_gaze_move_actual_geometry(rig):
    model,_,_=rig
    params={p.id:p for p in model.parameters}
    for name in ("ParamTyping","ParamTypingPulse","ParamListening","ParamAngleX","ParamAngleZ","ParamPoseSleep"):
        key=params[name].keyforms[-1]
        assert any(abs(x)+abs(y)>.0001 for offsets in key.deformer_offsets.values() for x,y in offsets)
    for name in ("ParamEyeBallX","ParamEyeBallY"):
        key=params[name].keyforms[-1]
        assert all(any(abs(x)+abs(y)>.001 for x,y in key.mesh_offsets[p]) for p in ("pupil_l","pupil_r"))


def test_cmo3_parent_coordinates_source_coordinates_draw_order_and_pngs(rig):
    model,folder,_=rig
    entries=unpack_caff(corrected_project(model,folder))
    xml=next(e.content for e in entries if e.path=="main.xml")
    root=ET.fromstring(xml)
    sources=[n for n in root.iter("CArtMeshSource") if n.get("xs.id")]
    assert len(sources)==len(model.parts)
    orders={p.id:p.draw_order for p in model.parts}
    for source in sources:
        name=source.find("./ACDrawableSource/CDrawableId").get("idstr")
        # Editor-native reference establishes source/editor points use pixels.
        pos=source.find("./float-array[@xs.n='positions']")
        assert max(map(float,pos.text.split()))==32
        for form in source.iter("CArtMeshForm"):
            pos=form.find("./float-array[@xs.n='positions']")
            assert max(map(abs,map(float,pos.text.split())))<3
            assert form.find("./ACDrawableForm/i[@xs.n='drawOrder']").text==str(orders[name])
    for texture in model.textures:
        original=(folder/texture.path).read_bytes()
        assert any(entry.content==original for entry in entries if entry.path.endswith(".png"))


def _decode_curve(curve):
    data=curve["Segments"]
    points=[(data[0],data[1])]
    index=2
    while index<len(data):
        assert data[index]==1
        a,b,t,v=data[index+1:index+3],data[index+3:index+5],data[index+5],data[index+6]
        assert points[-1][0]<a[0]<b[0]<t
        points.append((t,v))
        index+=7
    assert index==len(data)
    return points


def test_all_21_motion_durations_baselines_seams_and_sleep_transitions(rig):
    model,_,_=rig
    defaults={p.id:p.default for p in model.parameters}
    limits={p.id:(p.min,p.max) for p in model.parameters}
    motions={state:build_motion(state,spec,defaults) for state,spec in ANIMATIONS.items()}
    assert len(motions)==21
    for state,motion in motions.items():
        assert motion["Meta"]["Duration"]==sum(ANIMATIONS[state].delays_ms)/1000
        assert {c["Id"] for c in motion["Curves"]}==set(defaults)
        total=0
        for curve in motion["Curves"]:
            pts=_decode_curve(curve)
            assert pts[0][0]==0 and pts[-1][0]==motion["Meta"]["Duration"]
            low,high=limits[curve["Id"]]
            assert all(low-1e-6<=v<=high+1e-6 for _,v in pts)
            if motion["Meta"]["Loop"]:
                assert pts[0][1]==pts[-1][1]
            total+=len(pts)-1
        assert motion["Meta"]["TotalSegmentCount"]==total
        assert motion["Meta"]["TotalPointCount"]==len(defaults)+3*total
    def value(state,param,end):
        c=next(c for c in motions[state]["Curves"] if c["Id"]==param)
        return _decode_curve(c)[-1 if end else 0][1]
    for param in ("ParamPoseSleep","ParamEyeLOpen","ParamEyeROpen","ParamAngleZ","ParamAngleY"):
        assert value("sleep_enter",param,True)==value("sleep_loop",param,False)
        assert value("sleep_loop",param,True)==value("sleep_exit",param,False)
    assert value("sleep_loop","ParamEyeLOpen",False)==0
    assert value("sleep_exit","ParamEyeLOpen",True)==1


def test_metadata_uses_model_vertices_and_preserves_all_states(rig):
    model,_,manifest=rig
    data=metadata_for(model,manifest)
    assert set(data["capabilities"]["continuousMotions"])==set(ANIMATIONS)
    assert data["anchors"]["ground"]["drawable"]=="leg_r"
    assert data["anchors"]["ground"]["edge"]=="bottom"
    assert data["states"]["sleep_loop"]["disableGaze"]


def test_keyed_draw_order_and_clipping_survive_cmo3_serialization(rig):
    from build_rig import Parameter, Keyform
    original,folder,_=rig
    model=original.model_copy(deep=True)
    model.parameters.append(Parameter(id="ParamOcclusionTest",min=-1,max=1,default=0,
        keyforms=[Keyform(value=v,draw_order_overrides={"hand_l":order})
                  for v,order in [(-1,40),(0,160),(1,280)]]))
    pupil=next(p for p in model.parts if p.id=="pupil_l")
    pupil.clip_to=["eye_l"]
    xml=next(e.content for e in unpack_caff(corrected_project(model,folder)) if e.path=="main.xml")
    root=ET.fromstring(xml)
    objects={e.get("xs.id"):e for e in root.iter() if e.get("xs.id")}
    sources={e.find("./ACDrawableSource/CDrawableId").get("idstr"):e
             for e in root.iter("CArtMeshSource") if e.get("xs.id")}
    orders={int(e.text) for e in sources["hand_l"].findall(".//ACDrawableForm/i[@xs.n='drawOrder']")}
    assert orders=={40,160,280}
    clip=sources["pupil_l"].find("./ACDrawableSource/carray_list[@xs.n='clipGuidList']")
    assert len(clip)==1
    assert objects[clip[0].get("xs.ref")].get("note")=="eye_l"


def test_periodic_wave_keeps_velocity_through_zero_and_loop_boundary(rig):
    model,_,_=rig
    motion=build_motion("idle",ANIMATIONS["idle"],{p.id:p.default for p in model.parameters})
    segments=next(c["Segments"] for c in motion["Curves"] if c["Id"]=="ParamBodyAngleZ")
    first_slope=(segments[4]-segments[1])/(segments[3]-segments[0])
    last_slope=(segments[-1]-segments[-3])/(segments[-2]-segments[-4])
    assert first_slope>.5
    assert first_slope==pytest.approx(last_slope,abs=1e-5)
    # Around the halfway zero crossing the neighbouring derivatives agree,
    # rather than both being forcibly stopped by flat handles.
    before=2+3*7
    after=2+4*7
    derivative_before=(segments[before+6]-segments[before+4])/(segments[before+5]-segments[before+3])
    derivative_after=(segments[after+2]-segments[before+6])/(segments[after+1]-segments[before+5])
    assert derivative_before<-.5
    assert derivative_before==pytest.approx(derivative_after,abs=1e-5)


def test_v4_profile_swing_support_and_transition_event_contract(rig):
    from maple_motions import TRANSITIONS, TRANSITION_EVENTS
    model,_,_=rig
    defaults={p.id:p.default for p in model.parameters}
    defaults.update({p:0 for p in ["ParamHeadTurn","ParamBodyTurn","ParamHandLShape","ParamHandRShape","ParamTransitionProgress"]})
    def first(motion,param):
        return next(c["Segments"][1] for c in motion["Curves"] if c["Id"]==param)
    for side,sign in [("left",-1),("right",1)]:
        climb=build_motion(f"climb_{side}",ANIMATIONS[f"climb_{side}"],defaults)
        assert first(climb,"ParamHeadTurn")==sign
        assert first(climb,"ParamBodyTurn")==sign
        state=f"climb_to_top_{side}"
        transition=build_motion(state,TRANSITIONS[state],defaults)
        assert not transition["Meta"]["Loop"]
        assert transition["Meta"]["Duration"]==1.8
        assert [(e["Time"],e["Value"]) for e in transition["UserData"]]==TRANSITION_EVENTS
        assert first(transition,"ParamHeadTurn")==sign
        assert _decode_curve(next(c for c in transition["Curves"] if c["Id"]=="ParamHeadTurn"))[-1][1]==0
    cleaning=build_motion("clean_top",ANIMATIONS["clean_top"],defaults)
    assert first(cleaning,"ParamSwingVisible")==first(cleaning,"ParamPoseSwing")==1
    assert first(cleaning,"ParamHandLShape")==1
    # Long-running public loops stay in one painted view. Only short state
    # changes may cross the local front/middle/profile material exchanges.
    for state,spec in ANIMATIONS.items():
        motion=build_motion(state,spec,defaults)
        if motion["Meta"]["Loop"]:
            for curve in motion["Curves"]:
                if curve["Id"] in ("ParamHeadTurn","ParamBodyTurn"):
                    for _,value in _decode_curve(curve):
                        assert not (.20<=abs(value)<=.30 or .70<=abs(value)<=.80), state


def test_v4_visible_view_selection_and_native_turn_keys():
    from build_v4 import view_weight, TURN_KEYS
    import numpy as np
    assert len({f"{v:.4f}" for v in TURN_KEYS})==len(TURN_KEYS), "Native key serialization must not merge nearby turn keys"
    for turn in [-1,-.75,-.65,-.5,-.35,-.25,0,.25,.35,.5,.65,.75,1]:
        opacity=[view_weight(v,turn) for v in ["front","left","right","left_mid","right_mid"]]
        assert sum(value>0 for value in opacity)<=2
        if abs(turn) in (.25,.75):
            assert sorted(opacity)==pytest.approx([0,0,0,.5,1])
        else:
            assert sorted(opacity)==[0,0,0,0,1]
    # Test the actual linearly interpolated authoring keys, including the
    # guard intervals that retire the covered lower material.
    for turn in np.linspace(-1,1,2001):
        alphas=[np.interp(turn,TURN_KEYS,[view_weight(v,t) for t in TURN_KEYS])
                for v in ["front","left","right","left_mid","right_mid"]]
        assert 1-np.prod([1-a for a in alphas])>.99999


def test_v4_two_rope_tops_are_fixed_while_rigid_seat_endpoints_move():
    from build_v4 import hanging_rope_point
    from build_rig import rotate
    for side,top,bottom in [("l",(.355,0),(.383,.75)),("r",(.657,0),(.633,.75))]:
        for swing in [-1,-.5,0,.5,1]:
            assert hanging_rope_point(top,side,swing)==pytest.approx(top)
            assert hanging_rope_point(bottom,side,swing)==pytest.approx(rotate(bottom,(.508,0),8*swing))


def test_v4_active_sleeve_and_hand_share_wrist_through_pose_combinations(rig):
    from build_v4 import V4Builder
    from diagnose_rig import interpolate_keys,warp_points
    import numpy as np
    _,folder,manifest=rig
    builder=V4Builder(manifest,folder)
    builder._hierarchy()
    for side in ("l","r"):
        sleeve=builder._active_arm(side,"lower")
        hand=builder._active_arm(side,"hand")
        wrist=builder._joints(side)[2]
        for settings in [{"ParamPoseClimb":-1},{"ParamPoseClimb":1},
                         {"ParamPoseSwing":1,"ParamSwing":-1},
                         {"ParamPoseSwing":1,"ParamSwing":1},
                         {"ParamPoseClimb":.35,"ParamPoseSwing":.3,"ParamPoseTop":.8}]:
            grids={name:np.array(node.grid_vertices,dtype=float) for name,node in builder.nodes.items()}
            for parameter in builder.params.values():
                for key,weight in interpolate_keys(parameter,settings.get(parameter.id,parameter.default)):
                    for name,delta in key.deformer_offsets.items():
                        grids[name]+=np.array(delta)*weight
            def position(parent,source):
                point=np.array([source])
                while parent:
                    node=builder.nodes[parent]
                    point=warp_points(point,grids[parent],node.grid_rows,node.grid_cols)
                    parent=node.parent
                return point[0]
            assert position(sleeve,builder._source_joints(side)[2])==pytest.approx(position(hand,wrist),abs=1e-7)


def test_v4_cloth_elbow_is_shared_and_upright_upper_sleeve_keeps_width(rig):
    from build_v4 import V4Builder
    from diagnose_rig import interpolate_keys,warp_points
    import numpy as np
    _,folder,manifest=rig
    builder=V4Builder(manifest,folder)
    builder._hierarchy()
    for side in ("l","r"):
        upper=builder._active_arm(side,"upper")
        lower=builder._active_arm(side,"lower")
        shoulder,elbow,_=builder._source_joints(side)
        for settings in [{"ParamPoseSwing":1},{"ParamPoseClimb":1},
                         {"ParamPoseClimb":-1},{"ParamPoseClimb":.35,"ParamPoseSwing":.3,"ParamPoseTop":.8}]:
            grids={name:np.array(node.grid_vertices,dtype=float) for name,node in builder.nodes.items()}
            for parameter in builder.params.values():
                for key,weight in interpolate_keys(parameter,settings.get(parameter.id,parameter.default)):
                    for name,delta in key.deformer_offsets.items():
                        grids[name]+=np.array(delta)*weight
            def position(parent,source):
                point=np.array([source])
                while parent:
                    node=builder.nodes[parent]
                    point=warp_points(point,grids[parent],node.grid_rows,node.grid_cols)
                    parent=node.parent
                return point[0]
            for drop in [0,.04,.08]:
                seam=(elbow[0],elbow[1]+drop)
                assert position(upper,seam)==pytest.approx(position(lower,seam),abs=1e-7)
            if settings=={"ParamPoseSwing":1}:
                top=position(upper,shoulder)
                hem=position(upper,(shoulder[0],shoulder[1]+.08))
                assert abs(hem[0]-top[0])>.025, "An upright arm must not collapse its entire sleeve into a vertical strap"


def test_diagnostic_evaluates_identity_and_nested_warps(rig):
    import numpy as np
    from diagnose_rig import DiagnosticRenderer, warp_points
    model,folder,_=rig
    renderer=DiagnosticRenderer(model,folder,res=32)
    neutral=renderer.evaluate({})
    for mesh in model.meshes:
        assert np.allclose(neutral[mesh.part_id][0],mesh.vertices,atol=1e-10)
    # Full-canvas affine lattices are exact even outside the rest bounds.
    from build_rig import lattice
    grid=np.array(lattice(cols=3,rows=3))
    points=np.array([[.25,.7],[-.2,1.3]])
    translated=warp_points(points,grid+[.1,.2],3,3)
    rotated_grid=np.column_stack([-grid[:,1],grid[:,0]])
    combined=warp_points(translated,rotated_grid,3,3)
    assert np.allclose(combined,np.column_stack([-(points[:,1]+.2),points[:,0]+.1]))


def test_parent_view_opacity_is_serialized_and_inherited_without_mesh_forms(rig):
    from diagnose_rig import DiagnosticRenderer
    original,folder,_=rig
    model=original.model_copy(deep=True)
    parameter=next(p for p in model.parameters if p.id=="ParamGlasses")
    for key in parameter.keyforms:
        assert "Scene_Swing" not in key.deformer_offsets
        key.deformer_opacity_overrides["Scene_Swing"]=1-key.value
    renderer=DiagnosticRenderer(model,folder,res=32)
    assert renderer.evaluate({"ParamGlasses":0})["face_base"][1]==1
    assert renderer.evaluate({"ParamGlasses":.5})["face_base"][1]==pytest.approx(.5)
    assert renderer.evaluate({"ParamGlasses":1})["face_base"][1]==0
    root=ET.fromstring(next(e.content for e in unpack_caff(corrected_project(model,folder)) if e.path=="main.xml"))
    source=next(e for e in root.iter("CWarpDeformerSource") if e.get("xs.id") and
                e.find("./ACDeformerSource/CDeformerId").get("idstr")=="Scene_Swing")
    values={float(e.text) for e in source.findall(".//ACDeformerForm/f[@xs.n='opacity']")}
    assert values=={0,1}  # The half-opacity value above is interpolated.


@pytest.mark.parametrize("keys", [
    [-1,-.3,-.2999,-.25,-.2001,-.2,0,.2,.2001,.25,.2999,.3,1],
    [0,.5,.82,.8201,.87,.9199,.92,1],
    [0,.025,.05,1],
    [-30,0,30],
])
def test_native_parameter_tolerance_cannot_select_a_neighboring_guard_form(keys):
    from image2live2d.irr.schema import Parameter, Keyform
    from image2live2d.backends.live2d.cmo3.model_xml import _param_source
    parameter=Parameter(id="ParamDenseKeys",min=min(keys),max=max(keys),
                        keyforms=[Keyform(value=value) for value in keys])
    parent=ET.Element("parameters")
    _param_source(parent,parameter,"#1","#2")
    source=parent.find("CParameterSource")
    epsilon=float(source.find("f[@xs.n='snapEpsilon']").text)
    digits=int(source.find("i[@xs.n='decimalPlaces']").text)
    # The native authoring interpolator uses 1.5 * the parameter snap radius.
    # Every key must have a disjoint matching neighborhood, including the
    # closely spaced on/off guards used by view and standing/seated opacity.
    for value in keys:
        matching=[other for other in keys if abs(other-value)<1.5*epsilon]
        assert matching==[value]
    assert 3*epsilon < min(b-a for a,b in zip(keys,keys[1:]))
    assert len({round(value,digits) for value in keys})==len(keys)


def test_native_parameter_writer_rejects_keys_lost_at_serialized_precision():
    from image2live2d.irr.schema import Parameter, Keyform
    from image2live2d.backends.live2d.cmo3.model_xml import _param_source
    parameter=Parameter(id="ParamTooClose",min=0,max=1,
                        keyforms=[Keyform(value=v) for v in [0,.00001,1]])
    with pytest.raises(ValueError,match="keys collide"):
        _param_source(ET.Element("parameters"),parameter,"#1","#2")


def test_material_view_draw_order_is_explicit_and_keeps_each_view_part_order():
    from build_v4 import layer_draw_order, PAINT_ORDER
    for base in [60,78,80,90,180,220,230]:
        emitted=[layer_draw_order({"draw_order":base,"view":view})
                 for view in ["front","right","left","right_mid","left_mid"]]
        assert emitted==sorted(set(emitted))
    for view in PAINT_ORDER:
        orders=[layer_draw_order({"draw_order":base,"view":view})
                for base in [78,80,178,180,220,230]]
        assert orders==sorted(orders)
    assert layer_draw_order({"draw_order":154})==154  # Rope/palm interleave is unchanged.


@pytest.mark.parametrize("view", ["left", "left_mid", "right_mid", "right"])
def test_neck_texture_points_stay_unregistered_while_all_pose_forms_are_registered(view):
    import numpy as np
    from build_v4 import V4Builder
    from build_rig import grid_mesh
    from neck_registration import neck_point
    from image2live2d.irr.schema import Parameter, Keyform
    model=V4Builder.__new__(V4Builder)
    mesh=grid_mesh("neck_fixture",(.40,.34,.61,.43),5,5)
    source=np.array(mesh.vertices)
    model.meshes={"neck_fixture":mesh}
    keys=[-1,-.5,0,.5,1]
    model.params={name:Parameter(id=name,min=-1,max=1,keyforms=[Keyform(value=k) for k in keys])
                  for name in ["ParamHeadTurn","ParamBodyTurn"]}
    model._neck_binding({"id":"neck_fixture","view":view})
    assert np.array_equal(mesh.vertices,source)
    assert np.array_equal(mesh.vertices,mesh.uvs)
    for head in model.params["ParamHeadTurn"].keyforms:
        for body in model.params["ParamBodyTurn"].keyforms:
            actual=source+np.array(head.mesh_offsets[mesh.part_id])+np.array(body.mesh_offsets[mesh.part_id])
            expected=np.array([neck_point(p,view,head.value,body.value) for p in source])
            assert np.allclose(actual,expected,rtol=0,atol=1e-14)


def test_authoring_offset_weight_expands_to_standard_native_coordinate_forms(rig):
    """Inspect emitted XML coordinates independently of diagnostic evaluation."""
    from diagnose_rig import DiagnosticRenderer
    original,folder,_=rig
    model=original.model_copy(deep=True)
    node=next(d for d in model.deformers if d.id=="Scene_Swing")
    swing=next(p for p in model.parameters if p.id=="ParamSwing")
    gate=next(p for p in model.parameters if p.id=="ParamGlasses")
    for key in swing.keyforms:
        key.deformer_offsets[node.id]=[(.1*key.value,0.)]*len(node.grid_vertices)
    for key in gate.keyforms:
        key.deformer_offset_weights[node.id]=key.value
    root=ET.fromstring(next(e.content for e in unpack_caff(corrected_project(model,folder)) if e.path=="main.xml"))
    source=next(e for e in root.iter("CWarpDeformerSource") if e.get("xs.id") and
                e.find("./ACDeformerSource/CDeformerId").get("idstr")==node.id)
    native=[list(map(float,e.text.split())) for e in source.findall("./carray_list/CWarpDeformerForm/float-array")]
    canvas_width=model.textures[0].width
    expected=sorted(round(.1*k.value*g.value*canvas_width,4) for k in swing.keyforms for g in gate.keyforms)
    assert sorted(round(form[0],4) for form in native)==expected
    assert "deformer_offset_weights" not in ET.tostring(source,encoding="unicode"), "No authoring weight field may leak into native XML"
    renderer=DiagnosticRenderer(model,folder,res=32)
    before=renderer.evaluate({"ParamSwing":1,"ParamGlasses":0})["face_base"][0]
    halfway=renderer.evaluate({"ParamSwing":1,"ParamGlasses":.5})["face_base"][0]
    assert (halfway-before)[:,0]==pytest.approx([.05]*len(before))


@pytest.mark.parametrize("corrupt_crc", [False, True])
def test_native_caff_streaming_zip_checks_crc_without_central_directory(tmp_path, corrupt_crc):
    import io
    import zipfile
    from native_caff import read_project
    from image2live2d.backends.live2d.cmo3.caff import CaffEntry, pack_caff, _Reader

    class StreamingBuffer(io.BytesIO):
        def seek(self, *_):
            raise io.UnsupportedOperation("Native Editor ZIP streaming")

    buffer = StreamingBuffer()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("contents", b"<root>tiny editable project fixture</root>")
    stream = bytearray(buffer.getvalue().split(b"PK\x01\x02", 1)[0])
    assert b"PK\x07\x08" in stream  # actual data descriptor, no central directory
    if corrupt_crc:
        stream[stream.index(b"PK\x07\x08") + 4] ^= 1
    caff = bytearray(pack_caff([CaffEntry("main.xml", bytes(stream))], key=42))
    reader = _Reader(caff)
    reader.pos = 58
    reader.string(42); reader.string(42); reader.int64(42); reader.int32(42); reader.byte(42)
    caff[reader.pos] = 33 ^ 42  # mark the already-created ZIP stream as compressed
    path = tmp_path / "editor-fixture.cmo3"
    path.write_bytes(caff)
    if corrupt_crc:
        with pytest.raises(ValueError, match="CRC"):
            read_project(path)
    else:
        assert read_project(path)[0].content == b"<root>tiny editable project fixture</root>"
