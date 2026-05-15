const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const checks = [
  ['production index', path.join(root, 'dist', 'index.html')],
  ['electron main', path.join(root, 'electron', 'main.cjs')],
  ['electron preload', path.join(root, 'electron', 'preload.cjs')],
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

console.log('Electron production smoke passed.');
