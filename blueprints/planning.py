"""
planning blueprint — модуль планирования (сырьё → переработка → отгрузка).

Ответы владельца 2026-11-23: «распределение» = СТАНОК (не линия);
готовая продукция ждёт погрузки в холодильниках LaadKaai.
Ф1: три экрана + недельный календарь + экспорт Excel.
"""
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, send_file)
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import (db, PlanPurchase, PlanProduction, PlanShipment,
                    Machine, Contractor)
from utils import (resolve_user_id, log_audit, role_required, safe_commit,
                   safe_date, safe_float, safe_int)
import io

bp = Blueprint('planning', __name__)

STATUS_LABELS = {
    'planned': 'Gepland',
    'done': 'Gereed',
    'cancelled': 'Geannuleerd',
}


def _week_bounds(anchor):
    monday = anchor - timedelta(days=anchor.weekday())
    return monday, monday + timedelta(days=6)


def _anchor_date():
    raw = (request.args.get('d') or '').strip()
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except Exception:
        from models import now_local
        return now_local().date()


# ────────────────────────── календарь недели ──────────────────────────

@bp.route('/planning')
@login_required
@role_required('admin', 'director', 'technician')
def planning_calendar():
    """Недельный календарь: закупки / переработка / отгрузки по дням."""
    anchor = _anchor_date()
    week_start, week_end = _week_bounds(anchor)
    days = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        days.append({'date': d, 'purchases': [], 'production': [], 'shipments': []})
    by_day = {d['date']: d for d in days}
    for p in PlanPurchase.query.filter(PlanPurchase.planned_date.between(week_start, week_end)).all():
        by_day[p.planned_date]['purchases'].append(p)
    for p in PlanProduction.query.filter(PlanProduction.prod_date.between(week_start, week_end)).order_by(PlanProduction.priority.desc()).all():
        by_day[p.prod_date]['production'].append(p)
    for p in PlanShipment.query.filter(PlanShipment.ship_date.between(week_start, week_end)).all():
        by_day[p.ship_date]['shipments'].append(p)
    return render_template('planning.html', days=days,
                           week_start=week_start, week_end=week_end,
                           prev_d=(week_start - timedelta(days=1)).isoformat(),
                           next_d=(week_end + timedelta(days=1)).isoformat(),
                           today=anchor)


# ────────────────────────── 1. закупки сырья ──────────────────────────

@bp.route('/planning/purchases')
@login_required
@role_required('admin', 'director', 'technician')
def planning_purchases():
    anchor = _anchor_date()
    edit = PlanPurchase.query.get(safe_int(request.args.get('edit'))) if request.args.get('edit') else None
    items = PlanPurchase.query.order_by(PlanPurchase.planned_date.asc(), PlanPurchase.id.asc()).all()
    contractors = Contractor.query.order_by(Contractor.company_name).all()
    return render_template('planning_purchases.html', items=items,
                           contractors=contractors, edit=edit,
                           today=anchor.isoformat(), statuses=STATUS_LABELS)


@bp.route('/planning/purchases/save', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_purchase_save():
    pid = safe_int(request.form.get('id'))
    item = PlanPurchase.query.get(pid) if pid else PlanPurchase(created_by=resolve_user_id())
    if item is None:
        flash(_('Save failed'), 'error')
        return redirect(url_for('planning.planning_purchases'))
    item.article_code = (request.form.get('article_code') or '').strip()
    item.article_name = (request.form.get('article_name') or '').strip() or item.article_code or '?'
    item.qty = safe_float(request.form.get('qty'), 0) or 0
    item.unit = (request.form.get('unit') or 'kg').strip()
    item.contractor_id = safe_int(request.form.get('contractor_id')) or None
    d = safe_date(request.form.get('planned_date'))
    item.planned_date = d.date() if hasattr(d, 'date') else (d or item.planned_date)
    if item.planned_date is None:
        from models import now_local
        item.planned_date = now_local().date()
    item.notes = (request.form.get('notes') or '').strip()
    if not pid:
        db.session.add(item)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('create' if not pid else 'edit', 'plan_purchase', item.id, item.article_name)
        flash(_('Saved'), 'success')
    return redirect(url_for('planning.planning_purchases'))


@bp.route('/planning/purchases/<int:item_id>/status', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_purchase_status(item_id):
    item = PlanPurchase.query.get_or_404(item_id)
    st = (request.form.get('status') or '').strip()
    if st in STATUS_LABELS:
        item.status = st
        if not safe_commit():
            flash(_('Save failed'), 'error')
    return redirect(url_for('planning.planning_purchases'))


@bp.route('/planning/purchases/<int:item_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_purchase_delete(item_id):
    item = PlanPurchase.query.get_or_404(item_id)
    db.session.delete(item)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('delete', 'plan_purchase', item_id, item.article_name)
        flash(_('Deleted'), 'success')
    return redirect(url_for('planning.planning_purchases'))


# ──────────────────── 2. переработка (распределение) ────────────────────

@bp.route('/planning/production')
@login_required
@role_required('admin', 'director', 'technician')
def planning_production():
    anchor = _anchor_date()
    edit = PlanProduction.query.get(safe_int(request.args.get('edit'))) if request.args.get('edit') else None
    items = PlanProduction.query.order_by(PlanProduction.prod_date.asc(), PlanProduction.priority.desc(), PlanProduction.id.asc()).all()
    machines = Machine.query.order_by(Machine.name).all()
    return render_template('planning_production.html', items=items,
                           machines=machines, edit=edit,
                           today=anchor.isoformat(), statuses=STATUS_LABELS)


@bp.route('/planning/production/save', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_production_save():
    pid = safe_int(request.form.get('id'))
    item = PlanProduction.query.get(pid) if pid else PlanProduction(created_by=resolve_user_id())
    if item is None:
        flash(_('Save failed'), 'error')
        return redirect(url_for('planning.planning_production'))
    item.article_code = (request.form.get('article_code') or '').strip()
    item.article_name = (request.form.get('article_name') or '').strip() or item.article_code or '?'
    item.qty = safe_float(request.form.get('qty'), 0) or 0
    item.unit = (request.form.get('unit') or 'kg').strip()
    item.machine_id = safe_int(request.form.get('machine_id')) or None
    item.machine_name = (request.form.get('machine_name') or '').strip()
    if item.machine_id:
        m = db.session.get(Machine, item.machine_id)
        if m is not None:
            item.machine_name = m.name
    item.work_order = (request.form.get('work_order') or '').strip()
    item.lot = (request.form.get('lot') or '').strip()
    item.priority = safe_int(request.form.get('priority')) or 0
    d = safe_date(request.form.get('prod_date'))
    item.prod_date = d.date() if hasattr(d, 'date') else (d or item.prod_date)
    if item.prod_date is None:
        from models import now_local
        item.prod_date = now_local().date()
    item.notes = (request.form.get('notes') or '').strip()
    if not pid:
        db.session.add(item)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('create' if not pid else 'edit', 'plan_production', item.id, item.article_name)
        flash(_('Saved'), 'success')
    return redirect(url_for('planning.planning_production'))


@bp.route('/planning/production/<int:item_id>/status', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_production_status(item_id):
    item = PlanProduction.query.get_or_404(item_id)
    st = (request.form.get('status') or '').strip()
    if st in STATUS_LABELS:
        item.status = st
        if not safe_commit():
            flash(_('Save failed'), 'error')
    return redirect(url_for('planning.planning_production'))


@bp.route('/planning/production/<int:item_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_production_delete(item_id):
    item = PlanProduction.query.get_or_404(item_id)
    db.session.delete(item)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('delete', 'plan_production', item_id, item.article_name)
        flash(_('Deleted'), 'success')
    return redirect(url_for('planning.planning_production'))


# ────────────────────────── 3. отгрузки ──────────────────────────

@bp.route('/planning/shipments')
@login_required
@role_required('admin', 'director', 'technician')
def planning_shipments():
    anchor = _anchor_date()
    edit = PlanShipment.query.get(safe_int(request.args.get('edit'))) if request.args.get('edit') else None
    items = PlanShipment.query.order_by(PlanShipment.ship_date.asc(), PlanShipment.id.asc()).all()
    return render_template('planning_shipments.html', items=items,
                           edit=edit, today=anchor.isoformat(), statuses=STATUS_LABELS)


@bp.route('/planning/shipments/save', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_shipment_save():
    pid = safe_int(request.form.get('id'))
    item = PlanShipment.query.get(pid) if pid else PlanShipment(created_by=resolve_user_id())
    if item is None:
        flash(_('Save failed'), 'error')
        return redirect(url_for('planning.planning_shipments'))
    item.article_code = (request.form.get('article_code') or '').strip()
    item.article_name = (request.form.get('article_name') or '').strip() or item.article_code or '?'
    item.qty = safe_float(request.form.get('qty'), 0) or 0
    item.unit = (request.form.get('unit') or 'st').strip()
    item.client_name = (request.form.get('client_name') or '').strip()
    item.transport = (request.form.get('transport') or '').strip()
    d = safe_date(request.form.get('ship_date'))
    item.ship_date = d.date() if hasattr(d, 'date') else (d or item.ship_date)
    if item.ship_date is None:
        from models import now_local
        item.ship_date = now_local().date()
    item.notes = (request.form.get('notes') or '').strip()
    if not pid:
        db.session.add(item)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('create' if not pid else 'edit', 'plan_shipment', item.id, item.article_name)
        flash(_('Saved'), 'success')
    return redirect(url_for('planning.planning_shipments'))


@bp.route('/planning/shipments/<int:item_id>/status', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_shipment_status(item_id):
    item = PlanShipment.query.get_or_404(item_id)
    st = (request.form.get('status') or '').strip()
    if st in STATUS_LABELS:
        item.status = st
        if not safe_commit():
            flash(_('Save failed'), 'error')
    return redirect(url_for('planning.planning_shipments'))


@bp.route('/planning/shipments/<int:item_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_shipment_delete(item_id):
    item = PlanShipment.query.get_or_404(item_id)
    db.session.delete(item)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('delete', 'plan_shipment', item_id, item.article_name)
        flash(_('Deleted'), 'success')
    return redirect(url_for('planning.planning_shipments'))


# ────────────────────────── экспорт Excel ──────────────────────────

@bp.route('/planning/export.xlsx')
@login_required
@role_required('admin', 'director', 'technician')
def planning_export_xlsx():
    """Три листа: Inkoop / Productie / Verzending (плавный переезд из Excel)."""
    from openpyxl import Workbook
    wb = Workbook()

    ws = wb.active
    ws.title = 'Inkoop'
    ws.append([_('Date'), _('Code'), _('Name'), _('Qty'), _('Unit'),
               _('Supplier'), _('Status'), _('Notes')])
    for p in PlanPurchase.query.order_by(PlanPurchase.planned_date.asc()).all():
        ws.append([p.planned_date.isoformat(), p.article_code, p.article_name,
                   p.qty, p.unit, p.Contractor.company_name if p.contractor else '',
                   STATUS_LABELS.get(p.status, p.status), p.notes or ''])

    ws = wb.create_sheet('Productie')
    ws.append([_('Date'), _('Code'), _('Name'), _('Qty'), _('Unit'),
               _('Machine'), _('Work order'), _('Lot'), _('Priority'),
               _('Status'), _('Notes')])
    for p in PlanProduction.query.order_by(PlanProduction.prod_date.asc()).all():
        ws.append([p.prod_date.isoformat(), p.article_code, p.article_name,
                   p.qty, p.unit,
                   (p.machine.name if p.machine else p.machine_name) or '',
                   p.work_order or '', p.lot or '', p.priority,
                   STATUS_LABELS.get(p.status, p.status), p.notes or ''])

    ws = wb.create_sheet('Verzending')
    ws.append([_('Date'), _('Code'), _('Name'), _('Qty'), _('Unit'),
               _('Client'), _('Transport'), _('Status'), _('Notes')])
    for p in PlanShipment.query.order_by(PlanShipment.ship_date.asc()).all():
        ws.append([p.ship_date.isoformat(), p.article_code, p.article_name,
                   p.qty, p.unit, p.client_name or '', p.transport or '',
                   STATUS_LABELS.get(p.status, p.status), p.notes or ''])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name='planning.xlsx')

