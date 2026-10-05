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
from models import (db, FactorySection, FaultReport, Machine, ResponsibleGroup, User, UserSectionAccess, Verantwoordelijke, UserActivityLog, AuditLog)
from utils import log_audit, role_required, safe_commit
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

    return render_template('settings.html', users=users, sections=sections, groups=groups,
        responsible=responsible, responsible_list=responsible_list,
        responsible_access=responsible_access, machines=machines,
        faults_by_priority=faults_by_priority, faults_by_status=faults_by_status,
        top_machines_faults=top_machines_faults,
        sections_tree=SECTIONS_TREE)


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
    data = request.get_json()
    m = Machine.query.get_or_404(data.get('machine_id') or data.get('id'))
    m.floor_x = data.get('floor_x') or data.get('x')
    m.floor_y = data.get('floor_y') or data.get('y')
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})


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
    for m in Machine.query.all():
        machines.append({
            'id': m.id, 'name': m.name, 'status': m.status,
            'type': m.machine_type or '', 'serial': m.serial_number or '',
            'x': m.floor_x, 'y': m.floor_y, 'section_id': m.section_id
        })
    return jsonify({'sections': sections, 'machines': machines})
