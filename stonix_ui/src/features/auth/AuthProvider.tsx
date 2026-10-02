import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { login as loginApi, logout as logoutApi, me, type LoginPayload, AuthApiError } from './api';
import { disconnectSocket, getSocket, onConnectError } from '@/shared/realtime/socket';
import { useSocketEvent } from '@/shared/realtime/subscriptions';

export type AuthStatus = 'checking' | 'anonymous' | 'authenticated' | 'expired';

export interface AuthUser {
  user: string;
  expires_at: number;
}

interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  error: string | null;
  login: (payload: LoginPayload) => Promise<void>;
  logout: () => Promise<void>;
  clearError: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function expiresAtMs(expires_at: number): number {
  return expires_at > 1e12 ? expires_at : expires_at * 1000;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('checking');
  const [user, setUser] = useState<AuthUser | null>(null);
  const [error, setError] = useState<string | null>(null);
  const expiryTimerRef = useRef<number | null>(null);
  const sessionCheckRef = useRef<(() => Promise<void>) | null>(null);

  const clearExpiryTimer = useCallback(() => {
    if (expiryTimerRef.current != null) {
      window.clearTimeout(expiryTimerRef.current);
      expiryTimerRef.current = null;
    }
  }, []);

  const applySession = useCallback(
    (session: { user: string; expires_at: number }) => {
      setUser({ user: session.user, expires_at: session.expires_at });
      setStatus('authenticated');
      setError(null);
      clearExpiryTimer();
      const msUntilRefresh = expiresAtMs(session.expires_at) - Date.now() - 60_000;
      expiryTimerRef.current = window.setTimeout(
        () => {
          void sessionCheckRef.current?.();
        },
        Math.max(msUntilRefresh, 0),
      );
    },
    [clearExpiryTimer],
  );

  const handleSessionLost = useCallback(() => {
    clearExpiryTimer();
    disconnectSocket();
    setUser(null);
    setStatus('expired');
  }, [clearExpiryTimer]);

  const refreshSession = useCallback(async () => {
    try {
      const session = await me();
      applySession(session);
    } catch (err) {
      if (err instanceof AuthApiError && err.status === 401) {
        handleSessionLost();
        return;
      }
      if (err instanceof AuthApiError && err.status === 0) {
        setError(err.message);
      }
    }
  }, [applySession, handleSessionLost]);

  sessionCheckRef.current = refreshSession;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const session = await me();
        if (!cancelled) applySession(session);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof AuthApiError && err.status === 401) {
          setStatus('anonymous');
          setUser(null);
          return;
        }
        if (err instanceof AuthApiError && err.status === 0) {
          setError(err.message);
        }
        setStatus('anonymous');
        setUser(null);
      }
    })();
    return () => {
      cancelled = true;
      clearExpiryTimer();
    };
  }, [applySession, clearExpiryTimer]);

  useEffect(() => {
    if (status !== 'authenticated') return undefined;

    getSocket();
    const offConnectError = onConnectError(() => {
      void refreshSession();
    });

    return () => {
      offConnectError();
    };
  }, [status, refreshSession]);

  const statusRef = useRef(status);
  statusRef.current = status;

  useSocketEvent('session_expired', () => {
    if (statusRef.current === 'authenticated') {
      void refreshSession();
    }
  });

  const login = useCallback(
    async (payload: LoginPayload) => {
      setError(null);
      try {
        const session = await loginApi(payload);
        applySession(session);
      } catch (err) {
        const message =
          err instanceof AuthApiError ? err.message : 'Login fail — dubara koshish karein';
        setError(message);
        throw err;
      }
    },
    [applySession],
  );

  const logout = useCallback(async () => {
    setError(null);
    try {
      await logoutApi();
    } catch (err) {
      if (err instanceof AuthApiError && err.status !== 0) {
        setError(err.message);
      }
    } finally {
      clearExpiryTimer();
      disconnectSocket();
      setUser(null);
      setStatus('anonymous');
    }
  }, [clearExpiryTimer]);

  const clearError = useCallback(() => setError(null), []);

  const value: AuthContextValue = {
    status,
    user,
    error,
    login,
    logout,
    clearError,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
}
