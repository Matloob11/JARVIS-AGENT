const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  minimize: () => ipcRenderer.send('window-minimize'),
  maximize: () => ipcRenderer.send('window-maximize'),
  close: () => ipcRenderer.send('window-close'),
  retryMainLoad: () => ipcRenderer.send('retry-main-load'),
  onBackendExit: (callback) => ipcRenderer.on('backend-exit', (_event, code) => callback(code)),
  onMaximize: (callback) => ipcRenderer.on('window-maximized', () => callback()),
  onUnmaximize: (callback) => ipcRenderer.on('window-unmaximized', () => callback()),
});
