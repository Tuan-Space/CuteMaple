"""Small fan attachment/CMO serialization checks; native geometry is separate."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/authoring'))
from build_rig import (Deformer, DeformerType, Keyform, Parameter, Mesh, Meta,
                       Part, Rig, SemanticRole, Texture, corrected_project)
from free_arm_refinement import finish_fan_material_refinement
from diagnose_rig import DiagnosticRenderer, warp_points
from image2live2d.backends.live2d.cmo3.model_xml import _Shared, _build_deformers
from image2live2d.backends.live2d.cmo3 import unpack_caff


def small_builder():
    nodes, parts = {}, []
    key = Keyform(value=0)
    for side in ('l', 'r'):
        cuff = 'ActiveCuffMaterial_' + side
        fan = 'ActiveFanMaterial_' + side
        nodes[cuff] = Deformer(id=cuff, type=DeformerType.warp,
            grid_rows=3, grid_cols=3,
            grid_vertices=[(x/2+.12*(y/2)**2, y/2+.15*(x/2)**2)
                           for y in range(3) for x in range(3)])
        nodes[fan] = Deformer(id=fan, type=DeformerType.warp, parent=cuff,
            grid_rows=3, grid_cols=3, grid_vertices=[(x/2, y/2) for y in range(3) for x in range(3)])
        parts.extend([SimpleNamespace(id='clean_fan_'+side, parent_deformer=fan),
                      SimpleNamespace(id='hand_'+side+'_grip_palm', parent_deformer=cuff)])
        key.deformer_offsets[fan] = [(0.1, .2)]*9
        key.deformer_offset_weights[fan] = .3
        key.deformer_opacity_overrides[fan] = .8
        key.deformer_offsets[cuff] = [(.2, -.1)]*9
        # This existing mesh data includes the exact shaft at center and a
        # folded material edge. The hook must not replace opening with a scale.
        key.mesh_offsets['clean_fan_'+side] = [(.005, -.018), (.008, .021)]
        key.opacity_overrides['clean_fan_'+side] = .4
    return SimpleNamespace(nodes=nodes, parts=parts,
        params={'ParamCleanFanR': Parameter(id='ParamCleanFanR', min=0, max=1, keyforms=[key])})


def test_rigid_frame_preserves_palms_and_authored_opening_keys():
    builder = small_builder()
    before = copy.deepcopy(builder)
    finish_fan_material_refinement(builder)
    assert builder.parts == before.parts
    assert builder.params.keys() == before.params.keys()
    key = builder.params['ParamCleanFanR'].keyforms[0]
    old = before.params['ParamCleanFanR'].keyforms[0]
    assert key.mesh_offsets == old.mesh_offsets
    assert key.opacity_overrides == old.opacity_overrides
    for side in ('l', 'r'):
        cuff, fan = 'ActiveCuffMaterial_'+side, 'ActiveFanMaterial_'+side
        assert builder.nodes[cuff] == before.nodes[cuff]
        assert key.deformer_offsets[cuff] == old.deformer_offsets[cuff]
        rigid = builder.nodes[fan]
        assert rigid.type is DeformerType.rotation
        assert rigid.pivot == (.5, .5)
        assert rigid.parent == cuff
        assert rigid.grid_vertices is None
        assert fan not in key.deformer_offsets
        assert fan not in key.deformer_offset_weights
        assert fan not in key.deformer_opacity_overrides
    once = copy.deepcopy(builder)
    finish_fan_material_refinement(builder)
    assert builder == once


def test_native_serializer_links_rotation_origin_to_existing_cuff():
    builder = small_builder()
    finish_fan_material_refinement(builder)
    # Exercise the same native backend dispatcher used by the full CMO build.
    # Only the two selected rotation nodes are needed; their parent GUIDs are
    # explicitly supplied and must survive in targetDeformerGuid.
    rig = SimpleNamespace(deformers=[n for n in builder.nodes.values()
                                    if n.type is DeformerType.rotation])
    guids = {name: '#guid_'+name for name in builder.nodes}
    shared = _Shared()
    emitted = _build_deformers(shared, rig, 1024, 1024, '#coord', {}, {},
                              '#part', guids, '#root')
    assert [tag for tag, _ in emitted] == ['CRotationDeformerSource']*2
    for side, (_, ref) in zip(('l', 'r'), emitted):
        source = next(obj for obj in shared.objects if obj.get('xs.id') == ref)
        target = source.find(".//CDeformerGuid[@xs.n='targetDeformerGuid']")
        assert target.get('xs.ref') == guids['ActiveCuffMaterial_'+side]
        form = source.find('.//CRotationDeformerForm')
        assert float(form.get('originX')) == float(form.get('originY')) == 512
        assert float(form.get('angle')) == 0
        assert float(form.get('scale')) == 1
        assert form.get('isReflectX') == form.get('isReflectY') == 'false'
        assert source.find(".//carray_list[@xs.n='keyforms']").get('count') == '1'
        assert not source.findall('.//KeyformBindingSource')


def test_missing_legacy_fan_is_ignored_but_wrong_attachment_is_rejected():
    legacy = SimpleNamespace(parts=[], nodes={}, params={})
    finish_fan_material_refinement(legacy)
    builder = small_builder()
    builder.parts[0].parent_deformer = 'unexpected'
    with pytest.raises(ValueError, match='expected registered fan parent'):
        finish_fan_material_refinement(builder)


@pytest.mark.parametrize('shear,angle', [(0.,0.), (0.,.7), (.55,-.3)])
def test_offline_static_rotation_moves_origin_without_shearing_opening(shear,angle):
    """Authoring preview invariants, deliberately not a Core equivalence test."""
    c,s=np.cos(angle),np.sin(angle)
    matrix=np.array([[c,-s],[s,c]]) @ np.array([[1.7,shear],[0.,.45]])
    grid=np.array([(x/2,y/2) for y in range(3) for x in range(3)]) @ matrix.T + (.1,-.2)
    parent=Deformer(id='cloth',type=DeformerType.warp,grid_rows=3,grid_cols=3,
                    grid_vertices=grid.tolist())
    rigid=Deformer(id='fan',type=DeformerType.rotation,parent='cloth',pivot=(.5,.5))
    vertices=np.array([[.5,.5],[.5,.3],[.65,.4],[.4,.45]])
    meshes=[SimpleNamespace(part_id=name,vertices=vertices.tolist()) for name in ('rigid','warp')]
    parts=[SimpleNamespace(id=name,parent_deformer=node,opacity=1.,draw_order=i)
           for i,(name,node) in enumerate([('rigid','fan'),('warp','cloth')])]
    rig=SimpleNamespace(deformers=[parent,rigid],meshes=meshes,parts=parts,parameters=[])
    renderer=DiagnosticRenderer.__new__(DiagnosticRenderer)
    renderer.rig=rig
    renderer.meshes={mesh.part_id:mesh for mesh in meshes}
    renderer.nodes={node.id:node for node in rig.deformers}
    output=renderer.evaluate({})
    actual=output['rigid'][0]
    expected_cuff=warp_points([(.5,.5)],grid,3,3)[0]
    np.testing.assert_allclose(actual[0],expected_cuff,atol=1e-12,rtol=0)
    np.testing.assert_allclose(np.linalg.norm(actual-actual[0],axis=1),
                               np.linalg.norm(vertices-vertices[0],axis=1),atol=1e-12,rtol=0)
    # Existing warp-only semantics are intentionally unchanged.
    np.testing.assert_allclose(output['warp'][0],warp_points(vertices,grid,3,3),atol=1e-12,rtol=0)


@pytest.mark.parametrize('parent_type', ['warp', 'rotation'])
def test_corrected_project_converts_parent_frame_without_touching_source_uvs(tmp_path,parent_type):
    """Catch the native11 failure at the final CMO boundary, not the raw writer.

    The original shaft maps to rotation-local (0,0) at every opening key.
    Non-square canvas dimensions also catch incorrectly shared X/Y divisors.
    """
    from PIL import Image
    width,height=64,128
    Image.new('RGBA',(width,height),(200,100,30,255)).save(tmp_path/'fan.png')
    origin=(.5,.5)
    shaft=np.array([.526,.518])
    vertices=np.array([shaft,shaft+(.05,-.05),shaft+(.12,.03)])
    parent=(Deformer(id='parent',type=DeformerType.warp,grid_rows=2,grid_cols=2,
                     grid_vertices=[(0.,0.),(1.,0.),(0.,1.),(1.,1.)])
            if parent_type=='warp' else
            Deformer(id='parent',type=DeformerType.rotation,pivot=(.25,.35)))
    fan=Deformer(id='fan',type=DeformerType.rotation,parent='parent',pivot=origin)
    keys=[]
    for value in (0.,1.):
        scale=np.array([.3+.7*value,.4+.6*value])
        posed=np.array(origin)+(vertices-shaft)*scale
        keys.append(Keyform(value=value,mesh_offsets={'art':(posed-vertices).tolist()}))
    rig=Rig(meta=Meta(name='rotation coordinate test'),
        textures=[Texture(id='texture',path='fan.png',width=width,height=height)],
        parts=[Part(id='art',texture_id='texture',semantic_role=SemanticRole.accessory,
                    draw_order=1,parent_deformer='fan')],
        meshes=[Mesh(part_id='art',vertices=vertices.tolist(),uvs=vertices.tolist(),triangles=[(0,1,2)])],
        deformers=[parent,fan],parameters=[Parameter(id='ParamFan',min=0,max=1,default=1,keyforms=keys)])
    archive=corrected_project(rig,tmp_path)
    xml=next(e.content for e in unpack_caff(archive) if e.path=='main.xml')
    tree=ET.fromstring(xml)
    source=next(s for s in tree.iter('CArtMeshSource') if s.find('./ACDrawableSource/CDrawableId') is not None)
    static=next(c for c in source if c.get('xs.n')=='positions')
    np.testing.assert_allclose(np.fromstring(static.text,sep=' ').reshape(-1,2),
                               vertices*[width,height],atol=5e-5,rtol=0)
    editable=source.find(".//GEditableMesh2/float-array[@xs.n='point']")
    assert editable.text==static.text
    uv=next(c for c in source if c.get('xs.n')=='uvs')
    np.testing.assert_allclose(np.fromstring(uv.text,sep=' ').reshape(-1,2),vertices,atol=1e-6,rtol=0)
    forms=list(source.iter('CArtMeshForm'))
    assert len(forms)==2
    for value,form in zip((0.,1.),forms):
        coords=next(c for c in form if c.get('xs.n')=='positions')
        actual=np.fromstring(coords.text,sep=' ').reshape(-1,2)
        expected=(vertices-shaft)*[.3+.7*value,.4+.6*value]*[width,height]
        np.testing.assert_allclose(actual,expected,atol=5e-5,rtol=0)
        np.testing.assert_allclose(actual[0],(0.,0.),atol=1e-10,rtol=0)
    rotation=next(s for s in tree.iter('CRotationDeformerSource')
                  if s.find('./ACDeformerSource/CDeformerId') is not None
                  and s.find('./ACDeformerSource/CDeformerId').get('idstr')=='fan')
    form=rotation.find('.//CRotationDeformerForm')
    actual=np.array([float(form.get('originX')),float(form.get('originY'))])
    expected=origin if parent_type=='warp' else (np.array(origin)-parent.pivot)*[width,height]
    np.testing.assert_allclose(actual,expected,atol=1e-10,rtol=0)
    assert float(form.get('scale'))==1 and float(form.get('angle'))==0
