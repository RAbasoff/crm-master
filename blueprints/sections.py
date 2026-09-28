"""
sections blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from models import (db, FactorySection, Machine, User, Verantwoordelijke)
from utils import role_required, safe_commit, safe_float, safe_int

bp = Blueprint('sections', __name__)

@bp.route('/sections')
@login_required
def sections_list():
    sections = FactorySection.query.all()
    return render_template('sections.html', sections=sections)


@bp.route('/sections/new', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def section_new():
    if request.method == 'POST':
        s = FactorySection(
            name=request.form['name'],
            description=request.form.get('description', ''),
            section_type=request.form.get('section_type', 'workshop'),
            color=request.form.get('color', '#3498db'),
            floor_x=safe_float(request.form.get('floor_x'), 10),
            floor_y=safe_float(request.form.get('floor_y'), 10),
            width=safe_float(request.form.get('width'), 25),
            height=safe_float(request.form.get('height'), 25),
        )
        s.responsible_user_id = safe_int(request.form.get('responsible_user_id')) or None
        person_ids = request.form.getlist('responsible_person_ids')
        s.responsible_persons = [Verantwoordelijke.query.get(int(pid)) for pid in person_ids if pid]
        db.session.add(s)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('sections.sections_list'))
        flash(_('Section created'), 'success')
        return redirect(url_for('sections.sections_list'))
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    users = User.query.filter(User.is_active_user == True, User.role.in_(['admin', 'technician', 'director'])).all()
    return render_template('section_form.html', section=None, verantwoordelijken=verantwoordelijken, users=users)


@bp.route('/sections/<int:section_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def section_edit(section_id):
    s = FactorySection.query.get_or_404(section_id)
    if request.method == 'POST':
        s.name = request.form['name']
        s.description = request.form.get('description', '')
        s.section_type = request.form.get('section_type', s.section_type)
        s.color = request.form.get('color', s.color)
        s.floor_x = safe_float(request.form.get('floor_x'), s.floor_x)
        s.floor_y = safe_float(request.form.get('floor_y'), s.floor_y)
        s.width = safe_float(request.form.get('width'), s.width)
        s.height = safe_float(request.form.get('height'), s.height)
        s.responsible_user_id = safe_int(request.form.get('responsible_user_id')) or None
        person_ids = request.form.getlist('responsible_person_ids')
        s.responsible_persons = [Verantwoordelijke.query.get(int(pid)) for pid in person_ids if pid]
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('sections.sections_list'))
        flash(_('Section updated'), 'success')
        return redirect(url_for('sections.sections_list'))
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    users = User.query.filter(User.is_active_user == True, User.role.in_(['admin', 'technician', 'director'])).all()
    return render_template('section_form.html', section=s, verantwoordelijken=verantwoordelijken, users=users)


@bp.route('/sections/<int:section_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def section_delete(section_id):
    s = FactorySection.query.get_or_404(section_id)
    for m in s.machines:
        m.section_id = None
    db.session.delete(s)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('sections.sections_list'))
    flash(_('Section deleted'), 'success')
    return redirect(url_for('sections.sections_list'))


@bp.route('/api/sections/<int:section_id>')
@login_required
def api_section_info(section_id):
    s = FactorySection.query.get_or_404(section_id)
    machines_data = []
    for m in s.machines:
        machines_data.append({
            'id': m.id, 'name': m.name, 'status': m.status,
            'type': m.machine_type or '', 'serial': m.serial_number or ''
        })
    return jsonify({
        'id': s.id, 'name': s.name, 'description': s.description or '',
        'type': s.section_type, 'color': s.color,
        'responsible': ', '.join(p.naam for p in s.responsible_persons) if s.responsible_persons else None,
        'responsible_ids': [p.id for p in s.responsible_persons],
        'machines': machines_data,
        'machines_count': len(machines_data),
        'active_count': len([m for m in s.machines if m.status == 'active']),
        'broken_count': len([m for m in s.machines if m.status == 'broken'])
    })


@bp.route('/sections/<int:section_id>/assign-machine', methods=['POST'])
@login_required
@role_required('admin', 'director')
def section_assign_machine(section_id):
    section = FactorySection.query.get_or_404(section_id)
    data = request.get_json()
    machine_ids = data.get('machine_ids', [])
    if not machine_ids:
        single = data.get('machine_id')
        if single:
            machine_ids = [int(single)]
    if not machine_ids:
        return jsonify({'error': 'No machines selected'}), 400
    assigned = 0
    for mid in machine_ids:
        machine = Machine.query.get(int(mid))
        if machine:
            machine.section_id = section_id
            assigned += 1
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'assigned': assigned})


@bp.route('/sections/<int:section_id>/remove-machine/<int:machine_id>', methods=['POST'])
@login_required
@role_required('admin', 'director')
def section_remove_machine(section_id, machine_id):
    machine = Machine.query.get_or_404(machine_id)
    if machine.section_id == section_id:
        machine.section_id = None
        if not safe_commit():
            return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})
