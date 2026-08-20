import { useEffect, useRef, useState } from 'react';

import { Result } from 'better-result';
import { useTranslations } from 'use-intl';

import { BeatmapParser } from '../../core/beatmap/worker/client';
import { isBeatLeaderReplay } from '../../core/replay/parse-beatleader';
import { applyLegacyScoreSaberMetadata, isScoreSaberReplay } from '../../core/replay/parse-scoresaber';
import { replayMapHash, type Replay } from '../../core/replay/types';
import { extractMapArchive } from '../../sources/archive';
import { isViewerSourceEnabled } from '../../sources/source-config';
import { SourceError, sourceError } from '../../sources/source-error';
import type {
  BeatSaverMapSource,
  MapSourceFile,
  ScoreSaberReplayPlayer,
  SourceResult,
} from '../../sources/source-types';
import { parseMapPackage } from './parse-map-package';
import { sourceErrorMessage } from './source-error-message';
import type { DifficultyRow, MapIdentity, MapMeta, ViewerSourceLink } from './viewer-types';

export interface PendingSharedView {
  autoplay?: boolean;
  difficultyIndex?: number;
  beat?: number;
}

export interface LoadedSourceContext {
  identity?: MapIdentity;
  scoreId?: string;
  scoreIdBL?: string;
  sourceLink?: ViewerSourceLink;
  player?: ScoreSaberReplayPlayer;
}

interface UseViewerFileSourceOptions {
  setError: (message: string) => void;
  onClearViewer: () => void;
  onMapLoaded: () => void;
  onSourceLoaded: () => void;
}

const legacyDifficultyRanks = new Map(
  Object.entries({
    easy: 1,
    normal: 3,
    hard: 5,
    expert: 7,
    expertplus: 9,
  }),
);

function legacyMetadataFromFilename(name: string) {
  const match = /-([^-]+)-([^-]+)-([0-9a-f]{40})\.dat$/i.exec(name);
  if (match === null) return null;
  const difficulty = legacyDifficultyRanks.get(match[1]?.toLowerCase() ?? '');
  const characteristic = match[2];
  const hash = match[3];
  return difficulty === undefined || characteristic === undefined || hash === undefined
    ? null
    : { difficulty, characteristic, hash };
}

export function useViewerFileSource({
  setError,
  onClearViewer,
  onMapLoaded,
  onSourceLoaded,
}: UseViewerFileSourceOptions) {
  const t = useTranslations('viewer');
  const parserRef = useRef<BeatmapParser | null>(null);
  const coverUrlRef = useRef<string | null>(null);
  const replayRef = useRef<Replay | null>(null);
  const audioDataRef = useRef<ArrayBuffer | null>(null);
  const pendingSharedViewRef = useRef<PendingSharedView | null>(null);
  const sourceGenerationRef = useRef(0);
  const [mapMeta, setMapMeta] = useState<MapMeta | null>(null);
  const [coverUrl, setCoverUrl] = useState<string | null>(null);
  const [songBpm, setSongBpm] = useState(0);
  const [rows, setRows] = useState<DifficultyRow[]>([]);
  const [mapIdentity, setMapIdentity] = useState<MapIdentity | null>(null);
  const [shareScoreId, setShareScoreId] = useState<string | null>(null);
  const [shareScoreIdBL, setShareScoreIdBL] = useState<string | null>(null);
  const [sourceLink, setSourceLink] = useState<ViewerSourceLink | null>(null);
  const [replayPlayer, setReplayPlayer] = useState<ScoreSaberReplayPlayer | null>(null);

  function revokeCover() {
    if (coverUrlRef.current === null) return;
    URL.revokeObjectURL(coverUrlRef.current);
    coverUrlRef.current = null;
  }

  useEffect(() => {
    const parser = new BeatmapParser();
    parserRef.current = parser;
    return () => {
      sourceGenerationRef.current++;
      parser.dispose();
      parserRef.current = null;
      revokeCover();
    };
  }, []);

  function beginSourceRequest() {
    sourceGenerationRef.current++;
    return sourceGenerationRef.current;
  }

  function isSourceRequestCurrent(requestId: number) {
    return sourceGenerationRef.current === requestId;
  }

  async function parseReplay(data: ArrayBuffer, source: SourceError['source'] = 'local') {
    const bytes = new Uint8Array(data);
    // 本地回放文件解析（SaberLab raw 流）是纯本地计算，不需要远程源启用；
    // 源开关仅约束"远程拉取"（ScoreSaber/BeatLeader 在线流程）
    if (
      source !== 'local' &&
      ((isBeatLeaderReplay(bytes) && !isViewerSourceEnabled('beatleader')) ||
        (isScoreSaberReplay(bytes) && !isViewerSourceEnabled('scoresaber')))
    ) {
      return Result.err(
        new SourceError({
          message: t('errors.sourceDisabled'),
          source,
          operation: 'validate-replay-source',
        }),
      );
    }
    parserRef.current ??= new BeatmapParser();
    const parser = parserRef.current;
    return Result.tryPromise({
      try: () => parser.parseReplay(data),
      catch: (cause) =>
        sourceError(cause, {
          message: cause instanceof Error ? cause.message : 'replay could not be parsed',
          source,
          operation: 'parse-replay',
        }),
    });
  }

  async function loadSourceFiles(
    requestId: number,
    files: MapSourceFile[],
    replay: Replay | null = null,
    context: LoadedSourceContext = {},
  ) {
    if (!isSourceRequestCurrent(requestId)) return Result.ok<DifficultyRow[]>([]);
    const source =
      context.scoreId !== undefined
        ? 'scoresaber'
        : context.scoreIdBL !== undefined
          ? 'beatleader'
          : context.identity === undefined
            ? 'local'
            : 'beatsaver';
    setError('');
    replayRef.current = replay;
    setShareScoreId(context.scoreId ?? null);
    setShareScoreIdBL(context.scoreIdBL ?? null);
    setSourceLink(context.sourceLink ?? null);
    const fallbackPlayer = replay?.metadata.player
      ? { id: replay.metadata.player.id, name: replay.metadata.player.name, avatar: '', country: '' }
      : null;
    setReplayPlayer(context.player ?? fallbackPlayer);
    setMapIdentity(null);
    onClearViewer();
    revokeCover();
    setCoverUrl(null);
    if (!files.some((file) => file.name.toLowerCase() === 'info.dat')) {
      return Result.err(
        new SourceError({
          message: t('errors.missingInfo'),
          source,
          operation: 'find-map-info',
        }),
      );
    }
    parserRef.current ??= new BeatmapParser();
    const parser = parserRef.current;
    const mapPackage = await Result.tryPromise({
      try: () => parseMapPackage(files, parser, replay),
      catch: (cause) =>
        sourceError(cause, {
          message: cause instanceof Error ? cause.message : 'map files could not be parsed',
          source,
          operation: 'parse-map-package',
        }),
    });
    if (!isSourceRequestCurrent(requestId)) return Result.ok([]);
    if (mapPackage.isErr()) {
      setMapMeta(null);
      setRows([]);
      revokeCover();
      setCoverUrl(null);
      return Result.err(mapPackage.error);
    }
    setMapMeta(mapPackage.value.mapMeta);
    setSongBpm(mapPackage.value.songBpm);
    if (mapPackage.value.cover !== null) {
      const url = URL.createObjectURL(new Blob([mapPackage.value.cover.data], { type: mapPackage.value.cover.type }));
      coverUrlRef.current = url;
      setCoverUrl(url);
    }
    audioDataRef.current = mapPackage.value.audioData;
    setRows(mapPackage.value.rows);
    onSourceLoaded();
    onMapLoaded();
    setMapIdentity(context.identity ?? null);
    return Result.ok(mapPackage.value.rows);
  }

  function clearSource() {
    beginSourceRequest();
    pendingSharedViewRef.current = null;
    replayRef.current = null;
    audioDataRef.current = null;
    setMapMeta(null);
    setSongBpm(0);
    setRows([]);
    setMapIdentity(null);
    setShareScoreId(null);
    setShareScoreIdBL(null);
    setSourceLink(null);
    setReplayPlayer(null);
    onClearViewer();
    revokeCover();
    setCoverUrl(null);
  }

  async function loadFiles(
    files: File[],
    resolveReplayMap: (hash: string) => Promise<SourceResult<BeatSaverMapSource>>,
  ) {
    const requestId = beginSourceRequest();
    const result = await Result.gen(async function* () {
      pendingSharedViewRef.current = null;
      setMapIdentity(null);
      setShareScoreId(null);
      setShareScoreIdBL(null);
      setSourceLink(null);
      const sourceFiles: MapSourceFile[] = [];
      let replay: Replay | null = null;
      let identity: MapIdentity | undefined;
      for (const file of files) {
        if (!isSourceRequestCurrent(requestId)) return Result.ok(undefined);
        if (/\.zip$/i.test(file.name)) {
          const data = yield* Result.await(
            Result.tryPromise({
              try: () => file.arrayBuffer(),
              catch: (cause) =>
                sourceError(cause, {
                  message: `${file.name} could not be read`,
                  source: 'local',
                  operation: 'read-local-file',
                }),
            }),
          );
          if (!isSourceRequestCurrent(requestId)) return Result.ok(undefined);
          const archive = yield* Result.await(extractMapArchive(new Uint8Array(data)));
          if (!isSourceRequestCurrent(requestId)) return Result.ok(undefined);
          sourceFiles.push(...archive);
        } else if (/\.(dat|bsor)$/i.test(file.name)) {
          const data = yield* Result.await(
            Result.tryPromise({
              try: () => file.arrayBuffer(),
              catch: (cause) =>
                sourceError(cause, {
                  message: `${file.name} could not be read`,
                  source: 'local',
                  operation: 'read-local-file',
                }),
            }),
          );
          if (!isSourceRequestCurrent(requestId)) return Result.ok(undefined);
          const bytes = new Uint8Array(data);
          if (isScoreSaberReplay(bytes) || isBeatLeaderReplay(bytes)) {
            if (replay !== null) {
              return Result.err(
                new SourceError({
                  message: t('errors.oneReplay'),
                  source: 'local',
                  operation: 'validate-replay-files',
                }),
              );
            }
            replay = yield* Result.await(parseReplay(data));
            if (!isSourceRequestCurrent(requestId)) return Result.ok(undefined);
            const legacyMetadata = legacyMetadataFromFilename(file.name);
            if (legacyMetadata !== null) applyLegacyScoreSaberMetadata(replay, legacyMetadata);
          } else sourceFiles.push(file);
        } else sourceFiles.push(file);
      }
      if (replay !== null && sourceFiles.length === 0) {
        const hash = replayMapHash(replay);
        if (hash === null) {
          return Result.err(
            new SourceError({
              message: t('errors.replayMissingHash'),
              source: 'local',
              operation: 'validate-replay-map',
            }),
          );
        }
        const source = yield* Result.await(resolveReplayMap(hash));
        if (!isSourceRequestCurrent(requestId)) return Result.ok(undefined);
        sourceFiles.push(...source.files);
        identity = { key: source.key, hash: source.hash };
      }
      if (!isSourceRequestCurrent(requestId)) return Result.ok(undefined);
      if (replay !== null) pendingSharedViewRef.current = {};
      yield* Result.await(loadSourceFiles(requestId, sourceFiles, replay, { identity }));
      return Result.ok(undefined);
    });
    if (result.isErr() && isSourceRequestCurrent(requestId)) {
      pendingSharedViewRef.current = null;
      setMapMeta(null);
      setRows([]);
      revokeCover();
      setCoverUrl(null);
      const fallback =
        result.error.source === 'beatsaver' || result.error.source === 'scoresaber'
          ? t('errors.failedSource')
          : t('errors.failedMap');
      setError(sourceErrorMessage(result.error, fallback, t('errors.missingInfo')));
    }
  }

  return {
    audioDataRef,
    beginSourceRequest,
    clearSource,
    coverUrl,
    loadFiles,
    isSourceRequestCurrent,
    loadSourceFiles,
    mapIdentity,
    mapMeta,
    parseReplay,
    pendingSharedViewRef,
    replayPlayer,
    replayRef,
    rows,
    shareScoreId,
    shareScoreIdBL,
    songBpm,
    sourceLink,
  };
}
