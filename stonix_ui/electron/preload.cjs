const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  minimize: () => ipcRenderer.send('window-minimize'),
  maximize: () => ipcRenderer.send('window-maximize'),
  close: () => ipcRenderer.send('window-close'),
  onBackendExit: (callback) => ipcRenderer.on('backend-exit', (_event, code) => callback(code)),
  onMaximize: (callback) => ipcRenderer.on('window-maximized', () => callback()),
  onUnmaximize: (callback) => ipcRenderer.on('window-unmaximized', () => callback()),
});
