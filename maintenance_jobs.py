# -*- coding: utf-8 -*-
"""Самоочистка: старые логи, уведомления, служебный мусор + VACUUM.

Запуск по расписанию (см. config.AUTO_CLEANUP_HOUR): once() в сутки в
первый запрос внутри окна часа. Время можно задать в config.py или
положив файл instance/.cleanup_hour с числом 0-23 (без перезаливки).
"""
import os
import time as _time
from datetime import datetime, timedelta

_BASE = os.path.dirname(os.path.abspath(__file__))
_STAMP_DIR = os.path.join(_BASE, 'backups')
_STAMP = os.path.join(_STAMP_DIR, '.cleanup_stamp')
_VACUUM_STAMP = os.path.join(_STAMP_DIR, '.vacuum_stamp')


def _cleanup_hour():
    """Час запуска (0-23). Файл instance/.cleanup_hour переопределяет config."""
    hour = None
    try:
        import config
        hour = getattr(config, 'AUTO_CLEANUP_HOUR', 3)
    except Exception:
        hour = 3
    p = os.path.join(_BASE, 'instance', '.cleanup_hour')
    try:
        if os.path.exists(p):
            v = open(p, 'r', encoding='utf-8').read().strip()
            if v:
                hour = int(v)
    except Exception:
        pass
    try:
        hour = int(hour)
    except Exception:
        hour = 3
    return max(0, min(23, hour))


def _retention():
    """Сроки хранения (дни). Файл instance/.cleanup_days.json может переопределить."""
    days = {
        'activity': 90,        # user_activity_log — самая быстрорастущая таблица
        'notif_read': 30,      # прочитанные уведомления
        'notif_unread': 180,   # непрочитанные (на всякий случай дольше)
        'system_info': 90,     # system_log INFO/DEBUG
        'system_warn': 180,    # WARNING
        'system_error': 365,   # ERROR — дольше всех
        'audit': 365,          # audit_log
        'offline': 30,         # offline_mutation
    }
    p = os.path.join(_BASE, 'instance', '.cleanup_days.json')
    try:
        if os.path.exists(p):
            import json
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for k, v in (data or {}).items():
                if k in days:
                    days[k] = max(1, int(v))
    except Exception:
        pass
    return days


def run_auto_cleanup(force=False, do_vacuum=True):
    """Очистка старых данных. Возвращает словарь со счётчиками.

    force=True — выполнить сейчас (кнопка/тесты), без проверки расписания.
    """
    from flask import has_app_context
    if has_app_context():
        return _run_cleanup(force=force, do_vacuum=do_vacuum)
    from app import app as _app
    with _app.app_context():
        return _run_cleanup(force=force, do_vacuum=do_vacuum)


def _run_cleanup(force=False, do_vacuum=True):
    from models import db, UserActivityLog, Notification, SystemLog, AuditLog, OfflineMutation, RecordLock, now_local
    from schema import safe_commit
    days = _retention()
    now = now_local()
    stats = {}

    def _cut(n):
        return now - timedelta(days=n)

    try:
        n = UserActivityLog.query.filter(UserActivityLog.created_at < _cut(days['activity'])).delete(synchronize_session=False)
        stats['activity'] = n
    except Exception as e:
        stats['activity_err'] = str(e)[:120]
    try:
        n = Notification.query.filter(
            Notification.created_at < _cut(days['notif_read']),
            Notification.is_read == True,  # noqa: E712
        ).delete(synchronize_session=False)
        stats['notif_read'] = n
        n2 = Notification.query.filter(
            Notification.created_at < _cut(days['notif_unread'])
        ).delete(synchronize_session=False)
        stats['notif_unread'] = n2
    except Exception as e:
        stats['notif_err'] = str(e)[:120]
    try:
        from sqlalchemy import or_
        n_info = SystemLog.query.filter(
            SystemLog.created_at < _cut(days['system_info']),
            or_(SystemLog.level == 'INFO', SystemLog.level == 'DEBUG'))
        n_info = n_info.delete(synchronize_session=False)
        n_warn = SystemLog.query.filter(
            SystemLog.created_at < _cut(days['system_warn']),
            SystemLog.level == 'WARNING').delete(synchronize_session=False)
        n_err = SystemLog.query.filter(
            SystemLog.created_at < _cut(days['system_error']),
            SystemLog.level == 'ERROR').delete(synchronize_session=False)
        stats['system'] = n_info + n_warn + n_err
    except Exception as e:
        stats['system_err'] = str(e)[:120]
    try:
        n = AuditLog.query.filter(AuditLog.created_at < _cut(days['audit'])).delete(synchronize_session=False)
        stats['audit'] = n
    except Exception as e:
        stats['audit_err'] = str(e)[:120]
    try:
        n = OfflineMutation.query.filter(OfflineMutation.created_at < _cut(days['offline'])).delete(synchronize_session=False)
        stats['offline'] = n
    except Exception as e:
        stats['offline_err'] = str(e)[:120]
    try:
        n = RecordLock.query.filter(RecordLock.expires_at < now).delete(synchronize_session=False)
        stats['locks'] = n
    except Exception as e:
        stats['locks_err'] = str(e)[:120]

    try:
        if safe_commit():
            stats['commit'] = 'ok'
        else:
            stats['commit'] = 'failed'
    except Exception as e:
        stats['commit_err'] = str(e)[:120]

    # Ротация файла ошибок (>5MB → оставляем хвост)
    try:
        log_path = os.path.join(_BASE, 'logs', 'errors.log')
        if os.path.exists(log_path) and os.path.getsize(log_path) > 5 * 1024 * 1024:
            with open(log_path, 'rb') as f:
                f.seek(-200 * 1024, os.SEEK_END)
                tail = f.read()
            with open(log_path, 'wb') as f:
                f.write(b'... rotated by auto-clean %s ...\n' % datetime.now().strftime('%Y-%m-%d %H:%M').encode())
                f.write(tail)
            stats['errors_log'] = 'rotated'
    except Exception as e:
        stats['errors_log_err'] = str(e)[:120]

    # VACUUM раз в 7 дней (возвращает место, уплотняет деревья).
    # Только сырое sqlite3: VACUUM нельзя внутри транзакции SQLAlchemy.
    if do_vacuum:
        try:
            last = 0.0
            if os.path.exists(_VACUUM_STAMP):
                last = float(open(_VACUUM_STAMP, 'r', encoding='utf-8').read().strip() or '0')
            if force or (_time.time() - last) > 7 * 24 * 3600:
                import sqlite3
                db_path = os.path.join(_BASE, 'instance', 'werkplaats.db')
                con = sqlite3.connect(db_path, timeout=60)
                try:
                    con.execute('PRAGMA busy_timeout=60000')
                    con.execute('VACUUM')
                finally:
                    con.close()
                os.makedirs(_STAMP_DIR, exist_ok=True)
                with open(_VACUUM_STAMP, 'w', encoding='utf-8') as f:
                    f.write(str(_time.time()))
                stats['vacuum'] = 'ok'
        except Exception as e:
            stats['vacuum_err'] = str(e)[:120]

    try:
        os.makedirs(_STAMP_DIR, exist_ok=True)
        with open(_STAMP, 'w', encoding='utf-8') as f:
            f.write(datetime.now().strftime('%Y%m%d'))
    except Exception:
        pass
    return stats


def maybe_auto_cleanup():
    """Раз в сутки в час AUTO_CLEANUP_HOUR (первый запрос в окне часа)."""
    now = datetime.now()
    if now.hour != _cleanup_hour():
        return
    os.makedirs(_STAMP_DIR, exist_ok=True)
    today = now.strftime('%Y%m%d')
    try:
        if os.path.exists(_STAMP) and open(_STAMP, 'r', encoding='utf-8').read().strip() == today:
            return
    except Exception:
        pass
    try:
        stats = run_auto_cleanup()
        print('AUTO CLEANUP:', stats)
    except Exception as e:
        print('AUTO CLEANUP ERROR:', e)
