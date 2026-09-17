# 🔧 修改和运行源码

## 下载完整源码

推荐下载当前版本的 **CuteMaple-版本号-Source.zip**，完整解压即可得到代码、运行模型、贴图、分层素材和可编辑工程。这个 ZIP 已包含真实大文件，不需要原开发电脑或 F 盘目录。

也可以使用 Git（先安装 Git LFS）：

```powershell
git lfs install
git clone https://github.com/Tuan-Space/CuteMaple.git
cd CuteMaple
git lfs pull
```

GitHub 自动生成的 “Source code (zip)” 可能只包含 LFS 指针；请优先选上面完整源码包。

## 运行和修改程序

使用 Windows x64、Python 3.13、PowerShell 7。所有命令在源码根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\launch-dev.ps1
```

`pet_app.py` 管理桌宠状态、拖放和窗口；`monitor_ui.py` 管理监控面板；`web/src` 是 Live2D 播放器。仓库附带已构建的 `web/dist`，直接运行无需先安装 Node.js。修改播放器后，用 Node.js 22 或更新版本运行：

```powershell
npm ci --prefix web
npm run build --prefix web
npm test --prefix web
```

`launch-dev.ps1` 使用源码目录内的独立设置，方便调试。内存清理使用独立助手，源码首次运行没有编译助手时会明确提示；编译目录包后可以使用完整清理入口。

## 修改人物和动作

- `assets/authoring/model/Maple.cmo3`：当前实际原生导出的可编辑模型，已内嵌工程贴图。
- `assets/authoring/model/Maple-native-saved.can3`：由 Cubism 打开并保存的动画工程。
- `assets/authoring/model/Maple.can3`：对应的生成曲线工程，保留供曲线比对。
- `assets/authoring/source`：当前分层 PNG、图层清单、结构化模型输入及生成所用素材。
- `assets/authoring/Maple.psd`：原始拆层源画。
- `assets/live2d/Maple`：实际使用的 MOC、纹理和 19 组动作。

安装 Live2D Cubism Editor 后，模型可直接打开，也可以运行 `./open-editor.ps1`。

第一次在新位置打开动画时，运行：

```powershell
.\setup-authoring.ps1
.\open-editor.ps1 -Animation
```

脚本只把两处文件关联改成当前下载位置，不改变动画曲线，不依赖作者电脑上的路径。以后在该位置可以直接打开 CAN3。提交修改前，可运行 `./.venv-authoring/Scripts/python.exe tools/prepare_editor_project.py --portable` 恢复可搬移的关联。

CMO/CAN 中的共享参数、网格和道具隐藏曲线应保留；运行时只播放 19 组日常和登顶动作。编辑后使用 Cubism 原生导出。保持参数、网格 ID 与现有模型一致时，可在验证后更新 `assets/live2d/Maple` 中对应 MOC/纹理/动作；更改 ID 时还需同步 `Maple.pet.json` 的原生身份和接点配置。

秋千连续驱动的 `ParamSwing`、`ParamAngleZ`、`ParamLegLA`、`ParamLegRA` 使用五位小数、`snapEpsilon=0.00001`。请保留此精度，避免小幅摆动经过中心时被吸附为静止。`tools/authoring/refine_swing_precision.py` 只修正可编辑 CMO 的这四项精度，不修改网格或纹理；修改后仍需 Cubism 原生导出 MOC。对应原生位移回归位于 `web/tests/swing-native.test.ts`。

作者工具中部分生成器是现有制作链的组成模块，不是日常修改的前置步骤；首次修改请从当前 CMO/CAN 和分层文件开始，不需要重建早期工程。

## 构建软件

```powershell
.\.venv\Scripts\python.exe tools/validate_release.py .
.\build.ps1 -Mode Directory -Jobs 2 -SkipTests
```

清理助手使用 MSVC x64 原生 C++ 编译，不依赖 Python、Qt 或本机外部素材。安装 Visual Studio Build Tools 的 C++ 工具和 Windows SDK 后，可单独执行 `tools/build_native_cleaner.ps1`。生产构建不复制历史助手；`-TestBuild` 生成另名测试程序，只用于模拟故障，绝不执行真实内存清理或进入软件包。

原生故障回归使用 `tools/verify_native_cleaner.py`，只接受测试构建。最终程序的真实清理验收入口为 `CuteMaple-Live2D.exe --verify-cleanup --profile <新的测试目录绝对路径> --report <报告绝对路径> --allow-real-cleanup`：会请求一次真实 UAC，并通过桌宠入口执行三次清理，不能当作无副作用的诊断使用。`tools/validate_native_cleanup_acceptance.py` 只读取并核验结果，不执行清理。

版本号来自 `VERSION`。构建会编译主程序和独立清理助手，结果在 `dist`。这里的 `-SkipTests` 只跳过旧制作链的完整生成回归；日常改动仍应运行相关测试并检查画面。所有必需 SDK 源文件、Web 前端构建结果和素材都随仓库提供。

## 枫叶手账与安装程序

`journal_recurrence.py` 负责独立的公农历周期；`journal_store.py` 管理 SQLite、提醒队列、笔记和附件；其余 `journal_*` 模块提供界面、录音和安装退出协议。调试可使用独立的 `MEINIFENG_PROFILE_DIRECTORY`，避免触碰自己的资料库。普通首次启动会要求选择资料目录，程序目录不得作为资料库。

农历依赖固定为 lunar-python 1.4.8，时区数据固定为 tzdata 2025.2；许可保存在 `third_party`。录音和播放使用 Qt Multimedia，打包必须包含其 FFmpeg 后端，不可凭 DLL 名称删除依赖。

手账视觉配色与总览日期格位于 `journal_design.py`，六框公农历输入位于 `journal_dates.py`。`MilestoneRule` 与普通 `Rule` 通过 `parse_rule` 统一读取；调度与总览共用 `between`。天数从起始日算第 1 天，同日合并。

数据库版本为 3，迁移前在 backups 保存快照。`archived` 表示不再调度，`deleted` 区分主动删除；单次删除由 `occurrence_exclusions` 防止重建，永久删除保留最小排除键。旧历史只有存在主动删除审计记录才转入回收站。恢复周期不补发删除期间的提醒。

正文使用 Qt 可编辑文档，Markdown 为存储格式；未修改不重新序列化，复杂 HTML/扩展语法按原文显示，首次编辑原稿保存在 `note_originals`。图片仅允许资料库内的相对附件。文件复制与备份通过 `journal_jobs.py` 在后台执行，实时数据库写入仍在 Qt 主线程。

节假日由 `journal_holidays.py` 提供，内置 `assets/holidays/2026.json`，来源为 MIT 许可的 NateScarlet/holiday-cn，记录内含国务院通知链接。仅用户点击更新才联网；验证年份、格式、出处后原子替换资料库内缓存。尚未公布的空数据不能覆盖旧数据。首次打开手账、编辑或关闭均不暂停人物动作。

安装器使用 Inno Setup 6.7.3（从 https://jrsoftware.org/isdl.php 获取并核验签名）。安装该开发工具后运行：

```powershell
.\tools\build_installer.ps1 -PackageDirectory "完整目录包中的 CuteMaple-Live2D 文件夹" -Compiler "Inno Setup 的 ISCC.exe 路径"
```

安装标识固定在 `installer/CuteMaple.iss`，不要随版本改变。安装器仅清理上次安装清单中不再需要的文件，拒绝跨目录与目录链接；资料库永远不进入卸载清单。中文向导翻译来自 Inno Setup 官方仓库，文件内保留贡献者说明。

手账回归：`python -m pytest tests/test_journal.py tests/test_journal_ui.py`。`main.py --verify-journal --profile <全新测试路径> --output <结果路径>` 在隔离资料库生成界面证据；只有显式追加 `--hardware` 才短暂使用麦克风和播放测试音。不要把录音测试资料放进源码或交付包。

完整源码归档使用 `tools/package_release.py <源码目录> <输出ZIP> --source`，版本从 `VERSION` 读取。若本地保留了未提交的模型编辑稿，可显式增加 `--committed-file assets/authoring/model/Maple.cmo3`：归档使用已提交且已物化的 LFS 模型原件，本地编辑稿不会被覆盖。
