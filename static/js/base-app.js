/* base-app.js — вынесено из base.html (кешируется браузером).
   Переводы приходят в window.__BOOT.T (см. base.html boot-блок). */
function __T(k) { return (window.__BOOT && window.__BOOT.T && window.__BOOT.T[k]) || k; }

        // SIDEBAR
        function toggleSidebar() {
            const sb = document.getElementById('sidebar');
            const ov = document.getElementById('sidebarOverlay');
            sb.classList.toggle('open');
            ov.classList.toggle('open');
        }
        function closeSidebar() {
            document.getElementById('sidebar').classList.remove('open');
            document.getElementById('sidebarOverlay').classList.remove('open');
        }
        
        // Role switcher dropdown
        // Collapsible sidebar sections
        function toggleSbGroup(sectionEl) {
            const items = sectionEl.nextElementSibling;
            if (!items || !items.classList.contains('sb-group-items')) return;
            const isCollapsed = items.classList.contains('collapsed');
            if (isCollapsed) {
                items.style.maxHeight = items.scrollHeight + 'px';
                items.classList.remove('collapsed');
                sectionEl.classList.remove('collapsed');
                localStorage.removeItem('sb-collapsed-' + sectionEl.textContent.trim());
            } else {
                items.style.maxHeight = items.scrollHeight + 'px';
                requestAnimationFrame(() => {
                    items.style.maxHeight = '0';
                    items.classList.add('collapsed');
                    sectionEl.classList.add('collapsed');
                    localStorage.setItem('sb-collapsed-' + sectionEl.textContent.trim(), '1');
                });
            }
        }
        
        // Restore collapsed state from localStorage
        document.querySelectorAll('.sb-section').forEach(section => {
            const key = 'sb-collapsed-' + section.textContent.trim();
            if (localStorage.getItem(key) === '1') {
                const items = section.nextElementSibling;
                if (items && items.classList.contains('sb-group-items')) {
                    items.style.maxHeight = '0';
                    items.classList.add('collapsed');
                    section.classList.add('collapsed');
                }
            }
        });

        // EGWL: свёрнут по умолчанию; раскрыть, если мы внутри EGWL-раздела
        (function () {
            const egwl = document.querySelector('.sb-section[data-egwl]');
            const egwlItems = document.querySelector('.sb-group-items[data-egwl-items]');
            if (egwl && egwlItems) {
                const path = location.pathname || '';
                const inEgwl = /^\/(electricity|gas|air|water)(\/|$)/.test(path);
                const userPref = localStorage.getItem('sb-collapsed-' + egwl.textContent.trim());
                const collapse = () => {
                    egwlItems.style.maxHeight = '0';
                    egwlItems.classList.add('collapsed');
                    egwl.classList.add('collapsed');
                };
                const expand = () => {
                    egwlItems.style.maxHeight = egwlItems.scrollHeight + 'px';
                    egwlItems.classList.remove('collapsed');
                    egwl.classList.remove('collapsed');
                };
                if (userPref === '1' && !inEgwl) collapse();
                else if (inEgwl || userPref === '0') expand();
                else collapse();
            }

            // Свёрнутые модули: machines / maint / warehouse
            const MODULE_PATHS = {
                machines: /^\/(machines|equipment|tool-wear|assets)(\/|$)/,
                maint: /^\/(maintenance-calendar|maintenance-schedule|maintenance-plans)(\/|$)/,
                warehouse: /^\/(warehouse|consumable-reminders|purchase-requests)(\/|$)/,
            };
            Object.keys(MODULE_PATHS).forEach(function (name) {
                const sec = document.querySelector('.sb-section[data-module="' + name + '"]');
                const items = document.querySelector('.sb-group-items[data-module-items="' + name + '"]');
                if (!sec || !items) return;
                const path = location.pathname || '';
                const inside = MODULE_PATHS[name].test(path);
                const userPref = localStorage.getItem('sb-collapsed-' + sec.textContent.trim());
                const collapse = () => {
                    items.style.maxHeight = '0';
                    items.classList.add('collapsed');
                    sec.classList.add('collapsed');
                };
                const expand = () => {
                    items.style.maxHeight = items.scrollHeight + 'px';
                    items.classList.remove('collapsed');
                    sec.classList.remove('collapsed');
                };
                if (userPref === '1' && !inside) collapse();
                else if (inside || userPref === '0') expand();
                else collapse();
            });
        })();
        
        // Close sidebar on Escape
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') closeSidebar();
        });

        // CSRF helper for fetch POST requests
        function csrfFetch(url, opts = {}) {
            if (opts.method && opts.method.toUpperCase() !== 'GET') {
                opts.headers = opts.headers || {};
                opts.headers['X-CSRFToken'] = document.querySelector('meta[name="csrf-token"]').content;
            }
            return fetch(url, opts);
        }

        // Auto-inject CSRF token into all POST forms
        (function injectCsrf() {
            const token = document.querySelector('meta[name="csrf-token"]').content;
            function addToken(form) {
                if ((form.method === 'POST' || form.method === 'post') && !form.querySelector('input[name="csrf_token"]')) {
                    const inp = document.createElement('input');
                    inp.type = 'hidden'; inp.name = 'csrf_token'; inp.value = token;
                    form.appendChild(inp);
                }
            }
            document.addEventListener('DOMContentLoaded', function() {
                document.querySelectorAll('form').forEach(addToken);
                new MutationObserver(mutations => {
                    mutations.forEach(m => { m.addedNodes.forEach(n => {
                        if (n.tagName === 'FORM') addToken(n);
                        if (n.querySelectorAll) n.querySelectorAll('form').forEach(addToken);
                    });});
                }).observe(document.body, {childList: true, subtree: true});
            });
        })();

        // XSS-safe text escaping for innerHTML
        function esc(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

        // NOTIFICATIONS
        function toggleNotifications() {
            document.getElementById('notifPanel').classList.toggle('open');
            if (document.getElementById('notifPanel').classList.contains('open')) {
                loadNotifications();
            }
        }
        
        // Close notif on outside click
        document.addEventListener('click', function(e) {
            const panel = document.getElementById('notifPanel');
            const notifBtn = document.getElementById('notifBtn');
            if (panel.classList.contains('open') && !panel.contains(e.target) && !notifBtn.contains(e.target)) {
                panel.classList.remove('open');
            }
        });
        
        function loadNotifications() {
            fetch('/notifications').then(r => r.text()).then(html => {
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');
                const items = doc.querySelectorAll('.notif-item');
                const list = document.getElementById('notifList');
                list.innerHTML = '';
                if (items.length === 0) {
                    list.innerHTML = '<div style="padding:20px;text-align:center;color:#888">' + __T('No notifications') + '</div>';
                    return;
                }
                // DOM-сборка вместо innerHTML-шаблонов: заголовки/тексты
                // заявок — пользовательский ввод (XSS + ломалось на кавычках в link)
                items.forEach(item => {
                    const row = document.createElement('div');
                    row.className = 'notif-item' + (item.classList.contains('unread') ? ' unread' : '');
                    const id = item.dataset.id, link = item.dataset.link || '';
                    row.addEventListener('click', function () { markRead(id, link); });
                    const t = document.createElement('div');
                    t.className = 'notif-title';
                    t.textContent = item.querySelector('.notif-title')?.textContent || '';
                    const m = document.createElement('div');
                    m.className = 'notif-msg';
                    m.textContent = item.querySelector('.notif-msg')?.textContent || '';
                    const w = document.createElement('div');
                    w.className = 'notif-time';
                    w.textContent = item.querySelector('.notif-time')?.textContent || '';
                    row.appendChild(t); row.appendChild(m); row.appendChild(w);
                    list.appendChild(row);
                });
            });
        }

        function updateNotifCount() {
            fetch('/notifications/unread').then(r => r.json()).then(d => {
                const btn = document.getElementById('notifBtn');
                const countEl = document.getElementById('notifCount');
                if (d.count > 0) {
                    btn.classList.add('has-notif');
                    countEl.textContent = d.count > 99 ? '99+' : d.count;
                    countEl.style.display = 'block';
                    if (window.lastNotifCount !== undefined && d.count > window.lastNotifCount) {
                        playNotifSound();
                        var n = d.latest;
                        var added = d.count - (window.lastNotifCount || 0);
                        if (n) {
                            var msg = (n.title || 'Notification');
                            if (n.message) msg += ': ' + n.message;
                            if (added > 1) msg += ' (+' + (added - 1) + ')';
                            if (typeof showToast === 'function') {
                                showToast(msg, n.type === 'error' ? 'error' : 'info');
                            } else {
                                alert(msg);
                            }
                            if (n.link) {
                                try {
                                    var box = document.querySelector('.toast-container .toast:last-child');
                                    if (box) {
                                        box.style.cursor = 'pointer';
                                        box.title = __T('Open');
                                        box.onclick = function(){ window.location.href = n.link; };
                                    }
                                } catch (e) {}
                            }
                        } else if (typeof showToast === 'function') {
                            showToast(__T('New notification'), 'info');
                        }
                    }
                } else {
                    btn.classList.remove('has-notif');
                    countEl.style.display = 'none';
                }
                window.lastNotifCount = d.count;
            });
        }

        function markRead(id, link) {
            csrfFetch(`/notifications/${id}/read`, {method: 'POST'}).then(() => {
                if (link) window.location.href = link;
                updateNotifCount();
            });
        }
        
        function markAllRead() {
            csrfFetch('/notifications/read-all', {method: 'POST'}).then(() => {
                updateNotifCount();
                loadNotifications();
            });
        }
        
        function vibrateNotif() {
            try {
                // Android Chrome / Firefox — вибрация без разрешений
                if (navigator.vibrate) { navigator.vibrate([200, 100, 200]); }
                // iOS Safari не поддерживает vibrate() — там вибрирует системный Web Push
            } catch (e) {}
        }
                // Audio unlock for browser autoplay policy
        (function(){
            var unlocked = false;
            function unlockAudio(){
                if (unlocked) return; unlocked = true;
                try {
                    var Ctx = window.AudioContext || window.webkitAudioContext;
                    if (!Ctx) return;
                    var c = new Ctx();
                    if (c.state === 'suspended') c.resume();
                    window.__audioCtx = c;
                } catch(e) {}
            }
            ['click','touchstart','keydown'].forEach(function(ev){
                document.addEventListener(ev, unlockAudio, {once:true, passive:true});
            });
        })();
        
        // Audio unlock for autoplay policy
        (function(){
            var unlocked = false;
            function unlockAudio(){
                if (unlocked) return; unlocked = true;
                try {
                    var Ctx = window.AudioContext || window.webkitAudioContext;
                    if (!Ctx) return;
                    var c = new Ctx();
                    if (c.state === 'suspended') c.resume();
                    window.__audioCtx = c;
                } catch(e) {}
            }
            ['click','touchstart','keydown'].forEach(function(ev){
                document.addEventListener(ev, unlockAudio, {once:true, passive:true});
            });
        })();
        function playNotifSound() {
            try {
                const ctx = window.__audioCtx || new (window.AudioContext || window.webkitAudioContext)();
                if (ctx.state === 'suspended') { ctx.resume(); }
                window.__audioCtx = ctx;
                // Double-chime like incoming message
                [880, 1100].forEach((freq, i) => {
                    const osc = ctx.createOscillator();
                    const gain = ctx.createGain();
                    osc.connect(gain);
                    gain.connect(ctx.destination);
                    osc.frequency.value = freq;
                    osc.type = 'sine';
                    gain.gain.value = 0.2;
                    osc.start(ctx.currentTime + i * 0.15);
                    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.15 + 0.12);
                    osc.stop(ctx.currentTime + i * 0.15 + 0.12);
                });
            } catch(e) {}
            // Vibrate on mobile — like incoming message (short double pulse)
            try {
                if (navigator.vibrate) {
                    navigator.vibrate([200, 100, 200, 100, 400]);
                }
            } catch(e) {}
        }
        
        updateNotifCount();
        setInterval(updateNotifCount, 15000);

        // ── Обновления: блокирующее окно при открытии → «Обновить» →
        //    предупреждение о перезагрузке → рестарт. Без обновления окно
        //    закрыть нельзя; изменения вступают в силу после перезагрузки. ──
        window.APP_VERSION = window.__BOOT.APP_VERSION;
        function checkAppUpdate() {
            if (!window.APP_VERSION) return;
            if (document.getElementById('updateModal')) return;
            fetch('/api/version?since=' + encodeURIComponent(window.APP_VERSION))
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (!d || !d.updates || !d.updates.length) return;
                    openUpdateModal(d);
                })
                .catch(function () {});
        }
        function openUpdateModal(d) {
            if (document.getElementById('updateModal')) return;
            var m = document.createElement('div');
            m.id = 'updateModal';
            m.style.cssText = 'display:flex;position:fixed;inset:0;z-index:10001;background:rgba(0,0,0,.72);align-items:center;justify-content:center;padding:16px';
            // Окно без закрытия: фон, Esc и «позже» не работают — обновление обязательно
            var html = '<div style="background:#fff;border-radius:16px;max-width:460px;width:100%;padding:22px;text-align:left">'
                + '<h3 style="margin:0 0 10px">⬆️ ' + __T('New version') + ' ' + (d.version || '') + '</h3>'
                + '<div style="max-height:52vh;overflow:auto">';
            (d.updates || []).forEach(function (u) {
                html += '<div style="margin:0 0 12px"><b>' + u.version + '</b><ul style="margin:6px 0 0 18px;padding:0">';
                (u.notes || []).forEach(function (n) { html += '<li style="margin:4px 0">' + n + '</li>'; });
                html += '</ul></div>';
            });
            html += '</div>'
                + '<button id="updateDoBtn" style="width:100%;background:#27ae60;color:#fff;border:none;padding:14px;border-radius:10px;font-size:16px;font-weight:700;cursor:pointer;margin-top:8px">' + __T('Update') + '</button>'
                + '</div>';
            m.innerHTML = html;
            document.body.appendChild(m);
            document.body.style.overflow = 'hidden';
            document.getElementById('updateDoBtn').onclick = function () { showRestartWarning(m); };
        }
        function showRestartWarning(m) {
            m.innerHTML = '<div style="background:#fff;border-radius:16px;max-width:420px;width:100%;padding:24px;text-align:center">'
                + '<div style="font-size:38px;margin-bottom:8px">🔄</div>'
                + '<h3 style="margin:0 0 8px">' + __T('The program will be restarted') + '</h3>'
                + '<p style="color:#888;font-size:13px;margin:0 0 18px">' + __T('Updates take effect after restarting the app') + '</p>'
                + '<button id="updateRestartBtn" style="width:100%;background:#2c3e50;color:#fff;border:none;padding:14px;border-radius:10px;font-size:16px;font-weight:700;cursor:pointer">' + __T('Restart now') + '</button>'
                + '</div>';
            document.getElementById('updateRestartBtn').onclick = function () { location.reload(); };
        }
        checkAppUpdate();
        setInterval(checkAppUpdate, 600000);

        // Admin: switch user
        function switchUser(userId) {
            if (!userId) return;
            if (!confirm(__T('View as this user?'))) {
                document.getElementById('switchUserSelect').value = '';
                return;
            }
            location.href = '/switch-user/' + userId;
        }
        
        // Register Service Worker for PWA + Web Push (вибрация/звук на телефоне)
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/static/sw.js?v=6').then(function(reg){ try { reg.update(); } catch(e){} if (navigator.serviceWorker.controller) { navigator.serviceWorker.controller.postMessage({type:'SKIP_WAITING'}); } return reg; })
                    .then(reg => {
                        console.log('SW registered:', reg.scope);
                        initWebPush(reg);
                    })
                    .catch(err => console.log('SW registration failed:', err));
            });
        }

        // ── Web Push: подписка (админ / начальник ТС / механики) ──
        function urlBase64ToUint8Array(base64String) {
            const padding = '='.repeat((4 - base64String.length % 4) % 4);
            const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
            const rawData = window.atob(base64);
            return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
        }

        function initWebPush(reg) {
            // текущий пользователь — для перепривязки подписки при смене аккаунта
            window.APP_USER_ID = window.__BOOT.USER_ID;
            if (!('PushManager' in window) || !('Notification' in window)) return;

            function syncPushSub(sub) {
                return csrfFetch('/api/push/subscribe', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({subscription: sub.toJSON()})
                }).then(() => {
                    try {
                        localStorage.setItem('push_enabled', '1');
                        localStorage.setItem('push_synced_at', String(Date.now()));
                        localStorage.setItem('push_user', String(window.APP_USER_ID || ''));
                        localStorage.setItem('push_endpoint_tail', String(sub.endpoint || '').slice(-24));
                    } catch (e) {}
                    const b = document.getElementById('pushEnableBtn');
                    if (b) {
                        b.style.background = '#27ae60';
                        b.textContent = '✓ ' + __T('Notifications enabled');
                        setTimeout(function(){ if (b.parentNode) b.remove(); }, 4000);
                    }
                });
            }

            function subscribeNow() {
                return fetch('/api/push/vapid-public-key').then(r => r.json()).then((d) => {
                    const key = d.publicKey || d.key;
                    if (!key) return null;
                    const appKey = urlBase64ToUint8Array(key);
                    const keySig = key.slice(-24);
                    return reg.pushManager.getSubscription().then((sub) => {
                        function doSub() {
                            return reg.pushManager.subscribe({
                                userVisibleOnly: true,
                                applicationServerKey: appKey
                            }).then(function (s) {
                                try {
                                    localStorage.setItem('push_key_sig', keySig);
                                    localStorage.setItem('push_synced_at', String(Date.now()));
                                } catch (e) {}
                                return syncPushSub(s);
                            });
                        }
                        if (sub) {
                            // Всегда проверяем живость на сервере. Кэш localStorage
                            // («синхронизировано <1 часа назад») мешал
                            // самовосстановлению: мёртвая подписка не
                            // пересоздавалась до часа, в PWA — вообще никогда.
                            var savedSig = null, syncedUser = null, savedEp = null;
                            try {
                                savedSig = localStorage.getItem('push_key_sig');
                                syncedUser = localStorage.getItem('push_user');
                                savedEp = localStorage.getItem('push_endpoint_tail');
                            } catch (e) {}
                            var epTail = String(sub.endpoint || '').slice(-24);
                            var userId = String(window.APP_USER_ID || '');
                            return fetch('/api/push/status').then(function (r) { return r.json(); }).then(function (st) {
                                if (st && st.subscribed) {
                                    if (savedSig === keySig && syncedUser === userId && savedEp === epTail) {
                                        return Promise.resolve(); // жива и привязана — без лишнего POST
                                    }
                                    return syncPushSub(sub); // сменился пользователь / ключ / сама подписка
                                }
                                // подписка мёртва или привязана к другому — новая подписка
                                return sub.unsubscribe().then(doSub).catch(doSub);
                            }).catch(function () { return syncPushSub(sub); });
                        }
                        return doSub();
                    });
                });
            }

            // Доступно другим страницам: подписать/переподписать push
            window.__pushSubscribe = subscribeNow;

            // Кнопка «Включить уведомления» — жест для браузера (iOS требует нажатие)
            window.__showPushEnable = function() {
                if (document.getElementById('pushEnableBtn')) return;
                if (typeof Notification === 'undefined' || Notification.permission === 'denied') return;
                const b = document.createElement('button');
                b.id = 'pushEnableBtn';
                b.textContent = '🔔 ' + __T('Enable notifications');
                b.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:9000;padding:12px 22px;font-size:14px;font-weight:700;background:#5b9cf6;color:#fff;border:none;border-radius:12px;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.25)';
                function run() {
                    subscribeNow().then(function() {
                        b.style.background = '#27ae60';
                        b.textContent = '✓ ' + __T('Notifications enabled');
                        showToast('✓ ' + __T('Notifications enabled'));
                    }, function() {
                        showToast(__T('No subscription — open the app from the home screen icon'), 'error');
                    });
                }
                b.onclick = function() {
                    // разрешение уже есть — подписываемся СРАЗУ (в контексте нажатия)
                    if (Notification.permission === 'granted') { run(); return; }
                    Notification.requestPermission().then(function(perm) {
                        if (perm === 'granted') {
                            run();
                        } else if (perm === 'denied') {
                            b.style.background = '#c0392b';
                            b.textContent = '✕ ' + __T('Notifications blocked');
                            showToast(__T('Allow notifications in the browser settings'), 'error');
                        }
                    });
                };
                document.body.appendChild(b);
            };

            function pushEverEnabled() {
                try { return localStorage.getItem('push_enabled') === '1'; } catch (e) { return false; }
            }

            if (typeof Notification !== 'undefined' && Notification.permission !== 'denied') {
                if (Notification.permission === 'granted') {
                    // уже разрешено — подписываемся молча, кнопка не нужна
                    subscribeNow().then(function(){
                        try { localStorage.setItem('push_enabled', '1'); } catch (e) {}
                    }).catch(function(){
                        // iOS может требовать нажатие — покажем кнопку, только если ещё ни разу не включали
                        if (!pushEverEnabled()) window.__showPushEnable();
                    });
                } else {
                    // первое включение: одно нажатие — и подписка сохраняется для всей программы
                    if (!pushEverEnabled()) {
                        setTimeout(window.__showPushEnable, 1500);
                    }
                    // В PWA — сразу просим разрешение
                    if (window.navigator.standalone === true ||
                        window.matchMedia('(display-mode: standalone)').matches) {
                        try {
                            if (localStorage.getItem('push_asked') !== '1') {
                                localStorage.setItem('push_asked', '1');
                                setTimeout(function(){ Notification.requestPermission().then(function(p){ if (p==='granted') subscribeNow().catch(function(){}); }); }, 2500);
                            }
                        } catch (e) {}
                    }
                }
            }
        }
    

        
        // PWA Install — Android prompt + iOS instructions
        (function () {
            const isStandalone =
                window.matchMedia('(display-mode: standalone)').matches ||
                window.navigator.standalone === true;
            if (isStandalone) {
                document.documentElement.setAttribute('data-pwa', '1');
                const btn = document.getElementById('installBtn');
                if (btn) btn.style.display = 'none';
                return;
            }

            const ua = navigator.userAgent || '';
            const isIOS = /iPad|iPhone|iPod/.test(ua) ||
                          (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
            const isSafari = /Safari/.test(ua) && !/Chrome|CriOS|FxiOS/.test(ua);

            // ---- Android / Chrome: native install prompt ----
            let deferredPrompt = null;
            window.addEventListener('beforeinstallprompt', (e) => {
                e.preventDefault();
                deferredPrompt = e;
                const installBtn = document.getElementById('installBtn');
                if (installBtn) installBtn.style.display = 'flex';
                showInstallBanner('android');
            });

            window.installPWA = function installPWA() {
                if (deferredPrompt) {
                    deferredPrompt.prompt();
                    deferredPrompt.userChoice.then((choice) => {
                        if (choice.outcome === 'accepted') {
                            hideInstallBanner();
                            const installBtn = document.getElementById('installBtn');
                            if (installBtn) installBtn.style.display = 'none';
                        }
                        deferredPrompt = null;
                    });
                    return;
                }
                if (isIOS) openIosInstallHelp();
            };

            window.addEventListener('appinstalled', () => {
                hideInstallBanner();
                const installBtn = document.getElementById('installBtn');
                if (installBtn) installBtn.style.display = 'none';
            });

            // ---- iOS: banner + modal with Safari steps ----
            function showInstallBanner(mode) {
                if (document.getElementById('pwaInstallBanner')) return;
                try {
                    if (localStorage.getItem('pwa_install_dismissed') === '1') return;
                } catch (e) {}

                const banner = document.createElement('div');
                banner.id = 'pwaInstallBanner';
                banner.style.cssText =
                    'position:fixed;left:12px;right:12px;bottom:calc(12px + env(safe-area-inset-bottom));' +
                    'background:#1a1a2e;color:#fff;border-radius:14px;padding:12px 14px;z-index:9999;' +
                    'box-shadow:0 8px 28px rgba(0,0,0,.35);display:flex;align-items:center;gap:10px;' +
                    'font-size:13px;line-height:1.35;font-family:-apple-system,BlinkMacSystemFont,sans-serif';
                banner.innerHTML =
                    '<div style="font-size:22px">📲</div>' +
                    '<div style="flex:1"><strong>' + __T('Install ProMaster') + '</strong><br>' +
                    '<span style="opacity:.85">' +
                    (mode === 'android'
                        ? 'Добавьте программу на рабочий стол — как обычное приложение.'
                        : 'Safari → «Поделиться» → «На экран “Домой”»') +
                    '</span></div>' +
                    '<button id="pwaInstallGo" style="background:#5b9cf6;color:#fff;border:none;' +
                    'border-radius:10px;padding:9px 12px;font-weight:700;font-size:12px;cursor:pointer">Установить</button>' +
                    '<button id="pwaInstallX" style="background:transparent;color:#aaa;border:none;' +
                    'font-size:18px;cursor:pointer;padding:4px 6px">×</button>';
                document.body.appendChild(banner);
                document.getElementById('pwaInstallGo').onclick = () => {
                    if (deferredPrompt) installPWA();
                    else openIosInstallHelp();
                };
                document.getElementById('pwaInstallX').onclick = () => {
                    try { localStorage.setItem('pwa_install_dismissed', '1'); } catch (e) {}
                    hideInstallBanner();
                };
            }

            function hideInstallBanner() {
                const b = document.getElementById('pwaInstallBanner');
                if (b) b.remove();
            }

            window.openIosInstallHelp = function openIosInstallHelp() {
                let modal = document.getElementById('pwaIosHelp');
                if (modal) { modal.style.display = 'flex'; return; }
                modal = document.createElement('div');
                modal.id = 'pwaIosHelp';
                modal.style.cssText =
                    'display:flex;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:10000;' +
                    'align-items:center;justify-content:center;padding:16px';
                modal.innerHTML =
                    '<div style="background:#fff;border-radius:18px;max-width:360px;width:100%;padding:22px;' +
                    'font-family:-apple-system,BlinkMacSystemFont,sans-serif;position:relative">' +
                    '<button id="pwaIosClose" style="position:absolute;right:12px;top:10px;border:none;' +
                    'background:none;font-size:22px;color:#999;cursor:pointer">×</button>' +
                    '<div style="font-size:40px;text-align:center">📲</div>' +
                    '<h2 style="margin:8px 0 4px;font-size:18px;text-align:center">' + __T('Install on iPhone') + '</h2>' +
                    '<p style="color:#666;font-size:13px;text-align:center;margin-bottom:14px">' +
                    __T('The app will open from the icon like a normal app') + '</p>' +
                    '<ol style="margin:0 0 12px 18px;font-size:14px;line-height:1.7;color:#333">' +
                    '<li>' + __T('Open the site in') + ' <strong>Safari</strong> (не Chrome)</li>' +
                    '<li>Нажмите <strong>«Поделиться»</strong> <span style="font-size:16px">⬆️</span> внизу</li>' +
                    '<li>' + __T('Choose') + ' <strong>«На экран “Домой”»</strong></li>' +
                    '<li>Нажмите <strong>«Добавить»</strong></li>' +
                    '</ol>' +
                    '<p style="font-size:12px;color:#888;background:#f5f7fa;border-radius:10px;padding:10px;' +
                    'margin:0 0 14px">' + __T('After install open CRM from the icon') + ' <strong>ProMaster</strong> — ' +
                    'полный экран, без адресной строки.</p>' +
                    '<button id="pwaIosOk" style="width:100%;background:#1a1a2e;color:#fff;border:none;' +
                    'border-radius:12px;padding:12px;font-size:15px;font-weight:700;cursor:pointer">' + __T('OK') + '</button>' +
                    '</div>';
                document.body.appendChild(modal);
                const close = () => { modal.style.display = 'none'; };
                document.getElementById('pwaIosClose').onclick = close;
                document.getElementById('pwaIosOk').onclick = close;
                modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
            };

            // iOS Safari (not installed) — show banner after short delay
            if (isIOS && isSafari) {
                setTimeout(() => showInstallBanner('ios'), 1800);
                // topbar 📲 opens help
                const btn = document.getElementById('installBtn');
                if (btn) {
                    btn.style.display = 'flex';
                    btn.title = __T('Install on iPhone');
                }
            }
        })();
        
        // Maintenance reminders
        function checkMaintenanceReminders() {
            fetch('/api/maintenance/reminders').then(r => r.json()).then(items => {
                if (items.length === 0) return;
                const overdue = items.filter(i => i.overdue);
                const soon = items.filter(i => !i.overdue);
                if (overdue.length > 0 || soon.length > 0) {
                    const btn = document.getElementById('notifBtn');
                    btn.classList.add('has-notif');
                    // Add maintenance badge to nav
                    const navLink = document.querySelector('a[href="/maintenance-calendar"]');
                    if (navLink && !navLink.querySelector('.maint-badge')) {
                        const badge = document.createElement('span');
                        badge.className = 'maint-badge';
                        badge.style.cssText = 'background:#e74c3c;color:white;font-size:10px;padding:1px 5px;border-radius:10px;margin-left:4px;font-weight:bold';
                        badge.textContent = items.length;
                        navLink.appendChild(badge);
                    }
                }
            }).catch(() => {});
        }
        checkMaintenanceReminders();
        setInterval(checkMaintenanceReminders, 120000);
        
        // Global Search
        const searchInput = document.getElementById('globalSearch');
        const searchDropdown = document.getElementById('searchDropdown');
        let searchTimeout;
        
        if (searchInput) {
            searchInput.addEventListener('input', function() {
                clearTimeout(searchTimeout);
                const q = this.value.trim();
                if (q.length < 2) {
                    searchDropdown.style.display = 'none';
                    return;
                }
                searchTimeout = setTimeout(() => {
                    fetch(`/api/search?q=${encodeURIComponent(q)}`)
                        .then(r => r.json())
                        .then(data => {
                            if (data.results.length === 0) {
                                searchDropdown.innerHTML = '<div style="padding:20px;text-align:center;color:#999;font-size:13px">' + __T('No results') + '</div>';
                            } else {
                                let html = '';
                                data.results.slice(0, 8).forEach(r => {
                                    html += `<a href="${esc(r.url)}" style="display:flex;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid #f0f2f5;text-decoration:none;color:#333;transition:background 0.15s" onmouseover="this.style.background='#f8f9fc'" onmouseout="this.style.background='transparent'">
                                        <span style="font-size:18px;width:24px;text-align:center">${esc(r.icon)}</span>
                                        <div style="flex:1;min-width:0">
                                            <div style="font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.title)}</div>
                                            <div style="font-size:11px;color:#888;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.subtitle)}</div>
                                        </div>
                                    </a>`;
                                });
                                html += `<a href="/search?q=${encodeURIComponent(q)}" style="display:block;padding:12px;text-align:center;color:#5b9cf6;font-size:13px;font-weight:600;text-decoration:none;border-top:1px solid #eee">' + __T('View all results') + ' →</a>`;
                                searchDropdown.innerHTML = html;
                            }
                            searchDropdown.style.display = 'block';
                        });
                }, 300);
            });
            
            document.addEventListener('click', function(e) {
                if (!searchInput.contains(e.target) && !searchDropdown.contains(e.target)) {
                    searchDropdown.style.display = 'none';
                }
            });
            
            searchInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && this.value.trim().length >= 2) {
                    window.location.href = `/search?q=${encodeURIComponent(this.value.trim())}`;
                }
            });
        }
        
        // Dark Mode
        function toggleDarkMode() {
            document.body.classList.toggle('dark-mode');
            const isDark = document.body.classList.contains('dark-mode');
            localStorage.setItem('darkMode', isDark);
            document.querySelector('.dark-toggle').textContent = isDark ? '☀️' : '🌙';
        }
        
        // Load dark mode preference
        if (localStorage.getItem('darkMode') === 'true') {
            document.body.classList.add('dark-mode');
            document.querySelector('.dark-toggle').textContent = '☀️';
        }
        
        
        // Audio unlock: browsers block sound until user gesture
        (function(){
            var unlocked = false;
            function unlockAudio(){
                if (unlocked) return;
                unlocked = true;
                try {
                    var Ctx = window.AudioContext || window.webkitAudioContext;
                    if (Ctx) {
                        var c = new Ctx();
                        if (c.state === 'suspended') { c.resume(); }
                        // silent blip to prime
                        var o = c.createOscillator();
                        var g = c.createGain();
                        g.gain.value = 0;
                        o.connect(g); g.connect(c.destination);
                        o.start(0); o.stop(0.01);
                        window.__audioCtx = c;
                    }
                } catch (e) {}
            }
            ['click', 'touchstart', 'keydown'].forEach(function(ev){
                document.addEventListener(ev, unlockAudio, {once: true, passive: true});
            });
        })();

        // Toast Notifications
        function showToast(message, type = 'info') {
            const container = document.querySelector('.toast-container') || createToastContainer();
            const toast = document.createElement('div');
            toast.className = `toast toast-${type}`;
            toast.textContent = message;
            container.appendChild(toast);
            
            setTimeout(() => toast.classList.add('show'), 10);
            setTimeout(() => {
                toast.classList.remove('show');
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        }
        
        function createToastContainer() {
            const container = document.createElement('div');
            container.className = 'toast-container';
            document.body.appendChild(container);
            return container;
        }
        
        // Convert flash messages to toasts
        document.querySelectorAll('.alert').forEach(alert => {
            const type = alert.classList.contains('alert-success') ? 'success' : 
                        alert.classList.contains('alert-error') ? 'error' : 'info';
            showToast(alert.textContent, type);
            alert.style.display = 'none';
        });
    