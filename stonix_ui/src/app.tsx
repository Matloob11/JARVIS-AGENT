import { Api } from '@/api';
import { Toast } from '@/toast';
import '@/assets/styles/index.css';
import Dashboard from '@/views/Dashboard';

export const App = () => (
  <Api>
    <Toast>
      <Dashboard />
    </Toast>
  </Api>
);
