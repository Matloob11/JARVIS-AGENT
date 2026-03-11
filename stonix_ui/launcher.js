import { spawn } from 'child_process';
import waitOn from 'wait-on';

console.log('[Launcher] Starting Vite on 127.0.0.1:5173...');
const vite = spawn('npx', ['vite', '--no-open', '--host', '127.0.0.1', '--port', '5173'], {
    stdio: 'inherit',
    shell: true
});

// A simpler URL often works better with wait-on in different environments
const url = 'http://127.0.0.1:5173';
console.log(`[Launcher] Waiting for ${url} (max 10s)...`);

waitOn({
    resources: [url],
    timeout: 10000, // Reduced from 60s to 10s for faster fallback
    interval: 500,
    // By default, wait-on does a HEAD request which is fine for Vite
}).then(() => {
    console.log(`[Launcher] Vite is ready. Starting Electron...`);
    startElectron();
}).catch((err) => {
    console.error(`[Launcher] Wait-on notice: ${err.message}`);
    console.log('[Launcher] Starting Electron (fallback mode)...');
    startElectron();
});

function startElectron() {
    const electron = spawn('npx', ['electron', '.'], {
        stdio: 'inherit',
        shell: true
    });

    electron.on('exit', (code) => {
        console.log(`[Launcher] Electron exited with code ${code}`);
        vite.kill();
        process.exit(code || 0);
    });
}
