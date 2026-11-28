"""Maintenance schedule and schedule-generator routes."""
from datetime import datetime, timedelta

from flask import (Blueprint, request, redirect, url_for, flash, render_template,
                   jsonify, send_file, Response, current_app)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename

import os
import io

from models import (db, now_local, MaintenancePlan, MaintenanceSchedule, MaintenanceRecord, MaintenancePhoto,
                    MachinePart, PartMaintenanceLog, MachineTOChecklist, Machine, Monteur, FactorySection, User,
                    Equipment, EquipmentMaintenance, TechnicalWorkOrder, VoorraadItem, WorkEvent)
from security import director_only
from utils import resolve_user_id
from utils import (role_required, safe_commit, safe_int, safe_float, safe_date,
                   create_notification, log_audit, log_system, save_uploaded_file,
                   find_pdf_font, ensure_fpdf)

from . import bp
from .plans import skip_weekend


@bp.route('/maintenance-schedule')
@login_required
@role_required('admin', 'director', 'technician')
def maintenance_schedule():
    machines = Machine.query.order_by(Machine.name).all()
    equipment_list = Equipment.query.order_by(Equipment.name).all()
    schedules = MaintenanceSchedule.query.order_by(MaintenanceSchedule.id).all()
    # Group by target (machine or equipment)
    sched_by_target = {}
    for s in schedules:
        if s.target_type == 'equipment':
            key = f'e_{s.equipment_id}'
        elif s.target_type == 'custom':
            key = f'c_{s.target_name}'
        else:
            key = f'm_{s.machine_id}'
        sched_by_target.setdefault(key, []).append(s)
    return render_template('maintenance_schedule.html',
                           machines=machines, equipment_list=equipment_list,
                           schedules=schedules, sched_by_target=sched_by_target)

@bp.route('/maintenance-schedule/new', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def maintenance_schedule_new():
    target = request.form.get('target', '')
    title = request.form.get('title', '').strip()
    recurrence = request.form.get('recurrence', 'monthly')
    preferred_dow = safe_int(request.form.get('preferred_dow'))
    preferred_day = safe_int(request.form.get('preferred_day'))
    mtype = request.form.get('maintenance_type', 'preventive')
    description = request.form.get('description', '')

    if not target or not title:
        flash(_('Target and title are required'), 'error')
        return redirect(url_for('maintenance.maintenance_schedule'))

    # Parse target: m_5 = machine 5, e_3 = equipment 3
    machine_id = None
    equipment_id = None
    target_type = 'machine'
    target_name = ''

    if target.startswith('m_'):
        machine_id = safe_int(target[2:])
        target_type = 'machine'
    elif target.startswith('e_'):
        equipment_id = safe_int(target[2:])
        target_type = 'equipment'
    elif target == 'custom':
        # Use free-text input
        target_type = 'custom'
        target_name = request.form.get('custom_target', '').strip() or 'Other'
    else:
        target_type = 'custom'
        target_name = target

    s = MaintenanceSchedule(
        machine_id=machine_id, equipment_id=equipment_id,
        target_type=target_type, target_name=target_name,
        title=title, description=description,
        maintenance_type=mtype, recurrence=recurrence,
        preferred_dow=preferred_dow if preferred_dow is not None else None,
        preferred_day=preferred_day if preferred_day is not None else None,
        months_ahead=3, is_active=True
    )
    db.session.add(s)
    try:
        db.session.commit()
        flash(_('Schedule added'), 'success')
    except Exception as e:
        db.session.rollback()
        flash(_('Save failed: {}').format(str(e)[:200]), 'error')
    return redirect(url_for('maintenance.maintenance_schedule'))

@bp.route('/maintenance-schedule/<int:sched_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def maintenance_schedule_delete(sched_id):
    s = MaintenanceSchedule.query.get_or_404(sched_id)
    db.session.delete(s)
    if not safe_commit():
        flash(_('Delete failed'), 'error')
    else:
        flash(_('Schedule deleted'), 'success')
    return redirect(url_for('maintenance.maintenance_schedule'))

@bp.route('/maintenance-schedule/<int:sched_id>/toggle', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def maintenance_schedule_toggle(sched_id):
    s = MaintenanceSchedule.query.get_or_404(sched_id)
    s.is_active = not s.is_active
    if not safe_commit():
        flash(_('Save failed'), 'error')
    return redirect(url_for('maintenance.maintenance_schedule'))

@bp.route('/maintenance-schedule/generate', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def maintenance_schedule_generate():
    """Generate MaintenancePlan entries from active schedules."""
    months = safe_int(request.form.get('months'), 3)
    schedules = MaintenanceSchedule.query.filter_by(is_active=True).all()
    if not schedules:
        flash(_('No active schedules'), 'error')
        return redirect(url_for('maintenance.maintenance_schedule'))

    today = now_local().date()
    end_date = today + timedelta(days=months * 31)
    created = 0
    skipped = 0
    debug_info = []

    try:
        # Pre-fetch all existing plans in the date range for dedup
        existing_plans = MaintenancePlan.query.filter(
            MaintenancePlan.planned_start >= today,
            MaintenancePlan.planned_start <= end_date
        ).all()
        existing_set = set()
        for p in existing_plans:
            existing_set.add((p.machine_id, str(p.planned_start)))

        for sched in schedules:
            dates = _generate_schedule_dates(sched, today, end_date)
            debug_info.append(f'Sched {sched.id}: machine={sched.machine_id} title={sched.title} dates={len(dates)}')
            for d in dates:
                d = skip_weekend(d)
                if d.weekday() >= 5:
                    continue
                key = (sched.machine_id, str(d))
                if key in existing_set:
                    skipped += 1
                    continue
                existing_set.add(key)
                p = MaintenancePlan(
                    machine_id=sched.machine_id,
                    title=sched.title,
                    description=sched.description or sched.title,
                    maintenance_type=sched.maintenance_type,
                    status='planned',
                    planned_start=d,
                    recurrence=sched.recurrence,
                    created_by=resolve_user_id()
                )
                db.session.add(p)
                created += 1

        db.session.flush()
        if not safe_commit():
            flash(_('Generation failed — database error'), 'error')
            log_system('ERROR', 'maintenance', f'Schedule generation commit failed: created={created}')
        else:
            msg = _('{} events created, {} skipped').format(created, skipped)
            flash(msg, 'success')
            log_system('INFO', 'maintenance', f'Schedule generation: {created} created, {skipped} skipped. {"; ".join(debug_info[:3])}')
    except Exception as e:
        db.session.rollback()
        flash(_('Generation error: {}').format(str(e)[:100]), 'error')
        log_system('ERROR', 'maintenance', f'Schedule generation exception: {e}')

    return redirect(url_for('maintenance.maintenance_schedule'))

def _generate_schedule_dates(sched, start, end):
    """Generate dates for a schedule entry between start and end.
    Spreads events across different weekdays to avoid bunching."""
    dates = []
    dow = sched.preferred_dow  # 0=Mon..6=Sun
    day = sched.preferred_day  # 1-28
    mid = sched.machine_id or 0

    # Stagger: different machines get different days
    # Base = preferred_dow (e.g. Tuesday), offset by machine_id
    # Machine 1→Tue, 2→Wed, 3→Thu, 4→Fri, 5→Mon, 6→Tue...
    stagger_offset = mid % 5  # 0..4
    stagger_monthly = (mid % 25) + 1  # 1..25

    if sched.recurrence == 'weekly':
        # preferred_dow as base + offset by machine_id
        base_dow = dow if dow is not None else 0  # default Monday
        target_dow = (base_dow + stagger_offset) % 5  # stay Mon-Fri
        d = start
        days_ahead = target_dow - d.weekday()
        if days_ahead < 0: days_ahead += 7
        d = d + timedelta(days=days_ahead)
        while d < end:
            if d.weekday() < 5:
                dates.append(d)
            d += timedelta(weeks=1)

    elif sched.recurrence == 'biweekly':
        base_dow = dow if dow is not None else 0
        target_dow = (base_dow + stagger_offset) % 5  # Mon-Fri only
        d = start
        days_ahead = target_dow - d.weekday()
        if days_ahead < 0: days_ahead += 7
        d = d + timedelta(days=days_ahead)
        while d < end:
            if d.weekday() < 5:
                dates.append(d)
            d += timedelta(weeks=2)

    elif sched.recurrence == 'monthly':
        # Use preferred_day if set, otherwise stagger by machine_id
        target_day = day if day is not None else stagger_monthly
        target_day = min(target_day, 28)
        d = start.replace(day=target_day)
        if d < start:
            month = d.month + 1
            year = d.year
            if month > 12: month = 1; year += 1
            try:
                d = d.replace(year=year, month=month, day=target_day)
            except ValueError:
                d = d.replace(year=year, month=month, day=28)
        while d < end:
            dd = skip_weekend(d)
            if dd < end and dd.weekday() < 5:
                dates.append(dd)
            month = d.month + 1
            year = d.year
            if month > 12: month = 1; year += 1
            try:
                d = d.replace(year=year, month=month, day=min(target_day, 28))
            except ValueError:
                d = d.replace(year=year, month=month, day=28)

    elif sched.recurrence == 'quarterly':
        td = day if day is not None else stagger_monthly
        td = min(td, 28)
        try:
            d = start.replace(day=td)
        except ValueError:
            d = start.replace(day=28)
        if d < start:
            d = d + timedelta(days=1)
        while d < end:
            dd = skip_weekend(d)
            if dd < end and dd.weekday() < 5:
                dates.append(dd)
            month = d.month + 3
            year = d.year
            while month > 12:
                month -= 12
                year += 1
            try:
                d = d.replace(year=year, month=month, day=td)
            except ValueError:
                d = d.replace(year=year, month=month, day=28)

    elif sched.recurrence == 'semiannual':
        td = day if day is not None else stagger_monthly
        td = min(td, 28)
        try:
            d = start.replace(day=td)
        except ValueError:
            d = start.replace(day=28)
        if d < start:
            d = d + timedelta(days=1)
        while d < end:
            dd = skip_weekend(d)
            if dd < end and dd.weekday() < 5:
                dates.append(dd)
            month = d.month + 6
            year = d.year
            while month > 12:
                month -= 12
                year += 1
            try:
                d = d.replace(year=year, month=month, day=td)
            except ValueError:
                d = d.replace(year=year, month=month, day=28)

    elif sched.recurrence == 'yearly':
        td = day if day is not None else stagger_monthly
        td = min(td, 28)
        target_month = (start.month % 12) + 1
        try:
            d = start.replace(year=start.year, month=target_month, day=td)
        except ValueError:
            d = start.replace(year=start.year, month=target_month, day=28)
        if d < start:
            d = d.replace(year=d.year + 1)
        while d < end:
            dd = skip_weekend(d)
            if dd < end and dd.weekday() < 5:
                dates.append(dd)
            d = d.replace(year=d.year + 1)

    return dates

