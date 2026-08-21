# ChroViewer Port (SaberLab Modified Version)

This directory is a port and modified version of [Umbranoxio/chroviewer](https://github.com/Umbranoxio/chroviewer)
(GPL-2.0, derived from ChroMapper), released as a whole under **GPL-2.0**
(see [LICENSE](LICENSE) in this directory).

Upstream: https://github.com/Umbranoxio/chroviewer

## Modification List (relative to upstream)

| File / Area | Change |
|---|---|
| `src/sources/saberlab/provider.ts` | **Added**: SaberLab local map data source (talks to `/api/maps/{hash}/package`, local-first, remote sources disabled by default) |
| `src/routes/__root.tsx` | `RootDocument` changed to a fragment (document structure comes from index.html) — fixes a main-thread selectionchange infinite loop caused by combining React 19 `createRoot(#root)` with `<html>/<head>/<body>` root tags |
| `src/main.tsx` | Pure client-side `createRoot(#root)` mount; removed diagnostic heartbeat logging |
| `src/modules/viewer/use-viewer-remote-source.ts` et al. | Removed remote source entry points (`/api/source` rewrite, link-loading branch, source-picker link UI) — local-only |
| Orphaned files | Removed `environment-worker.ts` / `environment-worker-protocol.ts` (unreferenced) |
| Diagnostic logging | Removed 13 `[saberlab-trace]/[saberlab-exp]` log statements; defensive logic kept (5s environment timeout, environment failure does not block, 60s map timeout) |
| `vite.config.ts` | Restored default minify |
| `package.json` | Renamed to `saberlab-chro` |

## Build & Integration

```bash
pnpm build   # produces dist/, mounted at /chro/ by the SaberLab backend
```

Integration: SaberLab detail page "View Replay" → iframe
(`/chro/?replayUrl=<origin>/api/replays/{id}/raw`) → local map source.
