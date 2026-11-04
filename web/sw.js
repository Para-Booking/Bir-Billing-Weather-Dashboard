/* Service worker: lets the pages (especially flight mode) open with a weak or
   no signal. Pages and assets: network first, cached copy if the network is
   slow (3 s) or down. Live data (/api) and photos (/media) are never cached here;
   flight mode keeps its own last reading. */
const CACHE = 'bbw-shell-v3';
const SHELL = [
  '/', '/fly', '/station.html',
  '/css/app.css', '/css/fly.css',
  '/js/common.js', '/js/overview.js', '/js/station.js', '/js/fly.js',
  '/vendor/uPlot.iife.min.js', '/vendor/uPlot.min.css',
  '/manifest.webmanifest', '/icons/icon.svg', '/icons/icon-192.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function cacheKey(request) {
  const url = new URL(request.url);
  if (request.mode === 'navigate' && url.pathname.startsWith('/s/')) return '/station.html';
  return url.pathname;
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  const key = cacheKey(request);
  const network = fetch(request).then((res) => {
    if (res.ok) cache.put(key, res.clone());
    return res;
  });
  const timeout = new Promise((resolve) => setTimeout(resolve, 3000));
  try {
    const res = await Promise.race([network, timeout]);
    if (res) return res;
  } catch (e) { /* fall through to cache */ }
  const cached = await cache.match(key);
  if (cached) return cached;
  return network; // nothing cached: keep waiting for the network
}

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/media/')) return;
  event.respondWith(networkFirst(event.request));
});
