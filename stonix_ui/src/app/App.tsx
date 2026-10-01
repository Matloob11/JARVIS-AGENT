import { Toast } from '@/toast';
import '@/assets/styles/index.css';
import { AuthProvider, useAuth } from '@/features/auth/AuthProvider';
import LoginScreen from '@/features/auth/LoginScreen';
import Dashboard from '@/features/command-center/Dashboard';
import { LiveKitProvider } from '@/features/voice/LiveKitProvider';
import WindowControls from '@/shared/ui/WindowControls';

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
      <Dashboard />
    </LiveKitProvider>
  );
};

export const App = () => (
  <Toast>
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
