const fs = require('fs');
const path = require('path');

const pkgPath = path.join(__dirname, 'package.json');
const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));

pkg.scripts.dev = "npm run electron-dev";
pkg.scripts["vite-dev"] = "vite --no-open";
pkg.scripts["electron-dev"] = "concurrently --kill-others \"npm run vite-dev\" \"wait-on http://localhost:5173 && electron . --no-sandbox\"";

fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2));
console.log('Successfully updated package.json scripts');
console.log('Current dev script:', pkg.scripts.dev);
console.log('Current electron-dev script:', pkg.scripts['electron-dev']);
