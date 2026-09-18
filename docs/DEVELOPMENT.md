# 🔧 修改、构建与完整复现

## 获取完整源码

从 [GitHub Release](https://github.com/Tuan-Space/CuteMaple/releases/latest) 下载手动上传的 **CuteMaple-版本号-Source.zip**。它包含真实大文件、完整源码、模型和源画，附 `FILE-HASHES.json` 及 `SOURCE-REVISION.json`。GitHub 自动生成的 “Source code (zip)” 可能只有 Git LFS 指针，不能替代完整源码包。

也可以安装 [Git](https://git-scm.com/downloads) 和 [Git LFS](https://git-lfs.com/) 后执行：

```powershell
git lfs install
git clone https://github.com/Tuan-Space/CuteMaple.git
cd CuteMaple
git lfs pull
```

以下命令在解压后的源码根目录执行。路径可以不同，不需要作者电脑上的目录、缓存或其他项目。

## 开发环境

| 工具 | 验证版本／用途 | 获取位置 |
| --- | --- | --- |
| Windows x64 | Windows 10/11，桌面程序与原生助手 | Windows 自带 |
| Python | CPython 3.13；验收使用 3.13.7 | [python.org](https://www.python.org/downloads/windows/) |
| PowerShell | 7，用于启动和打包脚本 | [Microsoft](https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows) |
| Node.js | 22 或更新；验收使用 24.14.1，仅修改前端时需要 | [nodejs.org](https://nodejs.org/) |
| MSVC／Windows SDK | Visual Studio 2022 17.14.37027.9；Windows SDK 10.0.26100.0；安装 C++ x64 工具 | [Microsoft](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022) |
| Inno Setup | 6.7.3，构建安装器 | [jrsoftware.org](https://jrsoftware.org/isdl.php) |
| Live2D Cubism Editor | 5.3.04，编辑 CMO/CAN 并原生导出 MOC | [Live2D](https://www.live2d.com/en/cubism/download/editor/) |

Python 构建及测试依赖固定在 `config/requirements-build.txt`，模型工具依赖固定在 `config/requirements-authoring.txt`；前端依赖锁定在 `web/package-lock.json`。无需下载额外的私人素材。Cubism Editor 的功能与授权按官方许可使用。

## 运行与修改程序

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r config/requirements-build.txt
.\scripts\launch-dev.ps1
```

`main.py` 是简短启动入口。`src/pet_app.py` 管理人物和窗口，`src/monitor_ui.py` 管理监控面板，`src/journal_*.py` 管理手账，`web/src/` 是 Live2D 播放器，`native/cleaner/` 是原生清理助手。

开发启动器使用 `.build/dev-profile` 中的独立设置并禁用自启注册。测试手账时使用新的资料目录，不要选择个人资料库。源码入口也支持绝对路径启动，不依赖当前工作目录。

仓库已提供 `web/dist`，运行无需 Node.js。修改播放器后执行：

```powershell
npm ci --prefix web
npm run build --prefix web
npm test --prefix web
```

## 修改模型和源画

- `assets/authoring/model/Maple.cmo3`：与发布运行模型匹配的可编辑工程，贴图已内嵌。
- `assets/authoring/model/Maple-native-saved.can3`：Cubism 原生保存的动画工程。
- `assets/authoring/model/Maple.can3`：生成曲线工程，保留用于修改和比对。
- `assets/authoring/source/`：当前分层 PNG、图层清单、结构化模型输入与制作素材。
- `assets/authoring/Maple.psd`：分层源画；`assets/master/`：原始立绘。
- `assets/live2d/Maple/`：播放器使用的 MOC、贴图和 19 组动作。

```powershell
.\scripts\setup-authoring.ps1
.\scripts\open-editor.ps1
.\scripts\open-editor.ps1 -Animation
```

动画入口将工程内文件关联改为本次下载的位置，不改变动画曲线。移动工程或提交前可恢复相对关联：

```powershell
.\.venv-authoring\Scripts\python.exe tools/prepare_editor_project.py --portable
```

建议先复制模型工程再编辑。修改后使用 Cubism 原生导出；保持参数、网格 ID、物理配置和动作引用一致，核验后替换运行文件。改变 ID 时还需同步 `Maple.pet.json` 的身份和接点配置。秋千相关小幅参数的五位小数与 `snapEpsilon=0.00001` 应保留。

`tools/authoring/` 中的旧命名模块仍有被当前制作链调用的公共函数，因此保留。日常优化从当前工程开始，不需要重建历史版本；不要把历史生成器入口当成一键还原当前原生模型的命令。

## 构建完整安装包

安装表格中的 C++ 工具与 Inno Setup，然后执行：

```powershell
.\.venv\Scripts\python.exe tools/validate_release.py .
.\scripts\build.ps1 -Mode Directory -Jobs 2 -SkipTests
.\tools\build_installer.ps1 -PackageDirectory "构建输出中的 CuteMaple-Live2D 文件夹" -Compiler "Inno Setup 的 ISCC.exe 完整路径"
```

构建从 `src/` 暂存程序模块，打包运行资源、Qt Multimedia 后端和第三方许可，同时从本包 C++ 源码编译独立助手。输出在 `dist/`。`-SkipTests` 只跳过构建前回归，发布前仍需按下节验证。`VERSION` 是软件与安装器版本来源。

单独编译助手可以运行 `tools/build_native_cleaner.ps1`；默认输出到 `artifacts/native-cleaner`，源码程序可直接发现该助手。`-TestBuild` 生成模拟故障专用程序，不执行真实清理，不进入发布包。安装器保持固定安装标识，用户资料库不进入卸载清单。

## 完整性与回归

```powershell
.\.venv\Scripts\python.exe tools/verify_source_bundle.py .
.\.venv\Scripts\python.exe tools/run_regression.py --project-root . --output-dir artifacts/regression-new
```

完整性工具核对源画、模型、动画、运行资源及源码清单，不允许缺失文件、LFS 指针或模型引用指向包外。回归结果写入新的 `artifacts/` 目录，源码 ZIP 无需初始化 Git 即可运行。前端回归另用 `npm test --prefix web`。

`tools/verify_journal_polish.py` 可在隔离资料库检查五个主页面、编辑器和媒体状态；通过 `QT_SCALE_FACTOR` 测试不同缩放。模拟录音不会打开麦克风。`main.py --verify-journal --profile <新的绝对路径> --output <结果路径> --playback` 使用生成的静音 WAV 验证播放，不录音。

清理测试默认使用模拟助手。真实清理验收必须显式使用 `--verify-cleanup ... --allow-real-cleanup`，会请求 UAC 并执行实际清理，不能作为无副作用检查。

## 源码归档

```powershell
.\.venv\Scripts\python.exe tools/package_release.py . "输出源码ZIP路径" --source --revision HEAD
```

归档只读取指定 Git 提交及其本地 LFS 对象，包含本次提交号与逐文件校验值；不读取工作区修改或未跟踪文件。请先提交准备发布的代码，并执行 `git lfs pull` 补齐素材。未提交的模型稿会留在本地，归档使用已提交的匹配工程。

历史截图、录屏、日志和批量验收产物不进入当前源码；必要测试输入继续保留在 `tests/fixtures/`。完整复现指可以从公开代码和素材构建出功能一致的软件，并不要求不同机器的编译二进制逐字节相同。
