import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const policy = createRequire(import.meta.url)('./navigationPolicy.cjs') as {
  contentSecurityPolicy: () => string;
};

describe('Electron CSP map frame', () => {
  it('lets the OpenStreetMap embed load', () => {
    const csp = policy.contentSecurityPolicy();
    expect(csp).toContain('frame-src https://www.openstreetmap.org');
    expect(csp).not.toContain("frame-src 'none'");
  });
});
