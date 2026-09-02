"""
Monteurs blueprint — technician list, permissions, login/password management
"""
from flask import Blueprint, request, redirect, url_for, flash, render_template
from flask_login import login_required
from flask_babel import gettext as _

from models import db, User, Machine, UserSectionAccess
from config import SECTION_KEYS
from utils import role_required

bp = Blueprint('monteurs', __name__)


@bp.route('/monteurs')
@login_required
@role_required('admin')
def monteurs_list():
    monteurs = User.query.filter_by(role='technician').order_by(User.display_name).all()
    machines = Machine.query.order_by(Machine.name).all()
    return render_template('monteurs.html', monteurs=monteurs, machines=machines, section_keys=SECTION_KEYS)


@bp.route('/monteurs/<int:user_id>/permissions', methods=['POST'])
@login_required
@role_required('admin')
def monteur_permissions(user_id):
    u = User.query.get_or_404(user_id)
    if u.role != 'technician':
        flash(_('Only technicians can be edited here'), 'error')
        return redirect(url_for('monteurs.monteurs_list'))

    # Change username
    new_username = request.form.get('username', '').strip()
    if new_username and new_username != u.username:
        existing = User.query.filter_by(username=new_username).first()
        if existing:
            flash(_('Username already taken'), 'error')
            return redirect(url_for('monteurs.monteurs_list'))
        u.username = new_username

    u.access_level = request.form.get('access_level', 'full')
    UserSectionAccess.query.filter_by(user_id=u.id).delete()
    for key in request.form.getlist('allowed_sections'):
        db.session.add(UserSectionAccess(user_id=u.id, section_key=key))
    u.assigned_machines = []
    for mid in request.form.getlist('machines'):
        m = Machine.query.get(int(mid))
        if m:
            u.assigned_machines.append(m)
    u.is_active_user = 'is_active' in request.form
    new_pass = request.form.get('password')
    if new_pass:
        u.set_password(new_pass)
    db.session.commit()
    flash(_('Permissions updated for') + ' ' + (u.display_name or u.username), 'success')
    return redirect(url_for('monteurs.monteurs_list'))
