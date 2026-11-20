# -*- coding: utf-8 -*-
"""NIGHTLY 02:30 CHECK — внешняя страховка (правило владельца 2026-11-20).

Запускается НЕ веб-воркером, поэтому оживляет сайт даже при полном зависании.
PythonAnywhere → Tasks → New scheduled task:
  Command:  /home/rabasoff/crm-master/nightly_check.py
  Time:     02:30  (Daily)

Что делает: проверяет ответ сайта; если нет — перезагружает веб-воркеры
(touch wsgi) и повторяет проверку; пишет итог в backups/nightly_check.log.
"""
import datetime
import os
import subprocess
import time
import urllib.request

URL = 'https://rabasoff.pythonanywhere.com/login'
WSGI = '/var/www/rabasoff_pythonanywhere_com_wsgi.py'
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups', 'nightly_check.log')


def site_ok(timeout=60):
    try:
        with urllib.request.urlopen(URL, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def log(msg):
    line = '%s  %s\n' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass
    print(line.strip())


ok = site_ok()
log('check: site_ok=%s' % ok)
if not ok:
    try:
        os.utime(WSGI, None)
        log('reload: wsgi touched')
    except Exception as e:
        log('reload failed: %s' % e)
    time.sleep(25)
    ok2 = site_ok()
    log('recheck after reload: site_ok=%s' % ok2)
