/* Only public, fixed assets belong in this cache. Account documents and APIs are network-only. */
'use strict';
const CACHE = 'autoposter-public-v1';
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
  // A failed navigation gets only the constant public fallback. Never store the response.
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(async () => {
      const response = await (await caches.open(CACHE)).match(OFFLINE);
      return response || new Response('Connect to the Internet to open Auto Poster AI.', {
        status:503, headers:{'Content-Type':'text/plain; charset=utf-8','Cache-Control':'no-store'}
      });
    }));
  }
  // API requests, account fetches and other resources use normal network behavior.
});
