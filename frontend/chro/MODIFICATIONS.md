# ChroViewer 移植（SaberLab 修改版）说明

本目录是 [Umbranoxio/chroviewer](https://github.com/Umbranoxio/chroviewer)
（GPL-2.0，ChroMapper 衍生）的移植与修改版，整体以 **GPL-2.0** 发布
（见同目录 [LICENSE](LICENSE)）。

上游：https://github.com/Umbranoxio/chroviewer

## 修改清单（相对上游）

| 文件/区域 | 改动 |
|---|---|
| `src/sources/saberlab/provider.ts` | **新增**：SaberLab 本地谱面数据源（对接 `/api/maps/{hash}/package`，本地优先、远程默认关闭） |
| `src/routes/__root.tsx` | `RootDocument` 改为 fragment（文档结构由 index.html 提供）——修复 React 19 `createRoot(#root)` 与 `<html>/<head>/<body>` 根标记组合导致的 selectionchange 主线程死循环 |
| `src/main.tsx` | `createRoot(#root)` 纯客户端挂载；移除诊断心跳日志 |
| `src/modules/viewer/use-viewer-remote-source.ts` 等 | 删除远程源入口（`/api/source` 改写、link 加载分支、source-picker 的 link UI）——纯本地 |
| 孤儿文件 | 删除 `environment-worker.ts` / `environment-worker-protocol.ts`（无引用） |
| 诊断日志 | 移除 13 条 `[saberlab-trace]/[saberlab-exp]` 日志；保留防御逻辑（环境 5s 超时、环境失败不阻塞、map 60s 超时） |
| `vite.config.ts` | 恢复默认 minify |
| `package.json` | 更名 `saberlab-chro` |

## 构建与集成

```bash
pnpm build   # 产出 dist/，由 SaberLab 后端挂载 /chro/
```

集成方式：SaberLab 详情页「查看回放」→ iframe
（`/chro/?replayUrl=<origin>/api/replays/{id}/raw`）→ 本地谱面源。
