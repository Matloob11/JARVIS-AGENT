/**
 * Electron navigation and permission decisions. No Electron import, so tests can load it.
 */

function urlOrigin(raw) {
  try {
    return new URL(raw).origin;
  } catch {
    return null;
  }
}

function fileUrl(absPath) {
  return `file://${String(absPath).replace(/\\/g, '/')}`;
}

function filePathKey(fileUrlValue) {
  let rest = String(fileUrlValue).replace(/\\/g, '/').replace(/^file:\/\//i, '');
  rest = rest.replace(/^\/+/, '');
  try {
    rest = decodeURIComponent(rest);
  } catch {
    /* keep the raw path */
  }
  return rest.split('#')[0].split('?')[0].toLowerCase();
}

function isAppFilePage(targetUrl, paths) {
  if (!/^file:/i.test(String(targetUrl))) return false;
  const key = filePathKey(targetUrl);
  return key === filePathKey(fileUrl(paths.distIndex)) || key === filePathKey(fileUrl(paths.offlinePage));
}

function allowedNavigationOrigins(uiUrl, isDev) {
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

function isNavigationAllowed(targetUrl, uiUrl, paths) {
  if (/^file:/i.test(String(targetUrl))) {
    return isAppFilePage(targetUrl, paths);
  }
  const origin = urlOrigin(targetUrl);
  if (!origin) return false;
  return allowedNavigationOrigins(uiUrl, paths.isDev).has(origin);
}

function isPermissionAllowed(requestingUrl, permission, uiUrl, paths) {
  const requestUrl = requestingUrl || '';
  let fromApp = false;
  if (/^file:/i.test(requestUrl)) {
    fromApp = isNavigationAllowed(requestUrl, uiUrl, paths);
  } else {
    const origin = urlOrigin(requestUrl);
    fromApp = Boolean(
      origin && origin !== 'null' && allowedNavigationOrigins(uiUrl, paths.isDev).has(origin),
    );
  }
  if (!fromApp) return false;
  return permission === 'media' || permission === 'notifications';
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

module.exports = {
  urlOrigin,
  fileUrl,
  allowedNavigationOrigins,
  isNavigationAllowed,
  isPermissionAllowed,
  contentSecurityPolicy,
};
