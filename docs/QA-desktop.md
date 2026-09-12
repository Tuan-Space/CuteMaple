# 普通 Windows 桌面验收

在最终模型和前端已冻结后，对新的目录包运行以下命令。`$TaskExe` 必须是本次新包内实际 exe 的绝对路径，`$TaskReportRoot` 必须是新的空输出目录。不要对已撤回的 `190153`、`190759` 或 Windows 已检测的其他产物运行、重试或读取二进制。

```powershell
.\tools\run_desktop_qa.ps1 -Executable $TaskExe -ReportRoot $TaskReportRoot
```

默认执行同一程序 20 次启动检查，每次原生就绪后 20 秒，再执行 3600 秒完整场景。工具不关闭 Windows 防护、不改变图形后端、不禁用键盘/音量提供者。每次都使用独立 profile；不注册开机启动、不创建清理任务。发现相应程序或子进程的新检测后立即停止该候选的后续运行。

早期仅检查启动的候选可以使用 `-CandidateSmoke`。它只运行一次 20 秒，结果即使成功也标记 `CANDIDATE-SMOKE-PASSED-NOT-RELEASE`，不满足最终验收。离屏 `tools/smoke_package.ps1` 也不能替代普通桌面验收。

`NORMAL-DESKTOP-QA.json` 绑定程序 SHA256、全部模型文件和三个前端入口哈希、启动次数、运行时长、默认防护前后状态和观察时间。每个原始子报告自身记录三个关键模型入口的实际加载哈希；完整53文件的模型/前端基线另由模型QA和发布工具复核。切换外部资源后必须重新验收；编译时资源与实际装入资源的差异应另存 `ASSET-UPDATE.json`，不得改写编译快照。

## 真实交互

完整场景会调用公开应用方法检查暂停、显示、大小及动作，但不注入操作系统输入。鼠标视线、键盘/音频反应、拖放和显示器变化只有实际发生并被验证后才通过。没有执行的外部动作保持 pending。

`physicalDrags` 记录原生 Qt 左键按下、移动、释放，指针和窗口起终点、接触位置及当前 generation/token 的后续原生动作证据。轻点、模拟 Qt 事件、窗口未随指针移动、错误落地或被脚本打断的轨迹不算。新动作几何尚未到达时最多等待 3 秒；整个释放后检查最多 30 秒。`dragDrop=true` 后才移除 `physicalDragDrop` 待办。

## 原生异常日志

工具自动汇总每次运行非空的 `*.fault.log`，记录 `nativeExceptionObserved`、`nativeExceptionLogs`（路径、字节数、SHA256 和代码）。每次启动也有 `native-fault-review.json`。日志内容保留，不能通过关闭 faulthandler 或忽略日志获得通过。

原生异常默认 `nativeExceptionReview.status=pending`，不能自动通过最终验收。Windows faulthandler 的 “fatal exception” 文本可能来自继续搜索处理器的 vectored handler，因此还须检查后续原生 ready、退出码、子进程以及只读 WER/Application Error 记录。`0x8001010d` 是 COM 的 `RPC_E_CANTCALLOUT_ININPUTSYNCCALL`；它的字样本身既不能证明进程已崩溃，也不能证明问题已解决。

审查者必须针对最终实际日志另外保存审查后的报告，保留原始报告；不要预填成功字段。只有确实恢复的这一 COM 代码才允许设置 `reviewed-with-limitations`。审查字段须包括本次 `executableSha256`、所有对应 `faultLogSha256`、实际证据路径 `evidence`、`continuedNativeReady=true`、`cleanExitVerified=true`、`werCrashMatches=0` 和明确的 `limitations`。必须说明触发来源尚未完全定位，不能声称异常已消除。新代码、未知异常、未恢复、程序/日志哈希不匹配均不能使用该审查路径。

完成所有实际检查后，使用审查后的报告调用最终工具；仍 pending 时不要调用：

```powershell
.\.venv\Scripts\python.exe tools\finalize_release.py --package $TaskPackage --bundle $TaskBundle --smoke $TaskSmoke --tests $TaskTestCount --desktop-report $TaskReviewedReport --source-qa $TaskModelQa --source-runtime-qa $TaskRuntimeQa
```

最终工具要求 20 次相同程序/资源启动、3600 秒普通运行、全部必要交互、匹配的离屏资源校验和助手无权限诊断。原生异常限制会写入发布验证文件和说明。被标记 `requiresRecompile` 的旧候选不能转为最终包。

## 长测后的独立真实交互补测

原始20次启动和3600秒长测已技术完成，但用户当时缺席的真实操作仍未发生时，可以对**同一冻结EXE和全部53资源**补做观察，无需重跑一小时。先等原长测退出，再运行以下命令；`$TaskBaseReport` 指向未改写的原始 `NORMAL-DESKTOP-QA.json`，补测输出使用新的空目录：

```powershell
.\tools\run_desktop_supplement.ps1 -Executable $TaskExe -BaseReport $TaskBaseReport -ReportRoot $TaskSupplementRoot -DurationSeconds 300
```

补测调用已有程序的 `--verify-desktop --scenario startup`。普通图形后端、键盘、音量、鼠标、原生Qt拖放和屏幕观察器仍启用，跳过长测的自动动作编排，便于用户实际按键、移动鼠标、拖放桌宠或改变显示器/工作区。工具不注入输入，不修改原长测，也不会把未发生的检查写成通过；默认只观察5分钟，记录本次独立PID、起止时间、原报告、退出/子进程、原异常日志及防护前后状态。`SUPPLEMENT-EVIDENCE.json` 的 `RECORDED-NOT-RELEASE` 只表示录制成功。

补测仅能提供视线、键盘、音量、拖放、显示器变化这五类实际观察；其他生命周期检查、20次启动及连续3600秒始终必须来自原base/full。两段时间不相加，不覆盖原始 `passed`、`checks` 或 `review` 字段。新增原生异常必须另行真实审查，并在新的审查文件中覆盖base与补测**全部**实际fault SHA；原21个日志的审查不能自动覆盖第22个。

将独立审查文件和原始证据组合为新manifest：

```powershell
.\.venv\Scripts\python.exe tools\combine_desktop_evidence.py --bundle $TaskBundle --base-report $TaskBaseReport --supplement "$TaskSupplementRoot\SUPPLEMENT-EVIDENCE.json" --native-review $TaskNativeReview --output $TaskCombinedReport
```

若原长测本身已记录所有必需的真实交互，省略 `--supplement` 即可；无需额外启动。`--native-review` 可接收顶层审查字段，也可接收含 `nativeExceptionReview` 的完整独立审计文件。其绑定的 `inputSha256` 和 `{path, sha256}` 证据会重新打开核对。有任何实际观察仍缺失时，组合工具拒绝生成通过结果。

最终工具的 `--desktop-report` 使用 `$TaskCombinedReport`。它会再次打开原base aggregate、原soak、20份原启动、补测及其进程/异常日志，验证SHA、PID、各自时间区间和实际观测，再重建派生结论；不直接信manifest中的缓存布尔值。组合manifest及全部引用原件会随最终包存档。所有门槛通过后才以事务提交交付元数据：归档原 `BUILD-STATUS.json`、候选提示及原SHA清单，再更新当前状态、中文说明和完整SHA清单；验证或写入失败会保留/恢复原候选状态。

## v4 模型证据与外观审查

最终工具必须显式传入 `--source-qa` 与 `--source-runtime-qa`，不再默认使用旧 v3 报告。`validate_release.py` 的单独 `ok` 只表示文件清单通过，不是最终发布批准。

完整原生报告必须由 `verify_maple_model.py --refinement --atlas-audit <本次图集审计> --native-uv-audit <本次完整原生UV审计>` 产生，并且 `ready`、`actualNative`、`refinement`、`passed` 为 true、`errors` 为空。报告的 `reportKind` 为 `cutemaple-native-model-qa`；`modelRevision=v4` 根据实际元数据中的 refinement 版本、原生参数、23 个实际加载并完成的动作确认，不根据录制命令中的版本文字确认。录制工具的 `passed=true` 仅表示录制成功，不能代替该报告。

原生 UV 证据先通过官方 Core 的 `tools/authoring/audit_native_geometry.mjs` 读取真实 MOC，再由 `tools/authoring/verify_native_uv.py` 与对应冻结的工程、图集进行全量配对。下面的变量必须指向本次实际文件；每份输出都用新路径保存：

```powershell
node tools/authoring/audit_native_geometry.mjs $TaskMoc --output $TaskGeometryAudit
.\.venv\Scripts\python.exe tools/authoring/verify_native_uv.py --native-audit $TaskGeometryAudit --atlas-audit $TaskAtlasAudit --packed-cmo3 $TaskPackedCmo --source-cmo3 $TaskFrozenSourceCmo --output $TaskNativeUvAudit
.\.venv\Scripts\python.exe tools/verify_maple_model.py $TaskModel --refinement --atlas-audit $TaskAtlasAudit --native-uv-audit $TaskNativeUvAudit --output $TaskNativeQaDirectory
```

完整 UV 审计必须证明 134 个唯一网格全部通过，三角拓扑一致，最大图集坐标误差不超过 0.001 像素，并绑定实际 MOC、官方 Core 几何结果、冻结源工程、打包工程和图集的 SHA256。最终工具会重新打开原始审计、复查所有绑定文件，并核对 `nativeUvAudit` 摘要；只写 `verified=true` 或 130/134 通过均不够。原始审计会随最终包保存为 `NATIVE-UV-QA.json`。保留绑定的冻结证据路径；不能在验收后覆写它们。

图集报告分别记录 `atlasPixelsMatch` 和 `atlasCoordinateMappingVerified`。Editor 导出的透明 RGB 或预乘颜色往返差异不应伪称像素完全相同。坐标兼容只允许大小、alpha、完全不透明 RGB 及 alpha 大于 8 的整数预乘 RGB 全部一致，alpha 1–8 边缘差异单独记录且仍需视觉审查。每页审计必须绑定实际导出与坐标参考图集的哈希；最终工具会重新解码实际包内图集核对，而不只信报告布尔值。

源模型 QA 摘要必须填写 `modelVersion=v4`、`actualNative=true`、通过且无错误的 `technicalResults`，并以 `fullReportSha256` 绑定这份完整原生报告。摘要 `files` 中每个相对路径的 `sha256`，以及完整报告的 `resourceHashesAtStart`、`resourceHashesLoaded`、`resourceHashesAtEnd`，必须精确覆盖并匹配最终包里的全部模型与前端文件；包括动作、元数据、图集、着色器和许可证。旧资源、缺项、运行中变更均拒绝。

原生报告保留自动生成的 `visualReview=pending-human-review`。实际逐帧视觉审查完成后，在独立的源 QA 摘要中记录 `visualReview` 对象：`status=accepted`、实际审查证据路径列表 `evidence`，以及相同的 `fullReportSha256`；同时注明审查者 `reviewer` 和实际方法 `method`。审查者可以是检查过相应截图、动作帧的 Codex，此字段不增加用户批准步骤。未看过或未接受的画面不能填写 accepted；不要改写原生报告来伪造视觉审查。`diagnosticOnly=true` 或 `knownInvalidNeckUV=true` 的证据始终拒绝。

普通桌面报告还须保留实际键盘/音频事件计数、匹配视线的观测、物理拖放及释放后的原生活动、显示器变化后的窗口检查。仅将 `checks` 改成 true 不足以通过；相应外部检查仍在 pending 列表时也拒绝。20 次启动须为独立的运行记录，每次至少 20 秒且正常退出。其模型/前端哈希必须匹配本次最终包；防护状态记录和检测清单也不可省略。其余未执行的外部检查继续保留 pending，不据此声称已验证。

参考：[Microsoft COM 错误码](https://learn.microsoft.com/en-us/windows/win32/com/com-error-codes-3)、[CPython 3.13.7 faulthandler 源码](https://raw.githubusercontent.com/python/cpython/v3.13.7/Modules/faulthandler.c)、[Windows vectored exception handling](https://learn.microsoft.com/en-us/windows/win32/debug/vectored-exception-handling)。
