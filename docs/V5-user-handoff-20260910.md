# v5 native13 user test handoff

The user requested: “我自己测试就行，你弄好了就结束吧”. Package preparation is complete; real cleanup, ToDesk/audio and extended desktop acceptance are left to the user. This supersedes the earlier requirement to finish agent-run manual/long-duration acceptance before handing over. The original `UNVERIFIED` build status and all pending evidence fields remain intact; `finalize_release.py` was not called and its gates were not weakened.

- Source build: `7117d6c8a8be1f0190287d907ae33a5fa5640e34`, clean checkout at build time.
- Package: `dist/CuteMaple-Live2D-Directory-20260910-182350`.
- EXE SHA256: `792dca60f525b10d900f1a2effaa58f4ed3a629061b841a30a301d3a82781647`.
- Helper SHA256: `d882042a2694f0590ea3a13fdf249aca3257b232e7a0a5ec7c5c146983b5697a`.
- Distribution ZIP: `F:\cutemaple\美腻枫-v5-动作修复版-20260910.zip`, 202760273 bytes, SHA256 `49586ed843d01ca81522fa8290d56cd013790dd36d9887d4ca4cd58cbbc28958`. All 623 bundle files are included and unchanged; ZIP integrity was verified.
- Local entry: `F:\cutemaple\美腻枫 v5（动作修复版）.lnk`, targets the above package EXE.

Completed verification: 955 Python tests passed, one Windows privilege-dependent symlink fixture skipped, no failures; 43 frontend tests passed. Regression original is `.build/stage-20260910-182350/regression/summary.json`, SHA256 `c6d9227376262912bed6d576a942d6c7083be403dc659f531542b2dc91cc2160`. The complete production renderer report covers 31 motions and 68 captures, with no errors: `artifacts/native-v5-final-runtime-13/report.json`, SHA256 `af664ac483d5a117919202f5ed811b05eb6b201a4ed6f49730d4b7ce6358fb1d`. Its independently recorded model review and strict source-evidence validation pass. Packaged startup and renderer smoke evidence is in `artifacts/native-v5-final-smoke-13`; this is explicitly not an ordinary desktop hour or real-input test.

Original model/animation exports and diagnostic limitations remain documented in [V5-native13-review.md](V5-native13-review.md). Runtime promotion preserved the previous 38 model files in `artifacts/native05-before13-runtime` and their hashes in `artifacts/native05-before13-runtime-hashes.json`. The v4 EXE, ZIP and shortcut were not replaced.

The user approved one new-helper UAC attempt before taking over testing, but no UAC was initiated and no task authorization was changed. Ordinary desktop permission confirmed that the existing task still points to the v4 `20260909-100835` helper. XML backup: `artifacts/native-v5-final-task-before-13.xml`, SHA256 `cf9bb2048178c72440655a1e4bb5ce6f872ce746a6b6d8f6bb74578ab2781200`. A sandbox-only query had reported not found; that was not the actual task state. Formal settings were backed up before the normal program launch; no migration was needed.

One normal package process was launched for the intended UI test. The computer tool did not expose its Qt pet window; the app and its audio child remained running. No crash was established, fault logs were empty, and a read-only Defender check showed all four protection flags enabled with no matching detection events. This limited observation is not recorded as desktop acceptance. The user is instructed to use “性能与内存 → 修复清理授权…” before testing the new helper.

No three-run real cleanup, rapid-click UI result review, 20-start sequence, 60-minute desktop run, or actual ToDesk/audio-device switch acceptance is claimed. The ZIP includes Chinese startup/use instructions and `USER-TEST-HANDOFF.json` stating these limits.
