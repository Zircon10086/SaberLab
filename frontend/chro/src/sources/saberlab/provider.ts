/**
 * SaberLab 本地谱面源：从 SaberLab 后端拉取本地谱面库的 zip 包。
 *
 * 谱面数据完全本地（CustomLevels 文件夹 zip 打包），与 BeatSaver 源同构：
 * 返回 { key, hash, files }（BeatSaverMapSource 结构），可直接接入
 * use-viewer-remote-source 的 loadSourceFiles 流程。
 */
import { Result } from 'better-result';

import { extractMapArchive } from '../archive';
import { requestArrayBuffer } from '../http';
import { SourceError } from '../source-error';
import type { BeatSaverMapSource, DownloadProgressHandler, SourceResult } from '../source-types';

export async function fetchSaberLabMap(
  hash: string,
  options: { onProgress?: DownloadProgressHandler } = {},
): Promise<SourceResult<BeatSaverMapSource>> {
  return Result.gen(async function* () {
    if (!/^[0-9a-f]{40}$/i.test(hash)) {
      return Result.err(
        new SourceError({
          message: 'invalid Beat Saber map hash',
          source: 'local',
          operation: 'parse-map-hash',
        }),
      );
    }
    const archive = yield* Result.await(
      requestArrayBuffer(`/api/maps/${encodeURIComponent(hash)}/package`, {
        source: 'local',
        label: `SaberLab map ${hash.slice(0, 8)}`,
        operation: 'download-map-archive',
        onProgress: options.onProgress,
        // 防御：后端异常时 60s 超时，避免前端无限挂起
        signal: AbortSignal.timeout(60_000),
      }),
    );
    const files = yield* Result.await(extractMapArchive(new Uint8Array(archive)));
    if (!files.some((file) => file.name.toLowerCase() === 'info.dat')) {
      return Result.err(
        new SourceError({
          message: `SaberLab map ${hash} archive has no Info.dat`,
          source: 'local',
          operation: 'validate-map-archive',
        }),
      );
    }
    return Result.ok({ key: hash, hash, files });
  });
}
