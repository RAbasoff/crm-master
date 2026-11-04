/* ProMaster CRM — Service Worker (offline shell + smart caching)
 * Strategy:
 *  - static assets: cache-first (immutable-ish)
 *  - HTML pages: network-first, fallback to cache, fallback to offline.html
 *  - API / POST: always network (never cache mutations)
 */
const CACHE = 'promaster-v7';
const OFFLINE_URL = '/static/offline.html';

const PRECACHE = [
  OFFLINE_URL,
  '/static/manifest.json',
  '/static/icon-152.png',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/apple-touch-icon.png',
  '/static/js/scanner-common.js',
  '/static/js/html5-qrcode.min.js',
  '/static/js/offline-queue.js',
  '/static/fonts/DejaVuSans.ttf',
  '/gas/',
  '/gas/scan',
  '/gas/scan-list',
];

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') { self.skipWaiting(); }
});

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
         url.pathname.includes('/scan') ||
         url.pathname.includes('/offline/');
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

// Web Push: системное уведомление + вибрация (Android / iOS 16.4+ PWA)
self.addEventListener('push', (event) => {
  let data = { title: 'ProMaster', body: '', url: '/' };
  try {
    if (event.data) data = Object.assign(data, event.data.json());
  } catch (e) {
    try { data.body = event.data ? event.data.text() : ''; } catch (e2) {}
  }
  // Тег = тип + URL: все «fault» раньше схлопывались в одно уведомление
  // на экране блокировки (tag=ntype), теперь каждая заявка — своя.
  const tag = (data.tag || 'promaster') + ':' + (data.url || '');
  event.waitUntil((async () => {
    const title = data.title || 'ProMaster';
    const opts = {
      body: data.body || '',
      tag: tag,
      requireInteraction: true,
      vibrate: [300, 100, 300, 100, 400],
      icon: '/static/icon-192.png',
      badge: '/static/icon-192.png',
      data: { url: data.url || '/' },
    };
    try {
      await self.registration.showNotification(title, opts);
    } catch (e) {
      // iOS: часть опций может отвергаться — показываем минимальную версию
      try {
        await self.registration.showNotification(title, {
          body: data.body || '', tag: tag, data: opts.data
        });
      } catch (e2) {}
    }
  })());
});

// ── Жизненно важно для iPhone: при обновлении/пересоздании SW браузер
// может заменить push-подписку. Без этого обработчика подписка
// «осиротевает»: на сервере старая, на телефоне её больше нет —
// push молча перестают приходить.
function swUrlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil((async () => {
    try {
      const r = await fetch('/api/push/vapid-public-key');
      const d = await r.json();
      const key = d.publicKey || d.key;
      if (!key) return;
      const sub = await self.registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: swUrlBase64ToUint8Array(key),
      });
      // Синхронизацию с сервером сделает страница при следующем открытии —
      // она увидит новую подписку и отправит её на /api/push/subscribe.
      console.log('pushsubscriptionchange: resubscribed', !!sub);
    } catch (e) {
      console.log('pushsubscriptionchange failed:', e);
    }
  })());
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if ('focus' in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});
