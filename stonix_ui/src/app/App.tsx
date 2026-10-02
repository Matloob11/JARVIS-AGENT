import { useEffect, useState } from 'react';
import { Toast } from '@/toast';
import { AuthProvider, useAuth } from '@/features/auth/AuthProvider';
import LoginScreen from '@/features/auth/LoginScreen';
import Dashboard from '@/features/command-center/Dashboard';
import { LiveKitProvider } from '@/features/voice/LiveKitProvider';
import { SettingsProvider, useSettings } from '@/features/settings/useSettings';
import WindowControls from '@/shared/ui/WindowControls';
import { LogoRing } from '@/shared/ui/LogoRing';
import { Sprite } from '@/shared/ui/icons/Sprite';

const SESSION_CHECK_RECONNECT_KEY = 'jarvis-session-check-msg';

function sessionCheckMessage(): string {
  try {
    if (sessionStorage.getItem(SESSION_CHECK_RECONNECT_KEY) === 'reconnect') {
      sessionStorage.removeItem(SESSION_CHECK_RECONNECT_KEY);
      return 'Reconnecting…';
    }
  } catch {
    /* sessionStorage unavailable */
  }
  return 'Checking session…';
}

const HtmlPersona = () => {
  const { persona } = useSettings();

  useEffect(() => {
    document.documentElement.dataset.persona = persona;
  }, [persona]);

  return null;
};

const HtmlElectron = () => {
  useEffect(() => {
    const sync = () => {
      const isElectron = Boolean(
        (window as Window & { electronAPI?: { isElectron?: boolean } }).electronAPI?.isElectron,
      );
      document.documentElement.classList.toggle('is-electron', isElectron);
    };
    sync();
    window.addEventListener('focus', sync);
    return () => window.removeEventListener('focus', sync);
  }, []);

  return null;
};

const AuthenticatedApp = () => {
  const { status } = useAuth();
  const [checkMessage] = useState(sessionCheckMessage);

  if (status === 'checking') {
    return (
      <div className="neon-screen">
        <div className="session-check" role="status">
          <div className="session-check-ring">
            <LogoRing size={84} />
          </div>
          <span>{checkMessage}</span>
          <div className="session-check-track" aria-hidden="true">
            <i />
          </div>
        </div>
      </div>
    );
  }

  if (status !== 'authenticated') {
    return <LoginScreen />;
  }

  return (
    <LiveKitProvider>
      <SettingsProvider>
        <HtmlPersona />
        <div className="h-full min-h-0 w-full overflow-hidden">
          <Dashboard />
        </div>
      </SettingsProvider>
    </LiveKitProvider>
  );
};

export const App = () => (
  <Toast>
    <div
      id="cf-announce"
      className="vh"
      role="status"
      aria-live="assertive"
      aria-atomic="true"
    />
    <Sprite />
    <AuthProvider>
      <HtmlElectron />
      <div className="relative min-h-screen">
        <WindowControls />
        <AuthenticatedApp />
      </div>
    </AuthProvider>
  </Toast>
);
