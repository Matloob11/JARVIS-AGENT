/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_VORTEX_URL?: string;
  readonly VITE_BRIDGE_BASE?: string;
  readonly VITE_LIVEKIT_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare const __E2E_HOOKS__: boolean;
