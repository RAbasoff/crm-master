"""Maintenance plans and maintenance API routes."""
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
from . import _target_name

try:
    from blueprints.two import gen_two_number
    from models import MachineTOChecklist, TWOChecklistItem
    from blueprints.machines import ensure_machine_to_checklists
except Exception:
    gen_two_number = None


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


def _parse_target_id(tid):
    """Parse 'm12' / 'e3' / 'u7' → ('machine'|'equipment'|'mule', id)."""
    tid = (tid or '').strip()
    if tid.startswith('m') and tid[1:].isdigit():
        return 'machine', int(tid[1:])
    if tid.startswith('e') and tid[1:].isdigit():
        return 'equipment', int(tid[1:])
    if tid.startswith('u') and tid[1:].isdigit():
        return 'mule', int(tid[1:])
    return None, None

@bp.route('/maintenance-plans/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def maintenance_plan_new():
    if request.method == 'POST':
        targets = request.form.getlist('target_ids')
        title = (request.form.get('title') or '').strip() or _('Maintenance')
        try:
            planned_start = datetime.strptime(request.form['planned_start'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid date'), 'error')
            return redirect(url_for('maintenance.maintenance_plan_new'))
        if not targets:
            flash(_('Select machine / equipment'), 'error')
            return redirect(url_for('maintenance.maintenance_plan_new'))
        original = planned_start
        planned_start = skip_weekend(planned_start)
        if planned_start != original:
            flash(_('Date moved from weekend to Monday: {}').format(planned_start.strftime('%d-%m-%Y')), 'info')
        planned_end = safe_date(request.form.get('planned_end'))
        if planned_end:
            planned_end = planned_end.date()
        else:
            planned_end = planned_start + timedelta(days=3)
        create_two = request.form.get('create_two') == 'yes'
        worker_id = safe_int(request.form.get('worker_id')) or None
        worker_ids = [safe_int(x) for x in request.form.getlist('worker_ids') if safe_int(x)]
        if worker_ids and not worker_id:
            worker_id = worker_ids[0]
        created = 0
        last_p = None
        created_plans = []
        for tid in targets:
            kind, target_id = _parse_target_id(tid)
            if kind == 'machine':
                machine_id, equipment_id, mule_id = target_id, None, None
            elif kind == 'equipment':
                machine_id, equipment_id, mule_id = None, target_id, None
            elif kind == 'mule':
                machine_id, equipment_id, mule_id = None, None, target_id
            else:
                continue
            p = MaintenancePlan(
                machine_id=machine_id,
                equipment_id=equipment_id,
                mule_id=mule_id,
                title=title,
                description=request.form.get('description', ''),
                maintenance_type=request.form.get('maintenance_type', 'preventive'),
                status=request.form.get('status', 'planned'),
                planned_start=planned_start,
                planned_end=planned_end,
                window_days=(planned_end - planned_start).days + 1,
                is_external='is_external' in request.form,
                company_name=request.form.get('company_name', ''),
                company_contact=request.form.get('company_contact', ''),
                company_person=request.form.get('company_person', ''),
                worker_id=worker_id,
                parts_used=request.form.get('parts_used', '[]'),
                cost=safe_float(request.form.get('cost'), 0),
                report=request.form.get('report', ''),
                next_maintenance=(d := safe_date(request.form.get('next_maintenance'))) and d.date() or None,
                recurrence=request.form.get('recurrence', '') or None,
                notes=request.form.get('notes', ''),
                created_by=resolve_user_id()
            )
            for wid in worker_ids:
                w = Monteur.query.get(wid)
                if w:
                    p.workers.append(w)
            if 'offer_file' in request.files and request.files['offer_file'].filename:
                fn = save_uploaded_file(request.files['offer_file'], prefix='offer_')
                if fn:
                    p.offer_file = fn
            if 'work_act_file' in request.files and request.files['work_act_file'].filename:
                fn = save_uploaded_file(request.files['work_act_file'], prefix='act_')
                if fn:
                    p.work_act_file = fn
            db.session.add(p)
            created += 1
            last_p = p
            created_plans.append(p)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('maintenance.maintenance_plan_new'))
        if last_p is None:
            flash(_('No valid targets'), 'error')
            return redirect(url_for('maintenance.maintenance_plan_new'))
        # TWO — на КАЖДЫЙ созданный план со станком
        if create_two and (worker_ids or worker_id):
            created_twos = 0
            if gen_two_number:
                for p in created_plans:
                    if not p.machine_id:
                        continue
                    two = TechnicalWorkOrder(
                        number=gen_two_number(),
                        machine_id=p.machine_id,
                        description=f'{p.title}\n{p.description or ""}',
                        planned_date=p.planned_start,
                        status='assigned',
                        created_by=resolve_user_id()
                    )
                    db.session.add(two)
                    db.session.flush()
                    for wid in (worker_ids or ([worker_id] if worker_id else [])):
                        w = Monteur.query.get(wid)
                        if w:
                            two.workers.append(w)
                    try:
                        ensure_machine_to_checklists(p.machine_id)
                        period = (p.recurrence or 'monthly') or 'monthly'
                        items = MachineTOChecklist.query.filter_by(
                            machine_id=p.machine_id, period=period, is_active=True
                        ).order_by(MachineTOChecklist.sort_order, MachineTOChecklist.id).all()
                        if not items:
                            items = MachineTOChecklist.query.filter_by(
                                machine_id=p.machine_id, period='monthly', is_active=True
                            ).order_by(MachineTOChecklist.sort_order).all()
                        for i, it in enumerate(items, 1):
                            db.session.add(TWOChecklistItem(two_id=two.id, text=it.text, sort_order=i))
                    except Exception as _ce:
                        print('TWO checklist insert skip', _ce)
                    created_twos += 1
                safe_commit()
                if created_twos:
                    flash(_('Created TWO for machines: %(n)d', n=created_twos), 'success')
        flash(_('Created %(n)d plans', n=created) if created != 1 else _('Plan created'), 'success')
        return redirect(url_for('maintenance.maintenance_plans_list'))
    machines = Machine.query.order_by(Machine.name).all()
    workers = Monteur.query.filter_by(actief=True).all()
    return render_template('maintenance_plan_form.html', plan=None, machines=machines, workers=workers, equipment_list=Equipment.query.order_by(Equipment.name).all(),
                           mules=EquipmentMaintenance.query.filter(EquipmentMaintenance.record_type.isnot('repair')).order_by(EquipmentMaintenance.name).all())

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
            targets = request.form.getlist('target_ids')
            if not targets and request.form.get('machine_id'):
                targets = [f"m{request.form.get('machine_id')}"]
            kind, target_id = _parse_target_id(targets[0]) if targets else (None, None)
            if kind is None:
                raise ValueError('no target')
            p.mule_id = None
            if kind == 'machine':
                p.machine_id, p.equipment_id = target_id, None
            elif kind == 'mule':
                p.machine_id, p.equipment_id, p.mule_id = None, None, target_id
            else:
                p.machine_id, p.equipment_id = None, target_id
            p.planned_start = datetime.strptime(request.form['planned_start'], '%Y-%m-%d').date()
        except (ValueError, KeyError, IndexError):
            flash(_('Invalid machine or date'), 'error')
            return redirect(url_for('maintenance.maintenance_plan_edit', plan_id=p.id))
        p.title = request.form['title']
        p.description = request.form.get('description', '')
        p.maintenance_type = request.form.get('maintenance_type', p.maintenance_type)
        old_status = p.status
        p.status = request.form.get('status', p.status)
        p.planned_end = (d := safe_date(request.form.get('planned_end'))) and d.date() or None
        if p.planned_end and p.planned_start:
            p.window_days = (p.planned_end - p.planned_start).days + 1
        p.actual_start = (d := safe_date(request.form.get('actual_start'))) and d.date() or None
        p.actual_end = (d := safe_date(request.form.get('actual_end'))) and d.date() or None
        p.is_external = 'is_external' in request.form
        p.company_name = request.form.get('company_name', '')
        p.company_contact = request.form.get('company_contact', '')
        p.company_person = request.form.get('company_person', '')
        p.worker_id = safe_int(request.form.get('worker_id')) or None
        worker_ids = [safe_int(x) for x in request.form.getlist('worker_ids') if safe_int(x)]
        if worker_ids and not p.worker_id:
            p.worker_id = worker_ids[0]
        p.workers = []
        for wid in worker_ids:
            w = Monteur.query.get(wid)
            if w:
                p.workers.append(w)
        p.parts_used = request.form.get('parts_used', '[]')
        p.cost = safe_float(request.form.get('cost'), 0)
        p.report = request.form.get('report', '')
        p.next_maintenance = (d := safe_date(request.form.get('next_maintenance'))) and d.date() or None
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
                    equipment_id=p.equipment_id,
                    mule_id=p.mule_id,
                    title=p.title,
                    description=p.description,
                    maintenance_type=p.maintenance_type,
                    status='planned',
                    planned_start=new_date,
                    recurrence=p.recurrence,
                    created_by=resolve_user_id()
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
    return render_template('maintenance_plan_form.html', plan=p, machines=machines, workers=workers, equipment_list=Equipment.query.order_by(Equipment.name).all(),
                           mules=EquipmentMaintenance.query.filter(EquipmentMaintenance.record_type.isnot('repair')).order_by(EquipmentMaintenance.name).all())

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
    today = now_local().date()
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
                'id': p.id, 'title': p.title, 'machine': p.machine.name if p.machine else _target_name(p),
                'machine_id': p.machine_id, 'type': p.maintenance_type,
                'status': p.status, 'start': p.planned_start.isoformat(),
                'end': p.planned_end.isoformat() if p.planned_end else None,
                'is_external': p.is_external,
                'company': p.company_name if p.is_external else None,
                'worker': p.worker.naam if p.worker else None,
            })
    return jsonify(result)

@bp.route('/api/maintenance/today')
@login_required
@role_required('admin', 'director', 'technician')
def api_maintenance_today():
    """ТО на сегодня — для напоминания при входе (как смена ножей)."""
    today = now_local().date()
    items = []

    # Планы ТО, окно которых включает сегодня
    plans = MaintenancePlan.query.all()
    for pl in plans:
        if pl.status == 'completed' or not pl.planned_start:
            continue
        we = pl.window_end_date()
        if pl.planned_start <= today <= we:
            items.append({
                'kind': 'plan',
                'machine': _target_name(pl) if pl.machine else '—',
                'machine_id': pl.machine_id,
                'title': pl.title,
                'window': f"{pl.planned_start.strftime('%d-%m')} – {we.strftime('%d-%m')}",
                'type': pl.maintenance_type or 'preventive',
            })

    # Детали: замена / ТО сегодня
    for p in MachinePart.query.all():
        if p.machine is None:
            continue
        if p.next_replacement == today:
            items.append({'kind': 'replacement', 'machine': p.machine.name if p.machine else _target_name(p), 'machine_id': p.machine_id,
                          'title': p.name, 'window': today.strftime('%d-%m'), 'type': 'replacement'})
        if p.next_maintenance == today:
            items.append({'kind': 'maintenance', 'machine': p.machine.name if p.machine else _target_name(p), 'machine_id': p.machine_id,
                          'title': p.name, 'window': today.strftime('%d-%m'), 'type': 'maintenance'})

    # Оборудование
    try:
        for eq in Equipment.query.filter(Equipment.next_service_date == today).all():
            items.append({'kind': 'equipment', 'machine': eq.name, 'machine_id': None,
                          'title': eq.equipment_type or eq.name, 'window': today.strftime('%d-%m'), 'type': 'service'})
    except Exception:
        pass

    return jsonify(items)


@bp.route('/api/mule/reminders')
@login_required
def api_mule_reminders():
    """Ближайшее и просроченное ТО Mule (для всплывающих напоминаний)."""
    today = now_local().date()
    soon = today + timedelta(days=14)
    q = EquipmentMaintenance.query.filter(
        EquipmentMaintenance.next_date.isnot(None),
        EquipmentMaintenance.record_type.isnot('repair'),
        EquipmentMaintenance.status != 'completed')
    items = []
    for mro in q.all():
        d = mro.next_date.date() if isinstance(mro.next_date, datetime) else mro.next_date
        if not d or d > soon:
            continue
        items.append({
            'name': mro.name or mro.number,
            'number': mro.number,
            'machine': (mro.machine.name if mro.machine else (mro.serial or '')),
            'date': d.isoformat(),
            'days_left': (d - today).days,
            'overdue': bool(d < today),
        })
    items.sort(key=lambda x: (not x['overdue'], x['date']))
    return jsonify(items[:20])


@bp.route('/api/maintenance/reminders')
@login_required
def api_maintenance_reminders():
    today = now_local().date()
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
                'type': 'replacement', 'part': p.name, 'machine': p.machine.name if p.machine else _target_name(p),
                'machine_id': p.machine_id, 'part_id': p.id,
                'date': p.next_replacement.isoformat(),
                'days_left': days_left, 'overdue': days_left < 0
            })
        if p.next_maintenance and p.next_maintenance <= soon:
            days_left = (p.next_maintenance - today).days
            reminders.append({
                'type': 'maintenance', 'part': p.name, 'machine': p.machine.name if p.machine else _target_name(p),
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

