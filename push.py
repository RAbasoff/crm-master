"""Web Push (VAPID) — вибрация + звук на телефоне при заявках/сообщениях.

Ключи: instance/.vapid.json (не в git).
Отправка: pywebpush; при ошибке — тихо пропускаем (не блокируем CRM).
"""
import base64
import json
import os
from datetime import datetime

_VAPID_CACHE = None


def _vapid_keys():
    global _VAPID_CACHE
    if _VAPID_CACHE is not None:
        return _VAPID_CACHE
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', '.vapid.json')
    try:
        # utf-8-sig: файл мог быть сохранён с BOM (Windows) — иначе json падает
        with open(path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        _VAPID_CACHE = (data['public'], data['private'])
    except Exception:
        # env fallback
        _VAPID_CACHE = (os.environ.get('VAPID_PUBLIC_KEY', ''),
                        os.environ.get('VAPID_PRIVATE_KEY', ''))
    return _VAPID_CACHE


def get_public_key():
    pub, _ = _vapid_keys()
    return pub or ''


def _private_key_for_webpush(priv_b64):
    """pywebpush/py_vapid from_string ожидает base64url DER (не PEM)."""
    return priv_b64


def send_web_push(subscription_info, title, body, url=None, tag=None):
    """Один Web Push. Возвращает 'ok' | 'dead' | 'error'.

    НЕ трогает БД: сетевой вызов не должен держать транзакцию открытой —
    иначе остальные запросы ждут sqlite-лок секундами (кнопки тормозили).
    """
    from pywebpush import webpush, WebPushException
    pub, priv = _vapid_keys()
    if not pub or not priv:
        return 'error'
    payload = {
        'title': title or 'ProMaster',
        'body': body or '',
        'url': url or '/',
        'tag': tag or 'promaster',
    }
    try:
        resp = webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=_private_key_for_webpush(priv),
            # Apple требует валидный контакт в sub; https-URL надёжнее mailto с .local
            vapid_claims={'sub': 'https://rabasoff.pythonanywhere.com'},
            ttl=60 * 60 * 6,
        )
        return 'ok' if (resp and resp.status_code < 300) else 'error'
    except WebPushException as e:
        # 404/410 — подписка протухла; 403 BadJwtToken — подписка создана с другим
        # VAPID-ключом и не вылечится сама. Такие помечаем мёртвыми — клиент переподпишется.
        code = getattr(getattr(e, 'response', None), 'status_code', None)
        if code in (404, 410) or (code == 403 and 'BadJwtToken' in str(e)):
            return 'dead'
        return 'error'
    except Exception:
        return 'error'


def push_to_user(user_id, title, body, url=None, tag=None):
    """Отправить всем подпискам пользователя. Возвращает число успешных.

    Сначала читаем подписки и ЗАКРЫВАЕМ транзакцию, потом шлём (без БД),
    и только в конце — короткая запись last_used_at / удаление мёртвых.
    """
    from models import PushSubscription, db, now_local
    rows = [(s.id, s.endpoint, s.p256dh, s.auth)
            for s in PushSubscription.query.filter_by(user_id=user_id).all()]
    try:
        db.session.commit()  # не держим транзакцию на время HTTPS-отправок
    except Exception:
        db.session.rollback()
    ok_ids = []
    dead_ids = []
    for sid, endpoint, p256dh, auth in rows:
        res = send_web_push(
            {'endpoint': endpoint, 'keys': {'p256dh': p256dh, 'auth': auth}},
            title, body, url=url, tag=tag)
        if res == 'ok':
            ok_ids.append(sid)
        elif res == 'dead':
            dead_ids.append(sid)
    try:
        if dead_ids:
            PushSubscription.query.filter(
                PushSubscription.id.in_(dead_ids)
            ).delete(synchronize_session=False)
        if ok_ids:
            PushSubscription.query.filter(
                PushSubscription.id.in_(ok_ids)
            ).update({'last_used_at': now_local()}, synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
    return len(ok_ids)


def notify_and_push(user_id, title, message, ntype='info', link=None):
    """DB-уведомление + Web Push. create_notification уже шлёт push — без дубля."""
    from logs import create_notification
    create_notification(user_id, title, message, ntype, link)
