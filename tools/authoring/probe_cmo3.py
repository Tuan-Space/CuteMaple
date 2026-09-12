"""Small real Editor import/export probe; never used as the delivered model."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent / 'vendor'))
from image2live2d.irr.schema import Rig, Meta, Texture, Part, Mesh, Parameter, Keyform, SemanticRole
from image2live2d.backends.live2d.cmo3 import rig_to_cmo3

source = ROOT / 'assets/master/美腻枫_标准立绘_v1.png'
verts = [(x / 8, y / 8) for y in range(9) for x in range(9)]
tris = []
for y in range(8):
    for x in range(8):
        a = y * 9 + x
        tris.extend([(a, a + 1, a + 9), (a + 1, a + 10, a + 9)])
rig = Rig(meta=Meta(name='Maple_Format_Probe'),
          textures=[Texture(id='master', path=source.name, width=1024, height=1024)],
          parts=[Part(id='MapleProbe', semantic_role=SemanticRole.other, texture_id='master', draw_order=500)],
          meshes=[Mesh(part_id='MapleProbe', vertices=verts, uvs=verts, triangles=tris)],
          parameters=[Parameter(id='ParamProbe', min=-1, max=1, default=0, keyforms=[
              Keyform(value=v, mesh_offsets={'MapleProbe': [(v * .02, 0)] * len(verts)})
              for v in [-1, 0, 1]])])
out = ROOT / 'assets/authoring/Maple_Format_Probe.cmo3'
out.write_bytes(rig_to_cmo3(rig, source.parent))
print(out, out.stat().st_size)
