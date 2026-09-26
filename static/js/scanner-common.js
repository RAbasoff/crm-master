/* ============================================================
   scanner-common.js — shared barcode/QR scanner infrastructure
   PC:  external USB scanner (keyboard-wedge: Honeywell, any HID)
   Phone/tablet: camera via html5-qrcode
   ============================================================ */

const ScannerCommon = (function () {
    'use strict';

    /* ---- device detection ---- */
    function isMobile() {
        if (navigator.userAgentData && navigator.userAgentData.mobile !== undefined) {
            return navigator.userAgentData.mobile;
        }
        if (/Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)) {
            return true;
        }
        // iPad on iOS 13+ reports as Mac
        if (navigator.maxTouchPoints > 1 && /MacIntel/.test(navigator.platform)) return true;
        // touch-primary small screens
        if (window.matchMedia && window.matchMedia('(pointer: coarse)').matches && window.innerWidth < 1024) return true;
        return false;
    }

    /* ---- Russian keyboard layout → Latin (ЙЦУКЕН → QWERTY) ----
       USB scanners send physical key codes; when OS layout is Russian,
       "Z" arrives as "Я", "Q" as "Й", etc. Convert back. */
    const RU_TO_LAT = {
        'й':'q','ц':'w','у':'e','к':'r','е':'t','н':'y','г':'u','ш':'i','щ':'o','з':'p','х':'[','ъ':']',
        'ф':'a','ы':'s','в':'d','а':'f','п':'g','р':'h','о':'j','л':'k','д':'l','ж':';','э':"'",
        'я':'z','ч':'x','с':'c','м':'v','и':'b','т':'n','ь':'m','б':',','ю':'.','ё':'`',
        'Й':'Q','Ц':'W','У':'E','К':'R','Е':'T','Н':'Y','Г':'U','Ш':'I','Щ':'O','З':'P','Х':'{','Ъ':'}',
        'Ф':'A','Ы':'S','В':'D','А':'F','П':'G','Р':'H','О':'J','Л':'K','Д':'L','Ж':':','Э':'"',
        'Я':'Z','Ч':'X','С':'C','М':'V','И':'B','Т':'N','Ь':'M','Б':'<','Ю':'>','Ё':'~'
    };

    function ruToLat(str) {
        let out = '';
        for (let i = 0; i < str.length; i++) {
            out += RU_TO_LAT[str[i]] || str[i];
        }
        return out;
    }

    /* Detect if string contains Russian chars (likely wrong layout) */
    function hasCyrillic(str) {
        return /[а-яёА-ЯЁ]/.test(str);
    }

    /* Convert scanned code: if it has Cyrillic, map to Latin */
    function normalizeCode(code) {
        if (hasCyrillic(code)) {
            return ruToLat(code);
        }
        return code;
    }
    function statusHtml(mode) {
        if (mode === 'mobile') {
            return '<span class="scan-status" style="display:inline-flex;align-items:center;gap:6px;font-size:11px;color:#27ae60">' +
                   '<span style="width:8px;height:8px;border-radius:50%;background:#27ae60"></span>Camera ready</span>';
        }
        return '<span class="scan-status" style="display:inline-flex;align-items:center;gap:6px;font-size:11px;color:#27ae60">' +
               '<span style="width:8px;height:8px;border-radius:50%;background:#27ae60"></span>USB scanner ready</span>';
    }

    /* ---- init a scanner page ----
       config = {
         inputId:      'scannerInput',       // text input element id
         cameraBtnId:  'btnCamera',          // camera toggle button id (optional)
         cameraBlockId:'cameraBlock',        // camera container id (optional)
         readerId:     'reader',             // html5-qrcode element id (optional)
         onScan:       function(code){},     // callback for each scanned code
         hintText:     '...'                 // optional hint text override
       }
    ---- */
    function init(config) {
        const input = document.getElementById(config.inputId);
        if (!input) return;

        const mobile = isMobile();
        const btn = config.cameraBtnId ? document.getElementById(config.cameraBtnId) : null;
        const block = config.cameraBlockId ? document.getElementById(config.cameraBlockId) : null;

        /* camera UI: show on mobile, hide on desktop */
        if (btn) {
            if (mobile) {
                btn.style.display = '';
                // on mobile make camera the primary action
                btn.style.background = '#27ae60';
                btn.style.color = '#fff';
            } else {
                btn.style.display = 'none';
            }
        }
        if (block && !mobile) {
            block.style.display = 'none';
        }

        /* status / hint under input */
        if (config.hintSelector) {
            const hint = document.querySelector(config.hintSelector);
            if (hint) hint.innerHTML = statusHtml(mobile ? 'mobile' : 'usb');
        } else {
            const hint = input.parentElement && input.parentElement.querySelector('div[style*="font-size:11px"]');
            if (hint) hint.innerHTML = statusHtml(mobile ? 'mobile' : 'usb');
        }

        /* Enter → process */
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                let code = input.value.trim();
                if (code) {
                    code = normalizeCode(code);
                    input.value = '';
                    config.onScan(code);
                }
            }
        });

        /* auto-refocus on any click outside interactive elements */
        document.addEventListener('click', function (e) {
            if (e.target.closest('a') || e.target.closest('button') ||
                e.target.closest('select') || e.target.closest('input') ||
                e.target.closest('textarea') || e.target.closest('#reader') ||
                e.target.closest('.modal')) return;
            input.focus();
        });

        /* on mobile: if there is a camera start button and no manual interaction yet,
           auto-open camera after short delay (phone = camera-first UX) */
        if (mobile && config.autoStartCamera && config.cameraStartFn) {
            setTimeout(function () {
                config.cameraStartFn();
            }, 300);
        } else {
            input.focus();
        }

        return { isMobile: mobile, input: input };
    }

    /* ---- global keyboard-wedge catcher (works even if focus is elsewhere) ----
       Use on pages where the scanner might type into the document body.
       Prefix-aware: scanner sends characters very fast, ends with Enter.  */
    let _wedgeBuffer = '';
    let _wedgeTimer = null;
    let _wedgeHandler = null;

    function enableGlobalWedge(onScan) {
        _wedgeHandler = onScan;
        document.addEventListener('keydown', _onWedgeKey, true);
    }

    function disableGlobalWedge() {
        document.removeEventListener('keydown', _onWedgeKey, true);
        _wedgeHandler = null;
    }

    function _onWedgeKey(e) {
        // skip if user is typing in a form field (except our scanner input)
        const tag = (e.target.tagName || '').toLowerCase();
        const isOurInput = e.target.id && e.target.id.indexOf('scan') !== -1;
        if ((tag === 'input' || tag === 'textarea' || tag === 'select') && !isOurInput) return;

        if (e.key === 'Enter') {
            if (_wedgeBuffer.length > 2) {
                e.preventDefault();
                const code = normalizeCode(_wedgeBuffer);
                _wedgeBuffer = '';
                if (_wedgeHandler) _wedgeHandler(code);
            }
            return;
        }
        if (e.key.length === 1) {
            _wedgeBuffer += e.key;
            clearTimeout(_wedgeTimer);
            _wedgeTimer = setTimeout(function () { _wedgeBuffer = ''; }, 150);
        }
    }

    return {
        isMobile: isMobile,
        init: init,
        statusHtml: statusHtml,
        enableGlobalWedge: enableGlobalWedge,
        disableGlobalWedge: disableGlobalWedge,
        normalizeCode: normalizeCode,
        ruToLat: ruToLat
    };
})();
