/* Only public, fixed assets belong in this cache. Account documents and APIs are network-only. */
'use strict';
const CACHE = 'autoposter-public-v2';
const OFFLINE = '/offline';
const NAVIGATION_TIMEOUT_MS = 12000;
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
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (request.signal?.aborted) cancel();
  else request.signal?.addEventListener('abort', cancel, {once:true});
  let timer;
  try {
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new Error('The service did not respond in time.'));
      }, NAVIGATION_TIMEOUT_MS);
    });
    const response = await Promise.race([fetch(request, {signal:controller.signal}), deadline]);
    if (response.status >= 500) {
      // The host replied, but cannot serve the page. Release an unread error
      // body and show the same useful recovery screen instead of a raw gateway.
      if (response.body) void response.body.cancel().catch(() => {});
      return connectionFallback();
    }
    return response;
  } catch (_) {
    return connectionFallback();
  } finally {
    clearTimeout(timer);
    request.signal?.removeEventListener('abort', cancel);
  }
}

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
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
  // Bound GET document loading. POSTs, APIs and customer data stay untouched.
  if (request.mode === 'navigate') {
    event.respondWith(navigate(request));
  }
  // API requests, account fetches and other resources use normal network behavior.
});
