import { describe, expect, it } from 'vitest';
import { mapAuthError } from './api';

describe('mapAuthError owner_not_enrolled', () => {
  it('names the local enroll command on 503 owner_not_enrolled', () => {
    const res = new Response(null, { status: 503 });
    const err = mapAuthError(res, { error: 'owner_not_enrolled' });
    expect(err.status).toBe(503);
    expect(err.message).toContain('set_owner_credentials.py --target local');
  });
});
