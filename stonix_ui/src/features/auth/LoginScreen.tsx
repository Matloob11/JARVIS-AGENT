import { FormEvent, useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { LogoRing } from '@/shared/ui/LogoRing';
import { StatusShape } from '@/shared/ui/StatusShape';
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
    <div className="neon-screen">
      <div className="login-glass edge no-drag">
        <div className="login-brand-block">
          <LogoRing size={58} />
          <h1 className="login-brand-title">JARVIS & ANNA</h1>
          <p className="login-brand-tagline">Your AI Command Center</p>
        </div>

        <form className="login-form-grid" onSubmit={handleSubmit}>
          {sessionExpired && (
            <p className="login-hint" role="status">
              Session expire ho gaya — dubara login karein.
            </p>
          )}

          <p className="login-owner-line">Owner session: password and authenticator</p>

          <label className="login-lab">
            Password
            <span className="login-pw-wrap">
              <input
                className="login-in no-drag"
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
                className="login-eye-btn no-drag"
                onClick={() => setShowPassword(v => !v)}
                title={showPassword ? 'Hide password' : 'Show password'}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                tabIndex={-1}
              >
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </span>
          </label>

          {useRecovery ? (
            <label className="login-lab">
              Recovery code
              <input
                className="login-in no-drag"
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
            <label className="login-lab">
              Authenticator code
              <input
                className="login-in login-otp no-drag"
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
            className="login-recovery-toggle no-drag"
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
              <StatusShape s="err" label="Error" hideLabel />
              <span>{displayError}</span>
            </p>
          )}

          <button
            type="submit"
            className="n-btn primary login-submit no-drag"
            disabled={pending}
            data-testid="login-submit"
          >
            {pending ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p className="login-foot">Socket, voice and camera start only after you sign in.</p>
      </div>
    </div>
  );
};

export default LoginScreen;
