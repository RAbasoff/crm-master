"""
Maintenance blueprint — calendar, plans, schedule generator
"""
from datetime import datetime, timedelta

from flask import (Blueprint, request, redirect, url_for, flash, render_template,
                   jsonify, send_file, Response, current_app)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename

import os
import io

from models import (db, MaintenancePlan, MaintenanceSchedule, MaintenanceRecord, MaintenancePhoto,
                    MachinePart, PartMaintenanceLog, Machine, Monteur, FactorySection, User,
                    Equipment, EquipmentMaintenance, TechnicalWorkOrder, VoorraadItem)
from utils import (role_required, safe_commit, safe_int, safe_float, safe_date,
                   create_notification, log_audit, log_system, save_uploaded_file,
                   find_pdf_font, ensure_fpdf)

bp = Blueprint('maintenance', __name__)


@bp.route('/maintenance-calendar')
@login_required
def maintenance_calendar():
    today = datetime.utcnow().date()
    month = request.args.get('month', today.strftime('%Y-%m'))
    year, mon = map(int, month.split('-'))
    month_start = datetime(year, mon, 1).date()
    if mon == 12:
        month_end = datetime(year + 1, 1, 1).date()
    else:
        month_end = datetime(year, mon + 1, 1).date()
    
    # Get all parts with upcoming maintenance/replacement (eagerly load machine)
    from sqlalchemy.orm import joinedload
    if current_user.has_role('admin', 'director', 'technician'):
        parts = MachinePart.query.options(joinedload(MachinePart.machine)).all()
    else:
        machine_ids = [m.id for m in current_user.assigned_machines]
        parts = MachinePart.query.options(joinedload(MachinePart.machine)).filter(MachinePart.machine_id.in_(machine_ids)).all()
    
    # Build calendar events
    events = []
    # Batch-fetch all maintenance records for relevant machines
    all_machine_ids = list(set(p.machine_id for p in parts))
    # Also include machines that have plans (not all machines have parts)
    plan_machine_ids = [pl.machine_id for pl in MaintenancePlan.query.with_entities(MaintenancePlan.machine_id).distinct().all()]
    all_machine_ids = list(set(all_machine_ids + plan_machine_ids))
    all_maint_records = MaintenanceRecord.query.filter(
        MaintenanceRecord.machine_id.in_(all_machine_ids)
    ).all() if all_machine_ids else []
    maint_by_machine = {}
    for mr in all_maint_records:
        maint_by_machine.setdefault(mr.machine_id, []).append(mr)

    # Batch-fetch PartMaintenanceLog for all parts
    all_part_ids = [p.id for p in parts]
    part_logs = PartMaintenanceLog.query.filter(
        PartMaintenanceLog.part_id.in_(all_part_ids)
    ).all() if all_part_ids else []
    logs_by_part = {}
    for log in part_logs:
        logs_by_part.setdefault(log.part_id, []).append(log)

    for p in parts:
        if p.next_replacement and month_start <= p.next_replacement < month_end:
            # Check if replacement was done (has log entry after due date)
            done = False
            for log in logs_by_part.get(p.id, []):
                if log.action in ('replacement', 'replaced') and log.date and log.date.date() >= p.next_replacement:
                    done = True
                    break
            events.append({
                'date': p.next_replacement,
                'type': 'replacement',
                'part': p.name,
                'machine': p.machine.name,
                'machine_id': p.machine_id,
                'part_id': p.id,
                'category': p.category,
                'overdue': p.next_replacement < today and not done,
                'done': done,
                'plan_id': None,
                'equipment_id': None,
                'mro_id': None,
                'record_id': None
            })
        if p.next_maintenance and month_start <= p.next_maintenance < month_end:
            # Check if maintenance was done
            done = False
            for log in logs_by_part.get(p.id, []):
                if log.action in ('maintenance', 'replaced') and log.date and log.date.date() >= p.next_maintenance:
                    done = True
                    break
            events.append({
                'date': p.next_maintenance,
                'type': 'maintenance',
                'part': p.name,
                'machine': p.machine.name,
                'machine_id': p.machine_id,
                'part_id': p.id,
                'category': p.category,
                'overdue': p.next_maintenance < today and not done,
                'done': done,
                'plan_id': None,
                'equipment_id': None,
                'mro_id': None,
                'record_id': None
            })
        # Check maintenance records (from batch-fetched data)
        for mr in maint_by_machine.get(p.machine_id, []):
            if mr.next_maintenance and month_start <= mr.next_maintenance.date() < month_end:
                # Check if this maintenance was done (has a follow-up record)
                done = False
                for log in logs_by_part.get(p.id, []):
                    if log.date and log.date.date() >= mr.next_maintenance.date():
                        done = True
                        break
                events.append({
                    'date': mr.next_maintenance.date(),
                    'type': 'machine_maintenance',
                    'part': mr.description[:40],
                    'machine': p.machine.name,
                    'machine_id': p.machine_id,
                    'part_id': None,
                    'category': mr.maintenance_type,
                    'overdue': mr.next_maintenance.date() < today and not done,
                    'done': done,
                    'plan_id': None,
                    'equipment_id': None,
                    'mro_id': None,
                    'record_id': mr.id
                })
    
    # Add maintenance plans
    if current_user.has_role('admin', 'director', 'technician'):
        plans = MaintenancePlan.query.all()
    else:
        plan_machine_ids = [m.id for m in current_user.assigned_machines]
        plans = MaintenancePlan.query.filter(MaintenancePlan.machine_id.in_(plan_machine_ids)).all()
    for pl in plans:
        if not pl.planned_start:
            continue
        # Основное событие
        if month_start <= pl.planned_start < month_end:
            done = pl.status in ('completed',)
            # Also check MaintenanceRecord for recurring plans
            if not done:
                for mr in maint_by_machine.get(pl.machine_id, []):
                    if mr.date_performed and mr.date_performed.date() == pl.planned_start:
                        done = True
                        break
            events.append({
                'date': pl.planned_start,
                'type': 'plan',
                'part': pl.title[:40],
                'machine': pl.machine.name,
                'machine_id': pl.machine_id,
                'part_id': None,
                'category': pl.maintenance_type,
                'overdue': pl.planned_start < today and not done,
                'plan_id': pl.id,
                'status': 'completed' if done else pl.status,
                'done': done,
                'equipment_id': None,
                'mro_id': None,
                'record_id': None
            })
        # Периодические повторения (виртуальные события)
        if pl.recurrence and pl.recurrence != 'none':
            d = pl.planned_start
            limit = month_end + timedelta(days=1)
            for _ in range(200):
                if d >= limit:
                    break
                if d >= month_start and d != pl.planned_start:
                    # Check if this specific occurrence was completed
                    occ_done = False
                    for mr in maint_by_machine.get(pl.machine_id, []):
                        if mr.next_maintenance and mr.next_maintenance.date() == d:
                            occ_done = True
                            break
                        if mr.date_performed and mr.date_performed.date() == d:
                            occ_done = True
                            break
                    events.append({
                        'date': d,
                        'type': 'plan',
                        'part': pl.title[:40],
                        'machine': pl.machine.name,
                        'machine_id': pl.machine_id,
                        'part_id': None,
                        'category': pl.maintenance_type,
                        'overdue': d < today and not occ_done,
                        'plan_id': pl.id,
                        'status': 'completed' if occ_done else pl.status,
                        'done': occ_done,
                        'equipment_id': None,
                        'mro_id': None,
                        'record_id': None
                    })
                # Следующая дата
                if pl.recurrence == 'daily':
                    d += timedelta(days=1)
                elif pl.recurrence == 'weekly':
                    d += timedelta(weeks=1)
                elif pl.recurrence == 'biweekly':
                    d += timedelta(weeks=2)
                elif pl.recurrence == 'monthly':
                    try:
                        d = d.replace(year=d.year + (1 if d.month == 12 else 0), month=(d.month % 12) + 1)
                    except ValueError:
                        d = d.replace(year=d.year + (1 if d.month == 12 else 0), month=(d.month % 12) + 1, day=28)
                elif pl.recurrence == 'quarterly':
                    m = d.month + 3
                    y = d.year + (1 if m > 12 else 0)
                    m = ((m - 1) % 12) + 1
                    try:
                        d = d.replace(year=y, month=m)
                    except ValueError:
                        d = d.replace(year=y, month=m, day=28)
                elif pl.recurrence == 'yearly':
                    try:
                        d = d.replace(year=d.year + 1)
                    except ValueError:
                        d = d.replace(year=d.year + 1, day=28)
                else:
                    break

    # Add Equipment maintenance events
    from models import Equipment, EquipmentMaintenance
    equip_q = Equipment.query.filter(Equipment.next_service_date.isnot(None))
    if not current_user.has_role('admin', 'director', 'technician'):
        equip_q = equip_q.filter(Equipment.responsible_user_id == current_user.id)
    for eq in equip_q.all():
        if eq.next_service_date and month_start <= eq.next_service_date < month_end:
            # Check if maintenance was done after the due date
            done = False
            if eq.last_service_date and eq.last_service_date >= eq.next_service_date:
                done = True
            events.append({
                'date': eq.next_service_date,
                'type': 'equipment',
                'part': eq.name[:40],
                'machine': eq.equipment_type or eq.name[:20],
                'machine_id': None,
                'equipment_id': eq.id,
                'part_id': None,
                'category': eq.category or 'service',
                'overdue': eq.next_service_date < today and not done,
                'done': done,
                'plan_id': None,
                'mro_id': None,
                'record_id': None
            })

    # Add EquipmentMaintenance (MRO) records with next_date
    mro_q = EquipmentMaintenance.query.filter(EquipmentMaintenance.next_date.isnot(None))
    for mro in mro_q.all():
        if mro.next_date and month_start <= mro.next_date < month_end:
            done = mro.status == 'completed'
            events.append({
                'date': mro.next_date,
                'type': 'equipment_mro',
                'part': mro.name[:40],
                'machine': mro.number,
                'machine_id': mro.machine_id,
                'equipment_id': None,
                'part_id': None,
                'category': mro.periodicity or 'MRO',
                'overdue': mro.next_date < today and not done,
                'done': done,
                'plan_id': None,
                'mro_id': mro.id,
                'record_id': None
            })

    # Get overdue items (before today) — skip if there's a maintenance record after the due date
    overdue = []
    for p in parts:
        if p.next_replacement and p.next_replacement < today:
            # Check if replacement was done after the due date
            done_after = PartMaintenanceLog.query.filter(
                PartMaintenanceLog.part_id == p.id,
                PartMaintenanceLog.action.in_(['replaced', 'maintenance']),
                PartMaintenanceLog.date >= datetime.combine(p.next_replacement, datetime.min.time())
            ).first()
            if not done_after:
                overdue.append({'date': p.next_replacement, 'type': 'replacement', 'part': p.name, 'machine': p.machine.name, 'machine_id': p.machine_id, 'part_id': p.id, 'category': p.category, 'plan_id': None, 'equipment_id': None, 'mro_id': None, 'record_id': None})
        if p.next_maintenance and p.next_maintenance < today:
            done_after = PartMaintenanceLog.query.filter(
                PartMaintenanceLog.part_id == p.id,
                PartMaintenanceLog.action.in_(['replaced', 'maintenance']),
                PartMaintenanceLog.date >= datetime.combine(p.next_maintenance, datetime.min.time())
            ).first()
            if not done_after:
                overdue.append({'date': p.next_maintenance, 'type': 'maintenance', 'part': p.name, 'machine': p.machine.name, 'machine_id': p.machine_id, 'part_id': p.id, 'category': p.category, 'plan_id': None, 'equipment_id': None, 'mro_id': None, 'record_id': None})
    
    # Navigation
    prev_month = (month_start - timedelta(days=1)).strftime('%Y-%m')
    next_month = month_end.strftime('%Y-%m')
    
    return render_template('maintenance_calendar.html',
        month=month, month_start=month_start, month_end=month_end,
        events=sorted(events, key=lambda e: e['date']),
        overdue=overdue, today=today,
        prev_month=prev_month, next_month=next_month,
        timedelta=timedelta)

@bp.route('/maintenance-calendar/complete', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def maintenance_calendar_complete():
    """Mark a calendar event as completed."""
    ev_type = request.form.get('type', '')
    part_id = request.form.get('part_id')
    plan_id = request.form.get('plan_id')

    try:
        if ev_type == 'plan' and plan_id:
            plan = MaintenancePlan.query.get(int(plan_id))
            if plan:
                event_date = request.form.get('date')
                event_dt = datetime.strptime(event_date, '%Y-%m-%d') if event_date else datetime.utcnow()
                if plan.recurrence and plan.recurrence != 'none':
                    # Recurring plan — mark only THIS occurrence as done
                    mr = MaintenanceRecord(
                        machine_id=plan.machine_id,
                        maintenance_type=plan.maintenance_type,
                        description=f'{plan.title} ({event_date}) — completed by {current_user.display_name or current_user.username}',
                        performed_by=current_user.id,
                        date_performed=event_dt,
                        next_maintenance=None,
                        cost=0
                    )
                    db.session.add(mr)
                    db.session.commit()
                    flash(_('Occurrence marked as completed'), 'success')
                else:
                    # Non-recurring plan — mark whole plan done
                    plan.status = 'completed'
                    plan.actual_end = event_dt.date()
                    mr = MaintenanceRecord(
                        machine_id=plan.machine_id,
                        maintenance_type=plan.maintenance_type,
                        description=f'{plan.title} — completed by {current_user.display_name or current_user.username}',
                        performed_by=current_user.id,
                        date_performed=event_dt,
                        next_maintenance=None,
                        cost=0
                    )
                    db.session.add(mr)
                    db.session.commit()
                    flash(_('Plan marked as completed'), 'success')
            else:
                flash(_('Plan not found'), 'error')

        elif ev_type in ('replacement', 'maintenance') and part_id:
            part = MachinePart.query.get(int(part_id))
            if part:
                action = 'replacement' if ev_type == 'replacement' else 'maintenance'
                log = PartMaintenanceLog(
                    part_id=part.id,
                    action=action,
                    description=f'Completed from calendar by {current_user.display_name or current_user.username}',
                    performed_by=current_user.id,
                    date=datetime.utcnow()
                )
                db.session.add(log)
                today_d = datetime.utcnow().date()
                if action == 'replacement':
                    part.last_replacement = today_d
                    # Следующая дата по интервалу; без интервала — оставляем
                    # текущую дату, чтобы в календаре запись была зелёной (✅),
                    # а не исчезала при отметке «выполнено».
                    if part.replacement_interval_days:
                        part.next_replacement = today_d + timedelta(days=part.replacement_interval_days)
                else:
                    part.last_maintenance = today_d
                    if part.replacement_interval_days:
                        part.next_maintenance = today_d + timedelta(days=part.replacement_interval_days)
                part.status = 'ok'
                db.session.commit()
                flash(_('%(type)s marked as completed', type=action), 'success')
            else:
                flash(_('Part not found'), 'error')

        elif ev_type == 'machine_maintenance':
            record_id = request.form.get('record_id')
            machine_id = request.form.get('machine_id')
            event_date = request.form.get('date')
            mr = None
            if record_id:
                try:
                    mr = MaintenanceRecord.query.get(int(record_id))
                except (ValueError, TypeError):
                    mr = None
            if not mr and machine_id:
                try:
                    query = MaintenanceRecord.query.filter(
                        MaintenanceRecord.machine_id == int(machine_id),
                        MaintenanceRecord.next_maintenance.isnot(None)
                    )
                    if event_date:
                        query = query.filter(
                            db.func.date(MaintenanceRecord.next_maintenance) == event_date
                        )
                    mr = query.first()
                except (ValueError, TypeError):
                    mr = None
            if mr:
                # Не обнуляем next_maintenance — иначе событие исчезает из календаря.
                # Лог ниже делает запись «выполнено» (зелёной).
                mr.date_performed = datetime.utcnow()
                mr.performed_by = current_user.id
                # Create PartMaintenanceLog so calendar shows green (done=True)
                first_part = MachinePart.query.filter_by(machine_id=mr.machine_id).first()
                if first_part:
                    log = PartMaintenanceLog(
                        part_id=first_part.id,
                        action='maintenance',
                        description=f'{mr.description[:80]} — completed from calendar by {current_user.display_name or current_user.username}',
                        performed_by=current_user.id,
                        date=datetime.utcnow()
                    )
                    db.session.add(log)
                db.session.commit()
                flash(_('Maintenance marked as completed'), 'success')
            else:
                flash(_('Event not found'), 'error')

        elif ev_type == 'equipment':
            from models import Equipment
            equip_id = request.form.get('equipment_id')
            if equip_id:
                eq = Equipment.query.get(int(equip_id))
                if eq:
                    eq.last_service_date = datetime.utcnow().date()
                    if eq.service_interval_days:
                        eq.next_service_date = datetime.utcnow().date() + timedelta(days=eq.service_interval_days)
                    # без интервала — дата остаётся, запись остаётся в календаре как выполненная
                    db.session.commit()
                    flash(_('Equipment service marked as completed'), 'success')
                else:
                    flash(_('Equipment not found'), 'error')

        elif ev_type == 'equipment_mro':
            from models import EquipmentMaintenance
            mro_id = request.form.get('mro_id')
            if mro_id:
                mro = EquipmentMaintenance.query.get(int(mro_id))
                if mro:
                    mro.status = 'completed'
                    db.session.commit()
                    flash(_('MRO marked as completed'), 'success')
                else:
                    flash(_('MRO not found'), 'error')

        else:
            flash(_('Unknown event type'), 'error')

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash(_('Error: %(err)s', err=str(e)[:100]), 'error')

    month = request.form.get('month', datetime.utcnow().strftime('%Y-%m'))
    return redirect(url_for('maintenance.maintenance_calendar', month=month))

@bp.route('/maintenance-calendar/debug')
@login_required
@role_required('admin')
def maintenance_calendar_debug():
    """Debug endpoint to check calendar state."""
    plans = MaintenancePlan.query.all()
    records = MaintenanceRecord.query.all()
    lines = [f"=== Plans ({len(plans)}) ==="]
    for p in plans:
        lines.append(f"  ID={p.id} machine={p.machine_id} title={p.title} status={p.status} recur={p.recurrence} start={p.planned_start}")
    lines.append(f"\n=== Records ({len(records)}) ===")
    for r in records:
        lines.append(f"  ID={r.id} machine={r.machine_id} desc={str(r.description)[:50]} perf={r.date_performed}")
    lines.append(f"\n=== Done check ===")
    for p in plans:
        if p.planned_start:
            ds = p.status in ('completed',)
            dr = any(r.machine_id == p.machine_id and r.date_performed and r.date_performed.date() == p.planned_start for r in records)
            lines.append(f"  Plan {p.id} on {p.planned_start}: status_done={ds}, record_done={dr}")
    return '<pre>' + '\n'.join(lines) + '</pre>'

@bp.route('/maintenance-calendar/delete', methods=['POST'])
@login_required
@role_required('admin')
def maintenance_calendar_delete():
    """Delete a calendar event (admin only)."""
    ev_type = request.form.get('type', '')
    part_id = request.form.get('part_id')
    plan_id = request.form.get('plan_id')
    equipment_id = request.form.get('equipment_id')
    mro_id = request.form.get('mro_id')
    record_id = request.form.get('record_id')

    try:
        if ev_type in ('replacement', 'maintenance') and part_id:
            part = MachinePart.query.get(int(part_id))
            if part:
                if ev_type == 'replacement':
                    part.next_replacement = None
                else:
                    part.next_maintenance = None
                db.session.commit()
                flash(_('Event removed'), 'success')
            else:
                flash(_('Part not found'), 'error')

        elif ev_type == 'plan' and plan_id:
            p = MaintenancePlan.query.get(int(plan_id))
            if p:
                db.session.delete(p)
                db.session.commit()
                flash(_('Plan deleted'), 'success')
            else:
                flash(_('Plan not found'), 'error')

        elif ev_type == 'machine_maintenance':
            mr = None
            if record_id:
                mr = MaintenanceRecord.query.get(int(record_id))
            elif request.form.get('machine_id'):
                machine_id = int(request.form.get('machine_id'))
                event_date = request.form.get('date')
                query = MaintenanceRecord.query.filter(
                    MaintenanceRecord.machine_id == machine_id,
                    MaintenanceRecord.next_maintenance.isnot(None)
                )
                if event_date:
                    query = query.filter(
                        db.func.date(MaintenanceRecord.next_maintenance) == event_date
                    )
                mr = query.first()
            if mr:
                mr.next_maintenance = None
                db.session.commit()
                flash(_('Event removed'), 'success')
            else:
                flash(_('Event not found'), 'error')

        elif ev_type == 'equipment' and equipment_id:
            from models import Equipment
            eq = Equipment.query.get(int(equipment_id))
            if eq:
                eq.next_service_date = None
                db.session.commit()
                flash(_('Equipment service date removed'), 'success')
            else:
                flash(_('Equipment not found'), 'error')

        elif ev_type == 'equipment_mro' and mro_id:
            from models import EquipmentMaintenance
            mro = EquipmentMaintenance.query.get(int(mro_id))
            if mro:
                db.session.delete(mro)
                db.session.commit()
                flash(_('MRO record deleted'), 'success')
            else:
                flash(_('MRO not found'), 'error')

        else:
            flash(_('Unknown event type'), 'error')

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash(_('Error: %(err)s', err=str(e)[:100]), 'error')

    month = request.form.get('month', datetime.utcnow().strftime('%Y-%m'))
    return redirect(url_for('maintenance.maintenance_calendar', month=month))

@bp.route('/maintenance-calendar/move', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def maintenance_calendar_move():
    """Drag & drop: move a calendar event to a new date."""
    ev_type = request.form.get('type', '')
    plan_id = request.form.get('plan_id')
    new_date_str = request.form.get('new_date')  # YYYY-MM-DD

    if not new_date_str:
        return jsonify({'error': 'No date'}), 400

    try:
        new_date = datetime.strptime(new_date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400

    # Don't allow moving to weekends
    if new_date.weekday() >= 5:
        return jsonify({'error': 'Cannot move to weekend'}), 400

    try:
        if ev_type == 'plan' and plan_id:
            p = MaintenancePlan.query.get(int(plan_id))
            if p:
                p.planned_start = new_date
                if not safe_commit():
                    return jsonify({'error': 'Save failed'}), 500
                return jsonify({'ok': True, 'new_date': new_date_str})

        elif ev_type in ('replacement', 'maintenance'):
            part_id = request.form.get('part_id')
            if part_id:
                part = MachinePart.query.get(int(part_id))
                if part:
                    if ev_type == 'replacement':
                        part.next_replacement = new_date
                    else:
                        part.next_maintenance = new_date
                    if not safe_commit():
                        return jsonify({'error': 'Save failed'}), 500
                    return jsonify({'ok': True, 'new_date': new_date_str})

        return jsonify({'error': 'Event not found'}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)[:100]}), 500

@bp.route('/maintenance-calendar/export')
@login_required
def maintenance_calendar_export():
    import io
    today = datetime.utcnow().date()
    month = request.args.get('month', today.strftime('%Y-%m'))
    year, mon = map(int, month.split('-'))
    month_start = datetime(year, mon, 1).date()
    if mon == 12:
        month_end = datetime(year + 1, 1, 1).date()
    else:
        month_end = datetime(year, mon + 1, 1).date()

    # Collect events (same logic as calendar view)
    from sqlalchemy.orm import joinedload
    if current_user.has_role('admin', 'director', 'technician'):
        parts = MachinePart.query.options(joinedload(MachinePart.machine)).all()
    else:
        machine_ids = [m.id for m in current_user.assigned_machines]
        parts = MachinePart.query.options(joinedload(MachinePart.machine)).filter(MachinePart.machine_id.in_(machine_ids)).all()

    events = []
    for p in parts:
        if p.next_replacement and month_start <= p.next_replacement < month_end:
            events.append({'date': p.next_replacement, 'type': 'Replacement', 'part': p.name, 'machine': p.machine.name, 'machine_id': p.machine_id, 'overdue': p.next_replacement < today})
        if p.next_maintenance and month_start <= p.next_maintenance < month_end:
            events.append({'date': p.next_maintenance, 'type': 'Maintenance', 'part': p.name, 'machine': p.machine.name, 'machine_id': p.machine_id, 'overdue': p.next_maintenance < today})
        for mr in MaintenanceRecord.query.filter_by(machine_id=p.machine_id).all():
            if mr.next_maintenance and month_start <= mr.next_maintenance.date() < month_end:
                events.append({'date': mr.next_maintenance.date(), 'type': 'Machine Maintenance', 'part': mr.description[:40], 'machine': p.machine.name, 'machine_id': p.machine_id, 'overdue': mr.next_maintenance.date() < today})

    if current_user.has_role('admin', 'director', 'technician'):
        plans = MaintenancePlan.query.all()
    else:
        plan_machine_ids = [m.id for m in current_user.assigned_machines]
        plans = MaintenancePlan.query.filter(MaintenancePlan.machine_id.in_(plan_machine_ids)).all()
    for pl in plans:
        if not pl.planned_start:
            continue
        if month_start <= pl.planned_start < month_end:
            events.append({'date': pl.planned_start, 'type': 'Plan', 'part': pl.title[:40], 'machine': pl.machine.name, 'machine_id': pl.machine_id, 'overdue': pl.planned_start < today and pl.status not in ('completed', 'cancelled'), 'status': pl.status})
        if pl.recurrence and pl.recurrence != 'none' and pl.status not in ('completed', 'cancelled'):
            d = pl.planned_start
            limit = month_end + timedelta(days=1)
            for _occ in range(200):
                if d >= limit:
                    break
                if d >= month_start and d != pl.planned_start:
                    events.append({'date': d, 'type': 'Plan', 'part': pl.title[:40], 'machine': pl.machine.name, 'machine_id': pl.machine_id, 'overdue': d < today, 'status': pl.status})
                if pl.recurrence == 'daily':
                    d += timedelta(days=1)
                elif pl.recurrence == 'weekly':
                    d += timedelta(weeks=1)
                elif pl.recurrence == 'biweekly':
                    d += timedelta(weeks=2)
                elif pl.recurrence == 'monthly':
                    try:
                        d = d.replace(year=d.year + (1 if d.month == 12 else 0), month=(d.month % 12) + 1)
                    except ValueError:
                        d = d.replace(year=d.year + (1 if d.month == 12 else 0), month=(d.month % 12) + 1, day=28)
                elif pl.recurrence == 'quarterly':
                    m = d.month + 3
                    y = d.year + (1 if m > 12 else 0)
                    m = ((m - 1) % 12) + 1
                    try:
                        d = d.replace(year=y, month=m)
                    except ValueError:
                        d = d.replace(year=y, month=m, day=28)
                elif pl.recurrence == 'yearly':
                    try:
                        d = d.replace(year=d.year + 1)
                    except ValueError:
                        d = d.replace(year=d.year + 1, day=28)
                else:
                    break

    events.sort(key=lambda e: e['date'])

    # Real PDF via fpdf2 (user language)
    try:
        FPDF = ensure_fpdf().FPDF
    except Exception:
        flash(_('PDF export is temporarily unavailable'), 'error')
        return redirect(url_for('maintenance.maintenance_calendar'))
    month_title = month_start.strftime('%B %Y')
    title = _('Maintenance Calendar')
    headers = [_('Date'), _('Machine'), _('Part'), _('Type'), _('Status'), _('Overdue')]
    status_map = {
        'planned': _('Planned'),
        'completed': _('Completed'),
        'cancelled': _('Cancelled'),
        'in_progress': _('In progress'),
    }
    type_map = {
        'Plan': _('Plan'),
        'Replacement': _('Replacement'),
        'Maintenance': _('Maintenance'),
        'Machine Maintenance': _('Machine Maintenance'),
        'equipment_mro': _('MRO'),
    }

    font_path = find_pdf_font()
    unicode_font = bool(font_path)

    def pdf_text(s):
        s = str(s if s is not None else '')
        return s if unicode_font else s.encode('latin-1', 'replace').decode('latin-1')

    class CalPDF(FPDF):
        def header(self):
            fn = 'AppFont' if unicode_font else 'Helvetica'
            self.set_font(fn, 'B', 12)
            self.cell(0, 8, pdf_text(f'{title} — {month_title}'), new_x='LMARGIN', new_y='NEXT')
            self.set_font(fn, '', 8)
            self.cell(0, 5, pdf_text(f'{len(events)} {_("events")}'), new_x='LMARGIN', new_y='NEXT')
            self.ln(2)

        def footer(self):
            fn = 'AppFont' if unicode_font else 'Helvetica'
            self.set_y(-12)
            self.set_font(fn, '', 7)
            self.set_text_color(128)
            self.cell(0, 8, pdf_text(f'{_("Generated by ProMaster CRM")} — {_("page")} {self.page_no()}/{{nb}}'), align='C')

    pdf = CalPDF(orientation='L', unit='mm', format='A4')
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    if unicode_font:
        try:
            pdf.add_font('AppFont', '', font_path)
            pdf.add_font('AppFont', 'B', font_path)
            base_font = 'AppFont'
        except Exception:
            unicode_font = False
            base_font = 'Helvetica'
    else:
        base_font = 'Helvetica'
    pdf.add_page()

    col_w = [28, 55, 70, 40, 35, 25]
    pdf.set_font(base_font, 'B', 8)
    pdf.set_fill_color(44, 62, 80)
    pdf.set_text_color(255)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 8, pdf_text(h), border=1, fill=True)
    pdf.ln()
    pdf.set_font(base_font, '', 7.5)
    pdf.set_text_color(51)
    for idx, ev in enumerate(events):
        fill = (idx % 2 == 1)
        if fill:
            pdf.set_fill_color(249, 249, 249)
        if ev.get('overdue'):
            pdf.set_text_color(200, 40, 40)
        else:
            pdf.set_text_color(51)
        status = ev.get('status') or ('overdue' if ev.get('overdue') else 'scheduled')
        status_txt = status_map.get(status, status)
        row = [
            ev['date'].strftime('%d-%m-%Y'),
            ev.get('machine') or '',
            ev.get('part') or '',
            type_map.get(ev.get('type'), ev.get('type') or ''),
            status_txt,
            _('Yes') if ev.get('overdue') else _('No'),
        ]
        for i, val in enumerate(row):
            pdf.cell(col_w[i], 7, pdf_text(val), border=1, fill=fill)
        pdf.ln()
    pdf.set_text_color(51)

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return send_file(buf, mimetype='application/pdf',
                     download_name=f'calendar_{month}.pdf', as_attachment=True)

@bp.route('/maintenance-plans')
@login_required
def maintenance_plans_list():
    if current_user.has_role('admin', 'director', 'technician'):
        plans = MaintenancePlan.query.order_by(MaintenancePlan.planned_start.desc()).all()
    else:
        machine_ids = [m.id for m in current_user.assigned_machines]
        plans = MaintenancePlan.query.filter(MaintenancePlan.machine_id.in_(machine_ids)).order_by(MaintenancePlan.planned_start.desc()).all()
    machines = Machine.query.order_by(Machine.name).all()
    return render_template('maintenance_plans.html', plans=plans, machines=machines)

def skip_weekend(d):
    """Move date to next Monday if it falls on Saturday or Sunday."""
    if d.weekday() == 5:  # Saturday
        return d + timedelta(days=2)
    elif d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d

@bp.route('/maintenance-plans/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def maintenance_plan_new():
    if request.method == 'POST':
        try:
            machine_id = int(request.form['machine_id'])
            planned_start = datetime.strptime(request.form['planned_start'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid machine or date'), 'error')
            return redirect(url_for('maintenance.maintenance_plan_new'))
        # Skip weekends — move to Monday
        original = planned_start
        planned_start = skip_weekend(planned_start)
        if planned_start != original:
            flash(_('Date moved from weekend to Monday: {}').format(planned_start.strftime('%d-%m-%Y')), 'info')
        p = MaintenancePlan(
            machine_id=machine_id,
            title=request.form['title'],
            description=request.form.get('description', ''),
            maintenance_type=request.form.get('maintenance_type', 'preventive'),
            status=request.form.get('status', 'planned'),
            planned_start=planned_start,
            planned_end=(d := safe_date(request.form.get('planned_end'))) and d.date() or None,
            is_external='is_external' in request.form,
            company_name=request.form.get('company_name', ''),
            company_contact=request.form.get('company_contact', ''),
            company_person=request.form.get('company_person', ''),
            worker_id=safe_int(request.form.get('worker_id')) or None,
            parts_used=request.form.get('parts_used', '[]'),
            cost=safe_float(request.form.get('cost'), 0),
            report=request.form.get('report', ''),
            next_maintenance=(d := safe_date(request.form.get('next_maintenance'))) and d.date() or None,
            recurrence=request.form.get('recurrence', '') or None,
            notes=request.form.get('notes', ''),
            created_by=current_user.id
        )
        if 'offer_file' in request.files and request.files['offer_file'].filename:
            fn = save_uploaded_file(request.files['offer_file'], prefix='offer_')
            if fn:
                p.offer_file = fn
        if 'work_act_file' in request.files and request.files['work_act_file'].filename:
            fn = save_uploaded_file(request.files['work_act_file'], prefix='act_')
            if fn:
                p.work_act_file = fn
        db.session.add(p)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('maintenance.maintenance_plan_new'))
        
        # Check if TWO should be created
        create_two = request.form.get('create_two') == 'yes'
        worker_id = safe_int(request.form.get('worker_id')) or None
        
        if create_two and worker_id:
            # Create TWO from maintenance plan
            from blueprints.two import gen_two_number
            two = TechnicalWorkOrder(
                number=gen_two_number(),
                machine_id=p.machine_id,
                description=f'ТО: {p.title}\n{p.description}',
                planned_date=p.planned_start,
                status='assigned',
                created_by=current_user.id
            )
            db.session.add(two)
            db.session.flush()
            # Assign worker
            worker = Monteur.query.get(worker_id)
            if worker:
                two.workers.append(worker)
            if not safe_commit():
                flash(_('Save failed'), 'error')
                return redirect(url_for('maintenance.maintenance_plan_new'))
            log_audit('create', 'two_from_plan', two.id, f'{two.number} from plan {p.id}')
            flash(_('Maintenance plan created with TWO') + f': {two.number}', 'success')
        else:
            flash(_('Maintenance plan created'), 'success')
        
        return redirect(url_for('maintenance.maintenance_plan_detail', plan_id=p.id))
    machines = Machine.query.order_by(Machine.name).all()
    workers = Monteur.query.filter_by(actief=True).all()
    return render_template('maintenance_plan_form.html', plan=None, machines=machines, workers=workers)

@bp.route('/maintenance-plans/<int:plan_id>')
@login_required
def maintenance_plan_detail(plan_id):
    p = MaintenancePlan.query.get_or_404(plan_id)
    return render_template('maintenance_plan_detail.html', plan=p)

@bp.route('/maintenance-plans/<int:plan_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def maintenance_plan_edit(plan_id):
    p = MaintenancePlan.query.get_or_404(plan_id)
    if request.method == 'POST':
        try:
            p.machine_id = int(request.form['machine_id'])
            p.planned_start = datetime.strptime(request.form['planned_start'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid machine or date'), 'error')
            return redirect(url_for('maintenance.maintenance_plan_edit', plan_id=p.id))
        p.title = request.form['title']
        p.description = request.form.get('description', '')
        p.maintenance_type = request.form.get('maintenance_type', p.maintenance_type)
        p.status = request.form.get('status', p.status)
        p.planned_end = (d := safe_date(request.form.get('planned_end'))) and d.date() or None
        p.actual_start = (d := safe_date(request.form.get('actual_start'))) and d.date() or None
        p.actual_end = (d := safe_date(request.form.get('actual_end'))) and d.date() or None
        p.is_external = 'is_external' in request.form
        p.company_name = request.form.get('company_name', '')
        p.company_contact = request.form.get('company_contact', '')
        p.company_person = request.form.get('company_person', '')
        p.worker_id = safe_int(request.form.get('worker_id')) or None
        p.parts_used = request.form.get('parts_used', '[]')
        p.cost = safe_float(request.form.get('cost'), 0)
        p.report = request.form.get('report', '')
        p.next_maintenance = (d := safe_date(request.form.get('next_maintenance'))) and d.date() or None
        old_status = p.status
        p.recurrence = request.form.get('recurrence', '') or None
        p.notes = request.form.get('notes', '')
        if 'offer_file' in request.files and request.files['offer_file'].filename:
            fn = save_uploaded_file(request.files['offer_file'], prefix='offer_')
            if fn:
                p.offer_file = fn
        if 'work_act_file' in request.files and request.files['work_act_file'].filename:
            fn = save_uploaded_file(request.files['work_act_file'], prefix='act_')
            if fn:
                p.work_act_file = fn
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('maintenance.maintenance_plan_edit', plan_id=p.id))

        # Auto-create next recurring plan on completion
        if p.status == 'completed' and old_status != 'completed' and p.recurrence and p.recurrence != 'none':
            base = p.actual_end or p.planned_start
            rec = p.recurrence
            if rec == 'daily':
                new_date = base + timedelta(days=1)
            elif rec == 'weekly':
                new_date = base + timedelta(weeks=1)
            elif rec == 'biweekly':
                new_date = base + timedelta(weeks=2)
            elif rec == 'monthly':
                try:
                    new_date = base.replace(year=base.year + (1 if base.month == 12 else 0), month=(base.month % 12) + 1)
                except ValueError:
                    new_date = base.replace(year=base.year + (1 if base.month == 12 else 0), month=(base.month % 12) + 1, day=28)
            elif rec == 'quarterly':
                m = base.month + 3
                y = base.year + (1 if m > 12 else 0)
                m = ((m - 1) % 12) + 1
                try:
                    new_date = base.replace(year=y, month=m)
                except ValueError:
                    new_date = base.replace(year=y, month=m, day=28)
            elif rec == 'yearly':
                try:
                    new_date = base.replace(year=base.year + 1)
                except ValueError:
                    new_date = base.replace(year=base.year + 1, day=28)
            else:
                new_date = None

            if new_date:
                new_plan = MaintenancePlan(
                    machine_id=p.machine_id,
                    title=p.title,
                    description=p.description,
                    maintenance_type=p.maintenance_type,
                    status='planned',
                    planned_start=new_date,
                    recurrence=p.recurrence,
                    created_by=current_user.id
                )
                db.session.add(new_plan)
                if not safe_commit():
                    flash(_('Save failed'), 'error')
                    return redirect(url_for('maintenance.maintenance_plan_edit', plan_id=p.id))
                flash(_('Maintenance plan updated') + f'. {_("Next")}: {new_date.strftime("%d-%m-%Y")}', 'success')
            else:
                flash(_('Maintenance plan updated'), 'success')
        else:
            flash(_('Maintenance plan updated'), 'success')

        return redirect(url_for('maintenance.maintenance_plan_detail', plan_id=p.id))
    machines = Machine.query.order_by(Machine.name).all()
    workers = Monteur.query.filter_by(actief=True).all()
    return render_template('maintenance_plan_form.html', plan=p, machines=machines, workers=workers)

@bp.route('/maintenance-plans/<int:plan_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def maintenance_plan_delete(plan_id):
    p = MaintenancePlan.query.get_or_404(plan_id)
    db.session.delete(p)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('maintenance.maintenance_plans_list'))
    flash(_('Maintenance plan deleted'), 'success')
    return redirect(url_for('maintenance.maintenance_plans_list'))

@bp.route('/api/maintenance/plans')
@login_required
def api_maintenance_plans():
    today = datetime.utcnow().date()
    month = request.args.get('month', today.strftime('%Y-%m'))
    year, mon = map(int, month.split('-'))
    month_start = datetime(year, mon, 1).date()
    if mon == 12:
        month_end = datetime(year + 1, 1, 1).date()
    else:
        month_end = datetime(year, mon + 1, 1).date()

    if current_user.has_role('admin', 'director', 'technician'):
        plans = MaintenancePlan.query.all()
    else:
        machine_ids = [m.id for m in current_user.assigned_machines]
        plans = MaintenancePlan.query.filter(MaintenancePlan.machine_id.in_(machine_ids)).all()

    result = []
    for p in plans:
        if p.planned_start and month_start <= p.planned_start < month_end:
            result.append({
                'id': p.id, 'title': p.title, 'machine': p.machine.name,
                'machine_id': p.machine_id, 'type': p.maintenance_type,
                'status': p.status, 'start': p.planned_start.isoformat(),
                'end': p.planned_end.isoformat() if p.planned_end else None,
                'is_external': p.is_external,
                'company': p.company_name if p.is_external else None,
                'worker': p.worker.naam if p.worker else None,
            })
    return jsonify(result)

@bp.route('/api/maintenance/reminders')
@login_required
def api_maintenance_reminders():
    today = datetime.utcnow().date()
    soon = today + timedelta(days=14)

    # Filter at DB level: only parts with upcoming dates
    part_q = MachinePart.query.filter(
        db.or_(
            MachinePart.next_replacement <= soon,
            MachinePart.next_maintenance <= soon
        )
    )
    if not current_user.has_role('admin', 'director', 'technician'):
        machine_ids = [m.id for m in current_user.assigned_machines]
        part_q = part_q.filter(MachinePart.machine_id.in_(machine_ids))

    reminders = []
    for p in part_q.all():
        if p.next_replacement and p.next_replacement <= soon:
            days_left = (p.next_replacement - today).days
            reminders.append({
                'type': 'replacement', 'part': p.name, 'machine': p.machine.name,
                'machine_id': p.machine_id, 'part_id': p.id,
                'date': p.next_replacement.isoformat(),
                'days_left': days_left, 'overdue': days_left < 0
            })
        if p.next_maintenance and p.next_maintenance <= soon:
            days_left = (p.next_maintenance - today).days
            reminders.append({
                'type': 'maintenance', 'part': p.name, 'machine': p.machine.name,
                'machine_id': p.machine_id, 'part_id': p.id,
                'date': p.next_maintenance.isoformat(),
                'days_left': days_left, 'overdue': days_left < 0
            })

    consumables = VoorraadItem.query.filter(
        VoorraadItem.consumable_type.isnot(None),
        VoorraadItem.consumable_type != '',
        VoorraadItem.next_replacement.isnot(None),
        VoorraadItem.next_replacement <= soon
    ).all()
    for c in consumables:
        days_left = (c.next_replacement - today).days
        reminders.append({
            'type': 'consumable', 'part': c.naam, 'machine': c.compatible_machines or '—',
            'machine_id': None, 'part_id': c.id,
            'date': c.next_replacement.isoformat(),
            'days_left': days_left, 'overdue': days_left < 0,
            'consumable_type': c.consumable_type, 'volume': c.volume or ''
        })

    reminders.sort(key=lambda r: r['date'])
    return jsonify(reminders)

@bp.route('/maintenance-schedule')
@login_required
@role_required('admin', 'director', 'technician')
def maintenance_schedule():
    from models import Equipment
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

    today = datetime.utcnow().date()
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
                    created_by=current_user.id
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

