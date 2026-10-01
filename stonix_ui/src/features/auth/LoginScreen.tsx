import { FormEvent, useState } from 'react';
import { Eye, EyeOff, Radar } from 'lucide-react';
import { useAuth } from './AuthProvider';
import { AuthApiError } from './api';

const LoginScreen = () => {
  const { login, error, clearError, status } = useAuth();
  const [password, setPassword] = useState('');
  const [totp, setTotp] = useState('');
  const [recoveryCode, setRecoveryCode] = useState('');
  const [useRecovery, setUseRecovery] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [pending, setPending] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const displayError = localError ?? error;
  const sessionExpired = status === 'expired';

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (pending) return;

    const trimmedPassword = password.trim();
    if (!trimmedPassword) {
      setLocalError('Password zaroori hai');
      return;
    }

    if (useRecovery) {
      const code = recoveryCode.trim();
      if (!code) {
        setLocalError('Recovery code zaroori hai');
        return;
      }
      setPending(true);
      setLocalError(null);
      clearError();
      try {
        await login({ password: trimmedPassword, recovery_code: code });
      } catch (err) {
        if (!(err instanceof AuthApiError)) {
          setLocalError('Login fail — dubara koshish karein');
        }
      } finally {
        setPending(false);
      }
      return;
    }

    const code = totp.replace(/\D/g, '').slice(0, 6);
    if (code.length !== 6) {
      setLocalError('6-digit authenticator code likhein');
      return;
    }

    setPending(true);
    setLocalError(null);
    clearError();
    try {
      await login({ password: trimmedPassword, totp: code });
    } catch (err) {
      if (!(err instanceof AuthApiError)) {
        setLocalError('Login fail — dubara koshish karein');
      }
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="login-root">
      <div className="login-card no-drag">
        <div className="login-brand">
          <div className="brand-mark">
            <Radar size={18} />
          </div>
          <div>
            <h1 className="text-[15px] font-semibold tracking-[0.18em] text-command-text">STONIX</h1>
            <p className="text-[11px] text-command-muted">Owner session — password + authenticator</p>
          </div>
        </div>

        {sessionExpired && (
          <p className="login-hint" role="status">
            Session expire ho gaya — dubara login karein.
          </p>
        )}

        <form className="login-form" onSubmit={handleSubmit}>
          <label className="login-label">
            Password
            <div className="login-password-row">
              <input
                className="login-input no-drag"
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                value={password}
                onChange={e => {
                  setPassword(e.target.value);
                  setLocalError(null);
                }}
                data-testid="login-password"
              />
              <button
                type="button"
                className="icon-button login-toggle-password"
                onClick={() => setShowPassword(v => !v)}
                title={showPassword ? 'Hide password' : 'Show password'}
                tabIndex={-1}
              >
                {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </label>

          {useRecovery ? (
            <label className="login-label">
              Recovery code
              <input
                className="login-input no-drag"
                type="text"
                autoComplete="off"
                value={recoveryCode}
                onChange={e => {
                  setRecoveryCode(e.target.value);
                  setLocalError(null);
                }}
                data-testid="login-recovery"
              />
            </label>
          ) : (
            <label className="login-label">
              Authenticator code
              <input
                className="login-input no-drag login-totp-input"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                placeholder="000000"
                value={totp}
                onChange={e => {
                  setTotp(e.target.value.replace(/\D/g, '').slice(0, 6));
                  setLocalError(null);
                }}
                data-testid="login-totp"
              />
            </label>
          )}

          <button
            type="button"
            className="login-recovery-toggle"
            onClick={() => {
              setUseRecovery(v => !v);
              setLocalError(null);
              clearError();
            }}
          >
            {useRecovery ? 'Authenticator code use karein' : 'Recovery code use karein'}
          </button>

          {displayError && (
            <p className="login-error" data-testid="login-error" role="alert">
              {displayError}
            </p>
          )}

          <button
            type="submit"
            className="primary-button login-submit"
            disabled={pending}
            data-testid="login-submit"
          >
            {pending ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
};

export default LoginScreen;
