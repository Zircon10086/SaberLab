import path from 'node:path';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  // 挂载于 SaberLab /chro/ 下：绝对 base，保证 worker/环境/字体等
  // ${import.meta.env.BASE_URL} 资源路径正确（相对 base 会解析到 assets/ 下导致 404）
  base: '/chro/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  build: {
    outDir: 'dist',
    target: 'esnext',
    chunkSizeWarningLimit: 2000,
  },
  worker: {
    format: 'es',
  },
});
