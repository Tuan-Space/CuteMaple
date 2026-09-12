# CuteMaple Cubism renderer

This renderer uses the official, vendored Cubism SDK for Web **5-r.5**. It loads real MOC3 models and Cubism motion curves. A missing model, texture, motion, or unsupported expression is reported to the host; it never labels an image fallback as Live2D.

Build with Node.js 20 or newer:

```powershell
cd web
npm ci --ignore-scripts
npm test
npm run build
```

`dist/` is the complete offline runtime. It contains the Core runtime, all 13 official GLSL shader sources, and notices; Python packaging must include this directory and `assets/live2d`. The source archive URL and SHA256 are in `third_party/CubismSdkForWeb-5-r.5/PROVENANCE.json`.

## Host protocol

The page is `cutemaple://app/web/dist/index.html`, with a read-only scheme handler mapping paths into the bundle root. Register the Qt WebChannel object as `petBridge`, exposing the signal `command(str)` and the slot `report(str)`. Both carry JSON objects.

After wiring the signal, JavaScript sends `connected`. Python sends `configure` with `modelUrl` (normally `cutemaple://app/assets/live2d/Maple/Maple.model3.json`). JavaScript sends `ready` only after Core has validated the MOC3, loaded all referenced textures and motion groups, compiled and linked the asynchronous R5 shaders, and rendered a frame. `ready.states` lists the real groups.

Commands:

* `play`: `name`, integer `token`, `playback` (`loop`, `one_shot`, `counted_loop`), optional `fade` in seconds. `durationMs` and `cycles` are host metadata; native motion duration governs the renderer.
* `gaze`: normalized `x` and `y` in [-1,1], where positive y means up, and `enabled`.
* `pause`: `paused` freezes all animation, gaze smoothing, expressions, and physics.
* `expression`: `name`, `active`, optional `intensity` in [0,1]. Different expressions may be layered.
* `resize`: redraws at the current CSS dimensions and device pixel ratio. Python controls window size; renderer maintains the source canvas aspect ratio.

Reports include a `frame-ready` event with generation/token/name after drawing the current action. Python waits for the actual compositor frame before showing the character. `cycle` or `finished` carries `token`, `name`, `cycle`; `geometry` carries `token`, `name`, normalized `[x,y,width,height]` bounds and `anchors`; `error` carries `message`. Cycles come directly from R5 Cubism motion callbacks. Fade-out callbacks from interrupted motions are ignored. Python controls transitions for counted loops.

## Maple model contract

`FileReferences.Motions` must contain the 17 daily action names and 2 top transitions from `src/protocol.ts`, each with its own authored motion (the first entry is used). Include the EyeBlink group and standard Cubism eyes, head, and breath parameters. Supply expressions as normal `exp3.json` references or use the optional metadata mapping below.

Add `"CuteMaple": {"Metadata": "Maple.pet.json"}` to `model3.json` to configure Maple-specific parameters and contact anchors. Metadata is JSON:

```json
{
  "parameters": {"eyeX": "ParamEyeBallX", "eyeY": "ParamEyeBallY", "headX": "ParamAngleX", "headY": "ParamAngleY", "headZ": "ParamAngleZ", "bodyZ": "ParamBodyAngleZ", "breath": "ParamBreath"},
  "anchors": {"head": {"drawable": "ArtMeshHead"}, "ground": {"drawable": "ArtMeshFoot", "edge": "bottom"}},
  "states": {"climb_left": {"anchors": {"left": {"drawable": "ArtMeshLeftHand", "vertex": 2}}}},
  "expressions": {"glasses": [{"id": "ParamGlasses", "value": 1, "blend": "Overwrite"}]}
}
```

An anchor can reference a live mesh vertex, a live mesh edge (`top`, `bottom`, `left`, `right`), a live mesh centroid (omit `vertex` and `edge`), or a static normalized source-canvas point. Edge selectors remain correct when Editor optimizes vertex ordering during export. State anchor overrides apply on top of common anchors. Anchor names used by Python are `head`, `ground`, `left`, `right`, and `top`. If omitted, animated visible mesh bounds supply approximate anchors; explicitly declared anchors are validated against the real MOC3 before readiness. Authored contact anchors are preferred for climbing and swing support.

Expression names are `keyboard`, `audio`, `dizzy`, `smile`, `blush`, `glasses`, `fan`, and `maple`. The default dedicated parameters are `ParamTyping`, `ParamListening`, `ParamDizzy`, `ParamEyeLSmile`/`ParamEyeRSmile`/`ParamMouthForm`, `ParamCheek`, `ParamGlasses`, `ParamFan`, and `ParamMaple`. `ParamTypingPulse` drives cyclic writing movement while keyboard activity is present. A metadata or exp3 mapping overrides the defaults. Bindings use `Add`, `Multiply`, or `Overwrite`, and every effect fades in and out smoothly.

For development, serve the repository root on localhost and open `/web/dist/index.html?model=/assets/live2d/Maple/Maple.model3.json`. This explicit browser preview plays the real `idle` motion and logs reports. No network service is needed by the packaged pet.

## Native runtime verification

From the repository root, with the Python dependencies installed:

```powershell
.venv/Scripts/python.exe tools/verify_live2d_runtime.py assets/live2d/Maple/Maple.model3.json --screenshot artifacts/runtime.png
.venv/Scripts/python.exe tools/verify_maple_model.py
```

The first check validates real Core loading, native loop and one-shot callbacks, live geometry, and nonempty WebGL pixels through the production Qt host. The Maple check exercises every original action and all overlays, writes per-state screenshots, and compares same-time rendered parameter extremes. The fixed-time probes prevent unrelated breathing or blinking from making an unrigged parameter appear to work. Both run offscreen and copy model resources into an isolated temporary bundle.

The Maple report is `artifacts/maple-runtime/report.json`. All checks must pass, and the screenshots must still be reviewed for shape, contact, continuity, and preservation of Maple's appearance. Verification-only capture/probe functions are installed solely when the local page has `?diagnostics=1`; the normal pet URL does not expose them.
