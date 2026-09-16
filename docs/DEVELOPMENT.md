# 🔧 修改和运行源码

## 下载完整源码

推荐下载 Release 里的 **CuteMaple-2.0.0-Source.zip**，完整解压即可得到代码、运行模型、贴图、分层素材和可编辑工程。这个 ZIP 已包含真实大文件，不需要你原来的电脑或 F 盘目录。

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

如果只修改界面、清理代码没有变化，可以在本机用 `-ReuseCleanerFrom <已有目录包路径>` 复用已验证的独立清理助手。构建会核对清理源码和助手全部文件的 SHA256，再将其原样复制；不匹配会停止，且不会暗中重新编译助手。已有包需包含 `SOURCE-SNAPSHOT.json` 和 `verification/retained-helper.json`。

版本号来自 `VERSION`。构建会编译主程序和独立清理助手，结果在 `dist`。这里的 `-SkipTests` 只跳过旧制作链的完整生成回归；日常改动仍应运行相关测试并检查画面。所有必需 SDK 源文件、Web 前端构建结果和素材都随仓库提供。
