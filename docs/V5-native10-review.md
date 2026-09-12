# Native revision 10 review — not a release

The source snapshot is commit `382c832`. Cubism Editor 5.3.04 exported the packed HeadShoeFan project on 2026-09-10. The original nine export/editable files are preserved byte-for-byte under `assets/authoring/archives/v5-head-shoe-fan-native-20260910/`, with `HASHES.json`.

The MOC3 SHA-256 is `a52b2d920e86763d48b27f4d95265d4a756d79daaadb00cd1d118cdfedb89b74`. All 145 drawable geometries and all 145 UV mappings passed the existing checks; maximum UV error was 0.0007240773440362347 atlas pixels, below the unchanged 0.001 limit. The isolated installation is `artifacts/native-v5-head-shoe-fan-export-10/Maple`. Canonical runtime assets and the v4 rollback package were not promoted or replaced.

## Actual findings

- Both left and right continuous native recordings completed, with 145 and 142 captured frames respectively. This certifies recording coverage, not visual acceptance. The root reviewer inspected the climb/transfer/swing contact sheets and individual left frames 0059, 0060 and 0063.
- The revised left transfer shoe is less compressed and pointed. Original 09b parameter replays are separately labelled in the fan/shoe audit; they are not frames from the new recording.
- Hair outlines still visibly overlap during the turn. The 145-frame actual Core head audit found a maximum bun landmark separation of 31.7428 pixels at a 384-pixel canvas. Source landmark agreement had omitted a child hair lattice and used bilinear interpolation where the native export uses triangular interpolation. This is an unresolved visual defect in revision 10.
- The fan remains compressed in ground/top poses. Actual full-open material singular values improved from approximately `[1.052, 0.235]` to `[1.066, 0.567]`; that improvement does not establish a round fan. The root reviewer inspected actual cleanup frames 0025, 0066 and 0130. Shaft/cuff and shaft/hand attachment errors were below 0.00018 pixels in the measured samples.
- The cleanup recording did not satisfy its last resting-tail coverage check. Its original `passed: false` evidence and frames are retained; no GIF or successful-recording claim is substituted. This was a motion preview and did not execute system cleanup.

## Native animation round trip

Cubism opened the new BEZIER animation copy, played idle and happy, and saved a separate `Maple-native-saved.can3`. All 31 scenes, 2,232 parameter curves, key counts and key times remained present. The strict original equality comparison remains false.

The independent diagnosis found no Bezier control-value changes. All 16 changed anchor values in the visited scenes exactly match the installed Editor's `floor(value * 10000 + 0.5) / 10000` operation; maximum change is 0.000025. Control-time differences, at most 0.00004 frames (0.4 microseconds), match float storage and restricted one-third-time arithmetic. Evidence and installed-class provenance are in `artifacts/native-v5-head-shoe-fan-can-10/independent-roundtrip-diagnosis.json`. The packed CMO3 hash was rechecked after the Editor closed; playback's temporary model pose was not saved.

## Remaining delivery gates

Revision 10 is rejected for visual delivery. A subsequent source revision must fix the actual hair hierarchy and fan registration, undergo native export, quantitative checks and continuous visual review. Only an accepted model can be promoted and compiled. Final compiled cleanup (three real six-step operations plus repeated-click handling), genuine ToDesk/audio/device-change interaction, paused edge/top dragging, 20 start/exit cycles and a single 60-minute run must then use the same final directory package. Earlier bundle evidence cannot be reused for that package.
