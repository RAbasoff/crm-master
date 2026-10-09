"""Roles, group permissions, and access decorators."""
from functools import wraps
from flask import flash, redirect, url_for, request
from flask_login import current_user
from flask_babel import gettext as _
from models import GroupPermission, ResponsibleGroup, Verantwoordelijke

# Role hierarchy: admin > director > technician > user
# admin: full access, can modify program settings
# director/technician/user: access controlled by allowed_sections and group permissions

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


# ── Права механика (role=technician) ─────────────────────────────
# Механик НЕ видит модули «Аналитика» и «Система».

MECHANIC_DENIED_PREFIXES = (
    'reports.', 'stats.', 'archive.',          # Аналитика
    'settings.', 'users.', 'audit_log.',       # Система
    'export.', 'invoices.',                    # финансы
    'purchase.',                               # закупки — только начальство
)

MECHANIC_DENIED_PATHS = (
    '/reports', '/stats', '/archive', '/work-report',
    '/settings', '/users', '/audit-log',
    '/invoices', '/export',
    '/purchase-requests',
)


def is_mechanic(user=None):
    u = user or current_user
    return bool(u and getattr(u, 'is_authenticated', False) and getattr(u, 'role', '') == 'technician')


def is_floor_user(user=None):
    """Ответственный «цеховой» пользователь (Bartek, Hqshem, Pablo, Safa…).

    Видит только: SToringen, TWO, Communicatie + карта своих участков.
    """
    u = user or current_user
    if not (u and getattr(u, 'is_authenticated', False)):
        return False
    if getattr(u, 'role', '') in ('admin', 'director', 'technician'):
        return False
    # ResponsibleAuth (person login) — всегда узкое меню
    if hasattr(u, '_person'):
        return True
    # User с ролью user / responsible и доступом floor
    if getattr(u, 'role', '') in ('user', 'responsible') and getattr(u, 'access_level', '') in ('floor', 'limited', 'partial'):
        return True
    return False


def is_privileged(user=None):
    """Админ / начальник ТС / главный механик (в будущем)."""
    u = user or current_user
    return bool(u and getattr(u, 'is_authenticated', False) and u.has_role('admin', 'director'))


def mechanic_denied_endpoint(endpoint):
    """True, если механику нельзя на этот endpoint."""
    if not endpoint:
        return False
    return any(endpoint.startswith(p) for p in MECHANIC_DENIED_PREFIXES)


def enforce_mechanic_access():
    """Вызвать в before_request: 403/redirect для механика на аналитике и системе."""
    if not current_user.is_authenticated:
        return None
    if not is_mechanic(current_user):
        return None
    endpoint = getattr(request, 'endpoint', None) or ''
    path = getattr(request, 'path', '') or ''
    if mechanic_denied_endpoint(endpoint):
        flash(_('ДОСТУП ЗАКРЫТ. НЕ ДОСТАТОЧНО ПРАВ.'), 'error')
        return redirect(url_for('index'))
    for p in MECHANIC_DENIED_PATHS:
        if path == p or path.startswith(p + '/'):
            flash(_('ДОСТУП ЗАКРЫТ. НЕ ДОСТАТОЧНО ПРАВ.'), 'error')
            return redirect(url_for('index'))
    return None
