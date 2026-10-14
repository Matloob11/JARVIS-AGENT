import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const policy = createRequire(import.meta.url)('./navigationPolicy.cjs') as {
  fileUrl: (absPath: string) => string;
  isPermissionAllowed: (
    requestingUrl: string,
    permission: string,
    uiUrl: string,
    paths: { distIndex: string; offlinePage: string; isDev: boolean },
  ) => boolean;
};

const distIndex = 'C:/JARVIS/stonix_ui/dist/index.html';
const offlinePage = 'C:/JARVIS/stonix_ui/electron/offline.html';
const paths = { distIndex, offlinePage, isDev: false };
const uiUrl = 'https://jarvis.example';

describe('Electron file:// media permission', () => {
  it('allows media for the app dist page, including the three-slash file URL', () => {
    expect(policy.isPermissionAllowed(policy.fileUrl(distIndex), 'media', uiUrl, paths)).toBe(true);
    expect(policy.isPermissionAllowed('file:///C:/JARVIS/stonix_ui/dist/index.html', 'media', uiUrl, paths)).toBe(true);
  });

  it('allows media for the offline page', () => {
    expect(policy.isPermissionAllowed(policy.fileUrl(offlinePage), 'media', uiUrl, paths)).toBe(true);
  });

  it('denies media for another file and for a data URL whose origin is null', () => {
    expect(policy.isPermissionAllowed('file:///C:/Windows/notepad.exe', 'media', uiUrl, paths)).toBe(false);
    expect(policy.isPermissionAllowed('data:text/html,hi', 'media', uiUrl, paths)).toBe(false);
  });

  it('still allows media on the configured http origin and denies other permissions', () => {
    expect(
      policy.isPermissionAllowed('http://127.0.0.1:4173/login', 'media', 'http://127.0.0.1:4173', paths),
    ).toBe(true);
    expect(
      policy.isPermissionAllowed('http://127.0.0.1:4173/login', 'geolocation', 'http://127.0.0.1:4173', paths),
    ).toBe(false);
  });
});
