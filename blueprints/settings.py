"""
settings blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from config import SECTIONS_TREE
from models import (db, FactorySection, FaultReport, Machine, ResponsibleGroup, User, UserSectionAccess, Verantwoordelijke, UserActivityLog, AuditLog, FloorMapLine, SystemLog)
from utils import log_audit, role_required, safe_commit
from security import director_only
from sqlalchemy import func, case

bp = Blueprint('settings', __name__)


@bp.route('/settings/activity')
@login_required
@role_required('admin')
def settings_activity():
    """Подробный журнал: кто онлайн, что смотрит и делает."""
    from models import now_local
    now = now_local()
    online_window = now - timedelta(minutes=5)
    users = User.query.filter(User.is_active_user == True).order_by(User.display_name, User.username).all()

    rows = []
    for u in users:
        last = UserActivityLog.query.filter_by(user_id=u.id).order_by(UserActivityLog.id.desc()).first()
        last_act = UserActivityLog.query.filter_by(user_id=u.id, action='view').order_by(UserActivityLog.id.desc()).first()
        last_write = UserActivityLog.query.filter(
            UserActivityLog.user_id == u.id,
            UserActivityLog.method.in_(['POST', 'PUT', 'PATCH', 'DELETE'])
        ).order_by(UserActivityLog.id.desc()).first()
        is_online = bool(last and last.created_at and last.created_at >= online_window)
        rows.append({
            'user': u,
            'online': is_online,
            'last': last,
            'last_view': last_act,
            'last_write': last_write,
        })
    rows.sort(key=lambda r: (0 if r['online'] else 1,
                             -(r['last'].created_at.timestamp() if r['last'] and r['last'].created_at else 0)))

    # Лента действий
    feed = UserActivityLog.query.order_by(UserActivityLog.id.desc()).limit(120).all()
    audit = AuditLog.query.order_by(AuditLog.id.desc()).limit(40).all()
    return render_template('settings_activity.html', rows=rows, feed=feed, audit=audit, now=now)

@bp.route('/settings')
@login_required
@role_required('admin')
def settings():
    users = User.query.order_by(User.display_name).all()
    mechanics = User.query.filter_by(role='technician').order_by(User.display_name, User.username).all()
    sections = FactorySection.query.order_by(FactorySection.name).all()
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    responsible = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    machines = Machine.query.order_by(Machine.name).all()
    
    # Fault statistics for settings page
    faults_by_priority = {}
    for p in ['low', 'normal', 'high', 'critical']:
        faults_by_priority[p] = FaultReport.query.filter_by(priority=p).filter(
            FaultReport.status.in_(['open', 'accepted', 'in_progress', 'parts_ordered', 'waiting_parts', 'reopened'])
        ).count()
    
    faults_by_status = {}
    for s in ['open', 'accepted', 'in_progress', 'parts_ordered', 'waiting_parts', 'resolved', 'closed', 'reopened']:
        faults_by_status[s] = FaultReport.query.filter_by(status=s).count()
    
    # Top machines with faults — single aggregated query instead of N+1
    top_machines_faults = []
    fault_stats = db.session.query(
        FaultReport.machine_id,
        func.count().label('fault_count'),
        func.sum(case((FaultReport.status.in_(['open', 'accepted', 'in_progress']), 1), else_=0)).label('open_count'),
        func.sum(case((FaultReport.priority == 'critical', 1), else_=0)).label('critical_count')
    ).group_by(FaultReport.machine_id).all()
    machine_map = {m.id: m for m in machines}
    for mid, fc, oc, cc in fault_stats:
        m = machine_map.get(mid)
        if m and fc > 0:
            m.fault_count = fc
            m.open_count = oc or 0
            m.critical_count = cc or 0
            top_machines_faults.append(m)
    top_machines_faults.sort(key=lambda x: x.fault_count, reverse=True)
    
    # Responsible persons with access data
    responsible_list = [r for r in responsible if r.is_active]
    responsible_access = {}
    for r in responsible_list:
        if hasattr(r, 'allowed_sections') and r.allowed_sections:
            responsible_access[r.id] = [s.section_key for s in r.allowed_sections]
        else:
            responsible_access[r.id] = []

    # License + version for System tab
    from license import load_license, parse_license
    from config import APP_VERSION
    lic_key = load_license()
    lic_ok, lic_info = parse_license(lic_key)
    license_info = {
        'active': bool(lic_ok),
        'key': lic_key or '',
        'key_masked': (lic_key[:12] + '…' + lic_key[-6:]) if lic_key and len(lic_key) > 20 else (lic_key or '—'),
        'error': '' if lic_ok else (lic_info if isinstance(lic_info, str) else ''),
        'client': (lic_info or {}).get('client', '') if lic_ok else '',
        'seats': (lic_info or {}).get('seats', '') if lic_ok else '',
        'expire': (lic_info or {}).get('expire', '') if lic_ok else '',
        'version': APP_VERSION,
    }
    if license_info['expire']:
        try:
            exp_d = datetime.strptime(license_info['expire'], '%Y-%m-%d').date()
            license_info['days_left'] = (exp_d - datetime.now().date()).days
        except ValueError:
            license_info['days_left'] = None
    else:
        license_info['days_left'] = None

    return render_template('settings.html', users=users, mechanics=mechanics, sections=sections, groups=groups,
        responsible=responsible, responsible_list=responsible_list,
        responsible_access=responsible_access, machines=machines,
        faults_by_priority=faults_by_priority, faults_by_status=faults_by_status,
        top_machines_faults=top_machines_faults,
        sections_tree=SECTIONS_TREE, factory_sections=FactorySection.query.order_by(FactorySection.name).all(), all_machines=Machine.query.order_by(Machine.name).all(),
        license_info=license_info)


@bp.route('/settings/backup', methods=['POST'])
@login_required
@role_required('admin')
def settings_backup():
    """Create database backup"""
    from app import backup_database
    path = backup_database(force=True)
    if path:
        flash(_('Backup created: {}').format(os.path.basename(path)), 'success')
    else:
        flash(_('Backup failed'), 'error')
    return redirect(url_for('settings.settings'))


@bp.route('/settings/user/<int:user_id>/access', methods=['POST'])
@login_required
@role_required('admin')
def settings_user_access(user_id):
    u = User.query.get_or_404(user_id)
    data = request.get_json()
    # Update role
    if 'role' in data:
        u.role = data['role']
    # Update section access
    if 'sections' in data:
        u.allowed_sections = []
        for key in data['sections']:
            db.session.add(UserSectionAccess(user_id=u.id, section_key=key))
    # Update active status
    if 'is_active' in data:
        u.is_active_user = data['is_active']
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(request.referrer or '/')
    try:
        from security import invalidate_group_perm_cache
        invalidate_group_perm_cache(user_id)
    except Exception:
        pass
    log_audit('update', 'user_access', u.id, f'Updated access for {u.username}')
    return jsonify({'ok': True})


@bp.route('/settings/responsible-access', methods=['POST'])
@login_required
@role_required('admin')
def settings_responsible_access():
    """Save access rights for responsible persons (Verantwoordelijke)."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    for resp_id_str, sections in data.items():
        resp_id = int(resp_id_str)
        person = Verantwoordelijke.query.get(resp_id)
        if not person:
            continue
        # Store in UserSectionAccess with a special key format: "resp_{id}:{section}"
        # First, clear old entries for this responsible person
        UserSectionAccess.query.filter(
            UserSectionAccess.section_key.like(f'resp_{resp_id}:%')
        ).delete()
        # Add new entries
        for section_key in sections:
            db.session.add(UserSectionAccess(
                user_id=0,  # 0 = responsible person (not a real user)
                section_key=f'resp_{resp_id}:{section_key}'
            ))
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(request.referrer or '/')
    try:
        from security import invalidate_group_perm_cache
        invalidate_group_perm_cache()
    except Exception:
        pass
    log_audit('update', 'responsible_access', 0, f'Updated access for {len(data)} responsible persons')
    return jsonify({'ok': True})


@bp.route('/settings/section/<int:section_id>/update', methods=['POST'])
@login_required
@role_required('admin')
def settings_section_update(section_id):
    s = FactorySection.query.get_or_404(section_id)
    data = request.get_json()
    if 'name' in data:
        s.name = data['name']
    if 'color' in data:
        s.color = data['color']
    if 'floor_x' in data:
        s.floor_x = data['floor_x']
    if 'floor_y' in data:
        s.floor_y = data['floor_y']
    if 'width' in data:
        s.width = data['width']
    if 'height' in data:
        s.height = data['height']
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})


@bp.route('/settings/machine/<int:machine_id>/move-section', methods=['POST'])
@login_required
@role_required('admin')
def settings_machine_move_section(machine_id):
    """Move machine to a different section"""
    m = Machine.query.get_or_404(machine_id)
    data = request.get_json()
    new_section_id = data.get('section_id')
    if new_section_id:
        m.section_id = int(new_section_id)
    else:
        m.section_id = None
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(request.referrer or '/')
    log_audit('move', 'machine', m.id, f'{m.name} → section {new_section_id}')
    return jsonify({'ok': True})


@bp.route('/settings/machine/<int:machine_id>/assign-user', methods=['POST'])
@login_required
@role_required('admin')
def settings_machine_assign_user(machine_id):
    """Assign machine to a user"""
    m = Machine.query.get_or_404(machine_id)
    data = request.get_json()
    user_id = data.get('user_id')
    action = data.get('action', 'add')  # add or remove
    if action == 'add' and user_id:
        u = User.query.get(int(user_id))
        if u and m not in u.assigned_machines:
            u.assigned_machines.append(m)
    elif action == 'remove' and user_id:
        u = User.query.get(int(user_id))
        if u and m in u.assigned_machines:
            u.assigned_machines.remove(m)
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})


@bp.route('/api/map/lines', methods=['GET'])
@login_required
@role_required('admin', 'director', 'technician')
def map_lines_list():
    lines = FloorMapLine.query.order_by(FloorMapLine.id).all()
    return jsonify([{
        'id': ln.id, 'name': ln.name, 'description': ln.description or '',
        'color': ln.color, 'width': ln.width or 3,
        'label_x': ln.label_x, 'label_y': ln.label_y,
        'points': json.loads(ln.points_json or '[]'),
    } for ln in lines])


@bp.route('/api/map/lines', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def map_line_save():
    data = request.get_json() or {}
    pts = data.get('points') or []
    if not pts or len(pts) < 2:
        return jsonify({'error': 'Need at least 2 points'}), 400
    try:
        width = int(data.get('width') or 3)
    except (TypeError, ValueError):
        width = 3
    width = max(1, min(width, 20))
    ln = FloorMapLine(
        name=(data.get('name') or 'Line')[:120],
        description=(data.get('description') or '')[:2000],
        color=data.get('color') or '#2980b9',
        width=width,
        points_json=json.dumps(pts),
        created_by=current_user.id,
    )
    db.session.add(ln)
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'id': ln.id})


@bp.route('/api/map/lines/<int:line_id>/meta', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def map_line_meta(line_id):
    """Update name/description/color/width/label position of a drawn line."""
    ln = FloorMapLine.query.get_or_404(line_id)
    data = request.get_json() or {}
    if 'name' in data:
        ln.name = (data.get('name') or 'Line')[:120]
    if 'description' in data:
        ln.description = (data.get('description') or '')[:2000]
    if 'color' in data and data.get('color'):
        ln.color = data['color'][:20]
    if 'width' in data:
        try:
            ln.width = max(1, min(int(data.get('width') or 3), 20))
        except (TypeError, ValueError):
            pass
    if 'label_x' in data or 'label_y' in data:
        try:
            if data.get('label_x') is None or data.get('label_y') is None:
                ln.label_x = None
                ln.label_y = None
            else:
                ln.label_x = float(data['label_x'])
                ln.label_y = float(data['label_y'])
        except (TypeError, ValueError):
            pass
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({
        'ok': True, 'id': ln.id, 'name': ln.name,
        'description': ln.description or '',
        'label_x': ln.label_x, 'label_y': ln.label_y,
    })


@bp.route('/api/map/lines/<int:line_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only()
def map_line_delete(line_id):
    ln = FloorMapLine.query.get_or_404(line_id)
    db.session.delete(ln)
    if not safe_commit():
        return jsonify({'error': 'Delete failed'}), 500
    return jsonify({'ok': True})


@bp.route('/api/map/save-section', methods=['POST'])
@login_required
@role_required('admin')
def api_map_save_section():
    data = request.get_json()
    section_id = data.get('id')
    if section_id:
        s = FactorySection.query.get_or_404(section_id)
        s.name = data.get('name', s.name)
        s.description = data.get('description', s.description)
        s.section_type = data.get('section_type', s.section_type)
        s.color = data.get('color', s.color)
        s.floor_x = data.get('x', s.floor_x)
        s.floor_y = data.get('y', s.floor_y)
        s.width = data.get('w', s.width)
        s.height = data.get('h', s.height)
        resp_ids = data.get('responsible_ids', [])
        s.responsible_persons = [Verantwoordelijke.query.get(int(pid)) for pid in resp_ids if pid]
    else:
        s = FactorySection(
            name=data.get('name', 'New Section'),
            description=data.get('description', ''),
            section_type=data.get('section_type', 'workshop'),
            color=data.get('color', '#3498db'),
            floor_x=data.get('x', 10),
            floor_y=data.get('y', 10),
            width=data.get('w', 25),
            height=data.get('h', 25),
        )
        db.session.add(s)
        db.session.flush()
        resp_ids = data.get('responsible_ids', [])
        s.responsible_persons = [Verantwoordelijke.query.get(int(pid)) for pid in resp_ids if pid]
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'id': s.id})


@bp.route('/api/map/delete-section', methods=['POST'])
@login_required
@role_required('admin')
def api_map_delete_section():
    data = request.get_json()
    s = FactorySection.query.get_or_404(data['id'])
    for m in s.machines:
        m.section_id = None
    db.session.delete(s)
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})


@bp.route('/api/map/save-machine-pos', methods=['POST'])
@login_required
@role_required('admin')
def api_map_save_machine_pos():
    data = request.get_json() or {}
    m = Machine.query.get_or_404(data.get('machine_id') or data.get('id'))
    # 0.0 — валидная координата, не «пусто»
    try:
        if data.get('floor_x') is not None:
            m.floor_x = float(data['floor_x'])
        elif data.get('x') is not None:
            m.floor_x = float(data['x'])
        if data.get('floor_y') is not None:
            m.floor_y = float(data['floor_y'])
        elif data.get('y') is not None:
            m.floor_y = float(data['y'])
        if data.get('marker_size') is not None:
            m.marker_size = max(12, min(int(float(data['marker_size'])), 200))
        if data.get('marker_shape') in ('circle', 'square', 'rectangle', 'hexagon', 'diamond'):
            m.marker_shape = data['marker_shape']
    except (TypeError, ValueError):
        return jsonify({'error': 'Bad coordinates'}), 400
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'x': m.floor_x, 'y': m.floor_y, 'size': m.marker_size})


@bp.route('/api/map/assign-machine', methods=['POST'])
@login_required
@role_required('admin')
def api_map_assign_machine():
    data = request.get_json()
    m = Machine.query.get_or_404(data['machine_id'])
    m.section_id = data.get('section_id')
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})


@bp.route('/api/map/all')
@login_required
def api_map_all():
    sections = []
    for s in FactorySection.query.all():
        sections.append({
            'id': s.id, 'name': s.name, 'description': s.description or '',
            'type': s.section_type, 'color': s.color,
            'x': s.floor_x, 'y': s.floor_y, 'w': s.width, 'h': s.height,
            'responsible_ids': [p.id for p in s.responsible_persons],
            'responsible': ', '.join(p.naam for p in s.responsible_persons) if s.responsible_persons else None
        })
    machines = []
    from security import filter_machines_for_user
    for m in filter_machines_for_user(Machine.query).all():
        machines.append({
            'id': m.id, 'name': m.name, 'status': m.status,
            'type': m.machine_type or '', 'serial': m.serial_number or '',
            'x': m.floor_x, 'y': m.floor_y, 'section_id': m.section_id,
            'marker_size': m.marker_size or 30,
        })
    return jsonify({'sections': sections, 'machines': machines})


# ============================================================
# SYSTEM: license info + GitHub updates
# ============================================================

@bp.route('/settings/check-updates', methods=['POST'])
@login_required
@role_required('admin')
def settings_check_updates():
    """Проверить обновления на GitHub (без изменения файлов)."""
    try:
        from github_update import check_updates
        info = check_updates()
        return jsonify(info)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


@bp.route('/settings/run-update', methods=['POST'])
@login_required
@role_required('admin')
def settings_run_update():
    """Установить обновление кода с GitHub (данные не трогаются)."""
    try:
        from github_update import apply_update, restart_hint
        res = apply_update()
        res['restart_hint'] = restart_hint()
        log_audit('update', 'system', None, res.get('message', ''))
        return jsonify(res)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500


@bp.route('/settings/logs')
@login_required
@role_required('admin')
def settings_logs_page():
    """Страница логов ошибок (видна сразу)."""
    return render_template('settings_logs.html', logs=_load_error_logs())


@bp.route('/api/settings/logs')
@login_required
@role_required('admin')
def api_settings_logs():
    return jsonify({'ok': True, 'logs': _load_error_logs()})


def _load_error_logs(limit=40):
    """Последние ошибки: SystemLog ERROR/WARNING + хвост logs/errors.log."""
    rows = SystemLog.query.filter(
        SystemLog.level.in_(('ERROR', 'CRITICAL', 'WARNING', 'error', 'warn', 'warning'))
    ).order_by(SystemLog.id.desc()).limit(limit).all()
    logs = []
    for r in rows:
        logs.append({
            'when': r.created_at.strftime('%Y-%m-%d %H:%M:%S') if r.created_at else '',
            'level': r.level,
            'category': r.category or '',
            'message': (r.message or '')[:500],
            'source': r.source or '',
            'details': (r.details or '')[-1500:],
            'user': r.user.username if r.user else '',
        })
    # tail of file log (если есть)
    file_tail = []
    try:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'errors.log')
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            file_tail = [ln.rstrip() for ln in lines[-40:]]
    except Exception:
        pass
    try:
        from logs_util import read_log_tail
        file_tail = read_log_tail('app.log', 40)
        err_tail = read_log_tail('error.log', 20)
        file_tail = file_tail + (['--- error.log ---'] + err_tail if err_tail else [])
    except Exception:
        pass
    return {'db': logs, 'file_tail': file_tail}

@bp.route('/settings/logs/file/<filename>')
@login_required
@role_required('admin')
def settings_logs_file(filename):
    from flask import send_file
    import os
    fn = os.path.basename(filename)
    if fn not in ('app.log', 'error.log'):
        return jsonify({'error': 'not allowed'}), 400
    from logs_util import log_file_path
    path = log_file_path(fn)
    if not os.path.exists(path):
        return jsonify({'error': 'not found'}), 404
    return send_file(path, as_attachment=True, download_name=fn)


@bp.route('/api/settings/user/<int:user_id>/object-access', methods=['GET'])
@login_required
@role_required('admin')
def api_user_object_access_get(user_id):
    u = User.query.get_or_404(user_id)
    return jsonify({
        'ok': True,
        'section_ids': [s.id for s in (getattr(u, 'visible_sections', None) or [])],
        'hidden_machine_ids': [m.id for m in (getattr(u, 'hidden_machines', None) or [])],
    })


@bp.route('/api/settings/user/<int:user_id>/object-access', methods=['POST'])
@login_required
@role_required('admin')
def api_user_object_access(user_id):
    from models import FactorySection, Machine
    u = User.query.get_or_404(user_id)
    data = request.get_json() or {}
    sec_ids = [int(x) for x in (data.get('section_ids') or []) if str(x).isdigit()]
    hid_ids = [int(x) for x in (data.get('hidden_machine_ids') or []) if str(x).isdigit()]
    u.visible_sections = FactorySection.query.filter(FactorySection.id.in_(sec_ids)).all() if sec_ids else []
    u.hidden_machines = Machine.query.filter(Machine.id.in_(hid_ids)).all() if hid_ids else []
    if not safe_commit():
        return jsonify({'ok': False, 'error': 'save failed'}), 500
    log_audit('update', 'user_access', u.id, f'object-access sections={sec_ids} hidden={hid_ids}')
    return jsonify({'ok': True})


def _sms_allowed():
    return False  # SMS TEMPORARILY DISABLED

@bp.route('/api/settings/sms-stats')
@login_required
def api_sms_stats():
    if not _sms_allowed():
        return jsonify({'ok': True, 'today': 0, 'month': 0, 'left_month': 0, 'disabled': True})
    from sms_counter import sms_stats
    return jsonify(sms_stats())


@bp.route('/api/settings/sms-test', methods=['POST'])
@login_required
def sms_test_send():
    """Тестовое SMS на телефон текущего пользователя."""
    if not _sms_allowed():
        return jsonify({'ok': False, 'error': 'forbidden'}), 403
    from notify import send_sms, sms_configured
    phone = (getattr(current_user, 'phone', None) or '').strip()
    if not phone:
        return jsonify({'ok': False, 'error': 'No phone in profile'}), 400
    if not sms_configured():
        return jsonify({'ok': False, 'error': 'SMS not configured (.env SMS_ENABLED=1 + SMS_API_KEY)'}), 400
    ok = send_sms(phone, 'ProMaster: testbericht')
    return jsonify({'ok': bool(ok), 'phone': phone})


@bp.route('/api/settings/sms-users')
@login_required
def sms_users_list():
    """Список активных пользователей с телефонами (для модалки SMS)."""
    if not _sms_allowed():
        return jsonify({'ok': False, 'error': 'forbidden'}), 403
    users = User.query.filter(User.is_active_user == True).order_by(User.display_name, User.username).all()
    data = []
    for u in users:
        phone = (getattr(u, 'phone', None) or '').strip()
        data.append({
            'id': u.id,
            'name': u.display_name or u.username,
            'username': u.username,
            'role': u.role or '',
            'phone': phone,
        })
    return jsonify({'ok': True, 'users': data})


@bp.route('/api/settings/sms-send', methods=['POST'])
@login_required
def sms_send_bulk():
    """Отправить SMS выбранным пользователям (текст свой)."""
    if not _sms_allowed():
        return jsonify({'ok': False, 'error': 'forbidden'}), 403
    from notify import send_sms, sms_configured
    data = request.get_json() or {}
    user_ids = data.get('user_ids') or []
    text = (data.get('text') or '').strip()
    if not user_ids or not text:
        return jsonify({'ok': False, 'error': 'no recipients or empty text'}), 400
    if not sms_configured():
        return jsonify({'ok': False, 'error': 'SMS not configured'}), 400
    sent, errors = [], []
    for uid in user_ids:
        try:
            u = User.query.get(int(uid))
        except (TypeError, ValueError):
            continue
        if not u:
            continue
        phone = (getattr(u, 'phone', None) or '').strip()
        if not phone:
            errors.append((u.display_name or u.username) + ': no phone')
            continue
        if send_sms(phone, text[:160]):
            sent.append((u.display_name or u.username) + ' ' + phone)
        else:
            from notify import SMS_LAST_ERROR
            msg = (u.display_name or u.username) + ': ' + (SMS_LAST_ERROR or 'send failed')
            errors.append(msg[:200])
    log_audit('sms', 'bulk', 0, f'sent={len(sent)} errors={len(errors)} text={text[:60]}')
    return jsonify({'ok': True, 'sent': sent, 'errors': errors})
