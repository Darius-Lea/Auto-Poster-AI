/* Only public, fixed assets belong in this cache. Account documents and APIs are network-only. */
'use strict';
const CACHE = 'autoposter-public-v3';
const OFFLINE = '/offline';
const PUBLIC_ASSETS = new Set([
  OFFLINE,
  '/static/brand/mark.svg',
  '/static/brand/icon-192.png',
  '/static/brand/icon-512.png',
  '/static/brand/apple-touch-icon.png'
]);
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => Promise.all(
    [...PUBLIC_ASSETS].map(async path => {
      const response = await fetch(new Request(path, {credentials:'omit', cache:'reload', redirect:'error'}));
      if (!response.ok || response.redirected || response.type === 'opaque') throw new Error('Public asset unavailable');
      await cache.put(path, response);
    })
  )).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith('autoposter-public-') && key !== CACHE).map(key => caches.delete(key))
  )).then(() => self.clients.claim()));
});

async function connectionFallback() {
  // A constant public document is safe for every failed navigation. Never put
  // the requested URL, customer HTML, callbacks or session data in the cache.
  try {
    const cached = await (await caches.open(CACHE)).match(OFFLINE);
    if (cached) return cached;
  } catch (_) { /* Storage can be unavailable even when the worker is active. */ }
  return new Response('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Connect to continue · Auto Poster AI</title><main><h1>Connect to continue.</h1><p>We could not load this page. Your connection or the service may be taking longer than usual.</p><button type="button" onclick="window.location.reload()">Try again</button></main></html>', {
    status:503, headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}
  });
}

async function navigate(request) {
  // Render can take longer to wake than an ordinary page request. Do not abort
  // a healthy startup or replace genuine HTTP errors with an offline page.
  try { return await fetch(request); }
  catch (_) { return connectionFallback(); }
}

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Recovery can explicitly use the browser's ordinary network navigation.
  if (url.searchParams.get('_ap_retry') === '1') return;
  if (PUBLIC_ASSETS.has(url.pathname) && !url.search) {
    event.respondWith(caches.open(CACHE).then(async cache => {
      const stored = await cache.match(url.pathname);
      if (stored) return stored;
      const response = await fetch(new Request(url.href, {credentials:'omit', redirect:'error'}));
      if (response.ok && !response.redirected && response.type !== 'opaque') await cache.put(url.pathname, response.clone());
      return response;
    }));
    return;
  }
  // Only genuine network failures use recovery. HTTP errors remain visible.
  if (request.mode === 'navigate') {
    event.respondWith(navigate(request));
  }
  // API requests, account fetches and other resources use normal network behavior.
});
