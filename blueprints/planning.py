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
                    Machine, Contractor, FaultReport, MaintenancePlan,
                    VoorraadItem, VoorraadMutatie, now_local)
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
    # Ф3: умные подсказки
    low_stock = _low_stock_items()
    open_shipments = (PlanShipment.query.filter(PlanShipment.status == 'planned')
                      .order_by(PlanShipment.ship_date.asc()).limit(15).all())
    ship_checks = []
    for s in open_shipments:
        item = _fg_item_for(s.article_code, s.article_name)
        avail = (item.hoeveelheid or 0) if item else None
        ship_checks.append({'s': s, 'item': item, 'avail': avail,
                            'ok': avail is not None and avail >= (s.qty or 0)})
    open_plans = (PlanProduction.query.filter(PlanProduction.status == 'planned')
                  .order_by(PlanProduction.prod_date.asc()).limit(15).all())
    return render_template('planning.html', days=days,
                           week_start=week_start, week_end=week_end,
                           prev_d=(week_start - timedelta(days=1)).isoformat(),
                           next_d=(week_end + timedelta(days=1)).isoformat(),
                           today=anchor,
                           low_stock=low_stock, ship_checks=ship_checks,
                           open_plans=open_plans)


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
    _h = request.form.get('hours')
    item.hours = safe_float(_h, None) if _h not in (None, '') else None
    item.shift = (request.form.get('shift') or '').strip() or None
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
    avail_map = {}
    for s in items:
        item = _fg_item_for(s.article_code, s.article_name)
        avail_map[s.id] = {'avail': (item.hoeveelheid or 0) if item else None,
                           'ok': item is not None and (item.hoeveelheid or 0) >= (s.qty or 0)}
    return render_template('planning_shipments.html', items=items,
                           edit=edit, today=anchor.isoformat(), statuses=STATUS_LABELS,
                           avail_map=avail_map)


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


# ──────────────────── умные подсказки (Ф3) ────────────────────

def _low_stock_items():
    """Мин. остатки: складские позиции на/ниже минимума → предложение закупки."""
    return (VoorraadItem.query
            .filter(VoorraadItem.minimum > 0,
                    VoorraadItem.hoeveelheid <= VoorraadItem.minimum)
            .order_by(VoorraadItem.naam).all())


def _fg_item_for(article_code, article_name):
    """Готовая продукция на складе: по коду (SPN) или точному названию."""
    item = None
    if article_code:
        item = VoorraadItem.query.filter(
            VoorraadItem.supplier_part_number == article_code.strip()).first()
    if item is None and article_name:
        item = VoorraadItem.query.filter(
            VoorraadItem.naam == article_name.strip()).first()
    return item


@bp.route('/planning/purchases/from_stock/<int:item_id>', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_purchase_from_stock(item_id):
    """Подсказка: мин. остаток → черновик закупки в один клик."""
    item = VoorraadItem.query.get_or_404(item_id)
    need = (item.minimum or 0) - (item.hoeveelheid or 0)
    if need <= 0:
        need = item.minimum or 1
    p = PlanPurchase(
        article_code=(item.supplier_part_number or '').strip(),
        article_name=item.naam,
        qty=need, unit=item.eenheid or 'st',
        planned_date=now_local().date(),
        status='planned',
        notes=_('suggested by min stock level'),
        created_by=resolve_user_id())
    db.session.add(p)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('create', 'plan_purchase', p.id, f'auto: {p.article_name}')
        flash(_('Saved'), 'success')
    return redirect(url_for('planning.planning_purchases'))


@bp.route('/planning/production/<int:item_id>/consume', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_production_consume(item_id):
    """План → списание сырья: вычесть складскую позицию под план производства."""
    plan = PlanProduction.query.get_or_404(item_id)
    if request.method == 'POST':
        wi = VoorraadItem.query.get(safe_int(request.form.get('warehouse_item_id')) or 0)
        qty = safe_float(request.form.get('qty'), 0) or 0
        if wi is None or qty <= 0:
            flash(_('Save failed'), 'error')
            return redirect(url_for('planning.planning_production_consume', item_id=item_id))
        wi.hoeveelheid = (wi.hoeveelheid or 0) - qty
        db.session.add(VoorraadMutatie(
            item_id=wi.id, type='uitgaand', hoeveelheid=qty,
            opmerking=f'Planning {plan.id}: {plan.article_name}',
            user_id=resolve_user_id()))
        if not safe_commit():
            flash(_('Save failed'), 'error')
        else:
            log_audit('create', 'warehouse_movement', wi.id, f'plan {plan.id}: {wi.naam} -{qty}')
            flash(_('Saved'), 'success')
        return redirect(url_for('planning.planning_production'))
    warehouse = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    return render_template('planning_consume.html', plan=plan, warehouse=warehouse)

# ──────────────────── карта загрузки станков (Ф2) ────────────────────

DAY_CAPACITY_HOURS = 16  # 2 смены × 8 ч


def _week_conflicts(week_start, week_end):
    """Конфликты «машина × день»: поломка (заявка) и ТО (план)."""
    out = {}

    def _add(mid, d, tag):
        if mid and week_start <= d <= week_end:
            out.setdefault(mid, {}).setdefault(d, []).append(tag)

    for f in FaultReport.query.filter(FaultReport.machine_id.isnot(None)).all():
        start = f.created_at.date() if f.created_at else None
        if start is None:
            continue
        end = f.resolved_at.date() if f.resolved_at else week_end
        d = max(start, week_start)
        while d <= min(end, week_end):
            _add(f.machine_id, d, 'fault')
            d += timedelta(days=1)
    for pl in MaintenancePlan.query.filter(
            MaintenancePlan.machine_id.isnot(None),
            MaintenancePlan.status != 'cancelled').all():
        if not pl.planned_start:
            continue
        start = pl.planned_start.date() if hasattr(pl.planned_start, 'date') else pl.planned_start
        if pl.planned_end:
            end = pl.planned_end.date() if hasattr(pl.planned_end, 'date') else pl.planned_end
        elif pl.window_days:
            end = start + timedelta(days=max(int(pl.window_days or 0) - 1, 0))
        else:
            end = start
        d = max(start, week_start)
        while d <= min(end, week_end):
            _add(pl.machine_id, d, 'to')
            d += timedelta(days=1)
    return out


@bp.route('/planning/load')
@login_required
@role_required('admin', 'director', 'technician')
def planning_load():
    """Карта загрузки «машина × день»: план, часы, конфликты (поломка/ТО/
    перегрузка >16ч/дубль плана)."""
    anchor = _anchor_date()
    week_start, week_end = _week_bounds(anchor)
    days = [week_start + timedelta(days=i) for i in range(7)]
    conflicts = _week_conflicts(week_start, week_end)

    plans = PlanProduction.query.filter(
        PlanProduction.prod_date.between(week_start, week_end),
        PlanProduction.status != 'cancelled').all()
    machine_ids = {p.machine_id for p in plans if p.machine_id}
    machine_ids |= set(conflicts.keys())
    machines = (Machine.query.filter(Machine.id.in_(list(machine_ids) or [-1]))
                .order_by(Machine.name).all())

    rows = []
    cells = {}
    free_names = {}
    for p in plans:
        if p.machine_id:
            key = ('m', p.machine_id)
        else:
            nm = (p.machine_name or '').strip().lower()
            if not nm:
                continue
            key = ('n', nm)
            free_names.setdefault(nm, (p.machine_name or '').strip())
        cells.setdefault(key, {}).setdefault(p.prod_date, []).append(p)
    for m in machines:
        rows.append({'label': m.name, 'key': ('m', m.id)})
    for nm in sorted(free_names):
        rows.append({'label': free_names[nm], 'key': ('n', nm)})

    grid = []
    for row in rows:
        week_hours = 0.0
        week_cells = []
        for d in days:
            items = cells.get(row['key'], {}).get(d, [])
            total = sum((i.hours or 0) for i in items)
            week_hours += total
            tags = (list(conflicts.get(row['key'][1], {}).get(d, []))
                    if row['key'][0] == 'm' else [])
            if total > DAY_CAPACITY_HOURS:
                tags.append('overload')
            wo_counts = {}
            for i in items:
                if i.work_order:
                    wo_counts[i.work_order] = wo_counts.get(i.work_order, 0) + 1
            if any(v > 1 for v in wo_counts.values()):
                tags.append('duplicate')
            week_cells.append({'date': d, 'plans': items, 'total': total, 'tags': tags})
        grid.append({'label': row['label'], 'cells': week_cells, 'week_hours': week_hours})
    return render_template('planning_load.html', grid=grid, days=days,
                           week_start=week_start, week_end=week_end,
                           prev_d=(week_start - timedelta(days=1)).isoformat(),
                           next_d=(week_end + timedelta(days=1)).isoformat(),
                           capacity=DAY_CAPACITY_HOURS)


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
               _('Hours'), _('Shift'), _('Status'), _('Notes')])
    for p in PlanProduction.query.order_by(PlanProduction.prod_date.asc()).all():
        ws.append([p.prod_date.isoformat(), p.article_code, p.article_name,
                   p.qty, p.unit,
                   (p.machine.name if p.machine else p.machine_name) or '',
                   p.work_order or '', p.lot or '', p.priority,
                   p.hours if p.hours is not None else '', p.shift or '',
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

