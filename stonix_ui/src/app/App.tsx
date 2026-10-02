import { useEffect } from 'react';
import { Toast } from '@/toast';
import { AuthProvider, useAuth } from '@/features/auth/AuthProvider';
import LoginScreen from '@/features/auth/LoginScreen';
import Dashboard from '@/features/command-center/Dashboard';
import { LiveKitProvider } from '@/features/voice/LiveKitProvider';
import { SettingsProvider, useSettings } from '@/features/settings/useSettings';
import WindowControls from '@/shared/ui/WindowControls';
import { Sprite } from '@/shared/ui/icons/Sprite';

const HtmlPersona = () => {
  const { persona } = useSettings();

  useEffect(() => {
    document.documentElement.dataset.persona = persona;
  }, [persona]);

  return null;
};

const AuthenticatedApp = () => {
  const { status } = useAuth();

  if (status === 'checking') {
    return (
      <div className="login-root">
        <p className="text-sm text-command-muted">Session check…</p>
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
    <Sprite />
    <AuthProvider>
      <div className="relative min-h-screen">
        <div className="pointer-events-none fixed right-3 top-2 z-[500] [&_.window-button]:pointer-events-auto">
          <WindowControls />
        </div>
        <AuthenticatedApp />
      </div>
    </AuthProvider>
  </Toast>
);
