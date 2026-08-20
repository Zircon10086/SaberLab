import { queryOptions, skipToken } from '@tanstack/react-query';

import { fetchBeatLeaderPlayer } from './provider';

export function beatLeaderPlayerQueryOptions(playerId: string | undefined) {
  return queryOptions({
    queryKey: ['beatleader', 'player', playerId],
    queryFn:
      playerId === undefined
        ? skipToken
        : async ({ signal }) => {
            const result = await fetchBeatLeaderPlayer(playerId, { signal });
            if (result.isErr()) throw result.error;
            return result.value;
          },
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
}
