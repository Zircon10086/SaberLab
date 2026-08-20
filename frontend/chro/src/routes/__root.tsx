import '../styles.css';

import type { ReactNode } from 'react';

import { QueryClientProvider } from '@tanstack/react-query';
import { HeadContent, Outlet, Scripts, createRootRouteWithContext } from '@tanstack/react-router';

import logoUrl from '../app/assets/logo.svg?url';
import { defaultLocale } from '../i18n/config';
import { AppIntlProvider } from '../i18n/provider';
import type { RouterContext } from '../router';

export const Route = createRootRouteWithContext<RouterContext>()({
  head: () => ({
    meta: [
      { charSet: 'utf-8' },
      { httpEquiv: 'Content-Type', content: 'text/html; charset=utf-8' },
      { name: 'viewport', content: 'width=device-width, initial-scale=1.0, viewport-fit=cover' },
      { name: 'theme-color', content: '#facc15', 'data-react-helmet': 'true' },
    ],
    links: [
      {
        rel: 'icon',
        href: `${import.meta.env.BASE_URL}favicon.ico`,
        sizes: '16x16 32x32 48x48',
        type: 'image/x-icon',
      },
      {
        rel: 'icon',
        href: logoUrl,
        sizes: 'any',
        type: 'image/svg+xml',
      },
      {
        rel: 'apple-touch-icon',
        href: `${import.meta.env.BASE_URL}apple-touch-icon.png`,
        sizes: '180x180',
        type: 'image/png',
      },
      {
        rel: 'preload',
        href: `${import.meta.env.BASE_URL}fonts/geist-latin-400-normal.woff2`,
        as: 'font',
        type: 'font/woff2',
        crossOrigin: 'anonymous',
      },
      {
        rel: 'preload',
        href: `${import.meta.env.BASE_URL}fonts/geist-latin-500-normal.woff2`,
        as: 'font',
        type: 'font/woff2',
        crossOrigin: 'anonymous',
      },
      {
        rel: 'preload',
        href: `${import.meta.env.BASE_URL}fonts/geist-latin-600-normal.woff2`,
        as: 'font',
        type: 'font/woff2',
        crossOrigin: 'anonymous',
      },
      {
        rel: 'preload',
        href: `${import.meta.env.BASE_URL}fonts/GeistPixel-Square.woff2`,
        as: 'font',
        type: 'font/woff2',
        crossOrigin: 'anonymous',
      },
    ],
  }),
  component: RootComponent,
});

function RootComponent() {
  const { queryClient } = Route.useRouteContext();

  return (
    <RootDocument>
      <AppIntlProvider initialLocale={defaultLocale}>
        <QueryClientProvider client={queryClient}>
          <Outlet />
        </QueryClientProvider>
      </AppIntlProvider>
    </RootDocument>
  );
}

function RootDocument({ children }: { children: ReactNode }) {
  // SaberLab 移植说明：本应用是纯客户端 SPA（main.tsx 用 createRoot 挂在 #root div 上，
  // 文档结构由 index.html 提供），不能像上游 TanStack Start（SSR/hydrateRoot(document)）
  // 那样从根组件渲染 <html>/<head>/<body>。
  //
  // 原因：createRoot 挂在内层元素上时，React 19 会“收养”文档里真实的 html/body
  // （HostSingleton，fiber 挂到真实元素上），而根容器 #root 又位于 body 内部。
  // selectionchange 是唯一挂在 document 上监听的事件，其派发流程
  // （dispatchEventForPluginEventSystem）会在 HostRoot → #root.parentNode=body(有 fiber)
  // → body/html → HostRoot 之间无限循环——任何一次 selectionchange（如输入框
  // 选区变化/报错重渲染）都会把主线程永久卡死。
  //
  // <title>/<meta>/<link> 由 React 19 自动提升到 <head>，无需显式 <head> 包裹；
  // <Scripts> 在纯客户端模式下渲染为空。
  return (
    <>
      <HeadContent />
      {children}
      <Scripts />
    </>
  );
}
