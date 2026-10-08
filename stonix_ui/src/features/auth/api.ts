import { bridgeUrl } from '@/shared/config/runtime';

export type LoginPayload =
  | { password: string; totp: string }
  | { password: string; recovery_code: string };

export interface MeResponse {
  ok: boolean;
  user: string;
  session_id?: string;
  expires_at: number;
}

export class AuthApiError extends Error {
  readonly status: number;

  readonly retryAfter?: number;

  constructor(message: string, status: number, retryAfter?: number) {
    super(message);
    this.name = 'AuthApiError';
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

async function parseJson(res: Response): Promise<Record<string, unknown>> {
  try {
    return (await res.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

export function mapAuthError(res: Response, body: Record<string, unknown>): AuthApiError {
  if (res.status === 401) {
    return new AuthApiError('Password ya code ghalat hai', 401);
  }
  if (res.status === 429) {
    const headerRetry = res.headers.get('Retry-After');
    const bodyRetry = body.retry_after;
    const retryAfter =
      typeof bodyRetry === 'number'
        ? bodyRetry
        : headerRetry
          ? Number.parseInt(headerRetry, 10)
          : 60;
    const seconds = Number.isFinite(retryAfter) ? retryAfter : 60;
    return new AuthApiError(`Bohat zyada koshishen - ${seconds} s baad`, 429, seconds);
  }
  if (res.status === 503 && body.error === 'owner_not_enrolled') {
    return new AuthApiError(
      'Owner login set nahi. Apne terminal mein chalao: .venv_312/Scripts/python.exe scripts/ops/set_owner_credentials.py --target local',
      503,
    );
  }
  if (res.status === 503) {
    return new AuthApiError('Login abhi set nahi hua (owner enroll karein)', 503);
  }
  const detail = typeof body.error === 'string' ? body.error : res.statusText;
  return new AuthApiError(detail || 'Login fail', res.status);
}

async function authFetch(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(bridgeUrl(path), {
      ...init,
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new AuthApiError('Bridge offline', 0);
  }
}

export async function login(payload: LoginPayload): Promise<MeResponse> {
  const res = await authFetch('/auth/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  const body = await parseJson(res);
  if (!res.ok) {
    throw mapAuthError(res, body);
  }
  return body as unknown as MeResponse;
}

export async function me(): Promise<MeResponse> {
  const res = await authFetch('/auth/me', { method: 'GET' });
  const body = await parseJson(res);
  if (!res.ok) {
    throw mapAuthError(res, body);
  }
  return body as unknown as MeResponse;
}

export async function logout(): Promise<void> {
  try {
    const res = await authFetch('/auth/logout', { method: 'POST', body: '{}' });
    if (!res.ok) {
      const body = await parseJson(res);
      throw mapAuthError(res, body);
    }
  } catch (err) {
    if (err instanceof AuthApiError && err.status === 0) {
      throw err;
    }
    if (err instanceof AuthApiError) {
      throw err;
    }
    throw new AuthApiError('Bridge offline', 0);
  }
}
