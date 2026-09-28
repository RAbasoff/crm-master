"""
workers blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from models import (db, Monteur, ResponsibleGroup, User)
from utils import log_audit, role_required, safe_commit, safe_date, safe_float, safe_int

bp = Blueprint('workers', __name__)

@bp.route('/workers')
@login_required
@role_required('admin', 'director')
def workers_list():
    return render_template('workers.html', workers=Monteur.query.order_by(Monteur.naam).all())


@bp.route('/workers/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def worker_new():
    if request.method == 'POST':
        w = Monteur(naam=request.form['naam'], telefoon=request.form.get('telefoon',''),
                    specialisatie=request.form.get('specialisatie',''),
                    tarief_per_uur=safe_float(request.form.get('tarief_per_uur'), 0),
                    hire_date=(d := safe_date(request.form.get('hire_date'))) and d.date() or None,
                    fire_date=(d := safe_date(request.form.get('fire_date'))) and d.date() or None,
                    user_id=safe_int(request.form.get('user_id')) or None,
                    group_id=safe_int(request.form.get('group_id')) or None)
        db.session.add(w)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('workers.workers_list'))
        flash(_('Worker added') + f': {w.naam}', 'success')
        return redirect(url_for('workers.workers_list'))
    users = User.query.filter(User.is_active_user == True, User.role.in_(['technician', 'user'])).order_by(User.display_name).all()
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    return render_template('worker_form.html', worker=None, users=users, groups=groups)


@bp.route('/workers/<int:worker_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def worker_edit(worker_id):
    w = Monteur.query.get_or_404(worker_id)
    if request.method == 'POST':
        w.naam = request.form['naam']; w.telefoon = request.form.get('telefoon','')
        w.specialisatie = request.form.get('specialisatie','')
        w.tarief_per_uur = safe_float(request.form.get('tarief_per_uur'), 0)
        w.hire_date = (d := safe_date(request.form.get('hire_date'))) and d.date() or w.hire_date
        w.fire_date = (d := safe_date(request.form.get('fire_date'))) and d.date() or None
        w.actief = 'actief' in request.form
        w.user_id = safe_int(request.form.get('user_id')) or None
        w.group_id = safe_int(request.form.get('group_id')) or None
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('workers.workers_list'))
        flash(_('Worker updated'), 'success')
        return redirect(url_for('workers.workers_list'))
    users = User.query.filter(User.is_active_user == True, User.role.in_(['technician', 'user'])).order_by(User.display_name).all()
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    return render_template('worker_form.html', worker=w, users=users, groups=groups)


@bp.route('/workers/<int:worker_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def worker_delete(worker_id):
    w = Monteur.query.get_or_404(worker_id)
    name = w.naam
    db.session.delete(w)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('workers.workers_list'))
    log_audit('delete', 'worker', worker_id, name)
    flash(_('Worker deleted') + f': {name}', 'success')
    return redirect(url_for('workers.workers_list'))


@bp.route('/workers/<int:worker_id>/create-user', methods=['POST'])
@login_required
@role_required('admin')
def worker_create_user(worker_id):
    """Create a User account with technician role for a worker."""
    w = Monteur.query.get_or_404(worker_id)
    if w.user_id:
        flash(_('Worker already linked to a user'), 'error')
        return redirect(url_for('workers.worker_edit', worker_id=worker_id))
    
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    
    if not username or not password:
        flash(_('Username and password required'), 'error')
        return redirect(url_for('workers.worker_edit', worker_id=worker_id))
    
    if User.query.filter_by(username=username).first():
        flash(_('Username already exists'), 'error')
        return redirect(url_for('workers.worker_edit', worker_id=worker_id))
    
    parts = (w.naam or '').split(None, 1)
    u = User(
        username=username,
        display_name=w.naam,
        first_name=parts[0] if parts else '',
        last_name=parts[1] if len(parts) > 1 else '',
        role='technician',
        is_active_user=True,
    )
    u.ensure_display_name()
    u.set_password(password)
    db.session.add(u)
    db.session.flush()
    
    w.user_id = u.id
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('workers.worker_edit', worker_id=worker_id))
    
    log_audit('create', 'user_from_worker', u.id, f'{w.naam} -> {username} (technician)')
    flash(_('Login created for') + f' {w.naam}: {username}', 'success')
    return redirect(url_for('workers.worker_edit', worker_id=worker_id))
