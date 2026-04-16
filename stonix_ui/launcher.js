import { spawn } from 'child_process';
import waitOn from 'wait-on';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '..');

console.log('[Launcher] Initializing JARVIS Systems...');

// 🛠️ 1. Start Python Backend (Vortex Orchestrator)
console.log('[Launcher] Starting Backend (Vortex)...');
const backend = spawn('python', ['-m', 'src.core.vortex'], {
    stdio: 'inherit',
    shell: true,
    cwd: rootDir,
    env: { ...process.env, PYTHONPATH: rootDir }
});

// 🎨 2. Start Vite Frontend
console.log('[Launcher] Starting Vite on 127.0.0.1:5173...');
const vite = spawn('npx', ['vite', '--no-open', '--host', '127.0.0.1', '--port', '5173'], {
    stdio: 'inherit',
    shell: true
});

const url = 'http://127.0.0.1:5173';
console.log(`[Launcher] Waiting for ${url}...`);

waitOn({
    resources: [url],
    timeout: 15000,
    interval: 500,
}).then(() => {
    console.log(`[Launcher] Frontend ready. Starting Electron...`);
    startElectron();
}).catch((err) => {
    console.error(`[Launcher] Frontend wait-on failed: ${err.message}`);
    startElectron();
});

function startElectron() {
    const env = { ...process.env, DISABLE_BACKEND: '1' };
    delete env.ELECTRON_RUN_AS_NODE;

    const electron = spawn('npx', ['electron', '.'], {
        stdio: 'inherit',
        shell: true,
        env: env
    });

    // Cleanup all processes when Electron closes
    electron.on('exit', (code) => {
        console.log(`[Launcher] Electron exited. Shutting down...`);
        backend.kill();
        vite.kill();
        process.exit(code || 0);
    });
}
