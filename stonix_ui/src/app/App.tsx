import { Toast } from '@/toast';
import '@/assets/styles/index.css';
import Dashboard from '@/features/command-center/Dashboard';

export const App = () => (
  <Toast>
    <Dashboard />
  </Toast>
);
