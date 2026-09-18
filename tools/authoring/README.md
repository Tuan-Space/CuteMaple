# 模型编辑工具

日常修改请使用仓库当前 CMO3、CAN3 和分层素材，步骤见 [开发指南](../../docs/DEVELOPMENT.md)。

- `native_caff.py`：读取原生 Cubism 工程容器。
- `build_can3.py`：曲线工程生成、校验与关联工具。
- `pack_native_atlas.py`、`verify_native_uv.py`、`install_editor_export.py`：进阶图集、UV 和原生导出核验。
- 其余生成与局部变形模块是当前制作链和诊断工具的依赖，日常编辑不需要运行早期默认生成入口。

供应的序列化源码位于 `vendor/image2live2d`，许可证与固定源码哈希已随附。安装依赖运行 `scripts/setup-authoring.ps1`，不会覆盖模型或重新生成素材。
