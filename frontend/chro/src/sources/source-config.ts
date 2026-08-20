import type { ConfigurableViewerSource } from './source-types';

import { env } from '@/env';

export function isViewerSourceEnabled(source: ConfigurableViewerSource) {
  return env.VITE_ENABLED_SOURCES.includes(source);
}
