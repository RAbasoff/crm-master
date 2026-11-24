"""Maintenance calendar, yearly plan and works-calendar routes."""
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

from models import Equipment as _Eq, WeekendShift
from sqlalchemy.orm import joinedload
from flask_babel import format_date
import traceback
import calendar

from . import bp
from . import _target_name
from .plans import skip_weekend


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

    def _log_date(log):
        return log.date.date() if hasattr(log.date, 'date') else log.date

    def _is_part_done(p, logs, due, ltype):
        """Done only for THIS due date: work logged on the date, slightly
        early (≤14 days), or after it. Раньше любая запись внутри цикла
        помечала будущее ТО выполненным — «выполнено» стояло на работах,
        до которых ещё далеко."""
        acts = ('replacement', 'replaced') if ltype == 'replacement' else ('maintenance', 'maint', 'replaced')
        window_start = due - timedelta(days=14)
        for log in logs:
            if log.action not in acts or not log.date:
                continue
            ld = _log_date(log)
            if ld == due or (window_start <= ld < due) or ld > due:
                return True
        return False

    def _add_done_from_date(events, seen, date, ltype, p, record_id=None):
        """Force a green done event at a date (never lose the record)."""
        if not date or not (month_start <= date < month_end):
            return
        key = (p.id, ltype, date)
        if key in seen:
            return
        # mark existing planned event as done instead of duplicating
        for e in events:
            if e.get('part_id') == p.id and e.get('type') == ltype and e.get('date') == date:
                e['done'] = True
                e['overdue'] = False
                return
        seen.add(key)
        events.append({
            'date': date,
            'type': ltype,
            'part': p.name,
            'machine': p.machine.name if p.machine else _target_name(p),
            'machine_id': p.machine_id,
            'part_id': p.id,
            'category': p.category,
            'overdue': False,
            'done': True,
            'plan_id': None,
            'equipment_id': None,
            'mro_id': None,
            'record_id': record_id
        })

    done_seen = set()
    for p in parts:
        if p.next_replacement and month_start <= p.next_replacement < month_end:
            done = _is_part_done(p, logs_by_part.get(p.id, []), p.next_replacement, 'replacement')
            events.append({
                'date': p.next_replacement,
                'type': 'replacement',
                'part': p.name,
                'machine': p.machine.name if p.machine else _target_name(p),
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
            done = _is_part_done(p, logs_by_part.get(p.id, []), p.next_maintenance, 'maintenance')
            events.append({
                'date': p.next_maintenance,
                'type': 'maintenance',
                'part': p.name,
                'machine': p.machine.name if p.machine else _target_name(p),
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
        # Выполненные работы из журнала — запись остаётся в календаре (зелёной)
        for log in logs_by_part.get(p.id, []):
            if not log.date:
                continue
            ld = _log_date(log)
            if log.action in ('replacement', 'replaced'):
                ltype = 'replacement'
            elif log.action in ('maintenance', 'maint'):
                ltype = 'maintenance'
            else:
                continue
            _add_done_from_date(events, done_seen, ld, ltype, p)
        # last_replacement / last_maintenance — страховка от «исчезновения»
        if p.last_replacement:
            _add_done_from_date(events, done_seen, p.last_replacement, 'replacement', p)
        if p.last_maintenance:
            _add_done_from_date(events, done_seen, p.last_maintenance, 'maintenance', p)
        # Check maintenance records (from batch-fetched data)
        for mr in maint_by_machine.get(p.machine_id, []):
            if mr.next_maintenance and month_start <= mr.next_maintenance.date() < month_end:
                done = False
                for log in logs_by_part.get(p.id, []):
                    if log.date and _log_date(log) >= mr.next_maintenance.date():
                        done = True
                        break
                if mr.date_performed and mr.date_performed.date() >= mr.next_maintenance.date():
                    done = True
                events.append({
                    'date': mr.next_maintenance.date(),
                    'type': 'machine_maintenance',
                    'part': mr.description[:40],
                    'machine': p.machine.name if p.machine else _target_name(p),
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
            # выполненное ТО по журналу работ — остаётся зелёным
            if mr.date_performed:
                pd = mr.date_performed.date() if hasattr(mr.date_performed, 'date') else mr.date_performed
                if month_start <= pd < month_end:
                    skip = any(e.get('type') == 'machine_maintenance' and e.get('record_id') == mr.id and e.get('date') == pd for e in events)
                    if not skip:
                        events.append({
                            'date': pd,
                            'type': 'machine_maintenance',
                            'part': mr.description[:40],
                            'machine': p.machine.name if p.machine else _target_name(p),
                            'machine_id': p.machine_id,
                            'part_id': None,
                            'category': mr.maintenance_type,
                            'overdue': False,
                            'done': True,
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
    # Отметки «выполнено» в журнале оборудования (планы без станка):
    # _log_equipment_oh пишет запись OJ-{plan_id}-{YYYYMMDD} (журнал-отметка)
    oh_nums = {e.number for e in EquipmentMaintenance.query.filter(
        EquipmentMaintenance.number.like('OJ-%')).all()}
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
            if not done and f'OJ-{pl.id}-{pl.planned_start:%Y%m%d}' in oh_nums:
                done = True
            events.append({
                'date': pl.planned_start,
                'type': 'plan',
                'part': pl.title[:40],
                'machine': _target_name(pl),
                'machine_id': pl.machine_id,
                'part_id': None,
                'category': pl.maintenance_type,
                'overdue': pl.window_end_date() < today and not done,
                'plan_id': pl.id,
                'status': 'completed' if done else pl.status,
                'done': done,
                'window_start': pl.planned_start.isoformat() if pl.planned_start else None,
                'window_end': pl.window_end_date().isoformat() if pl.planned_start else None,
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
                    occ_done = pl.status in ('completed',)
                    for mr in maint_by_machine.get(pl.machine_id, []):
                        if mr.date_performed and mr.date_performed.date() == d:
                            occ_done = True
                            break
                        # заполнено при отметке «выполнено» на эту дату
                        if mr.next_maintenance and mr.next_maintenance.date() == d and mr.date_performed:
                            occ_done = True
                            break
                    if not occ_done and f'OJ-{pl.id}-{d:%Y%m%d}' in oh_nums:
                        occ_done = True
                    events.append({
                        'date': d,
                        'type': 'plan',
                        'part': pl.title[:40],
                        'machine': _target_name(pl),
                        'machine_id': pl.machine_id,
                        'part_id': None,
                        'category': pl.maintenance_type,
                        'overdue': (d + timedelta(days=3)) < today and not occ_done,
                        'plan_id': pl.id,
                        'status': 'completed' if occ_done else pl.status,
                        'done': occ_done,
                        'window_start': d.isoformat(),
                        'window_end': (d + timedelta(days=3)).isoformat(),
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
    # (Equipment/EquipmentMaintenance импортированы на уровне модуля;
    #  локальный import здесь затенял имя и ломал ранние запросы функции)
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
        # выполненное ТО оборудования — остаётся в календаре зелёным
        if eq.last_service_date and month_start <= eq.last_service_date < month_end:
            if not any(e.get('equipment_id') == eq.id and e.get('date') == eq.last_service_date for e in events):
                events.append({
                    'date': eq.last_service_date,
                    'type': 'equipment',
                    'part': eq.name[:40],
                    'machine': eq.equipment_type or eq.name[:20],
                    'machine_id': None,
                    'equipment_id': eq.id,
                    'part_id': None,
                    'category': eq.category or 'service',
                    'overdue': False,
                    'done': True,
                    'plan_id': None,
                    'mro_id': None,
                    'record_id': None
                })

    # Add EquipmentMaintenance (Mule TO) — по графику: периодичность даёт
    # повторные даты в месяце. Ремонты (record_type='repair') — не план.
    # Записи журнала OJ-* (отметки «выполнено» по планам без станка) — не
    # график: у них next_date нет у новых, а у старых он есть — отфильтровываем.
    mro_q = EquipmentMaintenance.query.filter(
        EquipmentMaintenance.next_date.isnot(None),
        EquipmentMaintenance.record_type.isnot('repair'),
        db.not_(EquipmentMaintenance.number.like('OJ-%')))
    m_last = month_end - timedelta(days=1)
    for mro in mro_q.all():
        if not mro.next_date:
            continue
        d0 = mro.next_date.date() if isinstance(mro.next_date, datetime) else mro.next_date
        dates = _occurrences_in_month(d0, (mro.periodicity or ''), month_start, m_last)
        # «Выполнено» — только реально отмеченные даты графика (✅ в календаре).
        # status записи ('completed' по умолчанию) — состояние самой работы,
        # а НЕ плана: раньше из-за этого все будущие ТО были зелёными.
        try:
            done_set = mro.completed_date_set()
        except Exception:
            done_set = set()
        m_name = (mro.machine.name if mro.machine else mro.number)
        for d in dates:
            # Автодаты графика не попадают на сб/вс (запрет работы в выходные);
            # вручную на выходной можно поставить только с подтверждением.
            if d != d0 and d.weekday() >= 5:
                d = skip_weekend(d)
            done = d.isoformat() in done_set
            events.append({
                'date': d,
                'type': 'equipment_mro',
                'part': mro.name[:40],
                'machine': m_name,
                'machine_id': mro.machine_id,
                'equipment_id': None,
                'part_id': None,
                'category': mro.periodicity or 'MRO',
                'overdue': d < today and not done,
                'done': done,
                'plan_id': None,
                'mro_id': mro.id,
                'record_id': None
            })
        # выполненные даты остаются зелёными в календаре (даже после сдвига плана)
        for iso in done_set:
            try:
                ld = datetime.strptime(iso, '%Y-%m-%d').date()
            except Exception:
                continue
            if month_start <= ld <= m_last and ld not in dates:
                events.append({
                    'date': ld,
                    'type': 'equipment_mro',
                    'part': mro.name[:40],
                    'machine': m_name,
                    'machine_id': mro.machine_id,
                    'equipment_id': None,
                    'part_id': None,
                    'category': mro.periodicity or 'MRO',
                    'overdue': False,
                    'done': True,
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
                overdue.append({'date': p.next_replacement, 'type': 'replacement', 'part': p.name, 'machine': p.machine.name if p.machine else _target_name(p), 'machine_id': p.machine_id, 'part_id': p.id, 'category': p.category, 'plan_id': None, 'equipment_id': None, 'mro_id': None, 'record_id': None})
        if p.next_maintenance and p.next_maintenance < today:
            done_after = PartMaintenanceLog.query.filter(
                PartMaintenanceLog.part_id == p.id,
                PartMaintenanceLog.action.in_(['replaced', 'maintenance']),
                PartMaintenanceLog.date >= datetime.combine(p.next_maintenance, datetime.min.time())
            ).first()
            if not done_after:
                overdue.append({'date': p.next_maintenance, 'type': 'maintenance', 'part': p.name, 'machine': p.machine.name if p.machine else _target_name(p), 'machine_id': p.machine_id, 'part_id': p.id, 'category': p.category, 'plan_id': None, 'equipment_id': None, 'mro_id': None, 'record_id': None})
    
    # Navigation
    prev_month = (month_start - timedelta(days=1)).strftime('%Y-%m')
    next_month = month_end.strftime('%Y-%m')
    
    # Прочее оборудование без даты ТО — чтобы не «терялось» из списка
    eq_all = _Eq.query.all()
    equipment_unscheduled = [e for e in eq_all if not e.next_service_date]

    # Дни выходные, где у кого-то из механиков отмечена работа (WeekendShift)
    weekend_work_days = set()
    try:
        _ws_from = month_start - timedelta(days=7)
        _ws_to = month_end + timedelta(days=7)
        for ws in WeekendShift.query.filter(
                WeekendShift.shift_date >= _ws_from,
                WeekendShift.shift_date < _ws_to).all():
            if ws.shift_date:
                weekend_work_days.add(ws.shift_date.isoformat())
    except Exception:
        pass

    # Локализованное имя месяца («ноябрь 2026» / «november 2026») — для печати
    try:
        month_name = format_date(month_start, 'LLLL y', rebase=False)
    except Exception:
        month_name = month_start.strftime('%B %Y')

    return render_template('maintenance_calendar.html',
        month=month, month_start=month_start, month_end=month_end,
        events=sorted(events, key=lambda e: e['date']),
        overdue=overdue, today=today,
        prev_month=prev_month, next_month=next_month,
        timedelta=timedelta,
        month_name=month_name,
        weekend_work_days=weekend_work_days,
        equipment_unscheduled=equipment_unscheduled)

def _log_equipment_oh(plan, ev_d, user):
    """Отметка ТО плана «Прочее оборудование» (без машины).

    В maintenance_record.machine_id NOT NULL — поэтому такие планы
    отмечаются записью в журнале оборудования (EquipmentMaintenance).
    """
    d = ev_d.date() if hasattr(ev_d, 'date') else ev_d
    # Номер уникален: повторная отметка того же ТО в тот же день не должна падать
    num = f'OJ-{plan.id}-{d:%Y%m%d}'
    if EquipmentMaintenance.query.filter_by(number=num).first():
        return
    db.session.add(EquipmentMaintenance(
        number=num,
        name=(plan.title or 'OH')[:200],
        machine_id=plan.machine_id,
        date=d,
        reason=f'{plan.title} — completed from calendar by {user.display_name or user.username}',
        # Без next_date/periodicity: это ЖУРНАЛ выполненной работы, а не
        # график. График живёт в самом плане (recurrence) — иначе запись
        # плодила дубли событий в календаре (plan + equipment_mro на одну дату).
        status='completed',
        created_by=user.id if isinstance(user.id, int) else None,
    ))


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
                try:
                    event_dt = datetime.strptime(event_date, '%Y-%m-%d') if event_date else datetime.utcnow()
                except ValueError:
                    event_dt = datetime.utcnow()
                ev_d = event_dt.date() if hasattr(event_dt, 'date') else event_dt
                if plan.recurrence and plan.recurrence != 'none':
                    # Recurring plan — mark only THIS occurrence as done
                    if plan.machine_id:
                        mr = MaintenanceRecord(
                            machine_id=plan.machine_id,
                            maintenance_type=plan.maintenance_type,
                            description=f'{plan.title} ({event_date}) — completed by {current_user.display_name or current_user.username}',
                            performed_by=resolve_user_id(),
                            date_performed=event_dt,
                            next_maintenance=event_dt,
                            cost=0
                        )
                        db.session.add(mr)
                        # также в журнал — чтобы событие осталось зелёным в календаре
                        first_part = MachinePart.query.filter_by(machine_id=plan.machine_id).first()
                        if first_part:
                            db.session.add(PartMaintenanceLog(
                                part_id=first_part.id,
                                action='maintenance',
                                description=f'{plan.title[:80]} — completed from calendar by {current_user.display_name or current_user.username}',
                                performed_by=resolve_user_id(),
                                date=event_dt
                            ))
                    else:
                        # План по «прочему оборудованию» (без машины) —
                        # журнал оборудования, иначе NOT NULL по machine_id
                        _log_equipment_oh(plan, ev_d, current_user)
                    db.session.commit()
                    flash(_('Occurrence marked as completed'), 'success')
                else:
                    # Non-recurring plan — mark whole plan done
                    plan.status = 'completed'
                    plan.actual_end = ev_d
                    if plan.machine_id:
                        mr = MaintenanceRecord(
                            machine_id=plan.machine_id,
                            maintenance_type=plan.maintenance_type,
                            description=f'{plan.title} — completed by {current_user.display_name or current_user.username}',
                            performed_by=resolve_user_id(),
                            date_performed=event_dt,
                            next_maintenance=event_dt,
                            cost=0
                        )
                        db.session.add(mr)
                        first_part = MachinePart.query.filter_by(machine_id=plan.machine_id).first()
                        if first_part:
                            db.session.add(PartMaintenanceLog(
                                part_id=first_part.id,
                                action='maintenance',
                                description=f'{plan.title[:80]} — completed from calendar by {current_user.display_name or current_user.username}',
                                performed_by=resolve_user_id(),
                                date=event_dt
                            ))
                    db.session.commit()
                    flash(_('Plan marked as completed'), 'success')
            else:
                flash(_('Plan not found'), 'error')

        elif ev_type in ('replacement', 'maintenance') and part_id:
            part = MachinePart.query.get(int(part_id))
            if part:
                action = 'replacement' if ev_type == 'replacement' else 'maintenance'
                event_date = request.form.get('date')
                try:
                    ev_d = datetime.strptime(event_date, '%Y-%m-%d').date() if event_date else datetime.utcnow().date()
                except ValueError:
                    ev_d = datetime.utcnow().date()
                log = PartMaintenanceLog(
                    part_id=part.id,
                    action=action,
                    description=f'Completed from calendar by {current_user.display_name or current_user.username}',
                    performed_by=resolve_user_id(),
                    date=datetime.combine(ev_d, datetime.min.time())
                )
                db.session.add(log)
                # Дату события НЕ сдвигаем в «сегодня» — иначе запись исчезает из месяца.
                # next_* = дата события + интервал (планирование), само событие остаётся зелёным.
                if action == 'replacement':
                    part.last_replacement = ev_d
                    if part.replacement_interval_days:
                        part.next_replacement = ev_d + timedelta(days=part.replacement_interval_days)
                else:
                    part.last_maintenance = ev_d
                    if part.maintenance_interval_days:
                        part.next_maintenance = ev_d + timedelta(days=part.maintenance_interval_days)
                    elif part.replacement_interval_days:
                        part.next_maintenance = ev_d + timedelta(days=part.replacement_interval_days)
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
                event_date = request.form.get('date')
                try:
                    ev_d = datetime.strptime(event_date, '%Y-%m-%d') if event_date else datetime.utcnow()
                except ValueError:
                    ev_d = datetime.utcnow()
                mr.date_performed = ev_d
                mr.performed_by = current_user.id
                # Create PartMaintenanceLog so calendar shows green (done=True)
                first_part = MachinePart.query.filter_by(machine_id=mr.machine_id).first()
                if first_part:
                    log = PartMaintenanceLog(
                        part_id=first_part.id,
                        action='maintenance',
                        description=f'{mr.description[:80]} — completed from calendar by {current_user.display_name or current_user.username}',
                        performed_by=resolve_user_id(),
                        date=ev_d
                    )
                    db.session.add(log)
                db.session.commit()
                flash(_('Maintenance marked as completed'), 'success')
            else:
                flash(_('Event not found'), 'error')

        elif ev_type == 'equipment':
            equip_id = request.form.get('equipment_id')
            if equip_id:
                eq = Equipment.query.get(int(equip_id))
                if eq:
                    event_date = request.form.get('date')
                    try:
                        ev_d = datetime.strptime(event_date, '%Y-%m-%d').date() if event_date else datetime.utcnow().date()
                    except ValueError:
                        ev_d = datetime.utcnow().date()
                    eq.last_service_date = ev_d
                    if eq.service_interval_days:
                        eq.next_service_date = ev_d + timedelta(days=eq.service_interval_days)
                    # без интервала — дата остаётся, запись остаётся в календаре как выполненная
                    db.session.commit()
                    flash(_('Equipment service marked as completed'), 'success')
                else:
                    flash(_('Equipment not found'), 'error')

        elif ev_type == 'equipment_mro':
            mro_id = request.form.get('mro_id')
            event_date = request.form.get('date')
            if mro_id:
                mro = EquipmentMaintenance.query.get(int(mro_id))
                if mro:
                    try:
                        ev_d = datetime.strptime(event_date, '%Y-%m-%d').date() if event_date else None
                    except ValueError:
                        ev_d = None
                    if ev_d is None:
                        nd = mro.next_date
                        ev_d = nd.date() if isinstance(nd, datetime) else nd
                    if ev_d is None:
                        ev_d = datetime.utcnow().date()
                    # 1) отметить именно ЭТУ дату графика выполненной
                    mro.mark_date_completed(ev_d)
                    # 2) план продолжается: next_date = следующее вхождение после неё
                    if (mro.periodicity or '').lower() in ('daily', 'weekly', 'biweekly',
                                                           'monthly', 'quarterly', 'semiannual', 'yearly'):
                        nxt = _next_occurrence_after(ev_d, (mro.periodicity or '').lower())
                        nd = mro.next_date
                        nd = nd.date() if isinstance(nd, datetime) else nd
                        if nxt and (nd is None or nxt > nd):
                            mro.next_date = nxt
                    db.session.commit()
                    flash(_('MRO marked as completed'), 'success')
                else:
                    flash(_('MRO not found'), 'error')

        else:
            flash(_('Unknown event type'), 'error')

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        flash(_('Error: %(err)s', err=str(e)[:100]), 'error')

    month = request.form.get('month', datetime.utcnow().strftime('%Y-%m'))
    return redirect(url_for('maintenance.maintenance_calendar', month=month))

# ── Квартальный график ТО на год (по каждой машине) ──────────────

_TO_DEFAULT_TASKS = [
    'Vervang verbruiksartikelen volgens schema',
    'Controleer hydrauliek / pneumatiek',
    "Onderhoudsact + foto's",
]

_TO_REC_DAYS = {
    'weekly': 7, 'biweekly': 14, 'monthly': 30, 'quarterly': 91,
    'semiannual': 182, 'yearly': 365,
}


def _add_months(d, k):
    """d + k месяцев (календарно, без сдвига на 30-дневные месяцы)."""
    y = d.year + (d.month - 1 + k) // 12
    m = (d.month - 1 + k) % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return d.replace(year=y, month=m, day=day)


def _occurrences(start, recurrence, lo, hi, limit=40):
    """Даты повторов события в диапазоне [lo, hi]."""
    if not start:
        return []
    rec = (recurrence or '').lower()
    step_days = {'weekly': 7, 'biweekly': 14}.get(rec)
    month_step = {'monthly': 1, 'quarterly': 3, 'semiannual': 6, 'yearly': 12}.get(rec)
    if not step_days and not month_step:
        return [start] if lo <= start <= hi else []
    out = []
    d = start
    n = 0
    while d <= hi and n < limit:
        if d >= lo:
            out.append(d)
        if step_days:
            d = d + timedelta(days=step_days)
        else:
            d = _add_months(d, month_step)
        n += 1
    return out


def _period_label(d0, span):
    """Период 2-4 дня: '12.04-14.04' (span = кол-во дней включительно).

    Конец не заходит на сб/вс — работы в выходные не планируются.
    """
    span = max(2, min(4, int(span or 3)))
    d1 = d0 + timedelta(days=span - 1)
    while d1.weekday() >= 5 and d1 > d0:
        d1 -= timedelta(days=1)
    s0, s1 = d0.strftime('%d.%m'), d1.strftime('%d.%m')
    return s0 if s1 == s0 else f'{s0}-{s1}'


def _next_occurrence_after(d, rec):
    """Следующее вхождение расписания после даты d."""
    step = {'daily': 1, 'weekly': 7, 'biweekly': 14}.get(rec)
    if step:
        return d + timedelta(days=step)
    mstep = {'monthly': 1, 'quarterly': 3, 'semiannual': 6, 'yearly': 12}.get(rec)
    if mstep:
        return _add_months(d, mstep)
    return None


def _occurrences_in_month(d0, rec, m_lo, m_hi):
    """Вхождения расписания (шаг от d0) внутри месяца [m_lo, m_hi] (включительно)."""
    if not d0 or d0 > m_hi:
        return []
    rec = (rec or '').lower()
    step = {'daily': 1, 'weekly': 7, 'biweekly': 14}.get(rec)
    if step:
        k = (m_lo - d0).days
        if k <= 0:
            first = d0
        else:
            first = d0 + timedelta(days=(k // step) * step)
            if first < m_lo:
                first += timedelta(days=step)
        out = []
        d = first
        while d <= m_hi and len(out) < 31:
            out.append(d)
            d += timedelta(days=step)
        return out
    mstep = {'monthly': 1, 'quarterly': 3, 'semiannual': 6, 'yearly': 12}.get(rec)
    if mstep:
        months = (m_lo.year - d0.year) * 12 + (m_lo.month - d0.month)
        kk = (months // mstep) * mstep if months > 0 else 0
        out = []
        while kk <= (months // mstep) * mstep + mstep * 2:
            d = _add_months(d0, kk)
            if d > m_hi:
                break
            if d >= m_lo:
                out.append(d)
            kk += mstep
        return out
    return [d0] if m_lo <= d0 <= m_hi else []


def _yearly_to_chart(year):
    """Строки графика: машины + скоростные ворота (Snelport).

    Сортировка — по цехам, внутри цеха по алфавиту. Ворота обслуживаются
    однотипно: общий список работ из maintenance_notes оборудования.
    """
    machines = Machine.query.filter(Machine.status == 'active').all()
    # ворота: скоростные (Snelport / DYNACO D-3xx) + секционные (Sectionaal poort / Port …)
    doors = Equipment.query.filter(db.or_(
        Equipment.equipment_type == 'Snelport',
        Equipment.name.like('%Snelport%'),
        Equipment.equipment_type.like('D-3%'),
        Equipment.equipment_type == 'Sectionaal poort',
        Equipment.name.like('Port %'),
    )).all()
    plans = [p for p in MaintenancePlan.query.all() if p.machine_id and p.planned_start]
    schedules = MaintenanceSchedule.query.filter_by(is_active=True).all()
    y_lo, y_hi = datetime(year, 1, 1).date(), datetime(year, 12, 31).date()
    q_lo = [datetime(year, 1, 1).date(), datetime(year, 4, 1).date(),
            datetime(year, 7, 1).date(), datetime(year, 10, 1).date()]
    q_hi = [datetime(year, 3, 31).date(), datetime(year, 6, 30).date(),
            datetime(year, 9, 30).date(), datetime(year, 12, 31).date()]

    def quarter_of(d):
        return (d.month - 1) // 3

    # ── Порядок цехов — по расположению на плане фабрики (сверху вниз,
    # слева направо); строки без цеха уходят в самый низ таблицы.
    _sections = FactorySection.query.all()
    _sec_rank = {s.id: ((s.floor_y if s.floor_y is not None else 1e9),
                        (s.floor_x if s.floor_x is not None else 1e9),
                        s.id) for s in _sections}
    _sec_names = {s.id: s.name for s in _sections}

    def sec_name_of(obj):
        sid = getattr(obj, 'section_id', None)
        return _sec_names.get(sid) or _('No section')

    def entry_key(e):
        """Порядок: цех по плану фабрики → станки → оборудование →
        по расстановке (координаты плана) → по имени. Без цеха — внизу."""
        kind, obj = e[0], e[1]
        sid = getattr(obj, 'section_id', None)
        if sid in _sec_rank:
            g = (0,) + _sec_rank[sid]
        else:
            g = (1, 0, 0, 0)
        kind_rank = 0 if kind == 'machine' else 1
        px = getattr(obj, 'floor_x', None)
        py = getattr(obj, 'floor_y', None)
        return (g
                + (kind_rank,
                   px if px is not None else 1e9,
                   py if py is not None else 1e9,
                   (obj.name or '').lower()))

    entries = [('machine', m, sec_name_of(m)) for m in machines]
    entries += [('equipment', d, sec_name_of(d)) for d in doors]
    entries.sort(key=entry_key)

    rows = []
    for idx, (kind, obj, sec) in enumerate(entries):
        q_plan = {0: {}, 1: {}, 2: {}, 3: {}}
        q_sched = {0: set(), 1: set(), 2: set(), 3: set()}
        if kind == 'machine':
            for p in plans:
                if p.machine_id != obj.id:
                    continue
                rec = (p.recurrence or 'none').lower()
                if rec in ('weekly', 'biweekly'):
                    continue  # частые операции — не квартальный выезд
                if p.recurrence and p.recurrence != 'none':
                    start = datetime.combine(p.planned_start, datetime.min.time()).date() if isinstance(p.planned_start, datetime) else p.planned_start
                    occ = _occurrences(start, p.recurrence, y_lo, y_hi)
                else:
                    d0 = p.planned_start.date() if isinstance(p.planned_start, datetime) else p.planned_start
                    occ = [d0] if y_lo <= d0 <= y_hi else []
                for d in occ:
                    q_plan[quarter_of(d)][d] = max(2, min(4, p.window_days or 3))
            for s in schedules:
                if s.machine_id != obj.id:
                    continue
                rec = (s.recurrence or 'monthly').lower()
                if rec in ('weekly', 'biweekly'):
                    continue
                for d in _occurrences(y_lo, s.recurrence or 'monthly', y_lo, y_hi):
                    q_sched[quarter_of(d)].add(d)
            _tqs = MachineTOChecklist.query.filter_by(machine_id=obj.id).filter(
                MachineTOChecklist.period.in_(('quarterly', 'semiannual', 'yearly'))
            ).order_by(MachineTOChecklist.period, MachineTOChecklist.id).all()
            tasks = [{'id': x.id, 'text': x.text} for x in _tqs] or [
                {'id': None, 'text': x} for x in _TO_DEFAULT_TASKS]
            sub = obj.machine_type or ''
        else:
            # скоростные ворота: единый список работ (однотипные)
            lines = [x.strip() for x in (obj.maintenance_notes or '').splitlines() if x.strip()]
            tasks = [{'id': None, 'text': x} for x in lines] or [
                {'id': None, 'text': x} for x in _TO_DEFAULT_TASKS]
            sub = obj.equipment_type or ''
        q_dates = {0: set(), 1: set(), 2: set(), 3: set()}
        for q in range(4):
            # приоритет: план (реальная дата) → шаблон → равномерная растяжка
            if q_plan[q]:
                d0 = min(q_plan[q])
                q_dates[q] = {d0}
                q_win = q_plan[q][d0]
            elif q_sched[q]:
                d0 = min(q_sched[q])
                q_dates[q] = {d0}
                q_win = 3
            else:
                offset = 7 + (idx * 7) % 78
                d = q_lo[q] + timedelta(days=offset)
                while d.weekday() >= 5:
                    d += timedelta(days=1)
                if d > q_hi[q]:
                    d = q_hi[q] - timedelta(days=q_hi[q].weekday())
                q_dates[q] = {d}
                q_win = 3
        quarters = []
        for q in range(4):
            d0 = sorted(q_dates[q])[0]
            quarters.append(_period_label(d0, 3))
        loc = getattr(obj, 'installation_location', None)
        if loc and str(loc).strip().lower() in ('none', 'null', '-', '—'):
            loc = None
        rows.append({
            'kind': kind,
            'tid': obj.id,
            'name': obj.name,
            'sub': sub,
            'loc': loc,
            'section': sec,
            'tasks': tasks,
            'quarters': quarters,
        })
    return rows


@bp.route('/maintenance/yearly-plan')
@login_required
@role_required('admin', 'director', 'technician', 'user', 'responsible')
def maintenance_yearly_plan():
    """Квартальный график ТО на следующий год (по каждой машине)."""
    today = datetime.utcnow().date()
    year = safe_int(request.args.get('year')) or (today.year + 1)
    rows = _yearly_to_chart(year)
    return render_template('maintenance_yearly_plan.html', rows=rows, year=year)


@bp.route('/maintenance/yearly-plan/tasks/<int:machine_id>', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only('tim', 'peter')
def maintenance_yearly_tasks_save(machine_id):
    """Сохранить список работ по машине (редактирование из графика ТО).

    Правится набор чек-листа машины с периодами quarterly/semiannual/yearly —
    именно он показан в графике. Новые позиции сохраняются как quarterly.
    """
    data = request.get_json() or {}
    items = data.get('items') or []
    if (data.get('kind') or '') == 'equipment':
        eq = Equipment.query.get_or_404(machine_id)
        texts = [(it.get('text') or '').strip() for it in items if (it.get('text') or '').strip()]
        eq.maintenance_notes = '\n'.join(texts)[:4000]
        if not safe_commit():
            return jsonify({'error': _('Save failed')}), 500
        log_audit('update', 'equipment', eq.id, f'TO works edited ({eq.name})')
        return jsonify({'ok': True})
    machine = Machine.query.get_or_404(machine_id)
    chart_periods = ('quarterly', 'semiannual', 'yearly')
    existing = MachineTOChecklist.query.filter(
        MachineTOChecklist.machine_id == machine_id,
        MachineTOChecklist.period.in_(chart_periods),
    ).all()
    by_id = {e.id: e for e in existing}
    keep = set()
    for it in items:
        iid = it.get('id')
        text = (it.get('text') or '').strip()
        if not text:
            continue
        if iid in by_id:
            by_id[iid].text = text[:500]
            keep.add(iid)
        else:
            db.session.add(MachineTOChecklist(
                machine_id=machine_id, period='quarterly', text=text[:500]))
    for eid, ent in by_id.items():
        if eid not in keep:
            db.session.delete(ent)
    if not safe_commit():
        return jsonify({'error': _('Save failed')}), 500
    log_audit('update', 'machine', machine_id, f'TO works edited ({machine.name})')
    return jsonify({'ok': True})


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
            eq = Equipment.query.get(int(equipment_id))
            if eq:
                eq.next_service_date = None
                db.session.commit()
                flash(_('Equipment service date removed'), 'success')
            else:
                flash(_('Equipment not found'), 'error')

        elif ev_type == 'equipment_mro' and mro_id:
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
        traceback.print_exc()
        flash(_('Error: %(err)s', err=str(e)[:100]), 'error')

    month = request.form.get('month', datetime.utcnow().strftime('%Y-%m'))
    return redirect(url_for('maintenance.maintenance_calendar', month=month))

@bp.route('/maintenance-calendar/move', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
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

    # Запрет работ в сб/вс: перенос на выходной только с явным подтверждением
    # (в интерфейсе — confirm, в API — флаг weekend_ok=1).
    if new_date.weekday() >= 5 and request.form.get('weekend_ok') != '1':
        return jsonify({'error': 'weekend', 'need_confirm': True}), 409

    try:
        if ev_type == 'plan' and plan_id:
            p = MaintenancePlan.query.get(int(plan_id))
            if p:
                # Перенос всего окна ТО (длина сохраняется)
                span = (p.planned_end - p.planned_start).days if p.planned_end else max((p.window_days or 4) - 1, 0)
                p.planned_start = new_date
                p.planned_end = new_date + timedelta(days=span)
                p.window_days = span + 1
                if not safe_commit():
                    return jsonify({'error': 'Save failed'}), 500
                return jsonify({'ok': True, 'new_date': new_date_str,
                                'window_start': p.planned_start.isoformat(),
                                'window_end': p.planned_end.isoformat()})

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

        elif ev_type == 'machine_maintenance':
            record_id = request.form.get('record_id')
            machine_id = request.form.get('machine_id')
            old_date = request.form.get('old_date')
            mr = None
            if record_id:
                try:
                    mr = MaintenanceRecord.query.get(int(record_id))
                except (ValueError, TypeError):
                    mr = None
            if not mr and machine_id:
                try:
                    q = MaintenanceRecord.query.filter(
                        MaintenanceRecord.machine_id == int(machine_id),
                        MaintenanceRecord.next_maintenance.isnot(None)
                    )
                    if old_date:
                        try:
                            od = datetime.strptime(old_date, '%Y-%m-%d').date()
                            q = q.filter(db.func.date(MaintenanceRecord.next_maintenance) == od)
                        except ValueError:
                            pass
                    mr = q.first()
                except (ValueError, TypeError):
                    mr = None
            if mr:
                mr.next_maintenance = datetime.combine(new_date, datetime.min.time())
                if not safe_commit():
                    return jsonify({'error': 'Save failed'}), 500
                return jsonify({'ok': True, 'new_date': new_date_str})

        elif ev_type == 'equipment':
            equipment_id = request.form.get('equipment_id')
            if equipment_id:
                eq = Equipment.query.get(int(equipment_id))
                if eq:
                    eq.next_service_date = new_date
                    if not safe_commit():
                        return jsonify({'error': 'Save failed'}), 500
                    return jsonify({'ok': True, 'new_date': new_date_str})

        elif ev_type == 'equipment_mro':
            mro_id = request.form.get('mro_id')
            if mro_id:
                mro = EquipmentMaintenance.query.get(int(mro_id))
                if mro:
                    # Перенос = новая якорная дата графика (следующие — по ней)
                    mro.next_date = new_date
                    if not safe_commit():
                        return jsonify({'error': 'Save failed'}), 500
                    return jsonify({'ok': True, 'new_date': new_date_str})

        return jsonify({'error': 'Event not found'}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)[:100]}), 500


@bp.route('/maintenance-calendar/window', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def maintenance_calendar_window():
    """Растянуть/сжать окно ТО. Принимает form (или JSON) и возвращает редирект."""
    data = request.get_json(silent=True) or request.form
    plan_id = data.get('plan_id')
    wants_json = request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if not plan_id:
        if wants_json:
            return jsonify({'error': 'No plan'}), 400
        flash(_('Plan not found'), 'error')
        return redirect(url_for('maintenance.maintenance_calendar'))
    p = MaintenancePlan.query.get(int(plan_id))
    if not p:
        if wants_json:
            return jsonify({'error': 'Plan not found'}), 404
        flash(_('Plan not found'), 'error')
        return redirect(url_for('maintenance.maintenance_calendar'))
    try:
        ws = datetime.strptime(data.get('window_start') or '', '%Y-%m-%d').date()
        we = datetime.strptime(data.get('window_end') or '', '%Y-%m-%d').date()
    except ValueError:
        if wants_json:
            return jsonify({'error': 'Invalid dates'}), 400
        flash(_('Invalid dates'), 'error')
        return redirect(url_for('maintenance.maintenance_calendar'))
    if we < ws:
        ws, we = we, ws
    if (we - ws).days > 60:
        if wants_json:
            return jsonify({'error': 'Window too long'}), 400
        flash(_('Window too long'), 'error')
        return redirect(url_for('maintenance.maintenance_calendar'))
    p.planned_start = ws
    p.planned_end = we
    p.window_days = (we - ws).days + 1
    if not safe_commit():
        if wants_json:
            return jsonify({'error': 'Save failed'}), 500
        flash(_('Save failed'), 'error')
        return redirect(url_for('maintenance.maintenance_calendar'))
    if wants_json:
        return jsonify({'ok': True, 'window_start': ws.isoformat(), 'window_end': we.isoformat()})
    flash(_('Period saved'), 'success')
    month = data.get('month') or ws.strftime('%Y-%m')
    return redirect(url_for('maintenance.maintenance_calendar', month=month))


# ── Календарь текущих работ (не ТО) ─────────────────────────────

@bp.route('/works-calendar')
@login_required
@role_required('admin', 'director', 'technician', 'user', 'responsible')
def works_calendar():
    today = datetime.utcnow().date()
    month = request.args.get('month', today.strftime('%Y-%m'))
    year, mon = map(int, month.split('-'))
    month_start = datetime(year, mon, 1).date()
    month_end = datetime(year + 1, 1, 1).date() if mon == 12 else datetime(year, mon + 1, 1).date()
    base = WorkEvent.query.filter(WorkEvent.date_start < month_end).order_by(WorkEvent.date_start).all()
    # Разложить повторения по дням месяца
    day_map = {}
    for ev in base:
        for ds, de in ev.expand_occurrences(month_start, month_end):
            item = {
                'id': ev.id, 'title': ev.title, 'description': ev.description or '',
                'kind': ev.event_kind or 'work', 'date_start': ds, 'date_end': de,
                'recurrence': ev.recurrence or 'none', 'is_recurring': (ev.recurrence or 'none') != 'none',
                'priority': ev.priority or 'normal', 'color': ev.color or '',
            }
            # показывать на всех днях интервала, кроме сб/вс
            d = ds
            while d <= de and d < month_end:
                if d >= month_start and d.weekday() < 5:
                    day_map.setdefault(d, []).append(item)
                d += timedelta(days=1)
    events = []
    for d in sorted(day_map.keys()):
        for item in day_map[d]:
            row = dict(item)
            row['date'] = d
            row['is_first'] = (d == item['date_start'])
            events.append(row)
    prev_month = (month_start - timedelta(days=1)).strftime('%Y-%m')
    next_month = month_end.strftime('%Y-%m')
    days_in_month = (month_end - month_start).days
    return render_template(
        'works_calendar.html',
        events=events, day_map=day_map, month=month,
        month_start=month_start, month_end=month_end,
        days_in_month=days_in_month,
        today=today, prev_month=prev_month, next_month=next_month, timedelta=timedelta,
    )


@bp.route('/works-calendar/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def works_calendar_new():
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        if not title:
            flash(_('Title is required'), 'error')
            return redirect(url_for('maintenance.works_calendar_new'))
        ev = WorkEvent(
            title=title,
            description=request.form.get('description', ''),
            event_kind=request.form.get('event_kind', 'work'),
            priority=request.form.get('priority', 'normal') or 'normal',
            color=request.form.get('color', '') or '',
            date_start=safe_date(request.form.get('date_start')),
            date_end=safe_date(request.form.get('date_end')),
            recurrence=request.form.get('recurrence', 'none') or 'none',
            created_by=resolve_user_id(),
        )
        if not ev.date_start:
            flash(_('Date is required'), 'error')
            return redirect(url_for('maintenance.works_calendar_new'))
        # Работы не ставим на выходные — перенос на понедельник
        while ev.date_start.weekday() >= 5:
            ev.date_start += timedelta(days=1)
        if ev.date_end:
            while ev.date_end.weekday() >= 5:
                ev.date_end += timedelta(days=1)
        db.session.add(ev)
        if not safe_commit():
            flash(_('Save failed'), 'error')
        else:
            flash(_('Event created'), 'success')
        return redirect(url_for('maintenance.works_calendar', month=ev.date_start.strftime('%Y-%m')))
    return render_template('work_event_form.html', event=None)


@bp.route('/works-calendar/<int:event_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def works_calendar_edit(event_id):
    ev = WorkEvent.query.get_or_404(event_id)
    if request.method == 'POST':
        ev.title = (request.form.get('title') or ev.title).strip()
        ev.description = request.form.get('description', '')
        ev.event_kind = request.form.get('event_kind', ev.event_kind)
        if request.form.get('priority'):
            ev.priority = request.form.get('priority') or 'normal'
        if 'color' in request.form:
            ev.color = request.form.get('color', '') or ''
        if request.form.get('recurrence'):
            ev.recurrence = request.form.get('recurrence') or 'none'
        ds = safe_date(request.form.get('date_start'))
        de = safe_date(request.form.get('date_end'))
        if ds:
            ev.date_start = ds
        ev.date_end = de
        if not safe_commit():
            flash(_('Save failed'), 'error')
        else:
            flash(_('Event updated'), 'success')
        return redirect(url_for('maintenance.works_calendar', month=ev.date_start.strftime('%Y-%m')))
    return render_template('work_event_form.html', event=ev)


@bp.route('/works-calendar/<int:event_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def works_calendar_delete(event_id):
    ev = WorkEvent.query.get_or_404(event_id)
    month = ev.date_start.strftime('%Y-%m')
    db.session.delete(ev)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        flash(_('Event deleted'), 'success')
    return redirect(url_for('maintenance.works_calendar', month=month))


@bp.route('/maintenance-calendar/export')
@login_required
def maintenance_calendar_export():
    today = datetime.utcnow().date()
    month = request.args.get('month', today.strftime('%Y-%m'))
    year, mon = map(int, month.split('-'))
    month_start = datetime(year, mon, 1).date()
    if mon == 12:
        month_end = datetime(year + 1, 1, 1).date()
    else:
        month_end = datetime(year, mon + 1, 1).date()

    # Collect events (same logic as calendar view)
    if current_user.has_role('admin', 'director', 'technician'):
        parts = MachinePart.query.options(joinedload(MachinePart.machine)).all()
    else:
        machine_ids = [m.id for m in current_user.assigned_machines]
        parts = MachinePart.query.options(joinedload(MachinePart.machine)).filter(MachinePart.machine_id.in_(machine_ids)).all()

    events = []
    for p in parts:
        if p.next_replacement and month_start <= p.next_replacement < month_end:
            events.append({'date': p.next_replacement, 'type': 'Replacement', 'part': p.name, 'machine': p.machine.name if p.machine else _target_name(p), 'machine_id': p.machine_id, 'overdue': p.next_replacement < today})
        if p.next_maintenance and month_start <= p.next_maintenance < month_end:
            events.append({'date': p.next_maintenance, 'type': 'Maintenance', 'part': p.name, 'machine': p.machine.name if p.machine else _target_name(p), 'machine_id': p.machine_id, 'overdue': p.next_maintenance < today})
        for mr in MaintenanceRecord.query.filter_by(machine_id=p.machine_id).all():
            if mr.next_maintenance and month_start <= mr.next_maintenance.date() < month_end:
                events.append({'date': mr.next_maintenance.date(), 'type': 'Machine Maintenance', 'part': mr.description[:40], 'machine': p.machine.name if p.machine else _target_name(p), 'machine_id': p.machine_id, 'overdue': mr.next_maintenance.date() < today})

    if current_user.has_role('admin', 'director', 'technician'):
        plans = MaintenancePlan.query.all()
    else:
        plan_machine_ids = [m.id for m in current_user.assigned_machines]
        plans = MaintenancePlan.query.filter(MaintenancePlan.machine_id.in_(plan_machine_ids)).all()
    for pl in plans:
        if not pl.planned_start:
            continue
        if month_start <= pl.planned_start < month_end:
            events.append({'date': pl.planned_start, 'type': 'Plan', 'part': pl.title[:40], 'machine': _target_name(pl), 'machine_id': pl.machine_id, 'overdue': pl.planned_start < today and pl.status not in ('completed', 'cancelled'), 'status': pl.status})
        if pl.recurrence and pl.recurrence != 'none' and pl.status not in ('completed', 'cancelled'):
            d = pl.planned_start
            limit = month_end + timedelta(days=1)
            for _occ in range(200):
                if d >= limit:
                    break
                if d >= month_start and d != pl.planned_start:
                    events.append({'date': d, 'type': 'Plan', 'part': pl.title[:40], 'machine': _target_name(pl), 'machine_id': pl.machine_id, 'overdue': d < today, 'status': pl.status})
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

