/* ProMaster CRM — Service Worker (offline shell + smart caching)
 * Strategy:
 *  - static assets: cache-first (immutable-ish)
 *  - HTML pages: network-first, fallback to cache, fallback to offline.html
 *  - API / POST: always network (never cache mutations)
 */
const CACHE = 'promaster-v1';
const OFFLINE_URL = '/static/offline.html';

const PRECACHE = [
  OFFLINE_URL,
  '/static/manifest.json',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/js/scanner-common.js',
  '/static/fonts/DejaVuSans.ttf',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

function isStatic(url) {
  return url.pathname.startsWith('/static/') ||
         url.pathname.startsWith('/static\\') ||
         /\.(css|js|png|jpg|jpeg|gif|svg|webp|ico|ttf|woff2?|json)$/.test(url.pathname);
}

function isNoCache(url) {
  return url.pathname.startsWith('/api/') ||
         url.pathname.includes('/export/') ||
         url.pathname.includes('/download') ||
         url.pathname.includes('/print') ||
         url.pathname.includes('/qr/') ||
         url.pathname.includes('/scan');
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (isNoCache(url)) return;

  // Static: cache-first
  if (isStatic(url)) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res && res.status === 200 && res.type === 'basic') {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(req, clone));
        }
        return res;
      }).catch(() => hit))
    );
    return;
  }

  // HTML pages: network-first
  if (req.headers.get('accept') && req.headers.get('accept').includes('text/html')) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.status === 200) {
            const clone = res.clone();
            caches.open(CACHE).then((c) => c.put(req, clone));
          }
          return res;
        })
        .catch(() =>
          caches.match(req).then((hit) =>
            hit || caches.match(OFFLINE_URL).then((off) =>
              off || new Response('<h1>Offline</h1>', {
                status: 503,
                headers: { 'Content-Type': 'text/html; charset=utf-8' },
              })
            )
          )
        )
    );
    return;
  }

  // everything else GET: stale-while-revalidate-lite
  event.respondWith(
    caches.match(req).then((hit) => {
      const net = fetch(req).then((res) => {
        if (res && res.status === 200 && res.type === 'basic') {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(req, clone));
        }
        return res;
      }).catch(() => hit);
      return hit || net;
    })
  );
});

// Allow page to ask SW to skip waiting / update cache version
self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') self.skipWaiting();
  if (event.data === 'clearCache') {
    caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k))));
  }
});
