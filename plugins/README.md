# SaberLab Plugins（第一方插件目录）

本目录是 SaberLab 的**第一方插件**位置：应用启动时按插件约定自动检测并加载。
当前**不开发第三方插件系统**（无专用接口/规范），本目录仅承载第一方插件。

## 当前插件

| 插件 | 说明 | 许可证 | 放置方式 |
|---|---|---|---|
| `chro/` | 3D 回放组件（Local-ChroViewer 构建产物） | GPL-2.0-only（外部独立项目） | 将 Local-ChroViewer 的 `saberlab/chro/` 分发产物放入本目录（含其 LICENSE） |

### chro 插件（3D 回放）

- 检测路径：`plugins/chro/index.html` 存在即被后端挂载为 `/chro/`；
  `/api/status` → `chro.available`；详情页「查看回放」= iframe 或灰字提示
- 源码**不在本仓库**（许可证边界）：构建于独立项目
  `../Local-ChroViewer`（GPL-2.0-only，ChroViewer 移植），其 SaberLab 分发目录为
  `saberlab/chro/`；此处仅存放**构建产物**
- release 发布包可集成该插件（`plugins/chro/` + 自带 GPL-2.0 LICENSE，
  独立作品聚合 `mere aggregation`）
- 版本指令（后续规划）：支持从 Local-ChroViewer 发布渠道自动下载/更新
