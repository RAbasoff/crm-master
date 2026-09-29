from datetime import datetime, timedelta
from functools import wraps
from flask import flash, redirect, url_for, request, session
from flask_login import current_user
from flask_babel import gettext as _
from models import db, Notification, AuditLog, GroupPermission, ResponsibleGroup, Verantwoordelijke, UserActivityLog, SystemLog, WorkReportEntry, WarehouseGroup
import os
from werkzeug.utils import secure_filename
import time as _time

# ── Schema / migrations live in schema.py (kept import-compatible) ──
from schema import (
    SCHEMA_VERSION,
    ensure_schema,
    run_migrations,
    run_data_migrations,
    safe_commit,
    _migrations_already_applied,
    _stamp_migrations_applied,
)

# Role hierarchy: admin > director > technician > user
# admin: full access, can modify program settings
# director/technician/user: access controlled by allowed_sections and group permissions

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
            if not current_user.has_role(*roles):
                flash(_('ДОСТУП ЗАКРЫТ. НЕ ДОСТАТОЧНО ПРАВ.'), 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_user_group_permissions(user):
    """Get permissions from user's group (via person_id link or role fallback)"""
    if user.person_id:
        person = Verantwoordelijke.query.get(user.person_id)
        if person and person.group_id:
            perms = GroupPermission.query.filter_by(group_id=person.group_id).all()
            return {p.section_key: p for p in perms}
    # Fallback: look up group by role name (e.g. role='technician' -> group 'Technician')
    if user.role and user.role != 'admin':
        group = ResponsibleGroup.query.filter_by(access_level=user.role).first()
        if group:
            perms = GroupPermission.query.filter_by(group_id=group.id).all()
            return {p.section_key: p for p in perms}
    return {}

def user_has_section_access(section_key, action='view'):
    # Admin always has full access
    if current_user.role == 'admin':
        return True
    # Check group permissions first
    group_perms = get_user_group_permissions(current_user)
    if section_key in group_perms:
        perm = group_perms[section_key]
        if action == 'view': return perm.can_view
        if action == 'create': return perm.can_create
        if action == 'edit': return perm.can_edit
        if action == 'delete': return perm.can_delete
        return perm.can_view
    # Section not in group permissions — check individual allowed_sections
    # (allowed_sections grant full view+create+edit on top of group permissions)
    if any(s.section_key == section_key for s in current_user.allowed_sections):
        if action in ('view', 'create', 'edit'):
            return True
        return False
    # No group perms and no individual override
    if group_perms:
        return False  # has a group but section not in group or allowed_sections
    # Fallback to access_level (only when no group permissions exist)
    if current_user.access_level == 'full':
        return True
    if current_user.access_level == 'limited':
        return False
    return any(s.section_key == section_key for s in current_user.allowed_sections)

def section_access_required(section_key, action='view'):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
            if not user_has_section_access(section_key, action):
                flash(_('ДОСТУП ЗАКРЫТ. НЕ ДОСТАТОЧНО ПРАВ.'), 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def create_notification(user_id, title, message, ntype='info', link=None):
    n = Notification(user_id=user_id, title=title, message=message, type=ntype, link=link)
    db.session.add(n)
    safe_commit()

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

def genereer_nummer():
    from models import Opdracht
    vandaag = datetime.utcnow()
    prefix = vandaag.strftime('%Y%m%d')
    laatste = Opdracht.query.filter(Opdracht.nummer.like(f'WO-{prefix}-%')).order_by(Opdracht.id.desc()).first()
    if laatste:
        num = int(laatste.nummer.split('-')[2]) + 1
    else:
        num = 1
    return f'WO-{prefix}-{num:04d}'

def date_plus_days(d, days):
    if d and days:
        return d + timedelta(days=days)
    return None

ALLOWED_UPLOAD_EXT = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt',
    '.mp4', '.webm', '.mov', '.mp3', '.wav',
    '.zip',
}

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


def find_pdf_font():
    """Locate a Unicode TTF for fpdf2. Prefers bundled DejaVu, then system fonts."""
    root = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.join(root, 'static', 'fonts', 'DejaVuSans.ttf'),
        os.path.join(root, 'static', 'fonts', 'arial.ttf'),
        r'C:\Windows\Fonts\dejavu\DejaVuSans.ttf',
        r'C:\Windows\Fonts\arial.ttf',
        r'C:\Windows\Fonts\calibri.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
        '/home/rabasoff/.local/share/fonts/DejaVuSans.ttf',
    ):
        if os.path.exists(cand):
            return cand
    return None


def ensure_fpdf():
    """Import fpdf2. Order: system package → pip install → vendor_fpdf (Helvetica-only)."""
    import sys
    import subprocess

    def _try_import():
        try:
            import fpdf
            # Prefer the real library, not our vendor copy, when both exist
            return fpdf
        except ImportError:
            return None

    # already imported?
    mod = _try_import()
    if mod is not None:
        return mod

    root = os.path.dirname(os.path.abspath(__file__))
    vendor = os.path.join(root, '.vendor')

    # 1) install into current interpreter (venv: `pip install fpdf2` — NOT --user)
    # 2) install into project .vendor
    last_err = ''
    for args in (
        [sys.executable, '-m', 'pip', 'install', '--no-cache-dir', 'fpdf2'],
        [sys.executable, '-m', 'pip', 'install', '--no-cache-dir', '--target', vendor, 'fpdf2'],
    ):
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=180)
            if p.returncode != 0:
                last_err = f'{" ".join(args)} rc={p.returncode}: {(p.stderr or p.stdout or "")[-400:]}'
                print(f'ensure_fpdf: {last_err}')
                continue
            if '--target' in args and vendor not in sys.path:
                sys.path.insert(0, vendor)
            for m in list(sys.modules):
                if m == 'fpdf' or m.startswith('fpdf.'):
                    del sys.modules[m]
            mod = _try_import()
            if mod is not None:
                print(f'ensure_fpdf: installed fpdf2 via {" ".join(args)}')
                return mod
        except Exception as e:
            last_err = f'{" ".join(args)}: {e}'
            print(f'ensure_fpdf: {last_err}')

    # 3) bundled copy in vendor_fpdf/ (always present in the repo)
    vendor_fp = os.path.join(root, 'vendor_fpdf')
    if os.path.isdir(vendor_fp) and vendor_fp not in sys.path:
        sys.path.insert(0, vendor_fp)
    for m in list(sys.modules):
        if m == 'fpdf' or m.startswith('fpdf.'):
            del sys.modules[m]
    mod = _try_import()
    if mod is not None:
        print(f'ensure_fpdf: using bundled vendor_fpdf ({mod.__file__})')
        return mod

    raise ImportError(f'fpdf2 is not installed and auto-install failed. {last_err}. '
                      f'On PA console run: pip install fpdf2')


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
