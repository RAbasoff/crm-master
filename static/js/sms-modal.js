/* ProMaster SMS modal — always available */
(function () {
  function el(id) { return document.getElementById(id); }

  window.openSmsModal = function () {
    try {
      var m = el('smsSendModal');
      if (m) m.style.display = 'block';
      var box = el('smsUserList');
      if (!box) return;
      box.innerHTML = '<div style="padding:8px;color:#888">...</div>';
      fetch('/api/settings/sms-users', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d || !d.users) { box.innerHTML = 'error'; return; }
          var html = '';
          d.users.forEach(function (u) {
            var ph = u.phone || '';
            html += '<label style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:6px;cursor:pointer;' + (ph ? '' : 'opacity:.45') + '">'
              + '<input type="checkbox" class="sms-user-cb" value="' + u.id + '" ' + (ph ? '' : 'disabled') + '>'
              + '<span style="flex:1"><b>' + u.name + '</b> <small style="color:#888">' + (u.role || '') + '</small></span>'
              + '<span style="font-family:monospace;font-size:12px">' + (ph || '—') + '</span></label>';
          });
          box.innerHTML = html || '<div style="padding:8px;color:#888">Geen gebruikers</div>';
        })
        .catch(function () { box.innerHTML = 'error'; });
    } catch (e) {
      alert('SMS error: ' + ((e && e.message) || e));
    }
  };

  window.closeSmsModal = function () {
    var m = el('smsSendModal');
    if (m) m.style.display = 'none';
  };

  window.smsToggleAll = function (on) {
    document.querySelectorAll('.sms-user-cb').forEach(function (cb) {
      if (!cb.disabled) cb.checked = on;
    });
  };

  window.smsSendSelected = function (btn) {
    var ids = [];
    document.querySelectorAll('.sms-user-cb:checked').forEach(function (cb) { ids.push(cb.value); });
    var ta = el('smsText');
    var text = (ta && ta.value) || '';
    if (!ids.length || !text.trim()) {
      alert('Selecteer gebruikers en voer een bericht in');
      return;
    }
    btn.disabled = true;
    var meta = document.querySelector('meta[name="csrf-token"]');
    fetch('/api/settings/sms-send', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': (meta && meta.content) || ''
      },
      body: JSON.stringify({ user_ids: ids, text: text })
    }).then(function (r) { return r.json(); }).then(function (d) {
      btn.disabled = false;
      var box = el('smsSendResult');
      if (box) {
        var sent = (d && d.sent) || [];
        var err = (d && d.errors) || [];
        box.innerHTML = '<span style="color:#27ae60">OK ' + sent.length + '</span>'
          + (err.length ? ' <span style="color:#c0392b">' + err.join('; ') + '</span>' : '');
      }
      if (typeof loadSms === 'function') loadSms();
    }).catch(function () {
      btn.disabled = false;
      alert('error');
    });
  };

  function bindOpens() {
    document.querySelectorAll('[data-sms-open]').forEach(function (el) {
      el.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        window.openSmsModal();
      });
    });
    // legacy inline onclick="openSmsModal()"
    document.querySelectorAll('[onclick*="openSmsModal"]').forEach(function (el) {
      el.addEventListener('click', function () { window.openSmsModal(); });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindOpens);
  } else {
    bindOpens();
  }
})();
