// Offline + instant repeat loads. The data file is the expensive part of a cold
// start (~160 KB gzipped over a mobile link), so serve it from cache first and
// refresh it in the background.
const VERSION = 'places-v1';
const SHELL = ['./', 'index.html', 'app.css', 'app.js', 'data/places.json'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION)
    .then((c) => c.addAll(SHELL))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const { request } = e;
  // Never cache API calls to a model provider, and never interfere with
  // anything cross-origin.
  if (request.method !== 'GET' || new URL(request.url).origin !== self.location.origin) return;

  e.respondWith((async () => {
    const cache = await caches.open(VERSION);
    const hit = await cache.match(request, { ignoreSearch: false });
    const fresh = fetch(request).then((res) => {
      if (res.ok) cache.put(request, res.clone());
      return res;
    });
    // Stale-while-revalidate: answer instantly from cache, update for next time.
    return hit || fresh;
  })());
});
