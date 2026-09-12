# 美腻枫可编辑模型的复现

这里的工具生成拆层图、PSD、可编辑 Cubism 工程和动作 JSON。运行模型的 `.moc3` 必须由安装了有效授权的 Live2D Cubism Editor 导出。源码序列化通过、IR 诊断图正确、文件以 `MOC3` 开头，都不能替代真实 Editor 打开、官方 Cubism SDK 加载和视觉验收。

当前正在制作独立的 `assets/authoring/revisions/v4`，修正近侧面攀爬、手袖残片、握绳、固定悬点及登顶衔接。v4 的工程生成、真实 Editor 导出及视觉验收完成前，不应作为已验收版本发布。

历史工程 [`assets/authoring/revisions/v3/Maple.cmo3`](../../assets/authoring/revisions/v3/Maple.cmo3) 保留用户实际保存的 2048×2048 图集，已完成原生导出和21动作运行检查；但用户随后指出正面攀爬、重复手袖及秋千姿态问题，技术检查不代表这些动作已通过外观验收。`20260908-190759` 构建仅是历史验证记录。根目录 `assets/authoring/Maple.cmo3`、v3及其原生导出均保留，不得覆盖。CAN3 的真实 Editor 打开、播放和保存验证仍待完成。

## 独立环境

在仓库根目录使用 Windows x64、Python 3.13 和 PowerShell 7：

```powershell
.\setup-authoring.ps1 -Python C:\Path\To\Python313\python.exe
.\.venv-authoring\Scripts\python.exe tools/authoring/verify_setup.py
```

`requirements-authoring.txt` 固定了拆层、PSD、IR 所需的直接及传递依赖版本。安装位置为 `.venv-authoring`，与 Qt 程序的 `.venv` 分开。设置检查只导入工具、核对供应源码 SHA-256，并在内存中往返一个小型 CAFF 容器，不覆盖任何图片或模型。

可编辑工程序列化器的最小源码已随仓库放在 `vendor/image2live2d`，无需再次克隆上游。其 Apache-2.0 许可、固定提交、原始及修改后哈希和修改说明见 [NOTICE.md](vendor/image2live2d/NOTICE.md) 与 [UPSTREAM.json](vendor/image2live2d/UPSTREAM.json)。旧的可选完整克隆 `tools/authoring/image2live2d` 不参与导入，也不参与打包。

## 素材输入和生成步骤

确定性输入包括 `assets/master/美腻枫_标准立绘_v1.png`，以及 `assets/authoring/generated/face-underpaint.png`、`costume-underpaint.png`。后两张是已生成并保留的遮挡补画输入；本流程不再次调用图像生成服务。可见原始角色像素、遮罩、配准参数与补画来源保存在拆层清单和审计文件中；完整生成提示词、文件哈希和配准方法见 [`assets/authoring/image-generation.json`](../../assets/authoring/image-generation.json)。

以下命令**会重新写入**相应生成文件。手工调整过工程或图层时先保存副本；首次安装不需要运行这些命令。

```powershell
.\.venv-authoring\Scripts\python.exe tools/authoring/prepare_layers.py
.\.venv-authoring\Scripts\python.exe tools/authoring/build_rig.py --output .build/authoring-generated --runtime .build/authoring-generated/runtime
```

第一步输出 `assets/authoring/layers/*.png`、`layers.json`、`Maple.psd` 和合成检查图。上述第二步把 `Maple.cmo3`、`Maple.rig.json`、`Maple.build-audit.json`、`Maple.model3-fragment.json` 写入 `.build/authoring-generated`，把 21 组动作、物理和模型参数映射写入其 `runtime` 子目录，不覆盖当前待审核工程。工程中随机 UUID、ZIP 时间戳可能变化，因此重新生成的 CMO3 不保证逐字节相同；输入贴图哈希和结构审计用于复查。

`build_rig.py --output <目录> --runtime <目录>` 可以将工程和运行 JSON 写到独立验证目录；不要把临时探针工程当作最终美腻枫模型。`diagnose_rig.py --help` 提供离线 IR 变形诊断，其双线性变形器插值与 Cubism 插值可能不同，输出明确属于作者诊断图。

## v4 精修工程

v4 使用 `revisions/v4/generated` 内留存的局部补画输入。`image-generation.json` 记录每张输入的生成来源和哈希，`layer-extraction-audit.json` 记录机械分层多边形与注册矩阵。原正面可见像素与生成的近侧面、中间视角、活动袖、手和坐姿下裙分层保存；不是整幅角色姿态图片交叉淡化。

```powershell
.\.venv-authoring\Scripts\python.exe tools/authoring/prepare_v4_layers.py
.\.venv-authoring\Scripts\python.exe tools/authoring/build_v4.py
.\.venv-authoring\Scripts\python.exe tools/authoring/diagnose_rig.py --rig assets/authoring/revisions/v4/Maple.rig.json --assets assets/authoring/revisions/v4 --runtime assets/authoring/revisions/v4/runtime --output artifacts/authoring-v4-review
```

这些命令只更新 v4，生成 PSD、PNG 图层、CMO3、IR 和暂存运行 JSON。21 个公开动作保留原时长，另有两个内部登顶动作，各长 1.8 秒；其原生事件为 0.32 秒 `top_grab`、0.85 秒 `wall_release`、1.65 秒 `settled`。CAN3 必须根据最后一版 CMO3 的参数 GUID 重新生成，不能搭配旧版工程。

活动袖以平展的纹理骨架注册，静态变形器再将它映射到动作关节，避免先压成窄条而丢失纹理。袖口的源 UV 与手掌源 UV 不同，由 metadata 的 `cuffSourceUv`、`handSourceUv` 分别给出；QA 在图集转换后检查两点实际接触。原抱手只在 `ParamArmPoseMode=0` 显示，活动放松手、支撑手、握手由独立手形参数与模式共同选择。

作者诊断图附黑、白背景，暴露内洞和半透明残片；仍须审查转向交换区与连续动作。`Maple.build-audit.json` 记录每个变形器的控制点数量，高密度共享转向变形器尚需实际 Core 帧时和桌面渲染检查。只有根任务确认的候选工程才可进入原生图集打包、Editor 导出与 `--refinement` 验证，历史诊断目录中的失败尝试不应发布。

`view_registration.py` 使用五个源视角的二维语义网格，固定画布边界，记录眼、下颌、肩腰和裙中片的共同目标。局部材质仅在转向值宽度约 0.10 的区间内切换；下层保持不透明、上层渐变，避免普通半透明叠层使脸部露底。行走固定 BodyTurn 为 ±0.18，所有公开循环都避开混合区。网格无翻折与局部混合通过数值检查后，仍须用连续预览判定外形和纹理是否协调。

## 原生导出和验证

当前供本轮人工导出的修正候选位于 `F:\cutemaple\Maple-v4-Export-NeckFixed`。首导与 Ready 导出对应作者快照分别保存在 `v4-first-native`、`v4-before-neck-texture-fix`。本轮只把四个颈部网格的静态纹理坐标恢复为原图坐标，并将等价基线变形移回标准参数关键形；全部 9,821 个原生关键形坐标、透明度、绘制顺序与参数身份保持不变。详见 `Maple.neck-texture-fix-audit.json`。图集打包在输出前独立检查全部网格的静态坐标能通过 Editor 的输入变换重建正确 UV，超出 0.001 图集像素容差则拒绝。结构和离线回归仍不代替新的真实原生导出验收。

精确的导出前交付档案位于 `artifacts/native-v4-neck-fixed-handoff/pre-editor`，其中 `Maple.cmo3` 和 `source-Maple.cmo3` 分别是实际交付的打包与源工程副本，另有 PNG、运行资源、原始审计及逐文件哈希。用户正常保存工作工程后，可通过 UV 工具的 `--packed-cmo3`、`--source-cmo3` 参数指向该档案；不要把重新构造的副本冒充原始字节。

以下是 v4 的操作流程，执行命令本身不代表验收通过。先完成离线连续帧检查，再将确认的作者候选打包到新的导出目录；工具拒绝覆盖已有图集输出，原工程与源图保持不变：

```powershell
.\.venv\Scripts\python.exe tools/authoring/pack_native_atlas.py --source assets/authoring/revisions/v4/Maple.cmo3 --template assets/authoring/revisions/v3/Maple.cmo3 --output F:\cutemaple\Maple-v4-Export-NeckFixed\Maple.cmo3
.\.venv-authoring\Scripts\python.exe tools/authoring/build_can3.py --relink assets/authoring/revisions/v4/Maple.can3 --model F:\cutemaple\Maple-v4-Export-NeckFixed\Maple.cmo3 --output F:\cutemaple\Maple-v4-Export-NeckFixed\Maple.can3
```

用 Cubism Editor 打开导出目录中的 `Maple.cmo3`。图集已打包，无需重新排版。通过“文件 → 输出嵌入文件 → 输出 MOC3 文件”（部分版本译为“导出运行时文件”），勾选“输出隐藏部件”和“输出隐藏图形网格”，保存为同目录 `Maple.moc3`。原生导出必须产生配套 `.model3.json` 和贴图。`Maple.can3` 也需要实际打开、播放并保存验证。

安装前先做只读原生 UV 配对检查，报告输出必须使用新路径：

```powershell
node tools/authoring/audit_native_geometry.mjs F:\cutemaple\Maple-v4-Export-NeckFixed\Maple.moc3 --output artifacts/native-export-check/geometry.json
.\.venv-authoring\Scripts\python.exe tools/authoring/verify_native_uv.py --native-audit artifacts/native-export-check/geometry.json --atlas-audit F:\cutemaple\Maple-v4-Export-NeckFixed\Maple.atlas-audit.json --output artifacts/native-export-check/uv-pairing.json
```

第一个工具使用现有官方 Core，记录默认顶点、UV、参数、作者绘制顺序与实际原生渲染顺序。第二个逐网格检查完整 UV、顶点对应、三角连接和图集页，支持导出顶点重排，并使用审计中的实际图集尺寸。MOC3、Core、原始/打包工程、图集及审计输入的 SHA256 在开始和结束均核对；缺层、重复 ID、非有限值、UV 越界、错页或运行中输入变化都会返回非零。工程移动或后续重建后，可显式提供 `--packed-cmo3` 和 `--source-cmo3` 指向原版本备份，哈希仍必须分别匹配审计中的 output/source SHA256；例如首导的原始工程可使用 `assets/authoring/revisions/v4-first-native/Maple.cmo3`。这不会修改旧审计或报告。这两个工具不安装模型，也不认证动画或画面质量；配套导出的实际纹理仍需与打包 PNG 比较，不能仅靠 UV 一致接受空纹理。

安装时明确配对 v4 作者运行资源，不能默认沿用现有 v3 动作。工具先检查实际 MOC3、参数、引用、贴图与哈希，再备份现有运行模型并切换：

```powershell
.\.venv\Scripts\python.exe tools/authoring/install_editor_export.py F:\cutemaple\Maple-v4-Export-NeckFixed\Maple.model3.json --runtime-source assets/authoring/revisions/v4/runtime
.\.venv\Scripts\python.exe tools/validate_release.py .
.\.venv\Scripts\python.exe tools/verify_live2d_runtime.py assets/live2d/Maple/Maple.model3.json
.\.venv\Scripts\python.exe tools/verify_maple_model.py --refinement --atlas-audit F:\cutemaple\Maple-v4-Export-NeckFixed\Maple.atlas-audit.json --output artifacts/maple-runtime-v4-review --timeout 240
```

验证运行时需要 Qt 应用环境 `.venv`，安装方式见主 README。原生模型检查之后，仍需完整连续录像和实际桌面检查；详见 [v4 验收记录](../../docs/QA-native-v4.md)。`build.ps1` 仅装入实际运行模块、模型和前端资源；不装入作者工具、PSD、拆层图、CMO3 或第三方工程写入器。

## 可编辑动画工程

v4 的 `Maple.can3` 包含 23 个场景（21 个公开动作及两个登顶过渡），每个场景有全部 56 个 Maple 参数的可编辑曲线；模型 GUID 与同目录 CMO3 一致。历史 v3 的 21 场景、48 参数工程仍保留。CAN3 使用 100 fps 编辑网格保持周期端点准确，中间关键帧时间量化最多 5 ms；实际运行用 motion3 文件没有改写。生成器 `build_can3.py` 根据官方样本的序列化格式独立建立 Maple 的对象图，不包含样本角色或动作。结构检查不能替代 Editor 打开、播放和保存验证。

文件整体搬移后，可在 Editor 重新链接模型；也可保留现有曲线、只更新路径：

```powershell
.\.venv-authoring\Scripts\python.exe tools/authoring/build_can3.py --relink C:\Moved\Maple.can3 --model C:\Moved\Maple.cmo3
```

不要对用户已修改的 CAN3 运行普通生成模式；`--relink` 原地更新会先保存备份，并检查模型参数 GUID 一致。

结构与动作测试位于 `tests/test_maple_authoring.py` 和 CAN3 测试文件中；在同时安装应用构建依赖和作者依赖的测试环境中运行。只安装应用依赖时，可选作者测试会明确跳过缺失依赖。
