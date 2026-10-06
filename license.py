# -*- coding: utf-8 -*-
"""Лицензионный ключ ProMaster.

Формат: BASE64(payload).BASE64(hmac)
payload = json {client, seats, expire: YYYY-MM-DD}
Секрет (LICENSE_SECRET) — только у вас; ключ можно проверить офлайн.
"""
import base64
import hashlib
import hmac
import json
import os
from datetime import date, datetime

SECRET_ENV = 'LICENSE_SECRET'
# резервный секрет (смените при установке)
DEFAULT_SECRET = 'PROMASTER-LICENSE-2026-CHANGE-ME'


def _secret():
    return os.environ.get(SECRET_ENV) or DEFAULT_SECRET


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip('=')


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def make_license(client: str, expire: str, seats: int = 15, secret: str = None) -> str:
    """Создать ключ. expire = 'YYYY-MM-DD' (срок задаёте вы)."""
    payload = {
        'client': client,
        'seats': int(seats),
        'expire': expire,
        'v': 1,
    }
    raw = json.dumps(payload, separators=(',', ':')).encode()
    secret = (secret or _secret()).encode()
    sig = hmac.new(secret, raw, hashlib.sha256).digest()
    return f'{_b64e(raw)}.{_b64e(sig)}'


def parse_license(key: str, secret: str = None):
    """Проверка подписи и срока. Возвращает (ok, payload|error)."""
    if not key or '.' not in key:
        return False, 'Формат ключа неверный'
    b64, sig = key.strip().split('.', 1)
    try:
        raw = _b64d(b64)
        sig_b = _b64d(sig)
    except Exception:
        return False, 'Ключ повреждён'
    secret = (secret or _secret()).encode()
    expect = hmac.new(secret, raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig_b, expect):
        return False, 'Ключ не подтверждён (недействителен)'
    try:
        payload = json.loads(raw.decode())
    except Exception:
        return False, 'Ключ повреждён'
    exp = payload.get('expire') or ''
    try:
        exp_d = datetime.strptime(exp, '%Y-%m-%d').date()
    except ValueError:
        return False, 'Нет срока действия'
    if date.today() > exp_d:
        return False, f'Срок действия истёк ({exp})'
    return True, payload


def license_file_path():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'instance', '.license')


def load_license():
    p = license_file_path()
    if not os.path.exists(p):
        return ''
    try:
        return open(p, 'r', encoding='utf-8').read().strip()
    except Exception:
        return ''


def save_license(key: str):
    p = license_file_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write((key or '').strip())
