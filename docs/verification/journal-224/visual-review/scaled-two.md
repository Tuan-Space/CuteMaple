# 2.2.4 · 150% / 200% 最终视觉验收

最终通过：106 / 106 张 PNG（每组 53 张），无未解决视觉问题。未修改源码。

初轮实际查看所有 106 帧，并对疑点查看原图，发现 3 帧 DisclosureIcon 彩色噪点。修复后，按每组 icon-recapture.json 实际列出的 8 帧（两组共 16 帧）再次逐帧查看，另放大核查两组 small-event-bottom 和 small-anniversary-bottom 共 4 张原图。现在箭头清楚，透明背景正常，原问题已解决。其余帧 SHA256 与初次查看记录一致。

核查结果：笔记工具栏按组换行，附件/撤销/重做图标清楚；回收站隐藏编辑工具并保留正文与附件；右侧勾选、全选/部分选择和批量按钮完整；小窗口底部正文、附件及设置页面可滚动，未见控件越界或工具/正文重叠。提醒表单错误提示与固定底部按钮分离。截图验收不替代交互或音频播放测试。

逐文件当前 SHA256、联系表、复验记录见 scaled-two.json；初次问题记录保留在 scaled-two-initial.json。

| 缩放组 | 文件 | 最终结果 | 复验 | 观察 |
|---|---|---|---|---|
| light-150 | anniversary.png | pass | 已复验 | 修复后重拍复验：提醒/周年表单卡片和字段清楚，滚动区与固定保存/取消无重叠。 |
| light-150 | event-save-error.png | pass | 已复验 | 修复后重拍复验：错误提示位于固定按钮上方，名称框与保存/取消不被遮挡。 |
| light-150 | event.png | pass | 已复验 | 修复后重拍复验：提醒/周年表单卡片和字段清楚，滚动区与固定保存/取消无重叠。 |
| light-150 | month-10.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-150 | month-11.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-150 | month-12.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-150 | notes-bulk-confirm.png | pass | 初轮已验、hash未变 | 确认范围、不可撤销说明和两个操作按钮完整，未见裁切。 |
| light-150 | notes-source.png | pass | 初轮已验、hash未变 | 源码模式仅保留附件、撤销/重做与模式切换；正文及附件区域可读。 |
| light-150 | notes-trash-all.png | pass | 初轮已验、hash未变 | 右侧勾选和全选状态清楚；批量恢复/永久删除按钮完整；只读预览未暴露编辑工具。 |
| light-150 | notes-trash-multiple.png | pass | 初轮已验、hash未变 | 右侧勾选和全选状态清楚；批量恢复/永久删除按钮完整；只读预览未暴露编辑工具。 |
| light-150 | notes-trash-none.png | pass | 初轮已验、hash未变 | 右侧勾选和全选状态清楚；批量恢复/永久删除按钮完整；只读预览未暴露编辑工具。 |
| light-150 | notes.png | pass | 初轮已验、hash未变 | 格式工具按组换行，图标清楚；正文/附件分区和滚动条可见，无重叠。 |
| light-150 | overview.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-150 | reminder-readonly.png | pass | 初轮已验、hash未变 | 只读提醒摘要、状态和关闭/发生记录按钮可读，无编辑控件。 |
| light-150 | reminders-bulk-confirm.png | pass | 初轮已验、hash未变 | 确认范围、不可撤销说明和两个操作按钮完整，未见裁切。 |
| light-150 | reminders-pending.png | pass | 初轮已验、hash未变 | 待处理说明、条目以及操作/关闭按钮完整可读。 |
| light-150 | reminders-trash-all.png | pass | 初轮已验、hash未变 | 右侧复选框完整；批量区域、选中数量、待处理反馈清楚。 |
| light-150 | reminders-trash-none.png | pass | 初轮已验、hash未变 | 右侧复选框完整；批量区域、选中数量、待处理反馈清楚。 |
| light-150 | reminders-trash-pending.png | pass | 初轮已验、hash未变 | 右侧复选框完整；批量区域、选中数量、待处理反馈清楚。 |
| light-150 | reminders.png | pass | 初轮已验、hash未变 | 实际查看该帧，未见裁切、重叠或无法辨认的控件。 |
| light-150 | settings.png | pass | 初轮已验、hash未变 | 设置卡片、长路径换行、开机启动名称与底部录音入口可读。 |
| light-150 | small-anniversary-bottom.png | pass | 已复验 | 修复后重拍并查看原始 PNG：更多设置前的箭头清楚、透明背景正常，无彩色噪点；底部说明及保存/取消完整。 |
| light-150 | small-anniversary.png | pass | 已复验 | 修复后重拍复验：提醒/周年表单卡片和字段清楚，滚动区与固定保存/取消无重叠。 |
| light-150 | small-event-bottom.png | pass | 已复验 | 修复后重拍并查看原始 PNG：更多设置前的箭头清楚、透明背景正常，无彩色噪点；底部说明及保存/取消完整。 |
| light-150 | small-event-save-error.png | pass | 已复验 | 修复后重拍复验：错误提示位于固定按钮上方，名称框与保存/取消不被遮挡。 |
| light-150 | small-event.png | pass | 已复验 | 修复后重拍复验：提醒/周年表单卡片和字段清楚，滚动区与固定保存/取消无重叠。 |
| light-150 | small-month-10.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-150 | small-month-11.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-150 | small-month-12.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-150 | small-notes-bottom.png | pass | 初轮已验、hash未变 | 格式工具按组换行，图标清楚；正文/附件分区和滚动条可见，无重叠。 |
| light-150 | small-notes-bulk-confirm.png | pass | 初轮已验、hash未变 | 确认范围、不可撤销说明和两个操作按钮完整，未见裁切。 |
| light-150 | small-notes-source.png | pass | 初轮已验、hash未变 | 源码模式仅保留附件、撤销/重做与模式切换；正文及附件区域可读。 |
| light-150 | small-notes-toolbar-bottom.png | pass | 初轮已验、hash未变 | 格式工具按组换行，图标清楚；正文/附件分区和滚动条可见，无重叠。 |
| light-150 | small-notes-trash-all.png | pass | 初轮已验、hash未变 | 右侧勾选和全选状态清楚；批量恢复/永久删除按钮完整；只读预览未暴露编辑工具。 |
| light-150 | small-notes-trash-multiple.png | pass | 初轮已验、hash未变 | 右侧勾选和全选状态清楚；批量恢复/永久删除按钮完整；只读预览未暴露编辑工具。 |
| light-150 | small-notes-trash-none.png | pass | 初轮已验、hash未变 | 右侧勾选和全选状态清楚；批量恢复/永久删除按钮完整；只读预览未暴露编辑工具。 |
| light-150 | small-notes-trash-preview-bottom.png | pass | 初轮已验、hash未变 | 只读标题/正文/附件分区清楚；无编辑、添加附件或保存提示；小窗内列表可滚动。 |
| light-150 | small-notes.png | pass | 初轮已验、hash未变 | 格式工具按组换行，图标清楚；正文/附件分区和滚动条可见，无重叠。 |
| light-150 | small-overview-bottom.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-150 | small-overview.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-150 | small-reminder-readonly.png | pass | 初轮已验、hash未变 | 只读提醒摘要、状态和关闭/发生记录按钮可读，无编辑控件。 |
| light-150 | small-reminders-bottom.png | pass | 初轮已验、hash未变 | 实际查看该帧，未见裁切、重叠或无法辨认的控件。 |
| light-150 | small-reminders-bulk-confirm.png | pass | 初轮已验、hash未变 | 确认范围、不可撤销说明和两个操作按钮完整，未见裁切。 |
| light-150 | small-reminders-pending.png | pass | 初轮已验、hash未变 | 待处理说明、条目以及操作/关闭按钮完整可读。 |
| light-150 | small-reminders-trash-all.png | pass | 初轮已验、hash未变 | 右侧复选框完整；批量区域、选中数量、待处理反馈清楚。 |
| light-150 | small-reminders-trash-none.png | pass | 初轮已验、hash未变 | 右侧复选框完整；批量区域、选中数量、待处理反馈清楚。 |
| light-150 | small-reminders-trash-pending.png | pass | 初轮已验、hash未变 | 右侧复选框完整；批量区域、选中数量、待处理反馈清楚。 |
| light-150 | small-reminders.png | pass | 初轮已验、hash未变 | 实际查看该帧，未见裁切、重叠或无法辨认的控件。 |
| light-150 | small-settings-bottom.png | pass | 初轮已验、hash未变 | 设置卡片、长路径换行、开机启动名称与底部录音入口可读。 |
| light-150 | small-settings.png | pass | 初轮已验、hash未变 | 设置卡片、长路径换行、开机启动名称与底部录音入口可读。 |
| light-150 | small-statistics-bottom.png | pass | 初轮已验、hash未变 | 统计卡片/列表分隔清楚，底部更正按钮可见。 |
| light-150 | small-statistics.png | pass | 初轮已验、hash未变 | 统计卡片/列表分隔清楚，底部更正按钮可见。 |
| light-150 | statistics.png | pass | 初轮已验、hash未变 | 统计卡片/列表分隔清楚，底部更正按钮可见。 |
| light-200 | anniversary.png | pass | 已复验 | 修复后重拍复验：提醒/周年表单卡片和字段清楚，滚动区与固定保存/取消无重叠。 |
| light-200 | event-save-error.png | pass | 已复验 | 修复后重拍复验：错误提示位于固定按钮上方，名称框与保存/取消不被遮挡。 |
| light-200 | event.png | pass | 已复验 | 修复后重拍复验：提醒/周年表单卡片和字段清楚，滚动区与固定保存/取消无重叠。 |
| light-200 | month-10.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-200 | month-11.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-200 | month-12.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-200 | notes-bulk-confirm.png | pass | 初轮已验、hash未变 | 确认范围、不可撤销说明和两个操作按钮完整，未见裁切。 |
| light-200 | notes-source.png | pass | 初轮已验、hash未变 | 源码模式仅保留附件、撤销/重做与模式切换；正文及附件区域可读。 |
| light-200 | notes-trash-all.png | pass | 初轮已验、hash未变 | 右侧勾选和全选状态清楚；批量恢复/永久删除按钮完整；只读预览未暴露编辑工具。 |
| light-200 | notes-trash-multiple.png | pass | 初轮已验、hash未变 | 右侧勾选和全选状态清楚；批量恢复/永久删除按钮完整；只读预览未暴露编辑工具。 |
| light-200 | notes-trash-none.png | pass | 初轮已验、hash未变 | 右侧勾选和全选状态清楚；批量恢复/永久删除按钮完整；只读预览未暴露编辑工具。 |
| light-200 | notes.png | pass | 初轮已验、hash未变 | 格式工具按组换行，图标清楚；正文/附件分区和滚动条可见，无重叠。 |
| light-200 | overview.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-200 | reminder-readonly.png | pass | 初轮已验、hash未变 | 只读提醒摘要、状态和关闭/发生记录按钮可读，无编辑控件。 |
| light-200 | reminders-bulk-confirm.png | pass | 初轮已验、hash未变 | 确认范围、不可撤销说明和两个操作按钮完整，未见裁切。 |
| light-200 | reminders-pending.png | pass | 初轮已验、hash未变 | 待处理说明、条目以及操作/关闭按钮完整可读。 |
| light-200 | reminders-trash-all.png | pass | 初轮已验、hash未变 | 右侧复选框完整；批量区域、选中数量、待处理反馈清楚。 |
| light-200 | reminders-trash-none.png | pass | 初轮已验、hash未变 | 右侧复选框完整；批量区域、选中数量、待处理反馈清楚。 |
| light-200 | reminders-trash-pending.png | pass | 初轮已验、hash未变 | 右侧复选框完整；批量区域、选中数量、待处理反馈清楚。 |
| light-200 | reminders.png | pass | 初轮已验、hash未变 | 实际查看该帧，未见裁切、重叠或无法辨认的控件。 |
| light-200 | settings.png | pass | 初轮已验、hash未变 | 设置卡片、长路径换行、开机启动名称与底部录音入口可读。 |
| light-200 | small-anniversary-bottom.png | pass | 已复验 | 修复后重拍并查看原始 PNG：更多设置前的箭头清楚、透明背景正常，无彩色噪点；底部说明及保存/取消完整。 |
| light-200 | small-anniversary.png | pass | 已复验 | 修复后重拍复验：提醒/周年表单卡片和字段清楚，滚动区与固定保存/取消无重叠。 |
| light-200 | small-event-bottom.png | pass | 已复验 | 修复后重拍并查看原始 PNG：更多设置前的箭头清楚、透明背景正常，无彩色噪点；底部说明及保存/取消完整。 |
| light-200 | small-event-save-error.png | pass | 已复验 | 修复后重拍复验：错误提示位于固定按钮上方，名称框与保存/取消不被遮挡。 |
| light-200 | small-event.png | pass | 已复验 | 修复后重拍复验：提醒/周年表单卡片和字段清楚，滚动区与固定保存/取消无重叠。 |
| light-200 | small-month-10.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-200 | small-month-11.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-200 | small-month-12.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-200 | small-notes-bottom.png | pass | 初轮已验、hash未变 | 格式工具按组换行，图标清楚；正文/附件分区和滚动条可见，无重叠。 |
| light-200 | small-notes-bulk-confirm.png | pass | 初轮已验、hash未变 | 确认范围、不可撤销说明和两个操作按钮完整，未见裁切。 |
| light-200 | small-notes-source.png | pass | 初轮已验、hash未变 | 源码模式仅保留附件、撤销/重做与模式切换；正文及附件区域可读。 |
| light-200 | small-notes-toolbar-bottom.png | pass | 初轮已验、hash未变 | 格式工具按组换行，图标清楚；正文/附件分区和滚动条可见，无重叠。 |
| light-200 | small-notes-trash-all.png | pass | 初轮已验、hash未变 | 右侧勾选和全选状态清楚；批量恢复/永久删除按钮完整；只读预览未暴露编辑工具。 |
| light-200 | small-notes-trash-multiple.png | pass | 初轮已验、hash未变 | 右侧勾选和全选状态清楚；批量恢复/永久删除按钮完整；只读预览未暴露编辑工具。 |
| light-200 | small-notes-trash-none.png | pass | 初轮已验、hash未变 | 右侧勾选和全选状态清楚；批量恢复/永久删除按钮完整；只读预览未暴露编辑工具。 |
| light-200 | small-notes-trash-preview-bottom.png | pass | 初轮已验、hash未变 | 只读标题/正文/附件分区清楚；无编辑、添加附件或保存提示；小窗内列表可滚动。 |
| light-200 | small-notes.png | pass | 初轮已验、hash未变 | 格式工具按组换行，图标清楚；正文/附件分区和滚动条可见，无重叠。 |
| light-200 | small-overview-bottom.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-200 | small-overview.png | pass | 初轮已验、hash未变 | 月份文字完整，日期单元和分类色点居中；小窗上下内容可滚动。 |
| light-200 | small-reminder-readonly.png | pass | 初轮已验、hash未变 | 只读提醒摘要、状态和关闭/发生记录按钮可读，无编辑控件。 |
| light-200 | small-reminders-bottom.png | pass | 初轮已验、hash未变 | 实际查看该帧，未见裁切、重叠或无法辨认的控件。 |
| light-200 | small-reminders-bulk-confirm.png | pass | 初轮已验、hash未变 | 确认范围、不可撤销说明和两个操作按钮完整，未见裁切。 |
| light-200 | small-reminders-pending.png | pass | 初轮已验、hash未变 | 待处理说明、条目以及操作/关闭按钮完整可读。 |
| light-200 | small-reminders-trash-all.png | pass | 初轮已验、hash未变 | 右侧复选框完整；批量区域、选中数量、待处理反馈清楚。 |
| light-200 | small-reminders-trash-none.png | pass | 初轮已验、hash未变 | 右侧复选框完整；批量区域、选中数量、待处理反馈清楚。 |
| light-200 | small-reminders-trash-pending.png | pass | 初轮已验、hash未变 | 右侧复选框完整；批量区域、选中数量、待处理反馈清楚。 |
| light-200 | small-reminders.png | pass | 初轮已验、hash未变 | 实际查看该帧，未见裁切、重叠或无法辨认的控件。 |
| light-200 | small-settings-bottom.png | pass | 初轮已验、hash未变 | 设置卡片、长路径换行、开机启动名称与底部录音入口可读。 |
| light-200 | small-settings.png | pass | 初轮已验、hash未变 | 设置卡片、长路径换行、开机启动名称与底部录音入口可读。 |
| light-200 | small-statistics-bottom.png | pass | 初轮已验、hash未变 | 统计卡片/列表分隔清楚，底部更正按钮可见。 |
| light-200 | small-statistics.png | pass | 初轮已验、hash未变 | 统计卡片/列表分隔清楚，底部更正按钮可见。 |
| light-200 | statistics.png | pass | 初轮已验、hash未变 | 统计卡片/列表分隔清楚，底部更正按钮可见。 |
