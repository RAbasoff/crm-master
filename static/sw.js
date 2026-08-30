const CACHE_NAME = 'promaster-v2';
const STATIC_ASSETS = [
    '/',
    '/static/manifest.json',
    '/static/icon-192.png',
    '/static/icon-512.png'
];

// Install - cache static assets
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => {
            return cache.addAll(STATIC_ASSETS);
        })
    );
    self.skipWaiting();
});

// Activate - clean old caches
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys => {
            return Promise.all(
                keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
            );
        })
    );
    self.clients.claim();
});

// Fetch strategy
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);
    
    // Skip non-GET requests
    if (event.request.method !== 'GET') return;
    
    // Skip chrome-extension and non-http
    if (!url.protocol.startsWith('http')) return;
    
    // API calls - Network First
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(
            fetch(event.request)
                .catch(() => new Response(JSON.stringify({error: 'Offline'}), {
                    headers: {'Content-Type': 'application/json'}
                }))
        );
        return;
    }
    
    // Static assets - Cache First
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.match(event.request).then(cached => {
                return cached || fetch(event.request).then(response => {
                    if (response.ok) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
                    }
                    return response;
                });
            })
        );
        return;
    }
    
    // HTML pages - Network First with cache fallback
    event.respondWith(
        fetch(event.request)
            .then(response => {
                if (response.ok) {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
                }
                return response;
            })
            .catch(() => {
                return caches.match(event.request).then(cached => {
                    return cached || caches.match('/');
                });
            })
    );
});

// Push notifications
self.addEventListener('push', event => {
    const data = event.data ? event.data.json() : {};
    const options = {
        body: data.body || 'Новое уведомление',
        icon: '/static/icon-192.png',
        badge: '/static/icon-192.png',
        vibrate: [200, 100, 200],
        tag: data.tag || 'default',
        renotify: true,
        data: { url: data.url || '/' },
        actions: [
            { action: 'open', title: 'Открыть' },
            { action: 'close', title: 'Закрыть' }
        ]
    };
    event.waitUntil(
        self.registration.showNotification(data.title || 'ProMaster', options)
    );
});

// Notification click
self.addEventListener('notificationclick', event => {
    event.notification.close();
    if (event.action === 'close') return;
    event.waitUntil(
        clients.matchAll({type: 'window'}).then(clientList => {
            for (const client of clientList) {
                if (client.url.includes(self.location.origin) && 'focus' in client) {
                    client.navigate(event.notification.data.url);
                    return client.focus();
                }
            }
            return clients.openWindow(event.notification.data.url);
        })
    );
});
