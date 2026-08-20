import { z } from 'zod';

import { configurableViewerSources } from './source-types';

// SaberLab 纯本地模式：默认关闭全部远程源（BeatSaver/ScoreSaber/BeatLeader
// 的浏览器直连会被 CORS 拦截——原版依赖 Nitro 代理转发，我们无代理，
// 本地谱面/回放场景不需要远程源）。如需远程源可构建时设置 VITE_ENABLED_SOURCES。
export const enabledViewerSourcesSchema = z
  .string()
  .default('')
  .transform((value) => value.split(',').map((source) => source.trim()).filter(Boolean))
  .pipe(z.array(z.enum(configurableViewerSources)).max(configurableViewerSources.length));
