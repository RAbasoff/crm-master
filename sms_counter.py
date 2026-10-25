# -*- coding: utf-8 -*-
"""SMS counter + provider notes for Belgium.

Провайдеры для Бельгии (практика):
- Twilio — стабильно, ~€0.07–0.09/SMS, не «бесплатно», но надёжно
- Bird (ex-MessageBird, NL/BE) — удобно для EU
- SMSAPI / Sinch — бюджетнее на объёмы
- Полностью бесплатно ~350 SMS/мес у нормальных BE-провайдеров почти нет;
  часто есть пробный период или дешёвый тариф.

Счётчик считается локально по отправленным SMS (logs + файл).
"""
import os
import json
from datetime import datetime, timedelta
from pathlib import Path

_COUNTER = Path(os.path.dirname(os.path.abspath(__file__))) / 'logs' / 'sms_counter.json'


def _load():
    try:
        return json.loads(_COUNTER.read_text(encoding='utf-8'))
    except Exception:
        return {'day': {'date': '', 'count': 0}, 'month': {'month': '', 'count': 0}, 'total': 0}


def _save(d):
    try:
        _COUNTER.parent.mkdir(parents=True, exist_ok=True)
        _COUNTER.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass


def sms_count_add(n=1):
    d = _load()
    now = datetime.now()
    day = now.strftime('%Y-%m-%d')
    mon = now.strftime('%Y-%m')
    if d.get('day', {}).get('date') != day:
        d['day'] = {'date': day, 'count': 0}
    if d.get('month', {}).get('month') != mon:
        d['month'] = {'month': mon, 'count': 0}
    d['day']['count'] += n
    d['month']['count'] += n
    d['total'] = d.get('total', 0) + n
    _save(d)
    return d


def sms_stats():
    d = _load()
    now = datetime.now()
    day = now.strftime('%Y-%m-%d')
    mon = now.strftime('%Y-%m')
    if d.get('day', {}).get('date') != day:
        d['day'] = {'date': day, 'count': 0}
    if d.get('month', {}).get('month') != mon:
        d['month'] = {'month': mon, 'count': 0}
    return {
        'today': d['day']['count'],
        'today_date': day,
        'month': d['month']['count'],
        'month_key': mon,
        'total': d.get('total', 0),
        'limit_month': 350,
        'left_month': max(0, 350 - d['month']['count']),
    }
