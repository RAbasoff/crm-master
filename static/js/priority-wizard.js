/* Priority wizard for fault reports.
 * When the user picks "Critical", ask 3 questions and compute the real priority:
 *   Q1 Is the machine powered?      NO → critical | YES → Q2
 *   Q2 Does the machine start?      NO → high     | YES → Q3
 *   Q3 Runs badly but still produces? YES → low  | NO  → normal
 * Strings come from window.PRIORITY_WIZARD (translated in the template).
 */
(function () {
    'use strict';
    var cfg = window.PRIORITY_WIZARD || {};
    var QUESTIONS = [
        cfg.q1 || 'Is the machine powered?',
        cfg.q2 || 'Does the machine start?',
        cfg.q3 || 'Does the machine work incorrectly but still produce?'
    ];
    var LABELS = {
        critical: cfg.crit || 'Critical',
        high: cfg.high || 'High',
        normal: cfg.norm || 'Normal',
        low: cfg.low || 'Low'
    };
    var ICONS = { critical: '🔴', high: '🟠', normal: '🔵', low: '🟢' };
    var COLORS = { critical: '#e74c3c', high: '#e67e22', normal: '#3498db', low: '#27ae60' };

    var overlay = null, step = 0, onResult = null;

    function build() {
        if (overlay) return;
        overlay = document.createElement('div');
        overlay.id = 'prioWizard';
        overlay.style.cssText = 'display:none;position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);align-items:center;justify-content:center;padding:16px';
        overlay.innerHTML =
            '<div style="background:#fff;border-radius:16px;max-width:420px;width:100%;padding:22px;box-shadow:0 12px 40px rgba(0,0,0,.3)">' +
            '  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">' +
            '    <div style="font-size:12px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.5px" id="pwTitle"></div>' +
            '    <button type="button" id="pwClose" style="background:none;border:none;font-size:22px;cursor:pointer;color:#999;line-height:1">✕</button>' +
            '  </div>' +
            '  <div id="pwStep" style="font-size:11px;color:#aaa;font-weight:700;margin-bottom:6px"></div>' +
            '  <div id="pwQuestion" style="font-size:19px;font-weight:800;line-height:1.35;margin-bottom:20px"></div>' +
            '  <div id="pwAnswers" style="display:flex;gap:10px"></div>' +
            '  <div id="pwResult" style="display:none;text-align:center;padding:8px 0 4px">' +
            '    <div style="font-size:15px;color:#666;margin-bottom:8px" id="pwResultLabel"></div>' +
            '    <div id="pwResultValue" style="font-size:24px;font-weight:800;margin-bottom:18px"></div>' +
            '    <button type="button" id="pwDone" style="width:100%;padding:14px;font-size:16px;font-weight:700;color:#fff;background:#27ae60;border:none;border-radius:12px;cursor:pointer"></button>' +
            '  </div>' +
            '</div>';
        document.body.appendChild(overlay);

        overlay.querySelector('#pwClose').onclick = function () { close('critical'); };
        overlay.querySelector('#pwDone').onclick = function () { close(null); };
        overlay.addEventListener('click', function (e) {
            if (e.target === overlay) close('critical');
        });
    }

    function close(prio) {
        overlay.style.display = 'none';
        var cb = onResult; onResult = null;
        // если вопрос завершён — результат сохраняем, иначе «критический»
        var result = lastResult || prio || 'critical';
        if (cb) cb(result);
    }

    var lastResult = null;

    function showStep() {
        var box = overlay.querySelector('#pwAnswers');
        var res = overlay.querySelector('#pwResult');
        res.style.display = 'none';
        box.style.display = 'flex';
        overlay.querySelector('#pwStep').textContent =
            (cfg.step || 'Step') + ' ' + (step + 1) + '/3';
        overlay.querySelector('#pwQuestion').textContent = QUESTIONS[step];
        box.innerHTML = '';
        ['yes', 'no'].forEach(function (k) {
            var b = document.createElement('button');
            b.type = 'button';
            b.textContent = (k === 'yes' ? (cfg.yes || 'Yes') : (cfg.no || 'No')).toUpperCase();
            b.style.cssText = 'flex:1;padding:18px 10px;font-size:18px;font-weight:800;color:#fff;border:none;border-radius:12px;cursor:pointer;' +
                'background:' + (k === 'yes' ? '#27ae60' : '#e74c3c');
            b.onclick = function () { answer(k === 'yes'); };
            box.appendChild(b);
        });
    }

    function answer(yes) {
        if (step === 0) {
            if (!yes) return finish('critical');
            step = 1; return showStep();
        }
        if (step === 1) {
            if (!yes) return finish('high');
            step = 2; return showStep();
        }
        finish(yes ? 'low' : 'normal');
    }

    function finish(prio) {
        lastResult = prio;
        overlay.querySelector('#pwAnswers').style.display = 'none';
        overlay.querySelector('#pwStep').textContent = '';
        overlay.querySelector('#pwQuestion').textContent = '';
        var res = overlay.querySelector('#pwResult');
        res.style.display = 'block';
        overlay.querySelector('#pwResultLabel').textContent = cfg.result || 'Priority set to:';
        var v = overlay.querySelector('#pwResultValue');
        v.textContent = (ICONS[prio] || '') + ' ' + (LABELS[prio] || prio).toUpperCase();
        v.style.color = COLORS[prio] || '#333';
        overlay.querySelector('#pwDone').textContent = cfg.done || 'Done';
        overlay.querySelector('#pwDone').style.background = COLORS[prio] || '#27ae60';
    }

    function open(cb) {
        build();
        step = 0;
        lastResult = null;
        onResult = cb;
        overlay.querySelector('#pwTitle').textContent = cfg.title || 'Priority check';
        showStep();
        overlay.style.display = 'flex';
    }

    window.PriorityWizard = {
        open: open,
        /* Desktop: <select name="priority"> — open wizard when "critical" chosen */
        attachSelect: function (sel) {
            if (!sel) return;
            sel.addEventListener('change', function () {
                if (sel.value === 'critical') {
                    open(function (p) { sel.value = p; });
                }
            });
        },
        /* Mobile: priority buttons + hidden input */
        attachButtons: function (input) {
            window.__pwApplyPriority = function (val) {
                document.querySelectorAll('.priority-btn').forEach(function (b) {
                    b.classList.toggle('sel', b.dataset.val === val);
                });
                if (input) input.value = val;
            };
            window.selPriority = function (el) {
                var val = el.dataset.val;
                if (val === 'critical') {
                    open(function (p) { window.__pwApplyPriority(p); });
                    return;
                }
                window.__pwApplyPriority(val);
            };
        }
    };
})();
