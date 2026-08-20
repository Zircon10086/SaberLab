import { createFileRoute } from '@tanstack/react-router';

import { viewerSearchSchema } from '../modules/viewer/viewer-search';

// 静态 meta（SaberLab 内嵌场景不需要 OG 卡片/服务端预览）
export const Route = createFileRoute('/')({
  ssr: false,
  validateSearch: viewerSearchSchema,
  head: () => ({
    meta: [
      { title: 'ChroViewer' },
      { name: 'description', content: 'Preview Beat Saber maps and replays in your browser' },
    ],
  }),
});
