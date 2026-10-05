"""Notifications and audit/system/activity logging."""
from flask import request, session
from flask_login import current_user
from models import db, Notification, AuditLog, UserActivityLog, SystemLog
from schema import safe_commit


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


def create_notification(user_id, title, message, ntype='info', link=None):
    """Создать уведомление.

    title/message хранятся как msgid (англ.) — перевод при показе
    на языке текущего пользователя. Push — на языке получателя.
    """
    n = Notification(user_id=user_id, title=title, message=message, type=ntype, link=link)
    db.session.add(n)
    safe_commit()
    # Web Push на языке получателя
    try:
        from flask_babel import force_locale, gettext as _t
        from push import push_to_user
        lang = _user_language(user_id)
        with force_locale(lang):
            push_to_user(user_id, _t(title), _t(message) if message else '', url=link, tag=ntype or 'info')
    except Exception:
        try:
            from push import push_to_user
            push_to_user(user_id, title, message or '', url=link, tag=ntype or 'info')
        except Exception:
            pass

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

def log_user_activity(action, page=None, method=None, entity_type=None, entity_id=None, details=None, duration_ms=None, status_code=None):
    """Log user activity (page views, actions, etc.)"""
    try:
        from models import UserActivityLog
        log = UserActivityLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            username=current_user.username if current_user.is_authenticated else None,
            action=action,
            page=page or (request.path if request else None),
            method=method or (request.method if request else None),
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            ip_address=request.remote_addr if request else None,
            user_agent=str(request.user_agent)[:300] if request else None,
            session_id=session.get('_id', '') if session else None,
            duration_ms=duration_ms,
            status_code=status_code
        )
        db.session.add(log)
        safe_commit()
    except Exception:
        db.session.rollback()

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
