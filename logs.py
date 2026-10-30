"""Notifications and audit/system/activity logging."""
from flask import request, session
from flask_login import current_user
from models import db, Notification, AuditLog, UserActivityLog, SystemLog
from schema import safe_commit

# Push отправляем в фоне: синхронная отправка (HTTPS к FCM/Apple на каждую
# подписку) блокировала ответ на 5–30 секунд — «кнопки откликаются медленно».
import threading
from concurrent.futures import ThreadPoolExecutor
_push_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='webpush')


def _user_language(user_id):
    """Язык пользователя для уведомлений."""
    try:
        from models import User
        u = db.session.get(User, user_id)
        if u and getattr(u, 'preferred_language', None):
            return u.preferred_language
        if u and u.id == getattr(current_user, 'id', None):
            return session.get('lang', 'ru')
    except Exception:
        pass
    return session.get('lang', 'ru') or 'ru'


def _send_push_job(row, title, message, ntype, link, lang='ru'):
    """Фоновая задача: ТОЛЬКО сетевая отправка (без БД!).

    Любая работа с БД в потоках держит sqlite-лок и тормозит основной
    запрос (было 20–90 сек на сохранение заявки).
    """
    sid, endpoint, p256dh, auth, user_id = row
    try:
        from app import app as _app
        from push import send_web_push
        from flask_babel import force_locale, gettext as _t
        with _app.app_context():  # контекст приложения — без БД
            with force_locale(lang):
                res = send_web_push(
                    {'endpoint': endpoint, 'keys': {'p256dh': p256dh, 'auth': auth}},
                    _t(title), _t(message) if message else '', url=link, tag=ntype or 'info')
    except Exception:
        res = 'error'
    if res == 'dead':
        # мёртвую подписку удаляем отдельной короткой задачей
        _push_pool.submit(_cleanup_dead_sub, sid)
    elif res == 'ok':
        _push_pool.submit(_touch_sub, sid)


def _cleanup_dead_sub(sid):
    try:
        from app import app as _app
        with _app.app_context():
            from models import PushSubscription, db
            PushSubscription.query.filter_by(id=sid).delete(synchronize_session=False)
            db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _touch_sub(sid):
    try:
        from app import app as _app
        with _app.app_context():
            from models import PushSubscription, db, now_local
            PushSubscription.query.filter_by(id=sid).update(
                {'last_used_at': now_local()}, synchronize_session=False)
            db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _notify_one(user_id, title, message, ntype, link):
    """Вставить уведомление (быстро) + Web Push в фоне."""
    n = Notification(user_id=user_id, title=title, message=message, type=ntype, link=link)
    db.session.add(n)
    safe_commit()
    try:
        # снимок подписок и языка — в контексте запроса; потоки получают готовые данные
        from models import PushSubscription
        lang = _user_language(user_id)
        rows = [(s.id, s.endpoint, s.p256dh, s.auth, s.user_id)
                for s in PushSubscription.query.filter_by(user_id=user_id).all()]
        for row in rows:
            _push_pool.submit(_send_push_job, row, title, message, ntype, link, lang)
    except Exception:
        pass


def create_notification(user_id, title, message, ntype='info', link=None):
    """Создать уведомление.

    title/message хранятся как msgid (англ.) — перевод при показе
    на языке текущего пользователя. Push — на языке получателя.
    Директору отдельные события шлют вызывающие маршруты (см. faults.py).
    """
    if not user_id:
        return
    _notify_one(user_id, title, message, ntype, link)

def log_audit(action, entity_type=None, entity_id=None, details=None):
    try:
        log = AuditLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            action=action, entity_type=entity_type, entity_id=entity_id,
            details=details, ip_address=request.remote_addr
        )
        db.session.add(log)
        safe_commit()
    except Exception:
        db.session.rollback()

# ── Журнал активности: буфер вместо INSERT+COMMIT на каждый запрос ──
# Один commit на пачку: раньше каждый просмотр страницы делал fsync в SQLite
# (это заметно тормозило ответ на медленном диске PythonAnywhere).
_activity_buf = []
_activity_flushed_at = 0.0
_ACTIVITY_MAX_AGE = 5.0      # сек: держим буфер не дольше
_ACTIVITY_MAX_ROWS = 25      # ...или не больше N строк


def log_user_activity(action, page=None, method=None, entity_type=None, entity_id=None, details=None, duration_ms=None, status_code=None):
    """Log user activity (page views, actions, etc.) — буферизованно."""
    try:
        from flask import has_request_context
        uid = uname = ip = ua = sid = None
        if has_request_context():
            if current_user.is_authenticated:
                uid = current_user.id
                uname = current_user.username
            ip = request.remote_addr
            ua = str(request.user_agent)[:300] if request.user_agent else None
            sid = session.get('_id', '') if session else None
            page = page or request.path
            method = method or request.method
        row = dict(
            user_id=uid, username=uname, action=action,
            page=page, method=method,
            entity_type=entity_type, entity_id=entity_id, details=details,
            ip_address=ip, user_agent=ua, session_id=sid,
            duration_ms=duration_ms, status_code=status_code,
        )
        _activity_buf.append(row)
        flush_activity_buffer(age=_ACTIVITY_MAX_AGE, rows=_ACTIVITY_MAX_ROWS)
    except Exception as e:
        print('log_user_activity ERR:', e)


def flush_activity_buffer(age=None, rows=None, force=False):
    """Сброс буфера активности в БД одной вставкой + одним commit.

    force=True — сбросить немедленно (бэкап/выгрузка/тесты).
    При ошибке строки ВОЗВРАЩАЮТСЯ в буфер (не теряются).
    """
    global _activity_flushed_at
    import time as _time
    if not _activity_buf:
        _activity_flushed_at = _time.time()
        return 0
    now = _time.time()
    if not force:
        too_many = rows is not None and len(_activity_buf) >= rows
        too_old = age is not None and (now - _activity_flushed_at) >= age and _activity_flushed_at > 0
        if _activity_flushed_at == 0.0:
            # первый буфер: ждём наполнения (rows) — иначе засекаем часы
            if not too_many:
                _activity_flushed_at = now
                return 0
        elif not (too_many or too_old):
            return 0
    batch = _activity_buf[:]
    try:
        from models import UserActivityLog
        from flask import current_app, has_app_context
        if has_app_context():
            _flush_batch(batch)
        else:
            from app import app as _app
            with _app.app_context():
                _flush_batch(batch)
        del _activity_buf[:len(batch)]
        _activity_flushed_at = _time.time()
        return len(batch)
    except Exception as e:
        print('flush_activity_buffer ERR:', e)
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass
        # не теряем строки: возвращаем в начало буфера
        _activity_buf[:0] = batch
        # чтобы не копились бесконечно — ограничение
        if len(_activity_buf) > 5000:
            del _activity_buf[:-1000]
        return 0


def _flush_batch(batch):
    from models import UserActivityLog, db
    from schema import safe_commit
    if len(batch) == 1:
        db.session.add(UserActivityLog(**batch[0]))
    else:
        db.session.execute(UserActivityLog.__table__.insert(), batch)
    if not safe_commit():
        raise RuntimeError('commit failed')

def log_system(level, category, message, details=None, source=None):
    """Log system events (errors, warnings, info)"""
    try:
        from models import SystemLog
        log = SystemLog(
            level=level,
            category=category,
            message=message,
            details=details,
            source=source,
            user_id=current_user.id if current_user.is_authenticated else None,
            ip_address=request.remote_addr if request else None
        )
        db.session.add(log)
        safe_commit()
    except Exception:
        db.session.rollback()


def _errors_log_path():
    import os
    base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, 'logs')
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, 'errors.log')


def log_exception(exc, category='http', source=None):
    """Сохранить исключение: traceback в файл + запись ERROR в SystemLog."""
    import traceback
    from datetime import datetime
    tb = traceback.format_exc() if exc else ''
    path = request.path if request else '?'
    method = request.method if request else '?'
    user = ''
    try:
        if current_user.is_authenticated:
            user = getattr(current_user, 'username', '') or str(current_user.id)
    except Exception:
        pass
    msg = f'{method} {path}: {type(exc).__name__ if exc else "Exception"}: {exc}'
    details = tb[-4000:] if tb else str(exc)
    try:
        with open(_errors_log_path(), 'a', encoding='utf-8') as f:
            f.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            f.write(details or '')
            f.write('\n')
    except Exception:
        pass
    try:
        log_system('ERROR', category, msg[:500], details=details, source=source)
    except Exception:
        pass
