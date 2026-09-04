const CACHE_NAME = 'pm-v4';
const PRECACHE = ['/static/icon-192.png', '/static/icon-512.png', '/static/manifest.json'];

self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
    e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
    if (e.request.method !== 'GET') return;
    const p = new URL(e.request.url).pathname;
    if (!p.startsWith('/static/')) return;
    e.respondWith(caches.match(e.request).then(r => {
        if (r) return r;
        return fetch(e.request).then(res => {
            if (res.ok) { const c = res.clone(); caches.open(CACHE_NAME).then(cache => cache.put(e.request, c)); }
            return res;
        });
    }));
});

self.addEventListener('push', e => {
    const d = e.data ? e.data.json() : {};
    e.waitUntil(self.registration.showNotification(d.title || 'ProMaster', { body: d.body || '', icon: '/static/icon-192.png', data: { url: d.url || '/' } }));
});

self.addEventListener('notificationclick', e => {
    e.notification.close();
    e.waitUntil(clients.openWindow(e.notification.data.url));
});
