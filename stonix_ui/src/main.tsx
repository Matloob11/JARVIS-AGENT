import { createRoot } from 'react-dom/client';
import { App } from './app/App';

if (__E2E_HOOKS__) {
  void import('./features/voice/e2eHooks').then(m => m.install());
}

createRoot(document.getElementById('root') as HTMLElement).render(<App />);
