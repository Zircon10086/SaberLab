import { Result } from 'better-result';
import { z } from 'zod';

import { env } from '../../env';
import { requestArrayBuffer, requestJson } from '../http';
import { SourceError } from '../source-error';
import type { DownloadProgressHandler, FetchRequest, ScoreSaberReplayPlayer, SourceResult } from '../source-types';

interface ResolveOptions {
  onProgress?: DownloadProgressHandler;
  request?: FetchRequest;
  signal?: AbortSignal;
}

interface BeatLeaderReference {
  kind: 'score';
  id: string;
}

const scoreSchema = z.object({
  id: z.number().int().nonnegative(),
  song: z.object({
    hash: z.string(),
  }),
  difficulty: z.object({
    value: z.number().int(),
    modeName: z.string().min(1),
  }),
  player: z.object({
    id: z.string().min(1),
    name: z.string(),
    avatar: z.string(),
    country: z.string().nullable().optional(),
  }),
  replay: z.url().nullable().optional(),
});

type ScoreContract = z.infer<typeof scoreSchema>;

const playerSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  avatar: z.string(),
  country: z.string(),
  rank: z.number().int().nonnegative(),
  countryRank: z.number().int().nonnegative(),
});

type PlayerContract = z.infer<typeof playerSchema>;

export function beatLeaderReference(input: string): BeatLeaderReference | null {
  const value = input.trim();
  const prefixedScore = /^(?:bl|beatleader):\s*(\d+)$/i.exec(value)?.[1];
  if (prefixedScore !== undefined) return { kind: 'score', id: prefixedScore };
  const normalized = /^[a-z]+:\/\//i.test(value) ? value : `https://${value}`;
  if (!URL.canParse(normalized)) return null;
  const url = new URL(normalized);
  if (!/(^|\.)beatleader\.xyz$/i.test(url.hostname) && !/(^|\.)beatleader\.com$/i.test(url.hostname)) return null;
  const score = /\/score\/(\d+)/i.exec(url.pathname)?.[1];
  if (score !== undefined && /^\d+$/.test(score)) return { kind: 'score', id: score };
  return null;
}

function replayMetadata(score: ScoreContract, requestedScoreId: string) {
  const scoreId = String(score.id || requestedScoreId);
  if (!score.replay) {
    return Result.err(
      new SourceError({
        message: `BeatLeader score ${requestedScoreId} has no replay`,
        source: 'beatleader',
        operation: 'validate-score',
      }),
    );
  }
  const player = score.player;
  return Result.ok({
    scoreId,
    hash: score.song.hash,
    difficulty: score.difficulty.value,
    characteristic: score.difficulty.modeName,
    playerId: player.id,
    player: {
      id: player.id,
      name: player.name,
      avatar: player.avatar,
      country: player.country ?? '',
    },
    replayUrl: score.replay,
  });
}

function replayPlayer(player: PlayerContract, fallback: ScoreSaberReplayPlayer) {
  return {
    id: player.id || fallback.id,
    name: player.name || fallback.name,
    avatar: player.avatar || fallback.avatar,
    country: player.country || fallback.country,
    rank: player.rank > 0 ? player.rank : undefined,
    countryRank: player.countryRank > 0 ? player.countryRank : undefined,
  };
}

export async function fetchBeatLeaderPlayer(
  playerId: string,
  options: ResolveOptions = {},
): Promise<SourceResult<ScoreSaberReplayPlayer>> {
  const player = await requestJson(`${env.VITE_BEATLEADER_API_URL}/player/${playerId}`, playerSchema, {
    ...options,
    source: 'beatleader',
    label: `BeatLeader player ${playerId}`,
    operation: 'load-player',
  });
  return player.map((value) => replayPlayer(value, { id: playerId, name: 'Player', avatar: '', country: '' }));
}

export async function fetchBeatLeaderReplayMetadata(scoreId: string, options: ResolveOptions = {}) {
  if (!/^\d+$/.test(scoreId)) {
    return Result.err(
      new SourceError({
        message: 'invalid BeatLeader score ID',
        source: 'beatleader',
        operation: 'parse-score-id',
      }),
    );
  }
  return Result.gen(async function* () {
    const score = yield* Result.await(
      requestJson(`${env.VITE_BEATLEADER_API_URL}/score/${scoreId}`, scoreSchema, {
        ...options,
        source: 'beatleader',
        label: `BeatLeader score ${scoreId}`,
        operation: 'load-score',
      }),
    );
    return replayMetadata(score, scoreId);
  });
}

export function fetchBeatLeaderReplayFile(url: string, options: ResolveOptions = {}) {
  return requestArrayBuffer(url, {
    ...options,
    source: 'beatleader',
    label: `BeatLeader replay`,
    operation: 'download-replay',
  });
}

const leaderboardEntrySchema = z
  .object({
    id: z.string().min(1).optional(),
    leaderboardId: z.string().min(1).optional(),
    difficulty: z.object({
      value: z.number().int(),
      modeName: z.string().min(1),
    }),
  })
  .transform((item) => {
    const modeName = item.difficulty.modeName;
    return {
      id: item.leaderboardId ?? item.id ?? '',
      difficulty: item.difficulty.value,
      gameMode: modeName.startsWith('Solo') ? modeName : `Solo${modeName}`,
    };
  });

const leaderboardsResponseSchema = z
  .union([
    z.object({ leaderboards: z.array(leaderboardEntrySchema) }),
    z.array(leaderboardEntrySchema),
    leaderboardEntrySchema,
  ])
  .transform((data) => {
    const items = Array.isArray(data) ? data : 'leaderboards' in data ? data.leaderboards : [data];
    return items;
  });

export async function fetchBeatLeaderLeaderboards(hash: string, options: ResolveOptions = {}) {
  return requestJson(`${env.VITE_BEATLEADER_API_URL}/leaderboards/hash/${hash}`, leaderboardsResponseSchema, {
    ...options,
    source: 'beatleader',
    label: `BeatLeader leaderboards for ${hash}`,
    operation: 'load-leaderboards',
  });
}
