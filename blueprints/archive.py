"""
Monthly archive blueprint — snapshot previous month + search
"""
import json
from datetime import datetime, timedelta

from flask import Blueprint, request, redirect, url_for, flash, render_template
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import (db, MonthlyArchive, FaultReport, Opdracht, WorkReport, TimeEntry,
                    VoorraadMutatie, MaintenancePlan, EquipmentMaintenance, TechnicalWorkOrder)
from utils import role_required, safe_commit

bp = Blueprint('archive', __name__)


@bp.route('/archive')
@login_required
@role_required('admin', 'director', 'technician')
def archive_page():
    q = (request.args.get('q') or '').strip()
    archives = MonthlyArchive.query.order_by(MonthlyArchive.archive_month.desc(), MonthlyArchive.section).all()
    months = {}
    search_hits = []
    if q:
        ql = q.lower()
        for a in archives:
            try:
                data = json.loads(a.data_json or '[]')
            except Exception:
                data = []
            if isinstance(data, list):
                for row in data:
                    blob = json.dumps(row, ensure_ascii=False).lower()
                    if ql in blob:
                        search_hits.append({'month': a.archive_month, 'section': a.section, 'row': row})
                        if len(search_hits) >= 50:
                            break
            elif isinstance(data, dict):
                blob = json.dumps(data, ensure_ascii=False).lower()
                if ql in blob:
                    search_hits.append({'month': a.archive_month, 'section': a.section, 'row': data})
            if len(search_hits) >= 50:
                break
    else:
        for a in archives:
            months.setdefault(a.archive_month, []).append(a)
    return render_template('archive.html', months=months, q=q, search_hits=search_hits)


@bp.route('/archive/create', methods=['POST'])
@login_required
@role_required('admin')
def archive_create():
    """Archive current month's data and reset for new month."""
    now = datetime.utcnow()
    prev_month = (now.replace(day=1) - timedelta(days=1))
    month_str = prev_month.strftime('%Y-%m')

    existing = MonthlyArchive.query.filter_by(archive_month=month_str).first()
    if existing:
        flash(_('This month is already archived'), 'warning')
        return redirect(url_for('archive.archive_page'))

    d_from = prev_month.replace(day=1)
    d_to = now.replace(day=1)

    faults = FaultReport.query.filter(FaultReport.created_at >= d_from, FaultReport.created_at < d_to).all()
    faults_data = [{'id': f.id, 'title': f.title, 'machine': f.machine.name if f.machine else '', 'priority': f.priority, 'status': f.status, 'created_at': f.created_at.strftime('%Y-%m-%d %H:%M')} for f in faults]
    db.session.add(MonthlyArchive(archive_month=month_str, section='faults', data_json=json.dumps(faults_data, ensure_ascii=False), created_by=current_user.id))

    orders = Opdracht.query.filter(Opdracht.aangemaakt >= d_from, Opdracht.aangemaakt < d_to).all()
    orders_data = [{'id': o.id, 'nummer': o.nummer, 'client': o.verantwoordelijke.naam if o.verantwoordelijke else '', 'status': o.status, 'total': float(o.totaal or 0), 'created_at': o.aangemaakt.strftime('%Y-%m-%d %H:%M')} for o in orders]
    db.session.add(MonthlyArchive(archive_month=month_str, section='orders', data_json=json.dumps(orders_data, ensure_ascii=False), created_by=current_user.id))

    reports = WorkReport.query.filter(WorkReport.created_at >= d_from, WorkReport.created_at < d_to).all()
    reports_data = [{'id': r.id, 'fault_id': r.fault_id, 'hours': float(r.time_spent_hours or 0), 'description': r.work_description or '', 'created_at': r.created_at.strftime('%Y-%m-%d %H:%M')} for r in reports]
    db.session.add(MonthlyArchive(archive_month=month_str, section='work_reports', data_json=json.dumps(reports_data, ensure_ascii=False), created_by=current_user.id))

    entries = TimeEntry.query.filter(TimeEntry.date >= d_from.date(), TimeEntry.date < d_to.date()).all()
    entries_data = [{'id': e.id, 'user_id': e.user_id, 'date': e.date.strftime('%Y-%m-%d'), 'hours': float(e.hours_worked or 0), 'notes': e.notes or ''} for e in entries]
    db.session.add(MonthlyArchive(archive_month=month_str, section='time_entries', data_json=json.dumps(entries_data, ensure_ascii=False), created_by=current_user.id))

    movements = VoorraadMutatie.query.filter(VoorraadMutatie.aangemaakt >= d_from, VoorraadMutatie.aangemaakt < d_to).all()
    wh_data = [{'id': m.id, 'item_id': m.item_id, 'type': m.type, 'qty': float(m.hoeveelheid or 0), 'note': m.opmerking or '', 'date': m.aangemaakt.strftime('%Y-%m-%d %H:%M')} for m in movements]
    db.session.add(MonthlyArchive(archive_month=month_str, section='warehouse', data_json=json.dumps(wh_data, ensure_ascii=False), created_by=current_user.id))

    plans = MaintenancePlan.query.filter(MaintenancePlan.created_at >= d_from, MaintenancePlan.created_at < d_to).all()
    plans_data = [{'id': p.id, 'title': p.title, 'machine': p.machine.name if p.machine else '', 'status': p.status, 'planned_start': p.planned_start.strftime('%Y-%m-%d') if p.planned_start else '', 'cost': float(p.cost or 0)} for p in plans]
    db.session.add(MonthlyArchive(archive_month=month_str, section='maintenance_plans', data_json=json.dumps(plans_data, ensure_ascii=False), created_by=current_user.id))

    eq_maint = EquipmentMaintenance.query.filter(EquipmentMaintenance.created_at >= d_from, EquipmentMaintenance.created_at < d_to).all()
    eq_data = [{'id': e.id, 'number': e.number, 'name': e.name, 'serial': e.serial or '', 'date': e.date.strftime('%Y-%m-%d') if e.date else '', 'status': e.status} for e in eq_maint]
    db.session.add(MonthlyArchive(archive_month=month_str, section='equipment_maintenance', data_json=json.dumps(eq_data, ensure_ascii=False), created_by=current_user.id))

    twos = TechnicalWorkOrder.query.filter(TechnicalWorkOrder.created_at >= d_from, TechnicalWorkOrder.created_at < d_to).all()
    two_data = [{'id': t.id, 'number': t.number, 'description': (t.description or '')[:120], 'status': t.status, 'created_at': t.created_at.strftime('%Y-%m-%d %H:%M')} for t in twos]
    db.session.add(MonthlyArchive(archive_month=month_str, section='two', data_json=json.dumps(two_data, ensure_ascii=False), created_by=current_user.id))

    stats = {
        'faults_total': len(faults_data),
        'faults_open': len([f for f in faults_data if f.get('status') in ('open', 'accepted', 'in_progress', 'reopened')]),
        'faults_critical': len([f for f in faults_data if f.get('priority') == 'critical']),
        'orders_total': len(orders_data),
        'work_reports': len(reports_data),
        'time_entries': len(entries_data),
        'warehouse_moves': len(wh_data),
        'maintenance_plans': len(plans_data),
        'equipment_maintenance': len(eq_data),
        'two_total': len(two_data),
        'two_completed': len([t for t in two_data if t.get('status') == 'completed']),
        'period': f'{d_from.strftime("%Y-%m-%d")} — {d_to.strftime("%Y-%m-%d")}',
    }
    db.session.add(MonthlyArchive(archive_month=month_str, section='statistics', data_json=json.dumps(stats, ensure_ascii=False), created_by=current_user.id))

    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('archive.archive_page'))
    flash(_('Month archived successfully'), 'success')
    return redirect(url_for('archive.archive_page'))


@bp.route('/archive/<month>/<section>')
@login_required
@role_required('admin', 'director', 'technician')
def archive_detail(month, section):
    archive = MonthlyArchive.query.filter_by(archive_month=month, section=section).first_or_404()
    data = json.loads(archive.data_json or '[]')
    return render_template('archive_detail.html', archive=archive, data=data, month=month, section=section)
