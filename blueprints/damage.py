# -*- coding: utf-8 -*-
"""Экономический ущерб — расчёт потерь от простоев и поломок.

Ущерб = простой станка (часы x €/час) + работа механиков (часы x ставка)
        + запчасти/материалы (€, вписывается по случаю).
"""
from datetime import datetime, timedelta
from flask import Blueprint, request, redirect, url_for, flash, render_template, Response
from flask_login import login_required
from flask_babel import gettext as _

from models import (db, AppSetting, FaultReport, Machine,
                    TechnicalWorkOrder, FaultWorkSession, now_local)
from utils import role_required, safe_commit, safe_float

bp = Blueprint('damage', __name__, url_prefix='/damage')


def get_setting(key, default=''):
    s = db.session.get(AppSetting, key)
    return s.value if s and s.value not in (None, '') else default


def set_setting(key, value):
    s = db.session.get(AppSetting, key)
    if s is None:
        s = AppSetting(key=key)
        db.session.add(s)
    s.value = str(value)


def _parse_date(s, fallback):
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return fallback


@bp.route('/')
@login_required
@role_required('admin', 'director')
def damage_report():
    today = now_local().date()
    d_from = request.args.get('from') or today.replace(day=1).isoformat()
    d_to = request.args.get('to') or today.isoformat()
    machine_id = request.args.get('machine_id', type=int)
    dt_from = _parse_date(d_from, today.replace(day=1))
    dt_to = _parse_date(d_to, today)
    labor_rate = safe_float(get_setting('labor_rate_per_hour', '25')) or 25.0

    mmap = {m.id: m for m in Machine.query.all()}
    q = FaultReport.query.filter(
        FaultReport.created_at >= datetime.combine(dt_from, datetime.min.time()),
        FaultReport.created_at < datetime.combine(dt_to + timedelta(days=1), datetime.min.time()))
    if machine_id:
        q = q.filter(FaultReport.machine_id == machine_id)
    faults = q.order_by(FaultReport.created_at.desc()).all()

    now = now_local()
    rows = []
    totals = dict(downtime_h=0.0, downtime_cost=0.0, labor_h=0.0,
                  labor_cost=0.0, parts_cost=0.0, total=0.0)
    for f in faults:
        start = f.created_at
        end = f.resolved_at or now
        downtime_h = max(0.0, (end - start).total_seconds() / 3600) if start else 0.0
        mm = mmap.get(f.machine_id)
        rate = safe_float(getattr(mm, 'loss_per_hour', None)) or 0.0
        downtime_cost = downtime_h * rate
        sess_min = sum((s.duration_minutes or 0) for s in (f.work_sessions or []))
        two_h = sum((t.time_spent_hours or 0) for t in (f.work_orders or []))
        labor_h = sess_min / 60.0 + two_h
        labor_cost = labor_h * labor_rate
        parts = safe_float(f.parts_cost) or 0.0
        total = downtime_cost + labor_cost + parts
        rows.append({
            'id': f.id, 'title': f.title or '', 'created': f.created_at,
            'machine': mm.name if mm else '—', 'status': f.status or '',
            'downtime_h': round(downtime_h, 1), 'rate': rate,
            'downtime_cost': round(downtime_cost, 2),
            'labor_h': round(labor_h, 2), 'labor_cost': round(labor_cost, 2),
            'parts_cost': parts, 'total': round(total, 2),
        })
        totals['downtime_h'] += downtime_h
        totals['downtime_cost'] += downtime_cost
        totals['labor_h'] += labor_h
        totals['labor_cost'] += labor_cost
        totals['parts_cost'] += parts
        totals['total'] += total
    totals = {k: round(v, 2) for k, v in totals.items()}

    # топ станков по ущербу
    by_machine = {}
    for r in rows:
        by_machine[r['machine']] = by_machine.get(r['machine'], 0.0) + r['total']
    top = sorted(by_machine.items(), key=lambda kv: -kv[1])[:8]

    return render_template('damage_report.html', rows=rows, totals=totals, top=top,
                           d_from=d_from, d_to=d_to, machine_id=machine_id,
                           machines=sorted(mmap.values(), key=lambda m: m.name or ''),
                           labor_rate=labor_rate)


@bp.route('/settings', methods=['POST'])
@login_required
@role_required('admin', 'director')
def damage_settings():
    rate = safe_float(request.form.get('labor_rate'))
    if rate is not None:
        set_setting('labor_rate_per_hour', rate)
    for m in Machine.query.all():
        v = request.form.get('rate_%s' % m.id)
        if v is not None and v != '':
            m.loss_per_hour = safe_float(v) or 0
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
    else:
        flash(_('Settings saved'), 'success')
    return redirect(url_for('damage.damage_report',
                            **_qs()))


@bp.route('/parts/<int:fid>', methods=['POST'])
@login_required
@role_required('admin', 'director')
def damage_parts(fid):
    f = FaultReport.query.get_or_404(fid)
    f.parts_cost = safe_float(request.form.get('parts_cost')) or 0
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
    return redirect(url_for('damage.damage_report', **_qs()))


def _qs():
    return {k: v for k, v in (('from', request.form.get('from') or request.args.get('from')),
                              ('to', request.form.get('to') or request.args.get('to')),
                              ('machine_id', request.form.get('machine_id') or request.args.get('machine_id'))) if v}


@bp.route('/export')
@login_required
@role_required('admin', 'director')
def damage_export():
    # тот же расчёт — в CSV (открывается в Excel)
    import csv, io as _io
    today = now_local().date()
    d_from = request.args.get('from') or today.replace(day=1).isoformat()
    d_to = request.args.get('to') or today.isoformat()
    machine_id = request.args.get('machine_id', type=int)
    dt_from = _parse_date(d_from, today.replace(day=1))
    dt_to = _parse_date(d_to, today)
    labor_rate = safe_float(get_setting('labor_rate_per_hour', '25')) or 25.0
    mmap = {m.id: m for m in Machine.query.all()}
    q = FaultReport.query.filter(
        FaultReport.created_at >= datetime.combine(dt_from, datetime.min.time()),
        FaultReport.created_at < datetime.combine(dt_to + timedelta(days=1), datetime.min.time()))
    if machine_id:
        q = q.filter(FaultReport.machine_id == machine_id)
    faults = q.order_by(FaultReport.created_at.desc()).all()
    now = now_local()
    buf = _io.StringIO()
    w = csv.writer(buf, delimiter=';')
    w.writerow(['ID', 'Datum', 'Machine', 'Storing', 'Status',
                'Stilstand (h)', 'Stilstand kosten', 'Arbeid (h)', 'Arbeid kosten',
                'Onderdelen', 'Totaal'])
    tot = 0.0
    for f in faults:
        start = f.created_at
        end = f.resolved_at or now
        dh = max(0.0, (end - start).total_seconds() / 3600) if start else 0
        mm = mmap.get(f.machine_id)
        rate = safe_float(getattr(mm, 'loss_per_hour', None)) or 0.0
        dc = dh * rate
        lh = sum((s.duration_minutes or 0) for s in (f.work_sessions or [])) / 60.0 + sum((t.time_spent_hours or 0) for t in (f.work_orders or []))
        lc = lh * labor_rate
        pc = safe_float(f.parts_cost) or 0.0
        total = dc + lc + pc
        tot += total
        w.writerow([f.id, (f.created_at.strftime('%d-%m-%Y') if f.created_at else ''),
                    mm.name if mm else '', (f.title or '').replace(';', ',')[:60], f.status,
                    round(dh, 1), round(dc, 2), round(lh, 2), round(lc, 2), pc, round(total, 2)])
    w.writerow([])
    w.writerow(['TOTAAL', '', '', '', '', '', '', '', '', '', round(tot, 2)])
    data = '\ufeff' + buf.getvalue()
    return Response(data, mimetype='text/csv; charset=utf-8', headers={
        'Content-Disposition': 'attachment; filename=economic_damage_%s_%s.csv' % (d_from, d_to)})
