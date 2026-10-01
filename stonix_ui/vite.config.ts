import { defineConfig } from 'vite';
import { resolve } from 'path';
import react from '@vitejs/plugin-react';
import type { ProxyOptions } from 'vite';

const devBridge = process.env.VITE_DEV_BRIDGE ?? 'http://127.0.0.1:5001';
const stripPrefix = process.env.VITE_DEV_BRIDGE_STRIP === '1';

function vortexProxy(): ProxyOptions {
  let targetOrigin = 'http://127.0.0.1:5001';
  try {
    targetOrigin = new URL(devBridge).origin;
  } catch {
    /* keep default */
  }
  const isRemote =
    !/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/?$/i.test(devBridge);

  return {
    target: devBridge,
    changeOrigin: true,
    ws: true,
    rewrite: stripPrefix
      ? (path: string) => path.replace(/^\/vortex/, '') || '/'
      : undefined,
    configure: proxy => {
      proxy.on('proxyReq', proxyReq => {
        if (isRemote) {
          proxyReq.setHeader('Origin', targetOrigin);
        }
      });
    },
  };
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    open: false,
    proxy: {
      '/vortex': vortexProxy(),
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    proxy: {
      '/vortex': vortexProxy(),
    },
  },
  resolve: {
    alias: [{ find: '@', replacement: resolve(__dirname, 'src') }],
  },
});
