# Maple v3 原生 Live2D 验证

验证时间：2026-09-08 18:58（UTC+8）。本次验证使用用户在 **Live2D Cubism Editor 中实际导出**的 v3 模型，来源为 `F:\cutemaple\Maple-Export`，经安装脚本合并 21 组动作和应用参数映射。运行模型为 `assets/live2d/Maple/Maple.moc3`（659,008 字节），`actualNative: true`。

## 验证结果

- 通过生产 QtWebEngine / QWebChannel / 本地资源协议加载真实 MOC3、2048 贴图、物理和官方 Cubism Web SDK 5-r.5；没有使用 PNG 回退。
- **21/21 动作**触发原生完成回调；**33 个场景截图**均有有效可见像素和几何数据；报告中没有加载、WebGL 或断言错误。
- 视线横向参数：向左 `-0.9987`、向右 `0.9966`、关闭后 `0.00146`；睡眠闭眼参数符合检查要求；打字与声音回应参数分别达到 `0.9871`。
- **10/10 固定时间探针**通过：视线、眼睛、睡眠、拖动、打字、声音、眩晕、眼镜、团扇、枫叶。探针对同一模型状态直接比较参数两端的原生渲染像素，排除了呼吸或眨眼造成的误判。
- 额外的实际 Maple 循环测试通过：`idle` 原生循环回调、暂停后恢复、`walk_right` 原生单次完成回调；输出 84,739 个有效可见像素，未报告 WebGL 错误。
- 前端 **11 项测试通过**，包括全部 21 个 Maple 动作文件的官方 Cubism 一致性检查。

复现命令：

```powershell
.venv/Scripts/python.exe tools/verify_maple_model.py
.venv/Scripts/python.exe tools/verify_live2d_runtime.py assets/live2d/Maple/Maple.model3.json --screenshot artifacts/maple-runtime-loop.png
cd web
npm test
```

完整数值报告：[report.json](../artifacts/maple-runtime/report.json)。关键截图：[待机](../artifacts/maple-runtime/idle.png)、[视线](../artifacts/maple-runtime/gaze_left.png)、[睡眠](../artifacts/maple-runtime/sleep_loop.png)、[打字回应](../artifacts/maple-runtime/keyboard.png)、[声音回应](../artifacts/maple-runtime/audio.png)、[秋千](../artifacts/maple-runtime/swing_cycle.png)、[攀爬](../artifacts/maple-runtime/climb_left.png)。截图位于本地 `artifacts` 验证目录，不进入程序运行包。

连续动态预览：[maple-preview.gif](../artifacts/maple-preview.gif)，384×384、195 个真实渲染帧、9.87 秒。内容为待机、左右视线、开心、秋千、回到待机；所有帧来自同一个运行中的原生模型，动作之间使用正常 Cubism 淡入淡出，未用姿态截图硬切模拟动画。仅为 GIF 阅读加了固定浅色底，记录说明与哈希见 [maple-preview.json](../artifacts/maple-preview.json)。用 `tools/record_maple_preview.py` 可复现。

## 外观观察与限制

已检查待机、视线、睡眠、拖动、攀爬、秋千、清理、打字、声音及配件截图。待机的脸部、发型、服装纹样和主要颜色保持 Maple 形象。

颈肩附近的透明孔洞在原标准立绘与拆层合成图中已经存在；对比后确认它们与源图一致，不能记作本次运行时新增破洞。大角度攀爬和秋千动作中的宽袖仍有可见拉伸，这是当前模型绑定的实际限制，**不能据此声称全部动作已达到完整美术验收标准**。

打字活动已验证会引起真实头部和手部动作；本次报告仅确认“打字活动回应”，不声明已验证执笔造型。技术验证不覆盖所有屏幕/DPI 下的可见桌面体验，也不代替最终用户对动作观感的判断。

模型及前端哈希、验证范围和限制的结构化记录见 [qa-native-v3.json](qa-native-v3.json)。本报告不替代 EXE 打包后的启动验证。
