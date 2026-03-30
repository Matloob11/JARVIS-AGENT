const { app, BrowserWindow, ipcMain, Menu, Tray } = require('electron');
const path = require('path');
const isDev = process.env.ELECTRON_IS_DEV !== '0' && !app.isPackaged;
const startBackendEnabled = process.env.DISABLE_BACKEND !== '1';
const { spawn } = require('child_process');

let mainWindow;
let backendProcess;
let tray = null;
let isQuitting = false;

const fs = require('fs');

function startBackend() {
  const rootDir = path.join(__dirname, '../../');
  console.log(`[Electron] Starting JARVIS Backend in ${rootDir}...`);
  
  let pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
  
  // Try to find a virtual environment's python
  const venvs = ['.venv_312', '.venv', 'venv'];
  const scriptsDir = process.platform === 'win32' ? 'Scripts' : 'bin';
  const pythonExec = process.platform === 'win32' ? 'python.exe' : 'python3';
  
  for (const venv of venvs) {
    const vPath = path.join(rootDir, venv, scriptsDir, pythonExec);
    if (fs.existsSync(vPath)) {
      pythonCmd = vPath;
      console.log(`[Electron] Using virtual env python: ${pythonCmd}`);
      break;
    }
  }
  
  const backendPath = path.join(rootDir, 'src/core/vortex.py');
  backendProcess = spawn(pythonCmd, [backendPath], {
    cwd: rootDir,
    stdio: 'inherit',
    shell: true
  });

  backendProcess.on('error', (err) => {
    console.error(`[Electron] Failed to start backend: ${err}`);
  });

  backendProcess.on('exit', (code) => {
    console.log(`[Electron] Backend process exited with code ${code}`);
    if (code !== 0 && !isQuitting) {
       console.log('[Electron] Attempting backend restart in 5s...');
       if (mainWindow) mainWindow.webContents.send('backend-exit', code);
       setTimeout(startBackend, 5000);
    }
  });
}

function createTray() {
  try {
    const iconPath = path.join(__dirname, '../public/favicon/favicon-32x32.png');
    tray = new Tray(iconPath);
    
    const contextMenu = Menu.buildFromTemplate([
      { label: 'Show JARVIS', click: () => mainWindow.show() },
      { type: 'separator' },
      { label: 'Quit JARVIS', click: () => {
        isQuitting = true;
        app.quit();
      }}
    ]);

    tray.setToolTip('JARVIS-AGENT');
    tray.setContextMenu(contextMenu);
    tray.on('double-click', () => mainWindow.show());
  } catch (err) {
    console.error(`[Electron] Failed to create tray: ${err.message}`);
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    show: false,
    frame: false,
    transparent: true,
    hasShadow: true,
    autoHideMenuBar: true,
    title: 'JARVIS & ANNA Neural Interface',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: path.join(__dirname, 'preload.cjs'),
      userAgent: 'JARVIS-ANNA-Neural-Shell (Electron)',
    },
    backgroundColor: '#00000000',
  });

  // Rebranding about panel if applicable
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
  }

  const startURL = isDev
    ? 'http://127.0.0.1:5173'
    : `file://${path.join(__dirname, '../dist/index.html')}`;

  mainWindow.loadURL(startURL);

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

  mainWindow.on('closed', () => (mainWindow = null));
}

app.on('ready', () => {
  if (startBackendEnabled) {
    startBackend();
  } else {
    console.log('[Electron] Automatic backend start disabled by environment flag.');
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
    console.log('[Electron] Terminating backend process...');
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
  mainWindow.hide(); // Hide to tray instead of quitting
});
