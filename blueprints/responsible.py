"""
responsible blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from config import SECTIONS_LIST
from models import (db, FactorySection, GroupPermission, Machine, MaintenancePlan, Monteur, Opdracht, ResponsibleGroup, User, Verantwoordelijke)
from utils import log_audit, role_required, safe_commit, safe_int

bp = Blueprint('responsible', __name__)

@bp.route('/responsible')
@login_required
@role_required('admin', 'director')
def responsible_list():
    group_id = request.args.get('group', '')
    q = Verantwoordelijke.query
    if group_id:
        q = q.filter_by(group_id=int(group_id))
    else:
        # Exclude persons without a group (technicians moved to Technische dienst)
        q = q.filter(Verantwoordelijke.group_id.isnot(None))
    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(Verantwoordelijke.naam).paginate(page=page, per_page=25, error_out=False)
    verantwoordelijken = pagination.items
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    all_sections = FactorySection.query.order_by(FactorySection.name).all()
    all_machines = Machine.query.order_by(Machine.name).all()
    return render_template('responsible.html', pagination=pagination, verantwoordelijken=verantwoordelijken,
        groups=groups, group_filter=int(group_id) if group_id else None,
        all_sections=all_sections, all_machines=all_machines)


@bp.route('/responsible/phonebook')
@login_required
def phone_directory():
    persons = Verantwoordelijke.query.filter(
        db.or_(Verantwoordelijke.telefoon != '', Verantwoordelijke.internal_phone != '', Verantwoordelijke.email != '')
    ).filter(Verantwoordelijke.is_active == True).order_by(Verantwoordelijke.naam).all()
    workers = Monteur.query.filter(Monteur.actief == True).order_by(Monteur.naam).all()
    users = User.query.filter(User.is_active_user == True).order_by(User.display_name).all()
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    return render_template('phone_directory.html', persons=persons, workers=workers, users=users, groups=groups)


@bp.route('/responsible/groups')
@login_required
@role_required('admin', 'director')
def responsible_groups():
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    return render_template('responsible_groups.html', groups=groups)


@bp.route('/responsible/groups/new', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def responsible_group_new():
    if request.method == 'POST':
        g = ResponsibleGroup(name=request.form['name'], description=request.form.get('description', ''))
        db.session.add(g)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('responsible.responsible_groups'))
        flash(_('Group created'), 'success')
        return redirect(url_for('responsible.responsible_groups'))
    return render_template('responsible_group_form.html', group=None)


@bp.route('/responsible/groups/<int:group_id>')
@login_required
@role_required('admin', 'director')
def responsible_group_detail(group_id):
    g = ResponsibleGroup.query.get_or_404(group_id)
    return render_template('responsible_group_detail.html', group=g)


@bp.route('/responsible/groups/<int:group_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def responsible_group_edit(group_id):
    g = ResponsibleGroup.query.get_or_404(group_id)
    if request.method == 'POST':
        g.name = request.form['name']
        g.description = request.form.get('description', '')
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('responsible.responsible_groups'))
        flash(_('Group updated'), 'success')
        return redirect(url_for('responsible.responsible_groups'))
    return render_template('responsible_group_form.html', group=g)


@bp.route('/responsible/groups/<int:group_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def responsible_group_delete(group_id):
    g = ResponsibleGroup.query.get_or_404(group_id)
    for m in g.members:
        m.group_id = None
    db.session.delete(g)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('responsible.responsible_groups'))
    flash(_('Group deleted'), 'success')
    return redirect(url_for('responsible.responsible_groups'))


@bp.route('/responsible/groups/<int:group_id>/permissions', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def group_permissions(group_id):
    group = ResponsibleGroup.query.get_or_404(group_id)
    
    if request.method == 'POST':
        # Clear existing permissions
        GroupPermission.query.filter_by(group_id=group.id).delete()
        
        # Add new permissions from form
        for key, name, icon in SECTIONS_LIST:
            can_view = f'{key}_view' in request.form
            can_create = f'{key}_create' in request.form
            can_edit = f'{key}_edit' in request.form
            can_delete = f'{key}_delete' in request.form
            
            if can_view or can_create or can_edit or can_delete:
                perm = GroupPermission(
                    group_id=group.id,
                    section_key=key,
                    can_view=can_view,
                    can_create=can_create,
                    can_edit=can_edit,
                    can_delete=can_delete
                )
                db.session.add(perm)
        
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('responsible.responsible_groups'))
        flash(_('Permissions updated'), 'success')
        return redirect(url_for('responsible.responsible_groups'))
    
    # Get current permissions
    perms = {p.section_key: p for p in group.permissions}
    
    return render_template('group_permissions.html', group=group, sections=SECTIONS_LIST, perms=perms)


@bp.route('/responsible/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def responsible_new():
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        full_name = f"{first_name} {last_name}".strip()

        c = Verantwoordelijke(
            naam=full_name,
            telefoon=request.form.get('telefoon', ''),
            internal_phone=request.form.get('internal_phone', ''),
            work_phone=request.form.get('work_phone', ''),
            email=request.form.get('email', ''),
            username=request.form.get('username', '').strip() or None,
            group_id=safe_int(request.form.get('group_id')) or None,
            access_level=request.form.get('access_level', 'floor'),
            notities=request.form.get('notities', '')
        )
        # Set password if provided
        password = request.form.get('password', '').strip()
        if password:
            confirm = request.form.get('confirm_password', '')
            if password != confirm:
                flash(_('Passwords do not match'), 'error')
                return redirect(url_for('responsible.responsible_new'))
            c.set_password(password)
        db.session.add(c)
        db.session.flush()
        # Assign sections
        section_ids = request.form.getlist('sections')
        c.resp_sections = []
        for sid in section_ids:
            s = FactorySection.query.get(int(sid))
            if s:
                c.resp_sections.append(s)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('responsible.responsible_list'))
        flash(_('Responsible person added') + f': {c.naam}', 'success')
        return redirect(url_for('responsible.responsible_list'))
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    sections = FactorySection.query.order_by(FactorySection.name).all()
    return render_template('responsible_form.html', verantwoordelijke=None, groups=groups, sections=sections)


@bp.route('/responsible/<int:resp_id>')
@login_required
@role_required('admin', 'director')
def responsible_detail(resp_id):
    c = Verantwoordelijke.query.get_or_404(resp_id)
    # Machines are now linked via Contractor, not directly to Verantwoordelijke
    machines = []
    # Find linked users
    users = User.query.filter_by(person_id=c.id).all()
    # Find linked sections
    sections = FactorySection.query.filter(
        FactorySection.responsible_persons.any(Verantwoordelijke.id == c.id)
    ).all()
    # Find linked orders
    orders = Opdracht.query.filter_by(responsible_id=c.id).order_by(Opdracht.aangemaakt.desc()).limit(20).all()
    # Find linked workers (via group)
    workers = Monteur.query.filter_by(group_id=c.group_id).all() if c.group_id else []
    today = datetime.utcnow().date()
    return render_template('responsible_detail.html',
        person=c, machines=machines, users=users, sections=sections, orders=orders, workers=workers, today=today)


@bp.route('/responsible/<int:resp_id>/assign-group', methods=['POST'])
@login_required
@role_required('admin', 'director')
def responsible_assign_group(resp_id):
    c = Verantwoordelijke.query.get_or_404(resp_id)
    data = request.get_json()
    c.group_id = data.get('group_id')
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})


@bp.route('/responsible/<int:resp_id>/assign-sections', methods=['POST'])
@login_required
@role_required('admin', 'director')
def responsible_assign_sections(resp_id):
    c = Verantwoordelijke.query.get_or_404(resp_id)
    data = request.get_json()
    section_ids = data.get('section_ids', [])
    c.resp_sections = []
    for sid in section_ids:
        s = FactorySection.query.get(int(sid))
        if s:
            c.resp_sections.append(s)
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})


@bp.route('/responsible/<int:resp_id>/quick-edit', methods=['POST'])
@login_required
@role_required('admin', 'director')
def responsible_quick_edit(resp_id):
    c = Verantwoordelijke.query.get_or_404(resp_id)
    # Update name from first_name field (inline form sends just first_name)
    first_name = request.form.get('first_name', '').strip()
    if first_name:
        # Preserve last name if exists
        parts = (c.naam or '').split(' ', 1)
        last_name = parts[1] if len(parts) > 1 else ''
        c.naam = f"{first_name} {last_name}".strip()
    c.position = request.form.get('position', '').strip() or c.position
    c.telefoon = request.form.get('telefoon', '').strip()
    c.internal_phone = request.form.get('internal_phone', '').strip()
    c.work_phone = request.form.get('work_phone', '').strip()
    c.email = request.form.get('email', '').strip()
    # Update username (skip if unchanged)
    new_username = request.form.get('username', '').strip() or None
    old_username = c.username or None
    if new_username != old_username:
        if new_username:
            existing_v = Verantwoordelijke.query.filter_by(username=new_username).first()
            if existing_v and existing_v.id != c.id:
                flash(_('Username already taken by') + f': {existing_v.naam}', 'error')
                return redirect(url_for('responsible.responsible_list'))
            existing_u = User.query.filter_by(username=new_username).first()
            if existing_u:
                flash(_('Username already taken by user') + f': {existing_u.display_name or existing_u.username} ({existing_u.role})', 'error')
                return redirect(url_for('responsible.responsible_list'))
        c.username = new_username
    # Update access level
    if request.form.get('access_level'):
        c.access_level = request.form.get('access_level')
    # Update active status
    c.is_active = 'is_active' in request.form
    # Update password if provided
    password = request.form.get('password', '').strip()
    if password:
        confirm = request.form.get('confirm_password', '')
        if password != confirm:
            flash(_('Passwords do not match'), 'error')
            return redirect(url_for('responsible.responsible_list'))
        c.set_password(password)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('responsible.responsible_list'))
    flash(_('Responsible person updated'), 'success')
    return redirect(url_for('responsible.responsible_list'))


@bp.route('/responsible/<int:resp_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def responsible_delete(resp_id):
    c = Verantwoordelijke.query.get_or_404(resp_id)
    name = c.naam
    cid = c.id
    # Clear ALL FK references before delete
    c.resp_sections = []
    Machine.query.filter_by(responsible_person_id=cid).update({'responsible_person_id': None})
    MaintenancePlan.query.filter_by(responsible_person_id=cid).update({'responsible_person_id': None})
    db.session.execute(section_responsible.delete().where(section_responsible.c.person_id == cid))
    User.query.filter_by(person_id=cid).update({'person_id': None})
    # Orders referencing this person — reassign to first active responsible before delete
    from models import Opdracht
    open_orders = Opdracht.query.filter_by(responsible_id=cid).count()
    fallback_resp = Verantwoordelijke.query.filter(Verantwoordelijke.id != cid, Verantwoordelijke.is_active == True).first()
    if open_orders and not fallback_resp:
        flash(_('Cannot delete: this person still has work orders and no other active responsible exists. Reassign orders first.'), 'error')
        return redirect(url_for('responsible.responsible_list'))
    if fallback_resp:
        Opdracht.query.filter_by(responsible_id=cid).update({'responsible_id': fallback_resp.id}, synchronize_session=False)
    db.session.flush()
    db.session.delete(c)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('responsible.responsible_list'))
    log_audit('delete', 'responsible', cid, name)
    flash(_('Responsible person deleted') + f': {name}', 'success')
    return redirect(url_for('responsible.responsible_list'))


@bp.route('/responsible/quick-add', methods=['POST'])
@login_required
@role_required('admin', 'director')
def responsible_quick_add():
    naam = request.form.get('naam', '').strip()
    if naam:
        c = Verantwoordelijke(
            naam=naam,
            position=request.form.get('position', '').strip(),
            telefoon=request.form.get('telefoon', '').strip(),
            email=request.form.get('email', '').strip(),
            username=request.form.get('username', '').strip() or None,
            group_id=safe_int(request.form.get('group_id')) or None
        )
        password = request.form.get('password', '').strip()
        if password:
            c.set_password(password)
        db.session.add(c)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('responsible.responsible_list'))
        flash(_('Responsible person added'), 'success')
    return redirect(url_for('responsible.responsible_list'))


@bp.route('/responsible/<int:resp_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def responsible_edit(resp_id):
    c = Verantwoordelijke.query.get_or_404(resp_id)
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        c.naam = f"{first_name} {last_name}".strip()
        c.telefoon = request.form.get('telefoon', '')
        c.internal_phone = request.form.get('internal_phone', '')
        c.work_phone = request.form.get('work_phone', '')
        c.email = request.form.get('email', '')
        # Update username
        new_username = request.form.get('username', '').strip() or None
        old_username = c.username or None
        if new_username != old_username:
            if new_username:
                existing_v = Verantwoordelijke.query.filter_by(username=new_username).first()
                if existing_v and existing_v.id != c.id:
                    flash(_('Username already taken by') + f': {existing_v.naam}', 'error')
                    return redirect(url_for('responsible.responsible_edit', resp_id=c.id))
                existing_u = User.query.filter_by(username=new_username).first()
                if existing_u:
                    flash(_('Username already taken by user') + f': {existing_u.display_name or existing_u.username} ({existing_u.role})', 'error')
                    return redirect(url_for('responsible.responsible_edit', resp_id=c.id))
            c.username = new_username
        c.group_id = safe_int(request.form.get('group_id')) or None
        c.access_level = request.form.get('access_level', c.access_level or 'floor')
        c.is_active = 'is_active' in request.form
        c.notities = request.form.get('notities', '')
        # Update password if provided
        password = request.form.get('password', '').strip()
        if password:
            confirm = request.form.get('confirm_password', '')
            if password != confirm:
                flash(_('Passwords do not match'), 'error')
                return redirect(url_for('responsible.responsible_edit', resp_id=c.id))
            c.set_password(password)
        # Update sections
        section_ids = request.form.getlist('sections')
        c.resp_sections = []
        for sid in section_ids:
            s = FactorySection.query.get(int(sid))
            if s:
                c.resp_sections.append(s)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('responsible.responsible_list'))
        flash(_('Responsible person updated'), 'success')
        return redirect(url_for('responsible.responsible_list'))
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    sections = FactorySection.query.order_by(FactorySection.name).all()
    return render_template('responsible_form.html', verantwoordelijke=c, groups=groups, sections=sections)
