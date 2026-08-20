import type { QueryClient } from '@tanstack/react-query';
import { createRouter, defaultParseSearch } from '@tanstack/react-router';
import { AlertCircle, LoaderCircle } from 'lucide-react';
import { z } from 'zod';

import { createQueryClient } from './app/query-client';
import { routeTree } from './routeTree.gen';

type RouterSearchValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | RouterSearchValue[]
  | { readonly [key: string]: RouterSearchValue };

export interface RouterContext {
  queryClient: QueryClient;
}

const searchKeyAliases = new Map(
  Object.entries({
    map: 'map',
    replayurl: 'replayUrl',
    scoreid: 'scoreId',
    ssscoreid: 'scoreId',
    difficulty: 'difficulty',
    beat: 'beat',
    autoplay: 'autoplay',
    lightshow: 'lightshow',
    settings: 'settings',
    party: 'party',
    playerid: 'playerId',
    tournamentid: 'tournamentId',
    roomid: 'roomId',
    matchid: 'matchId',
    watcherplayerid: 'watcherPlayerId',
    authtoken: 'authToken',
  }),
);

const stringSearchAliases = {
  party: ['party'],
  map: ['map'],
  replayUrl: ['replayurl'],
  scoreId: ['scoreid', 'ssscoreid'],
  playerId: ['playerid'],
  tournamentId: ['tournamentid'],
  roomId: ['roomid'],
  matchId: ['matchid'],
  watcherPlayerId: ['watcherplayerid'],
  authToken: ['authtoken'],
};

export function parseUrlSearch(search: string) {
  const raw = z.record(z.string(), z.json()).parse(defaultParseSearch(search));
  const parsed: Record<string, RouterSearchValue> = {};
  for (const [key, value] of Object.entries(raw)) {
    const canonical = searchKeyAliases.get(key.toLowerCase());
    if (canonical === undefined) {
      parsed[key] = value;
      continue;
    }
    const direct = key.toLowerCase() === canonical.toLowerCase();
    if (parsed[canonical] === undefined || direct) parsed[canonical] = value;
  }
  const searchParams = new URLSearchParams(search);
  for (const [canonical, aliases] of Object.entries(stringSearchAliases)) {
    const entry = [...searchParams].find(([key]) => aliases.includes(key.toLowerCase()));
    if (entry !== undefined) parsed[canonical] = entry[1];
  }
  return parsed;
}

function stringifyUrlSearch(search: Record<string, RouterSearchValue>) {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(search)) appendSearchValue(searchParams, key, value);
  const next = searchParams.toString();
  return next === '' ? '' : `?${next}`;
}

function appendSearchValue(searchParams: URLSearchParams, key: string, value: RouterSearchValue) {
  if (value === null || value === undefined || value === '') return;
  if (Array.isArray(value)) {
    for (const item of value) appendSearchValue(searchParams, key, item);
    return;
  }
  searchParams.append(key, value instanceof Object ? JSON.stringify(value) : String(value));
}

function PendingFallback() {
  return (
    <main className="flex h-dvh items-center justify-center bg-black text-white">
      <LoaderCircle className="size-11 animate-spin" strokeWidth={1.75} aria-label="Loading" />
    </main>
  );
}

function ErrorFallback({ error }: { error: unknown }) {
  return (
    <main className="flex h-dvh flex-col items-center justify-center gap-4 bg-black text-white">
      <AlertCircle className="size-11" strokeWidth={1.75} />
      <span className="text-lg font-semibold tracking-wide">Something went wrong</span>
      {error instanceof Error && error.message !== '' && (
        <span className="max-w-[min(34rem,calc(100vw-1.5rem))] text-sm text-white/60">{error.message}</span>
      )}
      <button
        className="rounded-full border border-white/20 px-4 py-2 text-sm hover:bg-white/10"
        type="button"
        onClick={() => {
          window.location.reload();
        }}
      >
        Reload
      </button>
    </main>
  );
}

export function getRouter() {
  const queryClient = createQueryClient();

  return createRouter({
    routeTree,
    context: { queryClient },
    // 挂载于 SaberLab /chro/ 下：iframe 的 pathname 是 /chro/，忽略此前缀再匹配路由
    basepath: '/chro',
    defaultErrorComponent: ErrorFallback,
    defaultOnCatch: (error) => {
      console.error(error);
    },
    defaultPendingComponent: PendingFallback,
    defaultPreload: 'intent',
    defaultPreloadDelay: 30,
    defaultViewTransition: false,
    scrollRestoration: true,
    parseSearch: parseUrlSearch,
    stringifySearch: stringifyUrlSearch,
  });
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof getRouter>;
  }
}
