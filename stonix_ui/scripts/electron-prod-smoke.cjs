const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');

const root = path.resolve(__dirname, '..');
const checks = [
  ['production index', path.join(root, 'dist', 'index.html')],
  ['electron main', path.join(root, 'electron', 'main.cjs')],
  ['electron preload', path.join(root, 'electron', 'preload.cjs')],
  ['electron offline page', path.join(root, 'electron', 'offline.html')],
];

for (const [label, filePath] of checks) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`Missing ${label}: ${filePath}`);
  }
}

const electronBinary = require('electron');
if (!electronBinary || !fs.existsSync(electronBinary)) {
  throw new Error('Electron binary was not resolved from node_modules.');
}

const mainSource = fs.readFileSync(path.join(root, 'electron', 'main.cjs'), 'utf8');
if (!mainSource.includes('ELECTRON_IS_DEV') || !mainSource.includes('../dist/index.html')) {
  throw new Error('Electron main process is not wired for production dist loading.');
}

function waitForUrl(url, timeoutMs = 60_000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(url, (res) => {
        res.resume();
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 500) {
          resolve();
          return;
        }
        retry();
      });
      req.on('error', retry);
      function retry() {
        if (Date.now() - started > timeoutMs) {
          reject(new Error(`Timed out waiting for ${url}`));
          return;
        }
        setTimeout(tick, 400);
      }
    };
    tick();
  });
}

function run(command, args, options = {}) {
  return spawn(command, args, {
    cwd: root,
    stdio: 'inherit',
    shell: true,
    ...options,
  });
}

async function isUrlUp(url) {
  try {
    await waitForUrl(url, 2_000);
    return true;
  } catch {
    return false;
  }
}

async function main() {
  const previewUrl = 'http://127.0.0.1:4173';
  let preview = null;
  const previewAlreadyUp = await isUrlUp(previewUrl);

  if (!previewAlreadyUp) {
    preview = run('npm', ['run', 'preview', '--', '--host', '127.0.0.1', '--port', '4173'], {
      env: {
        ...process.env,
        VITE_BRIDGE_BASE: '/vortex',
        VITE_VORTEX_URL: '',
        VITE_DEV_BRIDGE: 'http://127.0.0.1:5199',
      },
    });
  }

  try {
    await waitForUrl(previewUrl);

    const electronProc = spawn(
      electronBinary,
      ['.'],
      {
        cwd: root,
        stdio: 'inherit',
        env: {
          ...process.env,
          ELECTRON_IS_DEV: '0',
          JARVIS_UI_URL: 'http://127.0.0.1:4173',
          JARVIS_ELECTRON_BACKEND: 'none',
          DISABLE_BACKEND: '1',
          ELECTRON_DISABLE_SANDBOX: '1',
        },
      },
    );

    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        electronProc.kill();
        resolve();
      }, 8_000);
      electronProc.on('exit', (code) => {
        clearTimeout(timer);
        if (code && code !== 0) {
          reject(new Error(`Electron smoke exited with code ${code}`));
          return;
        }
        resolve();
      });
      electronProc.on('error', reject);
    });
  } finally {
    preview?.kill();
  }

  console.log('Electron production smoke passed.');
}

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
