import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from '@tanstack/react-router';

import { getRouter } from './router';

import './styles.css';

const rootElement = document.getElementById('root');
if (rootElement === null) {
  throw new Error('missing #root');
}

const router = getRouter();
void router.load();

createRoot(rootElement).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
