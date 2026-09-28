"""
repairs blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from models import (db, EquipmentRepair, GasSystemComponent)
from utils import role_required, safe_commit, safe_date

bp = Blueprint('repairs', __name__)

@bp.route('/repairs')
@login_required
@role_required('admin', 'director', 'technician')
def repairs_list():
    gas_type = request.args.get('gas', '')
    status = request.args.get('status', '')
    q = EquipmentRepair.query
    if status:
        q = q.filter_by(status=status)
    if gas_type:
        q = q.join(GasSystemComponent).filter(GasSystemComponent.gas_type == gas_type)
    repairs = q.order_by(EquipmentRepair.date_broken.desc()).all()
    components = GasSystemComponent.query.order_by(GasSystemComponent.gas_type, GasSystemComponent.component_type).all()
    return render_template('repairs.html', repairs=repairs, components=components, gas_filter=gas_type, status_filter=status)


@bp.route('/repairs/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def repair_new():
    if request.method == 'POST':
        comp_id = request.form.get('component_id')
        if not comp_id:
            flash(_('Select component'), 'error')
            return redirect(url_for('repairs.repair_new'))
        comp_id = int(comp_id)
        comp = GasSystemComponent.query.get(comp_id)
        cost_str = request.form.get('repair_cost', '').strip()
        try:
            date_broken = datetime.strptime(request.form['date_broken'], '%Y-%m-%dT%H:%M')
        except (ValueError, KeyError):
            flash(_('Invalid date format'), 'error')
            return redirect(url_for('repairs.repair_new'))
        r = EquipmentRepair(
            component_id=comp_id,
            fault_description=request.form['fault_description'],
            date_broken=date_broken,
            repair_company=request.form.get('repair_company', ''),
            repair_description=request.form.get('repair_description', ''),
            repair_cost=float(cost_str) if cost_str else 0,
            date_sent=safe_date(request.form.get('date_sent'), '%Y-%m-%dT%H:%M'),
            date_repaired=safe_date(request.form.get('date_repaired'), '%Y-%m-%dT%H:%M'),
            date_installed=safe_date(request.form.get('date_installed'), '%Y-%m-%dT%H:%M'),
            status=request.form.get('status', 'broken'),
            notes=request.form.get('notes', ''),
            created_by=current_user.id
        )
        # Update component status
        if comp:
            if r.status == 'broken':
                comp.status = 'faulty'
            elif r.status in ('in_repair', 'repaired'):
                comp.status = 'replaced'
            elif r.status == 'installed':
                comp.status = 'ok'
                comp.installed_at = r.date_installed
        db.session.add(r)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('repairs.repairs_list'))
        flash(_('Repair record created'), 'success')
        return redirect(url_for('repairs.repairs_list'))
    components = GasSystemComponent.query.order_by(GasSystemComponent.gas_type, GasSystemComponent.component_type).all()
    return render_template('repair_form.html', repair=None, components=components)


@bp.route('/repairs/<int:repair_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def repair_edit(repair_id):
    r = EquipmentRepair.query.get_or_404(repair_id)
    if request.method == 'POST':
        try:
            r.date_broken = datetime.strptime(request.form['date_broken'], '%Y-%m-%dT%H:%M')
        except (ValueError, KeyError):
            flash(_('Invalid date format'), 'error')
            return redirect(url_for('repairs.repair_edit', repair_id=r.id))
        r.fault_description = request.form['fault_description']
        r.repair_company = request.form.get('repair_company', '')
        r.repair_description = request.form.get('repair_description', '')
        cost_str = request.form.get('repair_cost', '').strip()
        r.repair_cost = float(cost_str) if cost_str else 0
        r.date_sent = safe_date(request.form.get('date_sent'), '%Y-%m-%dT%H:%M')
        r.date_repaired = safe_date(request.form.get('date_repaired'), '%Y-%m-%dT%H:%M')
        r.date_installed = safe_date(request.form.get('date_installed'), '%Y-%m-%dT%H:%M')
        r.status = request.form.get('status', r.status)
        r.notes = request.form.get('notes', '')
        # Update component
        comp = r.component
        if comp:
            if r.status == 'installed':
                comp.status = 'ok'
                comp.installed_at = r.date_installed
            elif r.status == 'broken':
                comp.status = 'faulty'
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('repairs.repairs_list'))
        flash(_('Repair record updated'), 'success')
        return redirect(url_for('repairs.repairs_list'))
    components = GasSystemComponent.query.order_by(GasSystemComponent.gas_type, GasSystemComponent.component_type).all()
    return render_template('repair_form.html', repair=r, components=components)


@bp.route('/repairs/<int:repair_id>/status', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def repair_status(repair_id):
    r = EquipmentRepair.query.get_or_404(repair_id)
    new_status = request.form.get('status')
    if new_status in ('broken', 'in_repair', 'repaired', 'installed'):
        r.status = new_status
        comp = r.component
        if new_status == 'in_repair':
            r.date_sent = datetime.utcnow()
            if comp: comp.status = 'replaced'
        elif new_status == 'repaired':
            r.date_repaired = datetime.utcnow()
            if comp: comp.status = 'replaced'
        elif new_status == 'installed':
            r.date_installed = datetime.utcnow()
            if comp:
                comp.status = 'ok'
                comp.installed_at = r.date_installed
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('repairs.repairs_list'))
    flash(_('Status updated'), 'success')
    return redirect(url_for('repairs.repairs_list'))


@bp.route('/repairs/stats')
@login_required
@role_required('admin', 'director', 'technician')
def repairs_stats():
    all_repairs = EquipmentRepair.query.order_by(EquipmentRepair.date_broken.desc()).all()
    # Stats
    total = len(all_repairs)
    by_component = {}
    by_company = {}
    total_cost = 0
    total_days = 0
    days_count = 0
    for r in all_repairs:
        # By component type
        ctype = r.component.component_type if r.component else 'unknown'
        if ctype not in by_component:
            by_component[ctype] = {'count': 0, 'cost': 0, 'days': []}
        by_component[ctype]['count'] += 1
        by_component[ctype]['cost'] += r.repair_cost or 0
        # Days in repair
        if r.date_broken and r.date_installed:
            days = (r.date_installed - r.date_broken).days
            by_component[ctype]['days'].append(days)
            total_days += days
            days_count += 1
        # By company
        if r.repair_company:
            if r.repair_company not in by_company:
                by_company[r.repair_company] = {'count': 0, 'cost': 0}
            by_company[r.repair_company]['count'] += 1
            by_company[r.repair_company]['cost'] += r.repair_cost or 0
        total_cost += r.repair_cost or 0
    # Average days
    avg_days = round(total_days / days_count, 1) if days_count else 0
    # Add averages to by_component
    for ctype in by_component:
        days_list = by_component[ctype]['days']
        by_component[ctype]['avg_days'] = round(sum(days_list) / len(days_list), 1) if days_list else 0
    return render_template('repairs_stats.html',
        total=total, total_cost=total_cost, avg_days=avg_days,
        by_component=by_component, by_company=by_company,
        recent=all_repairs[:20])
