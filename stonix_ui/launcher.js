import { spawn } from 'child_process';
import waitOn from 'wait-on';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '..');

function readRootEnvValue(name) {
  const envPath = path.join(rootDir, '.env');
  if (!fs.existsSync(envPath)) return null;
  const text = fs.readFileSync(envPath, 'utf8');
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    if (key !== name) continue;
    let val = trimmed.slice(eq + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    return val;
  }
  return null;
}

function shouldStartVortex() {
  const backend = (process.env.JARVIS_ELECTRON_BACKEND || '').trim().toLowerCase();
  if (backend === 'local') return true;
  if (backend === 'none' || backend === 'agent') return false;
  return readRootEnvValue('JARVIS_HYBRID') !== '1';
}

console.log('[Launcher] Initializing JARVIS Systems...');

let backend = null;
if (shouldStartVortex()) {
  console.log('[Launcher] Starting Backend (Vortex)...');
  backend = spawn('python', ['-m', 'src.core.vortex'], {
    stdio: 'inherit',
    shell: true,
    cwd: rootDir,
    env: { ...process.env, PYTHONPATH: rootDir },
  });
} else {
  console.log('[Launcher] Skipping Vortex (hybrid mode or explicit backend mode).');
}

console.log('[Launcher] Starting Vite on 127.0.0.1:5173...');
const vite = spawn('npx', ['vite', '--no-open', '--host', '127.0.0.1', '--port', '5173'], {
  stdio: 'inherit',
  shell: true,
});

const url = 'http://127.0.0.1:5173';
console.log(`[Launcher] Waiting for ${url}...`);

waitOn({
  resources: [url],
  timeout: 15000,
  interval: 500,
})
  .then(() => {
    console.log('[Launcher] Frontend ready. Starting Electron...');
    startElectron();
  })
  .catch((err) => {
    console.error(`[Launcher] Frontend wait-on failed: ${err.message}`);
    startElectron();
  });

function startElectron() {
  const env = { ...process.env, DISABLE_BACKEND: '1', JARVIS_ELECTRON_BACKEND: 'none' };
  delete env.ELECTRON_RUN_AS_NODE;

  const electron = spawn('npx', ['electron', '.'], {
    stdio: 'inherit',
    shell: true,
    env,
  });

  electron.on('exit', (code) => {
    console.log('[Launcher] Electron exited. Shutting down...');
    backend?.kill();
    vite.kill();
    process.exit(code || 0);
  });
}
