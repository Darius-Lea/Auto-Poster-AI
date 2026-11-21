'use strict';
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');

function validateOrigin(raw) {
  if (!raw || raw !== raw.trim()) throw new Error('Set AUTOPOST_APP_ORIGIN to your actual deployed HTTPS origin. No default host is provided.');
  let url;
  try { url = new URL(raw); } catch { throw new Error('AUTOPOST_APP_ORIGIN must be a valid HTTPS origin.'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/' || (url.port && url.port !== '443')) {
    throw new Error('AUTOPOST_APP_ORIGIN requires HTTPS, standard port and an origin only: no credentials, path, query or fragment.');
  }
  const host = url.hostname.toLowerCase();
  if (net.isIP(host.replace(/^\[|\]$/g, '')) || !host.includes('.') || host === 'localhost' || /\.(localhost|local|internal|test|example|invalid)$/.test(host) || /(^|\.)(example\.(com|org|net))$/.test(host)) {
    throw new Error('AUTOPOST_APP_ORIGIN must name the actual public deployment, not a local, reserved, example or IP address.');
  }
  return url.origin;
}

function configForOrigin(origin) {
  return {
    appId: 'io.autoposterai.app',
    appName: 'Auto Poster AI',
    webDir: 'www',
    server: { url: origin, cleartext: false, allowNavigation: [] },
    android: { allowMixedContent: false, webContentsDebuggingEnabled: false },
    ios: { webContentsDebuggingEnabled: false },
    // No native plugins or service credentials are bundled.
    plugins: {}
  };
}
function configure() {
  const origin = validateOrigin(process.env.AUTOPOST_APP_ORIGIN);
  const target = path.resolve(__dirname, '../capacitor.config.json');
  fs.writeFileSync(target, JSON.stringify(configForOrigin(origin), null, 2) + '\n', {mode: 0o600});
  console.log('Mobile configuration prepared for the required HTTPS deployment; no service secrets included.');
}
if (require.main === module) {
  try { configure(); } catch (error) { console.error(error.message); process.exitCode = 1; }
}
module.exports = {validateOrigin, configForOrigin};
