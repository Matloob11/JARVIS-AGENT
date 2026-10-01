import { verifySync } from 'otplib';

/** Shared credentials for mock bridge login (e2e + dev:mock). */
export const TEST_PASSWORD = 'test-pass';

/** Fixed base32 secret — pair with any TOTP app using this secret for local login. */
export const TEST_TOTP_SECRET = 'JBSWY3DPEHPK3PXP';

export const TEST_RECOVERY = 'TEST-0000-0000';

/**
 * @param {string|number} token Six-digit TOTP code
 * @param {string} [secret] Base32 secret (defaults to TEST_TOTP_SECRET)
 * @returns {boolean}
 */
export function verifyTotp(token, secret = TEST_TOTP_SECRET) {
  const result = verifySync({ token: String(token), secret });
  return Boolean(result?.valid);
}
