"""
Tool wear blueprint — knife/tool wear tracking, warnings API
"""
from datetime import datetime

from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import db, ToolWear, Notification
from utils import (role_required, safe_commit, safe_int, check_tool_wear_notifications,
                   user_has_section_access, log_system)

bp = Blueprint('tool_wear', __name__)


@bp.route('/tool-wear')
@login_required
def tool_wear_page():
    tools = ToolWear.query.order_by(ToolWear.machine_name).all()
    default_machines = ['VULBUS 1', 'VULBUS 2', 'Lift Seydelmann', 'Станок BOLDT']
    if not tools:
        for m in default_machines:
            t = ToolWear(machine_name=m, tool_name='Ножи / Фреза', cycle_days=14,
                         last_replaced=datetime.utcnow().date())
            db.session.add(t)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(request.referrer or '/')
        tools = ToolWear.query.order_by(ToolWear.machine_name).all()

    today = datetime.utcnow().date()
    for t in tools:
        cycle = t.cycle_days or 14
        if t.last_replaced:
            days = (today - t.last_replaced).days
            t.wear_percent = min(100.0, round((days / cycle) * 100, 1))
        else:
            t.wear_percent = 100.0

    check_tool_wear_notifications()
    return render_template('tool_wear.html', tools=tools, today=today)


@bp.route('/tool-wear/add', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def tool_wear_add():
    machine_name = request.form.get('machine_name', '').strip()
    tool_name = request.form.get('tool_name', '').strip() or 'Ножи / Фреза'
    cycle_days = safe_int(request.form.get('cycle_days'), 14)
    if machine_name:
        t = ToolWear(machine_name=machine_name, tool_name=tool_name, cycle_days=cycle_days,
                     last_replaced=datetime.utcnow().date())
        db.session.add(t)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('tool_wear.tool_wear_page'))
        flash(_('Tool added'), 'success')
    return redirect(url_for('tool_wear.tool_wear_page'))


@bp.route('/tool-wear/update/<int:tool_id>', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def tool_wear_update(tool_id):
    tool = ToolWear.query.get_or_404(tool_id)
    tool.machine_name = request.form.get('machine_name', tool.machine_name).strip()
    tool.tool_name = request.form.get('tool_name', tool.tool_name).strip()
    tool.cycle_days = safe_int(request.form.get('cycle_days'), tool.cycle_days or 14)
    date_str = request.form.get('last_replaced')
    if date_str:
        tool.last_replaced = datetime.strptime(date_str, '%Y-%m-%d').date()
    tool.notes = request.form.get('notes', tool.notes)
    tool.updated_by = current_user.id
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('tool_wear.tool_wear_page'))
    flash(_('Tool updated'), 'success')
    return redirect(url_for('tool_wear.tool_wear_page'))


@bp.route('/tool-wear/delete/<int:tool_id>', methods=['POST'])
@login_required
@role_required('admin')
def tool_wear_delete(tool_id):
    tool = ToolWear.query.get_or_404(tool_id)
    db.session.delete(tool)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('tool_wear.tool_wear_page'))
    flash(_('Tool deleted'), 'success')
    return redirect(url_for('tool_wear.tool_wear_page'))


@bp.route('/tool-wear/reset/<int:tool_id>', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def tool_wear_reset(tool_id):
    tool = ToolWear.query.get_or_404(tool_id)
    tool.wear_percent = 0
    tool.last_replaced = datetime.utcnow().date()
    tool.updated_by = current_user.id
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('tool_wear.tool_wear_page'))
    Notification.query.filter_by(type='tool_wear', link='/tool-wear').delete()
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('tool_wear.tool_wear_page'))
    log_system('INFO', 'tool_wear', f'Tool replaced: {tool.machine_name} — {tool.tool_name}', source='tool_wear')
    flash(_('Tool replaced, wear reset to 0%'), 'success')
    return redirect(url_for('tool_wear.tool_wear_page'))


@bp.route('/api/tool-wear/warnings')
@login_required
def api_tool_wear_warnings():
    """Return tools with wear >= 80% — admin/director/masters/responsible with tool_wear."""
    allowed = False
    if current_user.has_role('admin', 'director', 'technician'):
        allowed = True
    else:
        try:
            allowed = user_has_section_access('tool_wear', 'view')
        except Exception:
            allowed = False
    if not allowed:
        return jsonify({'warnings': []})

    today = datetime.utcnow().date()
    warnings = []
    for t in ToolWear.query.all():
        cycle = t.cycle_days or 14
        if t.last_replaced:
            days = (today - t.last_replaced).days
            wear = min(100.0, round((days / cycle) * 100, 1))
        else:
            wear = 100.0
        if wear >= 80:
            warnings.append({
                'machine': t.machine_name,
                'tool': t.tool_name,
                'wear': wear,
                'last_replaced': t.last_replaced.strftime('%d.%m.%Y') if t.last_replaced else '—'
            })
    return jsonify({'warnings': warnings})
