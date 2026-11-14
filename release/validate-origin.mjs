import { pathToFileURL } from 'node:url';

/** Validate a public deployment origin without accepting executable input. */
export function validateOrigin(value) {
  if (typeof value !== 'string' || !value.trim() || value !== value.trim()) {
    throw new Error('AUTOPOST_APP_ORIGIN must be the actual HTTPS origin of your deployed app.');
  }
  const raw = value;
  let url;
  try { url = new URL(raw); } catch { throw new Error('App origin must be a valid HTTPS URL.'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/' || url.port) {
    throw new Error('Use a standard-port HTTPS origin only, without credentials, a path, query, or fragment.');
  }
  const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, '');
  if (!host.includes('.') || host.includes(':') || /^(?:\d{1,3}\.){3}\d{1,3}$/.test(host)
      || /(?:^|\.)(?:localhost|local|internal|invalid|test|example)$/.test(host)
      || /(?:^|\.)example\.(?:com|org|net)$/.test(host)) {
    throw new Error('App origin must use a public DNS hostname, not a local address or placeholder.');
  }
  if (![url.origin, `${url.origin}/`, `${url.origin}:443`, `${url.origin}:443/`].includes(raw)) {
    throw new Error('Use the canonical HTTPS origin, without escapes or additional URL components.');
  }
  return url.origin;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    process.stdout.write(`${validateOrigin(process.env.AUTOPOST_APP_ORIGIN)}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
