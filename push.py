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
        with open(path, 'r', encoding='utf-8') as f:
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
    """Один Web Push. True — доставлено в push-сервис."""
    from pywebpush import webpush, WebPushException
    pub, priv = _vapid_keys()
    if not pub or not priv:
        return False
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
            vapid_claims={'sub': 'mailto:admin@promaster.local'},
            ttl=60 * 60 * 6,
        )
        return bool(resp and resp.status_code < 300)
    except WebPushException as e:
        # 404/410 — подписка протухла; остальное не трогаем
        code = getattr(getattr(e, 'response', None), 'status_code', None)
        if code in (404, 410):
            try:
                from models import PushSubscription, db
                PushSubscription.query.filter_by(
                    endpoint=subscription_info.get('endpoint')
                ).delete()
                db.session.commit()
            except Exception:
                db.session.rollback()
        return False
    except Exception:
        return False


def push_to_user(user_id, title, body, url=None, tag=None):
    """Отправить всем подпискам пользователя. Возвращает число успешных."""
    from models import PushSubscription, db, now_local
    subs = PushSubscription.query.filter_by(user_id=user_id).all()
    ok = 0
    for s in subs:
        info = {
            'endpoint': s.endpoint,
            'keys': {'p256dh': s.p256dh, 'auth': s.auth},
        }
        if send_web_push(info, title, body, url=url, tag=tag):
            ok += 1
            s.last_used_at = now_local()
    return ok


def notify_and_push(user_id, title, message, ntype='info', link=None):
    """DB-уведомление + Web Push. create_notification уже шлёт push — без дубля."""
    from logs import create_notification
    create_notification(user_id, title, message, ntype, link)
