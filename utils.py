"""Shared helpers (facade). Heavy logic lives in focused modules:

- schema.py      — ensure_schema / migrations / safe_commit
- security.py    — roles & section access
- logs.py        — notifications & logs
- pdf_utils.py   — fpdf2 / fonts
"""
from datetime import datetime, timedelta
from flask_login import current_user
from flask_babel import gettext as _
from models import db, Notification, AuditLog, GroupPermission, ResponsibleGroup, Verantwoordelijke, UserActivityLog, SystemLog, WorkReportEntry, WarehouseGroup
import os
from werkzeug.utils import secure_filename
import time as _time

# ── re-exports (import-compatible with old `from utils import …`) ──
from schema import (
    SCHEMA_VERSION,
    ensure_schema,
    run_migrations,
    run_data_migrations,
    safe_commit,
    _migrations_already_applied,
    _stamp_migrations_applied,
)
from security import (
    role_required,
    get_user_group_permissions,
    user_has_section_access,
    section_access_required,
)
from logs import (
    create_notification,
    log_audit,
    log_user_activity,
    log_system,
)
from pdf_utils import find_pdf_font, ensure_fpdf

def next_number_suffix(model_cls, field_name, day_prefix):
    """Max numeric suffix for DAY_PREFIX-NNNN document numbers. Race-safe enough for SQLite; callers retry on IntegrityError."""
    field = getattr(model_cls, field_name)
    like = f'{day_prefix}-%'
    max_n = 0
    for (val,) in db.session.query(field).filter(field.like(like)).all():
        if not val:
            continue
        try:
            max_n = max(max_n, int(str(val).rsplit('-', 1)[-1]))
        except (ValueError, TypeError):
            continue
    return max_n + 1


def genereer_nummer():
    from models import Opdracht
    day = datetime.utcnow().strftime('%Y%m%d')
    num = next_number_suffix(Opdracht, 'nummer', f'WO-{day}')
    return f'WO-{day}-{num:04d}'

def date_plus_days(d, days):
    if d and days:
        return d + timedelta(days=days)
    return None


WORK_SHIFT_TYPES = ('full', 'morning', 'afternoon')
OFF_SHIFT_TYPES = ('off', 'sick')


def _parse_hhmm(value, fallback):
    try:
        return datetime.strptime(value, '%H:%M').time()
    except (ValueError, TypeError):
        return fallback


def get_day_shift(user_id, date):
    """WeekendShift на дату (или None)."""
    from models import WeekendShift
    return WeekendShift.query.filter_by(user_id=user_id, date=date).first()


def get_active_schedule(user_id):
    from models import WorkSchedule
    return WorkSchedule.query.filter_by(user_id=user_id, is_active=True).first()


def is_work_date(user_id, date):
    """True, если дата — рабочая для пользователя.

    Суббота/воскресенье могут быть рабочими по WeekendShift
    (full/morning/afternoon) — назначает начальник ТС / админ.
    """
    shift = get_day_shift(user_id, date)
    if shift and shift.shift_type in OFF_SHIFT_TYPES:
        return False
    if shift and shift.shift_type in WORK_SHIFT_TYPES:
        return True

    weekday = date.isoweekday()  # 1=Mon .. 7=Sun
    schedule = get_active_schedule(user_id)
    if schedule:
        try:
            work_days = [int(d.strip()) for d in (schedule.work_days or '').split(',') if d.strip()]
        except ValueError:
            work_days = [1, 2, 3, 4, 5]
        return weekday in work_days
    return weekday <= 5


def get_work_hours(user_id, date=None):
    """(start, end) времени работы на дату; None, если день не рабочий."""
    if date is None:
        date = datetime.now().date()
    if not is_work_date(user_id, date):
        return None
    shift = get_day_shift(user_id, date)
    schedule = get_active_schedule(user_id)
    default_start = _parse_hhmm('08:00', datetime.now().replace(hour=8, minute=0).time())
    default_end = _parse_hhmm('17:00', datetime.now().replace(hour=17, minute=0).time())
    start = _parse_hhmm(schedule.shift_start, default_start) if schedule else default_start
    end = _parse_hhmm(schedule.shift_end, default_end) if schedule else default_end
    if shift and shift.shift_type == 'morning':
        midday = _parse_hhmm('13:00', default_end)
        end = min(end, midday)
    elif shift and shift.shift_type == 'afternoon':
        midday = _parse_hhmm('13:00', default_start)
        start = max(start, midday)
    return start, end


def is_user_at_work(user):
    """True, если сейчас рабочее время по графику пользователя.

    Учитывает: work_days, shift_start/shift_end, WeekendShift
    (off/sick — нерабочий; full/morning/afternoon — рабочая смена,
    в т.ч. суббота, назначенная начальником ТС / админом),
    утверждённый отпуск. Без активного графика — стандартный Пн-Пт 08:00-17:00.
    """
    from models import Vacation
    now = datetime.now()
    today = now.date()

    # Отпуск
    vac = Vacation.query.filter(
        Vacation.user_id == user.id,
        Vacation.status == 'approved',
        Vacation.date_from <= today,
        Vacation.date_to >= today,
    ).first()
    if vac:
        return False

    hours = get_work_hours(user.id, today)
    if not hours:
        return False
    start, end = hours
    return start <= now.time() <= end


def user_schedule_restricted(user):
    """Ограничение по графику действует для механиков (technician / связанный Monteur).
    admin и director не ограничиваются."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    role = getattr(user, 'role', '') or ''
    if role in ('admin', 'director'):
        return False
    if role == 'technician':
        return True
    # Пользователь, привязанный к профилю механика (Monteur)
    if getattr(user, 'worker_profile', None):
        return True
    return False

ALLOWED_UPLOAD_EXT = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt',
    '.mp4', '.webm', '.mov', '.mp3', '.wav',
    '.zip',
}


def upload_ext_allowed(filename):
    """True if filename extension is on the upload whitelist (.svg/.html excluded — XSS)."""
    if not filename:
        return False
    ext = os.path.splitext(secure_filename(filename))[1].lower()
    return ext in ALLOWED_UPLOAD_EXT

def save_uploaded_file(file, prefix=''):
    """Save upload with a unique filename to avoid collisions and stale browser cache."""
    if file and file.filename:
        original = secure_filename(file.filename)
        ext = os.path.splitext(original)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXT:
            return None
        import uuid as _uuid
        filename = f"{prefix}{_uuid.uuid4().hex[:16]}{ext}"
        from flask import current_app
        file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
        return filename
    return None

def translate_text(text, target_lang):
    if not text or target_lang == 'auto':
        return text
    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source='auto', target=target_lang)
        return translator.translate(text)
    except Exception:
        return text


def sanitize_like(query_str):
    """Escape LIKE wildcards in user input to prevent ILIKE injection."""
    if not query_str:
        return ''
    return query_str.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')



def safe_int(value, default=0):
    """Parse int from user input without crashing. Returns default on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_float(value, default=0.0):
    """Parse float from user input without crashing. Returns default on failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_date(value, fmt='%Y-%m-%d'):
    """Parse datetime from user input without crashing. Returns None on failure."""
    if not value or not str(value).strip():
        return None
    try:
        return datetime.strptime(str(value).strip(), fmt)
    except (ValueError, TypeError):
        return None


# ============================================================
# RECORD LOCKS — prevent concurrent editing (admin priority)
# ============================================================

LOCK_TTL_MINUTES = 5  # lock expires after 5 minutes of inactivity


def acquire_lock(record_type, record_id, user_id, user_name):
    """Try to acquire edit lock on a record. Returns (True, None) or (False, lock_info).
    Admin always wins: breaks any existing lock."""
    from models import RecordLock
    from flask_login import current_user
    now = datetime.utcnow()
    expires = now + timedelta(minutes=LOCK_TTL_MINUTES)

    # Clean expired locks for this record
    RecordLock.query.filter(
        RecordLock.record_type == record_type,
        RecordLock.record_id == record_id,
        RecordLock.expires_at < now
    ).delete()

    existing = RecordLock.query.filter(
        RecordLock.record_type == record_type,
        RecordLock.record_id == record_id
    ).first()

    is_admin = current_user.is_authenticated and getattr(current_user, 'role', '') == 'admin'

    if existing:
        if existing.user_id == user_id:
            # Refresh own lock
            existing.locked_at = now
            existing.expires_at = expires
            safe_commit()
            return True, None
        if is_admin:
            # Admin breaks any lock
            db.session.delete(existing)
            lock = RecordLock(
                record_type=record_type,
                record_id=record_id,
                user_id=user_id,
                user_name=user_name,
                locked_at=now,
                expires_at=expires
            )
            db.session.add(lock)
            safe_commit()
            return True, None
        # Another user has the lock
        return False, {
            'user_name': existing.user_name or 'Unknown',
            'locked_at': existing.locked_at.strftime('%H:%M') if existing.locked_at else '?',
            'expires_at': existing.expires_at.strftime('%H:%M') if existing.expires_at else '?'
        }

    # No lock — acquire it
    lock = RecordLock(
        record_type=record_type,
        record_id=record_id,
        user_id=user_id,
        user_name=user_name,
        locked_at=now,
        expires_at=expires
    )
    db.session.add(lock)
    safe_commit()
    return True, None


def release_lock(record_type, record_id, user_id):
    """Release a lock held by user_id."""
    from models import RecordLock
    RecordLock.query.filter(
        RecordLock.record_type == record_type,
        RecordLock.record_id == record_id,
        RecordLock.user_id == user_id
    ).delete()
    safe_commit()


def refresh_lock(record_type, record_id, user_id):
    """Extend lock TTL (called periodically from edit forms)."""
    from models import RecordLock
    now = datetime.utcnow()
    lock = RecordLock.query.filter(
        RecordLock.record_type == record_type,
        RecordLock.record_id == record_id,
        RecordLock.user_id == user_id
    ).first()
    if lock:
        lock.expires_at = now + timedelta(minutes=LOCK_TTL_MINUTES)
        safe_commit()

def add_work_report(entry_text):
    """Add entry to Work Report log from anywhere in the app"""
    from flask_login import current_user
    try:
        entry = WorkReportEntry(
            user_id=current_user.id if current_user.is_authenticated else None,
            entry=entry_text
        )
        db.session.add(entry)
        safe_commit()
    except Exception:
        db.session.rollback()


def check_tool_wear_notifications():
    """Notify users with tool_wear access when knife wear reaches 80%."""
    from models import ToolWear, User, Machine, Notification, GroupPermission, Verantwoordelijke, UserSectionAccess
    today = datetime.utcnow().date()
    tools = ToolWear.query.all()
    if not tools:
        return

    # Collect all user IDs that should receive tool_wear notifications
    notify_user_ids = set()
    for u in User.query.filter_by(is_active_user=True).all():
        if u.role == 'admin':
            notify_user_ids.add(u.id)
            continue
        has_access = False
        if u.person_id:
            person = Verantwoordelijke.query.get(u.person_id)
            if person and person.group_id:
                perm = GroupPermission.query.filter_by(group_id=person.group_id, section_key='tool_wear').first()
                if perm and perm.can_view:
                    has_access = True
        if not has_access:
            if UserSectionAccess.query.filter_by(user_id=u.id, section_key='tool_wear').first():
                has_access = True
        if not has_access and u.access_level == 'full':
            has_access = True
        if has_access:
            notify_user_ids.add(u.id)

    if not notify_user_ids:
        return

    # Pre-fetch existing tool_wear notifications to avoid duplicates
    existing_notifs = set()
    for n in Notification.query.filter_by(type='tool_wear', link='/tool-wear').all():
        existing_notifs.add(n.user_id)

    for t in tools:
        cycle = t.cycle_days or 14
        if t.last_replaced:
            days_since = (today - t.last_replaced).days
            wear = min(100.0, round((days_since / cycle) * 100, 1))
        else:
            wear = 100.0
        if wear < 80:
            continue
        msg = f"{t.machine_name}: {t.tool_name} — {wear}% {_('wear')}"
        for uid in notify_user_ids:
            if uid in existing_notifs:
                continue
            create_notification(uid, _('Knife replacement needed'), msg, 'tool_wear', '/tool-wear')
            existing_notifs.add(uid)


BELGIAN_HOLIDAYS_FIXED = {
    (1, 1): "Nieuwjaar / Jour de l'An",
    (5, 1): "Dag van de Arbeid / Fête du Travail",
    (7, 21): "Nationale Feestdag / Fête nationale",
    (8, 15): "O-L-V-Hemelvaart / Assomption",
    (11, 1): "Allerheiligen / Toussaint",
    (11, 11): "Wapenstilstand / Armistice",
    (12, 25): "Kerstmis / Noël",
}


def get_easter(year):
    """Calculate Easter Sunday using Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime(year, month, day).date()


def get_belgian_holidays(year):
    """Return dict of {date: name} for Belgian public holidays."""
    holidays = {}
    for (m, d), name in BELGIAN_HOLIDAYS_FIXED.items():
        holidays[datetime(year, m, d).date()] = name
    easter = get_easter(year)
    holidays[easter - timedelta(days=2)] = "Goede Vrijdag / Vendredi saint"
    holidays[easter] = "Pasen / Pâques"
    holidays[easter + timedelta(days=1)] = "Paasmaandag / Lundi de Pâques"
    holidays[easter + timedelta(days=39)] = "Hemelvaart / Ascension"
    holidays[easter + timedelta(days=50)] = "Pinkstermaandag / Lundi de Pentecôte"
    return holidays
