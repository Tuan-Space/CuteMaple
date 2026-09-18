# Editable Cubism project serializer subset

This authoring-only subset is derived from
[image2live2d](https://github.com/Wzhang3912/image2live2d), commit
`714e7cc9191f1ef6c9a1732c80e8b566ec127d6b`, under the accompanying Apache-2.0
[LICENSE](LICENSE). `UPSTREAM.json` records each source file, the upstream
SHA-256, the retained file SHA-256, and modifications.

Only the IR schema, CAFF container, editable CMO3 XML/project serialization,
and physics JSON helpers are included. Namespace initializers are replaced
with empty initializers so optional conversion and machine-learning modules
are not imported. `cmo3/model_xml.py` uses `cmo3/irr_helpers.py`, which retains
exactly one constant and four pure keyform functions extracted from the
upstream `moc3_emit.py` source. The binary MOC3 writer is **not** included.

Local changes to `irr/schema.py` and `cmo3/model_xml.py` also retain explicit
clipping targets, inverse clipping, and keyed draw order in the editable
CMO3 project. The local `Keyform.deformer_opacity_overrides` extension and
opacity-only driver recognition serialize native `ACDeformerForm.opacity`
on a shared view warp. Child parts inherit this visibility, avoiding the
multiplication of every eye mesh's keyforms by all view-turn keys.
The authoring-only `Keyform.deformer_offset_weights` extension multiplies
coordinate displacements while constructing the Cartesian product of native
parameter keys. It emits ordinary `CWarpDeformerForm` coordinate arrays;
there is no additional native XML field or runtime dependency. This keeps
the rest/active arm transition on one physical wrist path.
The native parameter serializer also derives `snapEpsilon` from the minimum
serialized key spacing, sets sufficient decimal display precision, and rejects
keys that collide at the serialized precision. This keeps dense material
guard keys distinct during the Editor interpolation and export steps.
`UPSTREAM.json` preserves the original upstream hashes and
records separate hashes and descriptions for these modified source files.

The application never imports this directory, and `scripts/build.ps1` excludes it
from runtime packages. Produce runtime `.moc3` files using Live2D Cubism
Editor, then verify the exported model with the real Cubism SDK.

The CMO3 writer is a third-party serializer, not an official Live2D SDK.
Successful serialization and structural tests do not establish that a
project opens correctly or that its deformations are visually acceptable.
