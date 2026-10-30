/* Photo Viewer — лайтбокс с пролистыванием для всех галерей.
   Клик по фото → полноэкранный просмотр: стрелки, свайп, клавиши,
   плёнка миниатюр, счётчик «3 / 7». Заменяет window.open(this.src).
   Использование: onclick="openPhotoViewer(this)" (+ контейнеру data-gallery). */
(function () {
  'use strict';

  var state = { imgs: [], idx: 0, root: null };

  function ensureDom() {
    if (document.getElementById('pvOverlay')) return;
    var o = document.createElement('div');
    o.id = 'pvOverlay';
    o.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(10,12,18,0.94);z-index:10050;' +
      'flex-direction:column;align-items:center;justify-content:center;touch-action:none;-webkit-user-select:none;user-select:none';
    o.innerHTML =
      '<div id="pvTopBar" style="position:absolute;top:0;left:0;right:0;display:flex;align-items:center;justify-content:space-between;padding:10px 14px;">' +
        '<div id="pvCounter" style="color:#fff;font-size:14px;font-weight:600;background:rgba(0,0,0,0.35);padding:6px 12px;border-radius:20px;"></div>' +
        '<button id="pvClose" aria-label="close" style="width:42px;height:42px;border-radius:50%;border:none;background:rgba(255,255,255,0.15);color:#fff;font-size:22px;cursor:pointer;line-height:1">&times;</button>' +
      '</div>' +
      '<button id="pvPrev" aria-label="prev" style="position:absolute;left:8px;top:50%;transform:translateY(-50%);width:52px;height:52px;border-radius:50%;border:none;background:rgba(255,255,255,0.18);color:#fff;font-size:26px;cursor:pointer;z-index:2">&#8249;</button>' +
      '<button id="pvNext" aria-label="next" style="position:absolute;right:8px;top:50%;transform:translateY(-50%);width:52px;height:52px;border-radius:50%;border:none;background:rgba(255,255,255,0.18);color:#fff;font-size:26px;cursor:pointer;z-index:2">&#8250;</button>' +
      '<img id="pvImg" alt="" style="max-width:92vw;max-height:72vh;object-fit:contain;border-radius:8px;box-shadow:0 8px 40px rgba(0,0,0,0.5);background:#111">' +
      '<div id="pvDesc" style="color:#ccc;font-size:13px;margin-top:10px;max-width:92vw;text-align:center;min-height:18px;"></div>' +
      '<div id="pvStrip" style="position:absolute;bottom:0;left:0;right:0;display:flex;gap:6px;overflow-x:auto;padding:10px 12px;background:linear-gradient(transparent,rgba(0,0,0,0.55));justify-content:flex-start;"></div>';
    document.body.appendChild(o);

    function on(el, ev, fn) { document.getElementById(el).addEventListener(ev, fn); }
    on('pvClose', 'click', closePhotoViewer);
    on('pvPrev', 'click', function (e) { e.stopPropagation(); step(-1); });
    on('pvNext', 'click', function (e) { e.stopPropagation(); step(1); });
    o.addEventListener('click', function (e) {
      if (e.target === o || e.target.id === 'pvImg' || e.target.id === 'pvDesc') closePhotoViewer();
    });

    // Свайпы: влево/вправо — листание, вниз — закрыть
    var sx = 0, sy = 0, tracking = false;
    o.addEventListener('touchstart', function (e) {
      if (e.touches.length !== 1) return;
      sx = e.touches[0].clientX; sy = e.touches[0].clientY; tracking = true;
    }, { passive: true });
    o.addEventListener('touchend', function (e) {
      if (!tracking) return;
      tracking = false;
      var t = e.changedTouches[0];
      var dx = t.clientX - sx, dy = t.clientY - sy;
      if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy)) {
        step(dx < 0 ? 1 : -1);
      } else if (dy > 70 && Math.abs(dy) > Math.abs(dx)) {
        closePhotoViewer();
      }
    }, { passive: true });

    document.addEventListener('keydown', function (e) {
      var ov = document.getElementById('pvOverlay');
      if (!ov || ov.style.display === 'none') return;
      if (e.key === 'Escape') { closePhotoViewer(); }
      else if (e.key === 'ArrowRight') { step(1); }
      else if (e.key === 'ArrowLeft') { step(-1); }
    });
  }

  function render() {
    var img = document.getElementById('pvImg');
    var cur = state.imgs[state.idx];
    if (!cur) return;
    img.src = cur.getAttribute('src');
    img.alt = cur.getAttribute('alt') || '';
    document.getElementById('pvCounter').textContent = (state.idx + 1) + ' / ' + state.imgs.length;
    document.getElementById('pvDesc').textContent = cur.getAttribute('alt') || cur.getAttribute('title') || '';
    var strip = document.getElementById('pvStrip');
    strip.innerHTML = '';
    if (state.imgs.length > 1) {
      state.imgs.forEach(function (im, i) {
        var t = document.createElement('img');
        t.src = im.getAttribute('src');
        t.style.cssText = 'width:52px;height:52px;object-fit:cover;border-radius:6px;cursor:pointer;flex:0 0 auto;border:2px solid ' +
          (i === state.idx ? '#fff' : 'transparent') + ';opacity:' + (i === state.idx ? '1' : '0.55');
        t.addEventListener('click', function (e) {
          e.stopPropagation();
          state.idx = i;
          render();
        });
        strip.appendChild(t);
      });
    }
    // предзагрузка соседей
    [state.idx - 1, state.idx + 1].forEach(function (i) {
      if (state.imgs[i]) { var p = new Image(); p.src = state.imgs[i].getAttribute('src'); }
    });
  }

  function step(d) {
    if (!state.imgs.length) return;
    state.idx = (state.idx + d + state.imgs.length) % state.imgs.length;
    render();
  }

  window.openPhotoViewer = function (el) {
    ensureDom();
    var root = el.closest('[data-gallery]') || el.parentElement;
    var list = root ? Array.prototype.slice.call(root.querySelectorAll('img')) : [el];
    state.imgs = list.length ? list : [el];
    state.idx = Math.max(0, state.imgs.indexOf(el));
    var ov = document.getElementById('pvOverlay');
    ov.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    render();
  };

  window.closePhotoViewer = function () {
    var ov = document.getElementById('pvOverlay');
    if (ov) ov.style.display = 'none';
    document.body.style.overflow = '';
  };

  window.photoViewerStep = step;
})();
