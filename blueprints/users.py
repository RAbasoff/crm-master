"""
users blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from config import Config, LANGUAGES, SECTION_KEYS
from models import (db, User, Machine, UserSectionAccess,
                    Verantwoordelijke, Monteur, AuditLog, CylinderLog, CylinderOrder,
                    FactorySection, FaultReport, Invoice, MachineDocument, MachinePart,
                    MaintenancePlan, MaintenanceRecord, Message, Notification,
                    PartMaintenanceLog, PurchaseRequest, TechnicalWorkOrder, TimeEntry,
                    Vacation, WeekendShift, WorkReport, WorkSchedule)
from utils import create_notification, log_audit, log_system, role_required, safe_commit, safe_date, safe_float, safe_int, sanitize_like

bp = Blueprint('users', __name__)

@bp.route('/users')
@login_required
@role_required('admin')
def users_list():
    users = User.query.all()
    machines = Machine.query.order_by(Machine.name).all()
    return render_template('users.html', users=users, section_keys=SECTION_KEYS, machines=machines)


@bp.route('/users/<int:user_id>')
@login_required
@role_required('admin', 'director')
def user_cabinet(user_id):
    u = User.query.get_or_404(user_id)
    return render_template('user_cabinet.html', user=u)


@bp.route('/users/<int:user_id>/change-password', methods=['POST'])
@login_required
@role_required('admin')
def user_change_password(user_id):
    u = User.query.get_or_404(user_id)
    new_pass = request.form.get('new_password')
    confirm_pass = request.form.get('confirm_password')
    if not new_pass:
        flash(_('Password cannot be empty'), 'error')
    elif new_pass != confirm_pass:
        flash(_('Passwords do not match'), 'error')
    else:
        u.set_password(new_pass)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('users.user_cabinet', user_id=u.id))
        flash(_('Password changed for %(username)s', username=u.username), 'success')
    return redirect(url_for('users.user_cabinet', user_id=u.id))


@bp.route('/users/<int:user_id>/cabinet-update', methods=['POST'])
@login_required
@role_required('admin')
def user_cabinet_update(user_id):
    u = User.query.get_or_404(user_id)

    # Change username
    new_username = request.form.get('username', '').strip()
    if new_username and new_username != u.username:
        existing = User.query.filter_by(username=new_username).first()
        if existing:
            flash(_('Username already taken'), 'error')
            return redirect(url_for('users.user_cabinet', user_id=u.id))
        u.username = new_username

    u.first_name = request.form.get('first_name', u.first_name)
    u.last_name = request.form.get('last_name', u.last_name)
    u.display_name = request.form.get('display_name', u.display_name)
    u.phone = request.form.get('phone', u.phone)
    u.role = request.form.get('role', u.role)
    u.access_level = request.form.get('access_level', u.access_level)
    u.is_active_user = 'is_active' in request.form
    u.hire_date = (d := safe_date(request.form.get('hire_date'))) and d.date() or u.hire_date
    u.fire_date = (d := safe_date(request.form.get('fire_date'))) and d.date() or None
    # Password change (optional)
    new_pass = request.form.get('new_password')
    if new_pass:
        confirm_pass = request.form.get('confirm_password')
        if new_pass != confirm_pass:
            flash(_('Passwords do not match'), 'error')
            return redirect(url_for('users.users_list'))
        u.set_password(new_pass)
    # Update allowed sections
    UserSectionAccess.query.filter_by(user_id=u.id).delete()
    for key in request.form.getlist('allowed_sections'):
        db.session.add(UserSectionAccess(user_id=u.id, section_key=key))
    # Update assigned machines
    u.assigned_machines = []
    for mid in request.form.getlist('machines'):
        m = Machine.query.get(int(mid))
        if m:
            u.assigned_machines.append(m)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('users.users_list'))
    flash(_('User updated'), 'success')
    return redirect(url_for('users.users_list'))


@bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def user_delete(user_id):
    if user_id == current_user.id:
        flash(_('Cannot delete yourself'), 'error')
        return redirect(url_for('users.user_cabinet', user_id=user_id))
    u = User.query.get_or_404(user_id)
    username = u.username
    uid = u.id
    # Delete NOT NULL FK rows
    Message.query.filter((Message.sender_id == uid) | (Message.receiver_id == uid)).delete(synchronize_session=False)
    Notification.query.filter_by(user_id=uid).delete()
    TimeEntry.query.filter_by(user_id=uid).delete()
    Vacation.query.filter_by(user_id=uid).delete()
    WorkSchedule.query.filter_by(user_id=uid).delete()
    WorkReport.query.filter_by(technician_id=uid).delete()
    PurchaseRequest.query.filter_by(requester_id=uid).delete()
    # Null out nullable FKs
    FactorySection.query.filter_by(responsible_user_id=uid).update({'responsible_user_id': None})
    Machine.query.filter_by(responsible_user_id=uid).update({'responsible_user_id': None})
    MachinePart.query.filter_by(responsible_user_id=uid).update({'responsible_user_id': None})
    PartMaintenanceLog.query.filter_by(performed_by=uid).update({'performed_by': None})
    MachineDocument.query.filter_by(uploaded_by=uid).update({'uploaded_by': None})
    MaintenanceRecord.query.filter_by(performed_by=uid).update({'performed_by': None})
    MaintenancePlan.query.filter_by(created_by=uid).update({'created_by': None})
    MaintenancePlan.query.filter_by(responsible_user_id=uid).update({'responsible_user_id': None})
    Monteur.query.filter_by(user_id=uid).update({'user_id': None})
    Invoice.query.filter_by(signed_by=uid).update({'signed_by': None})
    Invoice.query.filter_by(created_by=uid).update({'created_by': None})
    FaultReport.query.filter_by(reporter_id=uid).delete()
    FaultReport.query.filter_by(technician_id=uid).update({'technician_id': None})
    PurchaseRequest.query.filter_by(reviewer_id=uid).update({'reviewer_id': None})
    PurchaseRequest.query.filter_by(requester_id=uid).delete()
    TechnicalWorkOrder.query.filter_by(created_by=uid).update({'created_by': None})
    AuditLog.query.filter_by(user_id=uid).update({'user_id': None})
    TimeEntry.query.filter_by(approved_by=uid).update({'approved_by': None})
    Vacation.query.filter_by(approved_by=uid).update({'approved_by': None})
    CylinderLog.query.filter_by(performed_by=uid).update({'performed_by': None})
    CylinderOrder.query.filter_by(ordered_by=uid).update({'ordered_by': None})
    WeekendShift.query.filter_by(created_by=uid).update({'created_by': None})
    # Association tables
    db.session.execute(user_machine.delete().where(user_machine.c.user_id == uid))
    db.session.execute(fault_technicians.delete().where(fault_technicians.c.technician_id == uid))
    db.session.execute(two_workers.delete().where(two_workers.c.worker_id == uid))
    # UserSectionAccess with cascade
    UserSectionAccess.query.filter_by(user_id=uid).delete()
    # Delete user
    db.session.delete(u)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('index'))
    flash(_('User %(username)s deleted', username=username), 'success')
    return redirect(url_for('index'))


@bp.route('/users/new', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def user_new():
    if request.method == 'POST':
        u = User(
            username=request.form['username'],
            first_name=request.form.get('first_name', ''),
            last_name=request.form.get('last_name', ''),
            display_name=request.form.get('display_name', ''),
            phone=request.form.get('phone', ''),
            role=request.form.get('role', 'user'),
            access_level=request.form.get('access_level', 'full'),
            person_id=safe_int(request.form.get('person_id')) or None,
            hire_date=(d := safe_date(request.form.get('hire_date'))) and d.date() or None
        )
        u.set_password(request.form['password'])
        db.session.add(u)
        db.session.flush()
        # Save allowed sections
        for key in request.form.getlist('allowed_sections'):
            db.session.add(UserSectionAccess(user_id=u.id, section_key=key))
        # Save assigned machines
        for mid in request.form.getlist('machines'):
            m = Machine.query.get(int(mid))
            if m:
                u.assigned_machines.append(m)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('users.users_list'))
        flash(_('User created'), 'success')
        return redirect(url_for('users.users_list'))
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    return render_template('user_form.html', user=None, machines=Machine.query.all(), section_keys=SECTION_KEYS, verantwoordelijken=verantwoordelijken)


@bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def user_edit(user_id):
    u = User.query.get_or_404(user_id)
    if request.method == 'POST':
        new_username = request.form.get('username', '').strip()
        if new_username and new_username != u.username:
            # Check if username already exists
            existing = User.query.filter_by(username=new_username).first()
            if existing:
                flash(_('Username already exists'), 'error')
                return redirect(url_for('users.user_edit', user_id=user_id))
            u.username = new_username
        u.first_name = request.form.get('first_name', u.first_name)
        u.last_name = request.form.get('last_name', u.last_name)
        u.display_name = request.form.get('display_name', u.display_name)
        u.phone = request.form.get('phone', u.phone or '')
        u.role = request.form.get('role', u.role)
        u.access_level = request.form.get('access_level', u.access_level)
        u.person_id = safe_int(request.form.get('person_id')) or None
        u.is_active_user = 'is_active' in request.form
        u.hire_date = (d := safe_date(request.form.get('hire_date'))) and d.date() or u.hire_date
        u.fire_date = (d := safe_date(request.form.get('fire_date'))) and d.date() or None
        new_pass = request.form.get('password')
        if new_pass:
            u.set_password(new_pass)
        # Update allowed sections
        UserSectionAccess.query.filter_by(user_id=u.id).delete()
        for key in request.form.getlist('allowed_sections'):
            db.session.add(UserSectionAccess(user_id=u.id, section_key=key))
        # Update assigned machines
        u.assigned_machines = []
        for mid in request.form.getlist('machines'):
            m = Machine.query.get(int(mid))
            if m:
                u.assigned_machines.append(m)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('users.users_list'))
        flash(_('User updated'), 'success')
        return redirect(url_for('users.users_list'))
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    return render_template('user_form.html', user=u, machines=Machine.query.all(), section_keys=SECTION_KEYS, verantwoordelijken=verantwoordelijken)
