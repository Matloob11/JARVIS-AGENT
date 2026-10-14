const { app, BrowserWindow, ipcMain, Menu, Tray, shell, session } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const http = require('http');
const https = require('https');

const isDev = process.env.ELECTRON_IS_DEV !== '0' && !app.isPackaged;
const DEFAULT_UI_URL = 'https://jarvis.srv1802669.hstgr.cloud';
const OFFLINE_PAGE = path.join(__dirname, 'offline.html');
const DIST_INDEX = path.join(__dirname, '../dist/index.html');

let mainWindow;
let backendProcess;
let tray = null;
let isQuitting = false;
let pendingVpsUrl = DEFAULT_UI_URL;

function readRootEnvValue(name) {
  const envPath = path.join(__dirname, '../../.env');
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

function resolveBackendMode() {
  if (process.env.DISABLE_BACKEND === '1') {
    return 'none';
  }
  const explicit = (process.env.JARVIS_ELECTRON_BACKEND || '').trim().toLowerCase();
  if (explicit === 'none' || explicit === 'local' || explicit === 'desktop' || explicit === 'agent') {
    return explicit === 'agent' ? 'desktop' : explicit;
  }
  const hybrid = readRootEnvValue('JARVIS_HYBRID');
  if (hybrid === '1') {
    return 'none';
  }
  return 'local';
}

function resolvePythonCmd(rootDir) {
  let pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
  const venvs = ['.venv_312', '.venv', 'venv'];
  const scriptsDir = process.platform === 'win32' ? 'Scripts' : 'bin';
  const pythonExec = process.platform === 'win32' ? 'python.exe' : 'python3';
  for (const venv of venvs) {
    const vPath = path.join(rootDir, venv, scriptsDir, pythonExec);
    if (fs.existsSync(vPath)) {
      pythonCmd = vPath;
      break;
    }
  }
  return pythonCmd;
}

function startLocalBackend() {
  const rootDir = path.join(__dirname, '../../');
  const pythonCmd = resolvePythonCmd(rootDir);
  console.log(`[Electron] Starting local bridge (vortex) in ${rootDir}...`);

  const backendPath = path.join(rootDir, 'src/core/vortex.py');
  backendProcess = spawn(pythonCmd, [backendPath], {
    cwd: rootDir,
    stdio: 'inherit',
    shell: true,
  });

  backendProcess.on('error', (err) => {
    console.error(`[Electron] Failed to start local backend: ${err}`);
  });

  backendProcess.on('exit', (code) => {
    console.log(`[Electron] Local backend exited with code ${code}`);
    backendProcess = null;
    if (code !== 0 && !isQuitting) {
      console.log('[Electron] Attempting local backend restart in 5s...');
      if (mainWindow) mainWindow.webContents.send('backend-exit', code);
      setTimeout(startLocalBackend, 5000);
    }
  });
}

function startDesktopNode() {
  const rootDir = path.join(__dirname, '../../');
  const pythonCmd = resolvePythonCmd(rootDir);
  const agentCtl = path.join(rootDir, 'scripts/agent_ctl.py');
  console.log('[Electron] Starting Desktop Node via agent_ctl node start...');

  backendProcess = spawn(
    pythonCmd,
    [agentCtl, 'node', 'start', '--by', 'user', '--reason', 'electron'],
    {
      cwd: rootDir,
      stdio: 'inherit',
      shell: false,
    },
  );

  backendProcess.on('error', (err) => {
    console.error(`[Electron] agent_ctl node start failed: ${err}`);
  });

  backendProcess.on('exit', (code) => {
    console.log(`[Electron] agent_ctl node start finished with code ${code}`);
    backendProcess = null;
    if (code !== 0 && !isQuitting && mainWindow) {
      mainWindow.webContents.send('backend-exit', code);
    }
  });
}

function startBackendForMode(mode) {
  if (mode === 'local') {
    startLocalBackend();
  } else if (mode === 'desktop') {
    startDesktopNode();
  }
}

function fileDistUrl() {
  return `file://${DIST_INDEX.replace(/\\/g, '/')}`;
}

function getUiUrl() {
  return (process.env.JARVIS_UI_URL || DEFAULT_UI_URL).trim();
}

function urlOrigin(raw) {
  try {
    return new URL(raw).origin;
  } catch {
    return null;
  }
}

function contentSecurityPolicy() {
  return [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-src 'none'",
    "form-action 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https:",
    "font-src 'self' data:",
    "media-src 'self' blob: data: https: http://127.0.0.1:* http://localhost:*",
    "worker-src 'self' blob:",
    "connect-src 'self' https: wss: http://127.0.0.1:* http://localhost:* ws://127.0.0.1:* ws://localhost:*",
  ].join('; ');
}

function installContentSecurityPolicy() {
  const csp = contentSecurityPolicy();
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const headers = { ...(details.responseHeaders || {}) };
    const url = details.url || '';
    if (url.startsWith('file:') || url.startsWith('devtools:') || url.startsWith('chrome-extension:')) {
      callback({ responseHeaders: headers });
      return;
    }
    headers['Content-Security-Policy'] = [csp];
    callback({ responseHeaders: headers });
  });
}

function allowedNavigationOrigins(uiUrl) {
  const origins = new Set();
  const uiOrigin = urlOrigin(uiUrl);
  if (uiOrigin && uiOrigin !== 'null') origins.add(uiOrigin);
  if (isDev) {
    origins.add('http://127.0.0.1:5173');
    origins.add('http://localhost:5173');
    origins.add('http://127.0.0.1:4173');
    origins.add('http://localhost:4173');
  }
  return origins;
}

function isNavigationAllowed(targetUrl, uiUrl) {
  if (targetUrl.startsWith('file://')) {
    const normalized = targetUrl.replace(/\\/g, '/').toLowerCase();
    const distNormalized = fileDistUrl().toLowerCase();
    const offlineNormalized = `file://${OFFLINE_PAGE.replace(/\\/g, '/')}`.toLowerCase();
    return normalized.startsWith(distNormalized.split('#')[0]) || normalized.startsWith(offlineNormalized);
  }
  const origin = urlOrigin(targetUrl);
  if (!origin) return false;
  return allowedNavigationOrigins(uiUrl).has(origin);
}

function probeUrlReachable(url, timeoutMs = 12_000) {
  return new Promise((resolve) => {
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      resolve(false);
      return;
    }
    const lib = parsed.protocol === 'https:' ? https : http;
    const req = lib.request(
      {
        method: 'HEAD',
        hostname: parsed.hostname,
        port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
        path: parsed.pathname + parsed.search,
        timeout: timeoutMs,
      },
      (res) => {
        res.resume();
        resolve(res.statusCode >= 200 && res.statusCode < 500);
      },
    );
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
    req.on('error', () => resolve(false));
    req.end();
  });
}

async function loadProductionContent(win) {
  const backendMode = resolveBackendMode();
  const uiUrl = getUiUrl();
  pendingVpsUrl = uiUrl;

  if (process.env.JARVIS_UI_OFFLINE === '1' || backendMode === 'local') {
    await win.loadURL(fileDistUrl());
    return;
  }

  const reachable = await probeUrlReachable(uiUrl);
  if (!reachable) {
    console.warn(`[Electron] VPS unreachable at ${uiUrl}; showing offline page.`);
    await win.loadFile(OFFLINE_PAGE);
    return;
  }

  try {
    await win.loadURL(uiUrl);
  } catch (err) {
    console.warn(`[Electron] loadURL failed: ${err.message}`);
    await win.loadFile(OFFLINE_PAGE);
  }
}

async function retryVpsLoad() {
  if (!mainWindow || isDev) return;
  const uiUrl = pendingVpsUrl || getUiUrl();
  const reachable = await probeUrlReachable(uiUrl);
  if (!reachable) {
    await mainWindow.loadFile(OFFLINE_PAGE);
    return;
  }
  await mainWindow.loadURL(uiUrl);
}

function wireSecurity(win, uiUrl) {
  win.webContents.setWindowOpenHandler(({ url }) => {
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      return { action: 'deny' };
    }
    if (parsed.protocol === 'https:' || parsed.protocol === 'http:') {
      shell.openExternal(parsed.href);
    }
    return { action: 'deny' };
  });

  win.webContents.on('will-navigate', (event, url) => {
    if (!isNavigationAllowed(url, uiUrl)) {
      event.preventDefault();
    }
  });

  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback, details) => {
    const requestOrigin = urlOrigin(details.requestingUrl || '');
    const allowed = allowedNavigationOrigins(uiUrl);
    if (!requestOrigin || !allowed.has(requestOrigin)) {
      callback(false);
      return;
    }
    if (permission === 'media' || permission === 'notifications') {
      callback(true);
      return;
    }
    callback(false);
  });
}

function createTray() {
  try {
    const iconPath = path.join(__dirname, '../public/favicon/favicon-32x32.png');
    tray = new Tray(iconPath);

    const contextMenu = Menu.buildFromTemplate([
      { label: 'Show JARVIS & ANNA', click: () => mainWindow.show() },
      { type: 'separator' },
      {
        label: 'Quit JARVIS & ANNA',
        click: () => {
          isQuitting = true;
          app.quit();
        },
      },
    ]);

    tray.setToolTip('JARVIS & ANNA');
    tray.setContextMenu(contextMenu);
    tray.on('double-click', () => mainWindow.show());
  } catch (err) {
    console.error(`[Electron] Failed to create tray: ${err.message}`);
  }
}

function createWindow() {
  const uiUrl = getUiUrl();

  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    show: false,
    frame: false,
    transparent: true,
    hasShadow: true,
    autoHideMenuBar: true,
    title: 'JARVIS & ANNA',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, 'preload.cjs'),
      userAgent: 'JARVIS-ANNA-Neural-Shell (Electron)',
    },
    backgroundColor: '#00000000',
  });

  wireSecurity(mainWindow, uiUrl);

  if (process.platform === 'darwin') {
    app.setAboutPanelOptions({
      applicationName: 'JARVIS & ANNA',
      applicationVersion: '1.0.0',
    });
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  if (isDev) {
    mainWindow.show();
    mainWindow.loadURL('http://127.0.0.1:5173');
  } else {
    loadProductionContent(mainWindow);
  }

  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
    }
    return false;
  });

  mainWindow.on('maximize', () => {
    mainWindow.webContents.send('window-maximized');
  });

  mainWindow.on('unmaximize', () => {
    mainWindow.webContents.send('window-unmaximized');
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.on('ready', () => {
  installContentSecurityPolicy();
  const backendMode = resolveBackendMode();
  if (backendMode === 'none') {
    console.log('[Electron] Backend spawn disabled (mode none or DISABLE_BACKEND=1).');
  } else {
    startBackendForMode(backendMode);
  }
  createWindow();
  createTray();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  isQuitting = true;
  if (backendProcess) {
    console.log('[Electron] Terminating backend helper process...');
    backendProcess.kill();
  }
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});

ipcMain.on('window-minimize', () => {
  if (mainWindow) mainWindow.minimize();
});

ipcMain.on('window-maximize', () => {
  if (mainWindow) {
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow.maximize();
    }
  }
});

ipcMain.on('window-close', () => {
  if (mainWindow) mainWindow.hide();
});

ipcMain.on('retry-main-load', () => {
  retryVpsLoad();
});
