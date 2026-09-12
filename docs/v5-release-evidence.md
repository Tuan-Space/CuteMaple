# v5 发布证据接口

这些工具只读复核，不启动桌宠、清理、UAC 或修改防护。v4 原有入口不变；v5 由包内 `Maple.pet.json` 的 `refinement.version=5` 识别，不能用 CLI 字符串把 v4 证据升级。

原始 v5 模型包含 141 个唯一 drawable、67 个有限原生参数和 31 个完整动作；后续动作精修工程当前为 145 个 drawable、72 个参数，仍保留 31 个动作文件（21 类公开动作及 10 个内部片段）。精修版本必须与元数据声明的全部原生参数、drawable 库存逐项一致，不能仅满足数量。模型、源码审查与完整原生报告必须同版本，整个模型/前端库存一致；全部 mesh 的原始 UV 审计仍限制在 0.001 图集像素内，并在最终发布时重新打开原件验证。

手袖接点按实际姿态分别复核：静态 `restContacts` 保留 idle、happy、talk、petting、sleep_loop 及四个独立 rest 探针；`activeMotionContacts` 必须有 drag_left、drag_right、fall_float、land 四条 `{captureKey, binding, contacts}`。落地使用同一次动作的 `land-start` 原生起始采样，其余使用同名 capture。活动姿态必须绑定当前 generation/token/name，实际 ArmPoseMode 约为 1、两侧 HandShape 约为 0，元数据指定的活动手和袖口 opacity 均至少 0.95；两类接点均保持 0.012 画布尺寸的原始间隙上限，并核对测量坐标与距离一致。落地结束回到静态姿态不能替代起始活动手测量，睡眠体型比例门槛也保持不变。

## 桌面原件与独立观察

`run_desktop_qa.ps1` 的单次 3600 秒运行和至少 20 次启动全部保留，不能拼接时长。需要补观察时，`run_desktop_supplement.ps1` 只提供它实际记录到的字段；v5 要求每段同一 EXE、全部资源、cleaner 和 DLL 的完整目录 SHA 在运行前后保持一致。

`desktop_check.py` 的 `reactionObservations` 记录同一次真实原生 capture 的 `binding`、`captureBinding`、`typing`、`listening`、`actualVisibleEffects`。双响应必须同时有近期打字和声音、两个原生参数、画布内的叶子和音符、非零实际 Canvas 像素；只有排队的种类列表不能通过。暂停拖动必须分别有 left/right/top/cornerTop，原始 `pausedDrags` 含不变窗口/原生顶点/时钟的两次静态采样，以及至少一次真实恢复且没有补算暂停时间。静态片段与恢复片段必须属于同一 motion token。

ToDesk 来源无法由不读取按键结构的钩子推断。独立的 `cutemaple-v5-interaction-review` 记录实际观察者与方法，不是新的用户批准流程。它也记录真实的音频设备切换，机器数据必须显示同一进程中两个不同 endpointId 各自 ready、有非零采样，并在新端点之后看到原生听音回应。仅列出多个设备不能通过。

该 JSON 的字段契约如下；其中路径/SHA/PID/时间/索引必须来自已完成的原件，不能预填成功：

- 顶层：`schemaVersion: 1`、`reportKind: "cutemaple-v5-interaction-review"`、`status: "accepted"`、`reviewer`、`method`、带时区的 `reviewedAt`、实际完整 `packageSha256` 字典。
- `checks.toDeskKeyboard`：`report: {path, sha256}` 指向实际 `desktop-report.json`；`pid`、带时区的 `observedAt`、`method`、`inputSource: "ToDesk"`、`reactionIndex`，以及 `evidence: [{path, sha256}]` 指向当时的独立观察记录。所选反应必须含真实 LL hook 活动和可见原生打字参数。
- `checks.audioDeviceChange`：同样的 `report/pid/observedAt/method/evidence`，另有 `beforeHistoryIndex`、`afterHistoryIndex`，索引指向该进程原始 `providerStatusHistory`。两个索引不能指向不同会话。

provider-only 的 `observe_desktop_activity.py` 只用于诊断，不加载 PetWindow，不能替代上述桌面反应。状态历史有容量限制；缺失旧端点时保留原报告，在一次有用户实际操作的短补测中记录完整切换。

```powershell
.venv\Scripts\python.exe tools\combine_desktop_evidence.py --bundle <最终目录包中的CuteMaple-Live2D> --base-report <原20启动和一小时汇总> --native-review <实际原生异常审查> --interaction-review <实际v5观察审查> --output <新的组合JSON>
```

如果确实发生了补测，加 `--supplement <SUPPLEMENT-EVIDENCE.json>`，可重复。原长测已完整时零补测即可。组合器重新打开原报告、进程退出、日志和独立审查，并记录每个输入 SHA；finalizer 再次从原件重建，不信任缓存的 `passed`。

## 清理原件

最终 bundle 的 `cleaner/CuteMaple-Cleaner.exe` 必须有三次独立真实成功操作。输入是 `observe_cleanup.py --begin --runs 3` 在明确授权后生成的目录或 `report.json`；记录工具不申请授权。UAC 取消、只有 `--diagnose`、旧候选 helper 的结果均不能通过。

```powershell
.venv\Scripts\python.exe tools\validate_cleanup_evidence.py --bundle <最终CuteMaple-Live2D目录> --evidence <三次真实清理报告目录>
```

复核会重读 schema4 每 operation 的 request、握手、六个 worker 的 started/result、NTSTATUS/Win32 返回、supervisor 回执和实际进程退出。单步 15 秒、工作 45 秒、supervisor 60 秒、握手最多 10 秒必须未触发期限；三个 operation 不同且无残留子进程，源码与最终 helper SHA 一致。该读取验证不是远程防伪证明；明确标记为 mock/synthetic/diagnostic 的材料会被拒绝。

## 最终元数据

```powershell
.venv\Scripts\python.exe tools\finalize_release.py --package <包根目录> --bundle <包根目录\CuteMaple-Live2D> --smoke <同包smoke目录> --tests <实际通过数量> --desktop-report <上面的组合JSON> --source-qa <实际模型视觉审查摘要> --source-runtime-qa <完整原生报告> --cleanup-evidence <三次真实清理报告目录> --refined-motion-report <同模型精修原生报告>
```

任何缺项均停止且不改包。全部通过后才事务性归档原候选标签、生成准确状态和中文启动说明、保存桌面/清理原件映射并更新 SHA 清单。清理实测不证明开机注册、锁屏、休眠或所有设备情形均已验证；未覆盖范围仍须如实保留。
