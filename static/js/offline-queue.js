/* ProMaster CRM — Offline outbox
 * Saves POST form / fetch mutations locally (IndexedDB) when there is no
 * internet, assigns a temporary offline number, and replays them to the
 * server when connectivity returns. The server assigns the real document
 * number on replay (renumbering).
 *
 * Loaded from base.html on every page.
 */
(function () {
  'use strict';

  const DB_NAME = 'promaster-offline';
  const DB_VERSION = 1;
  const STORE = 'outbox';
  const TEMP_PREFIX = 'OFFLINE';
  const RETRY_INTERVAL_MS = 15000;

  // ---------- IndexedDB helpers ----------
  function openDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const os = db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
          os.createIndex('client_id', 'client_id', { unique: true });
          os.createIndex('status', 'status');
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function idbTx(mode, fn) {
    return openDb().then((db) => new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const store = tx.objectStore(STORE);
      const out = fn(store);
      tx.oncomplete = () => resolve(out && out.result !== undefined ? out.result : out);
      tx.onerror = () => reject(tx.error);
    }));
  }

  function putRecord(rec) {
    return idbTx('readwrite', (s) => s.put(rec));
  }

  function deleteRecord(id) {
    return idbTx('readwrite', (s) => s.delete(id));
  }

  function getAllPending() {
    return openDb().then((db) => new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const idx = tx.objectStore(STORE).index('status');
      const req = idx.getAll('pending');
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    }));
  }

  function countPending() {
    return getAllPending().then((rows) => rows.length).catch(() => 0);
  }

  // ---------- Temp numbering ----------
  function nextTempNumber() {
    const key = 'offlineTempSeq';
    const today = new Date();
    const y = today.getFullYear();
    const m = String(today.getMonth() + 1).padStart(2, '0');
    const d = String(today.getDate()).padStart(2, '0');
    const datePart = `${y}${m}${d}`;
    let state = {};
    try { state = JSON.parse(localStorage.getItem(key) || '{}'); } catch (e) { state = {}; }
    if (state.date !== datePart) state = { date: datePart, seq: 0 };
    state.seq = (state.seq || 0) + 1;
    localStorage.setItem(key, JSON.stringify(state));
    return `${TEMP_PREFIX}-${datePart}-${String(state.seq).padStart(4, '0')}`;
  }

  function uuid4() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : ((r & 0x3) | 0x8);
      return v.toString(16);
    });
  }

  // ---------- Toast / badge UI ----------
  function toast(message, type) {
    type = type || 'info';
    let wrap = document.getElementById('offlineToastWrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = 'offlineToastWrap';
      wrap.className = 'offline-toast-wrap';
      document.body.appendChild(wrap);
    }
    const el = document.createElement('div');
    el.className = 'offline-toast offline-toast-' + type;
    el.innerHTML = message;
    wrap.appendChild(el);
    setTimeout(() => {
      el.classList.add('offline-toast-hide');
      setTimeout(() => el.remove(), 400);
    }, 4500);
  }

  function updateBadge() {
    countPending().then((n) => {
      const btn = document.getElementById('offlineBtn');
      const cnt = document.getElementById('offlineCount');
      if (!btn || !cnt) return;
      if (n > 0) {
        btn.style.display = 'flex';
        btn.classList.add('offline-pending');
        cnt.style.display = 'block';
        cnt.textContent = String(n);
        btn.title = 'Оффлайн: ' + n + ' шт. в очереди';
      } else {
        btn.classList.remove('offline-pending');
        cnt.style.display = 'none';
        btn.title = navigator.onLine ? 'Онлайн' : 'Оффлайн';
      }
      btn.classList.toggle('offline-down', !navigator.onLine);
    });
  }

  function renderQueuePanel() {
    const list = document.getElementById('offlineQueueList');
    if (!list) return;
    getAllPending().then((rows) => {
      if (!rows.length) {
        list.innerHTML = '<div class="offline-empty">Очередь пуста — все данные отправлены</div>';
        return;
      }
      list.innerHTML = rows.map((r) => {
        const dt = r.created_at ? new Date(r.created_at).toLocaleString('ru') : '';
        const files = (r.files && r.files.length) ? ` · 📎${r.files.length}` : '';
        return `<div class="offline-q-item" data-id="${r.id}">
          <div class="offline-q-title">${esc(r.title || r.path)}${files}</div>
          <div class="offline-q-meta">
            <span class="offline-q-num">${esc(r.temp_number || '')}</span>
            <span>${esc(dt)}</span>
          </div>
          <div class="offline-q-path">${esc(r.path)}</div>
        </div>`;
      }).join('');
    });
  }

  function esc(t) {
    const d = document.createElement('div');
    d.textContent = t == null ? '' : String(t);
    return d.innerHTML;
  }

  window.toggleOfflinePanel = function () {
    const p = document.getElementById('offlinePanel');
    if (!p) return;
    p.classList.toggle('open');
    if (p.classList.contains('open')) renderQueuePanel();
  };

  window.syncOfflineNow = function () {
    if (!navigator.onLine) {
      toast('Нет интернета — синхронизация недоступна', 'warn');
      return;
    }
    toast('Синхронизация…', 'info');
    processQueue().then((res) => {
      if (res.synced > 0) {
        toast(`Синхронизировано: ${res.synced}`, 'success');
      }
      if (res.failed > 0) {
        toast(`Ошибок: ${res.failed}`, 'error');
      }
      if (res.synced === 0 && res.failed === 0) {
        toast('Очередь пуста', 'info');
      }
      renderQueuePanel();
      updateBadge();
    });
  };

  // ---------- Serialize form / files ----------
  function serializeForm(form) {
    const fd = new FormData(form);
    const fields = {};
    const files = [];
    fd.forEach((value, key) => {
      if (value instanceof File) {
        if (!value.name && value.size === 0) return;
        files.push({ field: key, filename: value.name, type: value.type || 'application/octet-stream', data: value });
      } else {
        if (Object.prototype.hasOwnProperty.call(fields, key)) {
          if (!Array.isArray(fields[key])) fields[key] = [fields[key]];
          fields[key].push(String(value));
        } else {
          fields[key] = String(value);
        }
      }
    });
    return { fields, files };
  }

  function rebuildFormData(entry) {
    const fd = new FormData();
    const fields = entry.fields || {};
    Object.keys(fields).forEach((k) => {
      const v = fields[k];
      if (Array.isArray(v)) v.forEach((x) => fd.append(k, x));
      else fd.append(k, v);
    });
    (entry.files || []).forEach((f) => {
      try {
        const blob = f.data instanceof Blob ? f.data : new Blob([f.data], { type: f.type });
        fd.append(f.field, blob, f.filename || 'file');
      } catch (e) { /* skip unreadable file */ }
    });
    return fd;
  }

  // ---------- Queue an offline mutation ----------
  async function enqueue(entry) {
    entry.client_id = entry.client_id || uuid4();
    entry.temp_number = entry.temp_number || nextTempNumber();
    entry.status = 'pending';
    entry.created_at = entry.created_at || new Date().toISOString();
    entry.attempts = 0;
    await putRecord(entry);
    updateBadge();
    toast(
      `<b>Сохранено локально</b><br><span class="offline-toast-num">${esc(entry.temp_number)}</span><br>` +
      `<small>Номер будет присвоен сервером при синхронизации</small>`,
      'success'
    );
    try { navigator.vibrate && navigator.vibrate([80, 40, 80]); } catch (e) {}
    return entry;
  }

  // ---------- Form submit interception ----------
  function ensureOfflineId(form) {
    if (!form.querySelector('input[name="_offline_client_id"]')) {
      const inp = document.createElement('input');
      inp.type = 'hidden';
      inp.name = '_offline_client_id';
      inp.value = uuid4();
      form.appendChild(inp);
    }
    return form.querySelector('input[name="_offline_client_id"]').value;
  }

  document.addEventListener('submit', function (e) {
    const form = e.target;
    if (!form || form.tagName !== 'FORM') return;
    const method = (form.method || 'GET').toLowerCase();
    if (method !== 'post') return;
    if (form.dataset.offlineSkip === '1') return;
    if (form.target === '_blank') return;

    // Always stamp a client id for idempotency (also used when online).
    ensureOfflineId(form);

    // Offline: save locally instead of submitting.
    if (!navigator.onLine) {
      e.preventDefault();
      e.stopPropagation();
      const action = form.getAttribute('action') || window.location.pathname;
      const title = form.dataset.offlineTitle
        || (form.querySelector('input[name="title"]') || {}).value
        || (form.querySelector('[name="description"]') || {}).value
        || document.title;
      const { fields, files } = serializeForm(form);
      enqueue({
        kind: 'form',
        method: 'POST',
        path: action,
        fields,
        files,
        title: String(title || '').slice(0, 180),
        temp_number: nextTempNumber(),
        client_id: fields._offline_client_id || uuid4(),
      });
    }
  }, true);

  // form.submit() does not fire the submit event — intercept it too.
  const origFormSubmit = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function () {
    const form = this;
    const method = (form.method || 'GET').toLowerCase();
    if (!navigator.onLine && method === 'post' && form.dataset.offlineSkip !== '1' && form.target !== '_blank') {
      ensureOfflineId(form);
      const action = form.getAttribute('action') || window.location.pathname;
      const title = form.dataset.offlineTitle
        || (form.querySelector('input[name="title"]') || {}).value
        || (form.querySelector('[name="description"]') || {}).value
        || document.title;
      const { fields, files } = serializeForm(form);
      enqueue({
        kind: 'form',
        method: 'POST',
        path: action,
        fields,
        files,
        title: String(title || '').slice(0, 180),
        temp_number: nextTempNumber(),
        client_id: fields._offline_client_id || uuid4(),
      });
      return;
    }
    return origFormSubmit.call(form);
  };

  // ---------- fetch wrapper (csrfFetch + raw fetch mutations) ----------
  const origFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    init = init || {};
    const method = (init.method || (input && input.method) || 'GET').toUpperCase();
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const isMutation = method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE';
    const isApi = /\/api\//.test(url) || /\/gas\/api\//.test(url) || /\/chat\/api\//.test(url);

    // Add offline idempotency header to mutations when possible
    if (isMutation && init.headers) {
      try {
        const h = new Headers(init.headers);
        if (!h.has('X-Offline-Client-Id') && url.indexOf('/api/offline/') === -1) {
          h.set('X-Offline-Client-Id', uuid4());
          init.headers = h;
        }
      } catch (e) { /* non-Headers headers */ }
    }

    if (!navigator.onLine && isMutation) {
      // Queue instead of failing
      const body = init.body;
      let entry = {
        kind: 'fetch',
        method,
        path: url,
        title: method + ' ' + url.replace(/^\//, '').slice(0, 80),
      };
      if (typeof body === 'string') {
        try {
          const parsed = JSON.parse(body);
          entry.kind = 'json';
          entry.json = parsed;
        } catch (e) {
          entry.raw = body;
          entry.content_type = 'application/x-www-form-urlencoded';
        }
      } else if (body instanceof FormData) {
        const fields = {};
        const files = [];
        body.forEach((value, key) => {
          if (value instanceof File) {
            if (!value.name && value.size === 0) return;
            files.push({ field: key, filename: value.name, type: value.type || 'application/octet-stream', data: value });
          } else {
            fields[key] = String(value);
          }
        });
        entry.fields = fields;
        entry.files = files;
      }
      await enqueue(entry);
      return new Response(JSON.stringify({ ok: true, offline: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    try {
      return await origFetch(input, init);
    } catch (err) {
      if (isMutation) {
        // Network drop while "online" — queue and pretend success to callers
        // that only care about persistence. Record raw body when possible.
        const body = init.body;
        const entry = {
          kind: typeof body === 'string' ? 'raw' : 'fetch',
          method,
          path: url,
          title: method + ' ' + url.replace(/^\//, '').slice(0, 80),
        };
        if (typeof body === 'string') {
          try {
            entry.kind = 'json';
            entry.json = JSON.parse(body);
          } catch (e) {
            entry.raw = body;
            entry.content_type = 'application/x-www-form-urlencoded';
          }
        }
        await enqueue(entry);
        return new Response(JSON.stringify({ ok: true, offline: true, queued: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      throw err;
    }
  };

  // ---------- Replay queue to server ----------
  async function refreshCsrf() {
    try {
      const r = await origFetch('/api/offline/csrf', { credentials: 'same-origin' });
      if (!r.ok) return null;
      const data = await r.json();
      const meta = document.querySelector('meta[name="csrf-token"]');
      if (meta && data.csrf_token) meta.content = data.csrf_token;
      return data.csrf_token;
    } catch (e) {
      return null;
    }
  }

  async function replayOne(entry) {
    const headers = {
      'X-Offline-Queue': '1',
      'X-Offline-Client-Id': entry.client_id || '',
      'X-Offline-Temp-Number': entry.temp_number || '',
      'X-Offline-Title': (entry.title || '').slice(0, 180),
      'X-Requested-With': 'XMLHttpRequest',
      'Accept': 'application/json',
    };

    // CSRF
    const meta = document.querySelector('meta[name="csrf-token"]');
    let csrf = meta ? meta.content : null;
    if (entry.fields && entry.fields.csrf_token) csrf = entry.fields.csrf_token;

    let body;
    if (entry.kind === 'json') {
      headers['Content-Type'] = 'application/json';
      headers['X-CSRFToken'] = csrf || '';
      body = JSON.stringify(entry.json || {});
    } else if (entry.kind === 'raw') {
      headers['Content-Type'] = entry.content_type || 'application/x-www-form-urlencoded';
      headers['X-CSRFToken'] = csrf || '';
      body = entry.raw || '';
    } else if (entry.fields || entry.files) {
      body = rebuildFormData(entry);
      if (csrf && entry.fields && !entry.fields.csrf_token) body.append('csrf_token', csrf);
      headers['X-CSRFToken'] = csrf || '';
    } else {
      headers['X-CSRFToken'] = csrf || '';
      body = null;
    }

    let res;
    try {
      res = await origFetch(entry.path, {
        method: entry.method || 'POST',
        headers,
        body,
        credentials: 'same-origin',
        redirect: 'follow',
      });
    } catch (e) {
      return { ok: false, network: true, error: String(e) };
    }

    // CSRF expired → refresh token and retry once
    if (res.status === 400 || res.status === 419) {
      const text = await res.text();
      if (/csrf/i.test(text)) {
        const fresh = await refreshCsrf();
        if (fresh) {
          if (entry.fields) entry.fields.csrf_token = fresh;
          return replayOne(entry);
        }
      }
      return { ok: false, error: 'csrf', detail: text.slice(0, 200) };
    }

    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      // Non-JSON (e.g. unexpected HTML) — treat 2xx as success
      if (res.ok) return { ok: true, number: entry.temp_number };
      return { ok: false, error: 'bad_response', status: res.status };
    }

    if (data && data.duplicate) {
      return { ok: true, duplicate: true, number: data.number, redirect: data.redirect, flashes: data.flashes };
    }
    if (data && data.ok) {
      return { ok: true, number: data.number, redirect: data.redirect, flashes: data.flashes };
    }
    return { ok: false, error: (data && (data.error || data.message)) || 'server_error', detail: data };
  }

  let processing = false;

  async function processQueue() {
    const result = { synced: 0, failed: 0, remaining: 0 };
    if (processing) return result;
    if (!navigator.onLine) return result;
    processing = true;
    try {
      let rows = await getAllPending();
      // Oldest first
      rows.sort((a, b) => (a.id || 0) - (b.id || 0));
      for (const entry of rows) {
        const res = await replayOne(entry);
        if (res.ok) {
          await deleteRecord(entry.id);
          result.synced += 1;
          const num = res.number || entry.temp_number;
          const msg = (res.flashes && res.flashes[0] && res.flashes[0].message) || '';
          const real = res.number || (msg.match(/\b((?:TWO|EQ|EPO|WO|INV|ORD)-[A-Z0-9]+-\d+)\b/) || [])[1];
          toast(
            `<b>Синхронизировано</b><br><span class="offline-toast-num">${esc(real || num)}</span>` +
            (real && real !== entry.temp_number ? `<br><small>было ${esc(entry.temp_number)} → стало ${esc(real)}</small>` : ''),
            'success'
          );
        } else if (res.network) {
          // Still offline / flaky — stop and retry later
          break;
        } else {
          entry.attempts = (entry.attempts || 0) + 1;
          entry.last_error = String(res.error || 'error') + (res.detail ? ': ' + JSON.stringify(res.detail).slice(0, 200) : '');
          // Keep failed items up to 5 attempts, then mark and leave for manual retry
          if (entry.attempts >= 5) {
            entry.status = 'error';
          }
          await putRecord(entry);
          result.failed += 1;
        }
      }
      result.remaining = await countPending();
    } finally {
      processing = false;
    }
    return result;
  }

  // ---------- Connectivity listeners ----------
  function onOnline() {
    updateBadge();
    toast('Интернет появился — синхронизация…', 'info');
    setTimeout(() => processQueue().then((r) => {
      if (r.synced > 0) toast(`Синхронизировано: ${r.synced}`, 'success');
      if (r.failed > 0) toast(`Ошибок синхронизации: ${r.failed}`, 'error');
      updateBadge();
      renderQueuePanel();
    }), 600);
  }

  function onOffline() {
    updateBadge();
    toast('Нет интернета — данные будут сохраняться локально', 'warn');
  }

  window.addEventListener('online', onOnline);
  window.addEventListener('offline', onOffline);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && navigator.onLine) {
      processQueue().then(updateBadge);
    }
  });

  setInterval(() => {
    if (navigator.onLine) processQueue().then(updateBadge);
  }, RETRY_INTERVAL_MS);

  // ---------- Init ----------
  document.addEventListener('DOMContentLoaded', () => {
    updateBadge();
    // Close panel on outside click
    document.addEventListener('click', (e) => {
      const panel = document.getElementById('offlinePanel');
      const btn = document.getElementById('offlineBtn');
      if (!panel) return;
      if (panel.classList.contains('open') && !panel.contains(e.target) && !(btn && btn.contains(e.target))) {
        panel.classList.remove('open');
      }
    });
    if (!navigator.onLine) {
      toast('Работа в оффлайн-режиме', 'warn');
    } else {
      // Silent catch-up after reload
      processQueue().then(updateBadge);
    }
  });

  // Expose for debugging
  window.OfflineOutbox = {
    pending: getAllPending,
    sync: processQueue,
    tempNumber: nextTempNumber,
  };
})();
