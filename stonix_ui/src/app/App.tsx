import { Toast } from '@/toast';
import '@/assets/styles/index.css';
import { AuthProvider, useAuth } from '@/features/auth/AuthProvider';
import LoginScreen from '@/features/auth/LoginScreen';
import Dashboard from '@/features/command-center/Dashboard';

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

  return <Dashboard />;
};

export const App = () => (
  <Toast>
    <AuthProvider>
      <AuthenticatedApp />
    </AuthProvider>
  </Toast>
);
