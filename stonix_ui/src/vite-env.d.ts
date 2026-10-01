/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_VORTEX_URL: string;
  readonly VITE_VORTEX_SECURITY_TOKEN: string;
  readonly VITE_LIVEKIT_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
