'use strict';

const net = require('node:net');

function validateOrigin(value) {
  if (typeof value !== 'string' || !value.trim()) throw new Error('AUTOPOST_APP_ORIGIN must be the real public HTTPS app origin.');
  const url = new URL(value.trim());
  const hostname = url.hostname.toLowerCase();
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/' ||
      (url.port && url.port !== '443') || hostname === 'localhost' || hostname.endsWith('.localhost') ||
      hostname.endsWith('.local') || !hostname.includes('.') || net.isIP(hostname.replace(/^\[|\]$/g, ''))) {
    throw new Error('Use a public HTTPS origin without a path, credentials, query, fragment, IP address or custom port.');
  }
  return url.origin;
}

function sameOrigin(candidate, origin) {
  try {
    const url = new URL(candidate);
    return url.protocol === 'https:' && !url.username && !url.password && url.origin === origin;
  } catch { return false; }
}

function browserFlow(candidate, origin) {
  if (!sameOrigin(candidate, origin)) return null;
  let path;
  try { path = decodeURIComponent(new URL(candidate).pathname).replace(/\/+/g, '/').replace(/\/+$/, ''); }
  catch { return null; }
  if (path === '/facebook/connect') return { label: 'Connect Facebook', destination: `${origin}/pages` };
  if (path === '/connections/canva/connect') return { label: 'Connect Canva', destination: `${origin}/connections` };
  if (path === '/billing/checkout') return { label: 'Manage your subscription', destination: `${origin}/control` };
  return null;
}

function externalHttps(candidate) {
  try {
    const url = new URL(candidate);
    return url.protocol === 'https:' && !url.username && !url.password;
  } catch { return false; }
}

module.exports = { validateOrigin, sameOrigin, browserFlow, externalHttps };
