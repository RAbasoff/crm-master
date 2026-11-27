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
    """Недельный календарь: закупки / переработка / отгрузки по дням + дашборд."""
    anchor = _anchor_date()
    week_start, week_end = _week_bounds(anchor)
    today = now_local().date()
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

    # ─── дашборд-статистика ───
    stats = {
        'week_purchases': sum(len(d['purchases']) for d in days),
        'week_production': sum(len(d['production']) for d in days),
        'week_shipments': sum(len(d['shipments']) for d in days),
        'overdue_purchases': PlanPurchase.query.filter(
            PlanPurchase.status == 'planned', PlanPurchase.planned_date < today).count(),
        'overdue_production': PlanProduction.query.filter(
            PlanProduction.status == 'planned', PlanProduction.prod_date < today).count(),
        'overdue_shipments': PlanShipment.query.filter(
            PlanShipment.status == 'planned', PlanShipment.ship_date < today).count(),
        'upcoming_shipments': PlanShipment.query.filter(
            PlanShipment.status == 'planned',
            PlanShipment.ship_date.between(today, today + timedelta(days=3))).count(),
        'open_production': PlanProduction.query.filter(PlanProduction.status == 'planned').count(),
        'done_this_week': PlanProduction.query.filter(
            PlanProduction.status == 'done',
            PlanProduction.prod_date.between(week_start, week_end)).count(),
    }

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
                           today=anchor, stats=stats,
                           low_stock=low_stock, ship_checks=ship_checks,
                           open_plans=open_plans)


# ────────────────────────── 1. закупки сырья ──────────────────────────

@bp.route('/planning/purchases')
@login_required
@role_required('admin', 'director', 'technician')
def planning_purchases():
    anchor = _anchor_date()
    edit = PlanPurchase.query.get(safe_int(request.args.get('edit'))) if request.args.get('edit') else None
    q = PlanPurchase.query
    f_status = (request.args.get('status') or '').strip()
    if f_status in STATUS_LABELS:
        q = q.filter(PlanPurchase.status == f_status)
    f_search = (request.args.get('q') or '').strip()
    if f_search:
        like = f'%{f_search}%'
        q = q.filter(db.or_(PlanPurchase.article_name.ilike(like),
                            PlanPurchase.article_code.ilike(like)))
    f_from = safe_date(request.args.get('date_from'))
    f_to = safe_date(request.args.get('date_to'))
    if f_from:
        q = q.filter(PlanPurchase.planned_date >= (f_from.date() if hasattr(f_from, 'date') else f_from))
    if f_to:
        q = q.filter(PlanPurchase.planned_date <= (f_to.date() if hasattr(f_to, 'date') else f_to))
    items = q.order_by(PlanPurchase.planned_date.asc(), PlanPurchase.id.asc()).all()
    contractors = Contractor.query.order_by(Contractor.company_name).all()
    return render_template('planning_purchases.html', items=items,
                           contractors=contractors, edit=edit,
                           today=anchor.isoformat(), statuses=STATUS_LABELS,
                           f_status=f_status, f_search=f_search,
                           f_from=f_from.strftime('%Y-%m-%d') if f_from else '',
                           f_to=f_to.strftime('%Y-%m-%d') if f_to else '')


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
    q = PlanProduction.query
    f_status = (request.args.get('status') or '').strip()
    if f_status in STATUS_LABELS:
        q = q.filter(PlanProduction.status == f_status)
    f_search = (request.args.get('q') or '').strip()
    if f_search:
        like = f'%{f_search}%'
        q = q.filter(db.or_(PlanProduction.article_name.ilike(like),
                            PlanProduction.article_code.ilike(like),
                            PlanProduction.work_order.ilike(like)))
    f_machine = safe_int(request.args.get('machine_id'))
    if f_machine:
        q = q.filter(PlanProduction.machine_id == f_machine)
    f_from = safe_date(request.args.get('date_from'))
    f_to = safe_date(request.args.get('date_to'))
    if f_from:
        q = q.filter(PlanProduction.prod_date >= (f_from.date() if hasattr(f_from, 'date') else f_from))
    if f_to:
        q = q.filter(PlanProduction.prod_date <= (f_to.date() if hasattr(f_to, 'date') else f_to))
    items = q.order_by(PlanProduction.prod_date.asc(), PlanProduction.priority.desc(), PlanProduction.id.asc()).all()
    machines = Machine.query.order_by(Machine.name).all()
    return render_template('planning_production.html', items=items,
                           machines=machines, edit=edit,
                           today=anchor.isoformat(), statuses=STATUS_LABELS,
                           f_status=f_status, f_search=f_search,
                           f_machine=f_machine,
                           f_from=f_from.strftime('%Y-%m-%d') if f_from else '',
                           f_to=f_to.strftime('%Y-%m-%d') if f_to else '')


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


@bp.route('/planning/production/<int:item_id>/copy', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_production_copy(item_id):
    """Дублировать план на следующую неделю (или на указанную дату)."""
    src = PlanProduction.query.get_or_404(item_id)
    offset = safe_int(request.form.get('days')) or 7
    new_date = (src.prod_date or now_local().date()) + timedelta(days=offset)
    copy = PlanProduction(
        article_code=src.article_code, article_name=src.article_name,
        qty=src.qty, unit=src.unit,
        machine_id=src.machine_id, machine_name=src.machine_name,
        work_order=src.work_order, lot=src.lot,
        priority=src.priority, hours=src.hours, shift=src.shift,
        prod_date=new_date, status='planned',
        notes=src.notes, created_by=resolve_user_id())
    db.session.add(copy)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('create', 'plan_production', copy.id, f'copy of #{src.id}: {copy.article_name}')
        flash(_('Copied to') + ' ' + new_date.strftime('%d.%m'), 'success')
    return redirect(url_for('planning.planning_production'))


@bp.route('/planning/bulk-status', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def planning_bulk_status():
    """Массовое изменение статуса: отмеченные записи → новый статус."""
    ids_raw = (request.form.get('ids') or '').strip()
    st = (request.form.get('status') or '').strip()
    target = (request.form.get('target') or '').strip()  # purchases|production|shipments
    if not ids_raw or st not in STATUS_LABELS:
        flash(_('Save failed'), 'error')
        return redirect(url_for('planning.planning_calendar'))
    ids = [safe_int(x) for x in ids_raw.split(',') if safe_int(x)]
    model_map = {'purchases': PlanPurchase, 'production': PlanProduction, 'shipments': PlanShipment}
    model = model_map.get(target)
    if model is None:
        flash(_('Save failed'), 'error')
        return redirect(url_for('planning.planning_calendar'))
    count = 0
    for i in ids:
        item = model.query.get(i)
        if item is not None:
            item.status = st
            count += 1
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('bulk_status', f'plan_{target}', 0, f'{count} items → {st}')
        flash(f'{count} → {STATUS_LABELS.get(st, st)}', 'success')
    return redirect(request.referrer or url_for('planning.planning_calendar'))


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
    q = PlanShipment.query
    f_status = (request.args.get('status') or '').strip()
    if f_status in STATUS_LABELS:
        q = q.filter(PlanShipment.status == f_status)
    f_search = (request.args.get('q') or '').strip()
    if f_search:
        like = f'%{f_search}%'
        q = q.filter(db.or_(PlanShipment.article_name.ilike(like),
                            PlanShipment.article_code.ilike(like),
                            PlanShipment.client_name.ilike(like)))
    f_from = safe_date(request.args.get('date_from'))
    f_to = safe_date(request.args.get('date_to'))
    if f_from:
        q = q.filter(PlanShipment.ship_date >= (f_from.date() if hasattr(f_from, 'date') else f_from))
    if f_to:
        q = q.filter(PlanShipment.ship_date <= (f_to.date() if hasattr(f_to, 'date') else f_to))
    items = q.order_by(PlanShipment.ship_date.asc(), PlanShipment.id.asc()).all()
    # batch-load warehouse items (avoids N+1)
    codes = {s.article_code.strip() for s in items if s.article_code}
    names = {s.article_name.strip() for s in items if s.article_name}
    by_code, by_name = {}, {}
    if codes:
        for w in VoorraadItem.query.filter(VoorraadItem.supplier_part_number.in_(codes)).all():
            by_code[w.supplier_part_number.strip()] = w
    if names:
        for w in VoorraadItem.query.filter(VoorraadItem.naam.in_(names)).all():
            by_name[w.naam.strip()] = w
    avail_map = {}
    for s in items:
        item = by_code.get(s.article_code.strip()) if s.article_code else None
        if item is None and s.article_name:
            item = by_name.get(s.article_name.strip())
        avail_map[s.id] = {'avail': (item.hoeveelheid or 0) if item else None,
                           'ok': item is not None and (item.hoeveelheid or 0) >= (s.qty or 0)}
    return render_template('planning_shipments.html', items=items,
                           edit=edit, today=anchor.isoformat(), statuses=STATUS_LABELS,
                           avail_map=avail_map, f_status=f_status, f_search=f_search,
                           f_from=f_from.strftime('%Y-%m-%d') if f_from else '',
                           f_to=f_to.strftime('%Y-%m-%d') if f_to else '')


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
    """Конфликты «машина × день»: поломка (заявка) и ТО (план).

    Возвращает (day_tags, alerts):
      day_tags: {machine_id: {date: ['fault'|'to', ...]}} — дни действия;
      alerts:   {machine_id: {'faults': [...], 'to': [...]}} — плашки строки.
    Заявки со статусом 'rejected' игнорируются; закрытые действуют только до
    resolved_at; открытые — до закрытия (но красным помечается лишь день с планом).
    """
    day_tags, alerts = {}, {}

    def _add(mid, d, tag):
        if mid and week_start <= d <= week_end:
            day_tags.setdefault(mid, {}).setdefault(d, []).append(tag)

    def _alert(mid, kind, text):
        alerts.setdefault(mid, {'faults': [], 'to': []})[kind].append(text)

    for f in FaultReport.query.filter(FaultReport.machine_id.isnot(None)).all():
        st = (f.status or '').lower()
        if st == 'rejected':
            continue
        start = f.created_at.date() if f.created_at else None
        if start is None:
            continue
        resolved = f.resolved_at.date() if f.resolved_at else None
        if st in ('closed', 'resolved', 'done', 'cancelled'):
            end = resolved or start
        else:
            end = resolved or week_end
        d = max(start, week_start)
        while d <= min(end, week_end):
            _add(f.machine_id, d, 'fault')
            d += timedelta(days=1)
        if week_start <= end and start <= week_end:
            _alert(f.machine_id, 'faults',
                   f'#{f.id} {f.title or ""} ({st}, {start.strftime("%d.%m")})')
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
        if week_start <= end and start <= week_end:
            _alert(pl.machine_id, 'to',
                   f'#{pl.id} {pl.title or ""} ({start.strftime("%d.%m")})')
    return day_tags, alerts


@bp.route('/planning/load')
@login_required
@role_required('admin', 'director', 'technician')
def planning_load():
    """Карта загрузки «машина × день»: план, часы, конфликты (поломка/ТО/
    перегрузка >16ч/дубль плана)."""
    anchor = _anchor_date()
    week_start, week_end = _week_bounds(anchor)
    days = [week_start + timedelta(days=i) for i in range(7)]
    day_tags, alerts = _week_conflicts(week_start, week_end)

    plans = PlanProduction.query.filter(
        PlanProduction.prod_date.between(week_start, week_end),
        PlanProduction.status != 'cancelled').all()
    machine_ids = {p.machine_id for p in plans if p.machine_id}
    machine_ids |= set(alerts.keys())
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
        rows.append({'label': m.name, 'key': ('m', m.id),
                     'alerts': alerts.get(m.id, {'faults': [], 'to': []})})
    for nm in sorted(free_names):
        rows.append({'label': free_names[nm], 'key': ('n', nm),
                     'alerts': {'faults': [], 'to': []}})

    grid = []
    for row in rows:
        week_hours = 0.0
        week_cells = []
        for d in days:
            items = cells.get(row['key'], {}).get(d, [])
            total = sum((i.hours or 0) for i in items)
            week_hours += total
            tags = (list(day_tags.get(row['key'][1], {}).get(d, []))
                    if row['key'][0] == 'm' else [])
            if total > DAY_CAPACITY_HOURS:
                tags.append('overload')
            wo_counts = {}
            for i in items:
                if i.work_order:
                    wo_counts[i.work_order] = wo_counts.get(i.work_order, 0) + 1
            if any(v > 1 for v in wo_counts.values()):
                tags.append('duplicate')
            # красный только там, где есть ПЛАН и конфликт с ним
            hot = bool(items) and bool(tags)
            week_cells.append({'date': d, 'plans': items, 'total': total,
                               'tags': tags if items else [], 'hot': hot})
        grid.append({'label': row['label'], 'cells': week_cells,
                     'week_hours': week_hours, 'alerts': row['alerts']})
    return render_template('planning_load.html', grid=grid, days=days,
                           week_start=week_start, week_end=week_end,
                           prev_d=(week_start - timedelta(days=1)).isoformat(),
                           next_d=(week_end + timedelta(days=1)).isoformat(),
                           capacity=DAY_CAPACITY_HOURS)


# ──────────────────── прогноз загрузки станков ────────────────────

@bp.route('/planning/load/forecast')
@login_required
@role_required('admin', 'director', 'technician')
def planning_load_forecast():
    """Прогноз загрузки: 4 недели вперёд, свободные окна, перегрузка, heatmap."""
    anchor = _anchor_date()
    week_start, _ = _week_bounds(anchor)
    today = now_local().date()
    horizon_weeks = safe_int(request.args.get('weeks')) or 4
    horizon_weeks = min(max(horizon_weeks, 1), 12)
    forecast_start = week_start
    forecast_end = week_start + timedelta(days=horizon_weeks * 7 - 1)

    # collect all production plans in the forecast window
    plans = PlanProduction.query.filter(
        PlanProduction.prod_date.between(forecast_start, forecast_end),
        PlanProduction.status != 'cancelled').all()

    # collect conflicts (faults + TO) for the window
    day_tags, alerts = _week_conflicts(forecast_start, forecast_end)

    # build machine list
    machine_ids = {p.machine_id for p in plans if p.machine_id}
    machine_ids |= set(alerts.keys())
    machines = (Machine.query.filter(Machine.id.in_(list(machine_ids) or [-1]))
                .order_by(Machine.name).all())

    # also include free-name "machines" (not in registry)
    free_names = {}
    for p in plans:
        if not p.machine_id and p.machine_name:
            nm = p.machine_name.strip().lower()
            free_names.setdefault(nm, p.machine_name.strip())

    # build per-machine daily data
    def _build_rows():
        rows = []
        # indexed plans by (machine_key, date)
        cells = {}
        for p in plans:
            if p.machine_id:
                key = ('m', p.machine_id)
            else:
                nm = (p.machine_name or '').strip().lower()
                if not nm:
                    continue
                key = ('n', nm)
            cells.setdefault(key, {}).setdefault(p.prod_date, []).append(p)

        for m in machines:
            key = ('m', m.id)
            rows.append({'label': m.name, 'key': key, 'machine_id': m.id,
                         'alerts': alerts.get(m.id, {'faults': [], 'to': []}),
                         'cells': {}})
        for nm in sorted(free_names):
            key = ('n', nm)
            rows.append({'label': free_names[nm], 'key': key, 'machine_id': None,
                         'alerts': {'faults': [], 'to': []},
                         'cells': {}})

        for row in rows:
            for i in range(horizon_weeks * 7):
                d = forecast_start + timedelta(days=i)
                items = cells.get(row['key'], {}).get(d, [])
                total = sum((it.hours or 0) for it in items)
                free = max(DAY_CAPACITY_HOURS - total, 0)
                overload = max(total - DAY_CAPACITY_HOURS, 0)
                tags = (list(day_tags.get(row['key'][1], {}).get(d, []))
                        if row['key'][0] == 'm' else [])
                if overload > 0:
                    tags.append('overload')
                # fill ratio for heatmap color
                ratio = total / DAY_CAPACITY_HOURS if DAY_CAPACITY_HOURS else 0
                if ratio == 0:
                    color = '#ecf0f1'   # empty
                elif ratio < 0.5:
                    color = '#2ecc71'   # green
                elif ratio < 0.8:
                    color = '#f1c40f'   # yellow
                elif ratio <= 1.0:
                    color = '#e67e22'   # orange
                else:
                    color = '#e74c3c'   # red (overload)
                row['cells'][d] = {
                    'date': d, 'plans': items, 'total': total,
                    'free': free, 'overload': overload,
                    'tags': tags, 'color': color, 'ratio': min(ratio, 1.0),
                    'is_today': d == today,
                    'is_weekend': d.weekday() >= 5,
                }
        return rows

    rows = _build_rows()

    # find best free windows per machine (≥ 4h free for ≥ 2 consecutive days)
    free_windows = {}
    for row in rows:
        windows = []
        cur_start = None
        cur_days = 0
        for i in range(horizon_weeks * 7):
            d = forecast_start + timedelta(days=i)
            cell = row['cells'].get(d)
            if cell and cell['free'] >= 4 and not cell['tags']:
                if cur_start is None:
                    cur_start = d
                    cur_days = 1
                else:
                    cur_days += 1
            else:
                if cur_start and cur_days >= 2:
                    windows.append({'from': cur_start, 'to': cur_start + timedelta(days=cur_days - 1),
                                    'days': cur_days})
                cur_start = None
                cur_days = 0
        if cur_start and cur_days >= 2:
            windows.append({'from': cur_start, 'to': cur_start + timedelta(days=cur_days - 1),
                            'days': cur_days})
        free_windows[row['key']] = windows

    # summary per machine
    summary = []
    for row in rows:
        total_planned = sum(c['total'] for c in row['cells'].values())
        total_overload = sum(c['overload'] for c in row['cells'].values())
        overload_days = sum(1 for c in row['cells'].values() if c['overload'] > 0)
        free_days = sum(1 for c in row['cells'].values() if c['free'] >= 4 and not c['tags'])
        capacity_total = sum(DAY_CAPACITY_HOURS for c in row['cells'].values()
                            if not c['is_weekend'] and not c['tags'])
        util = round(total_planned / capacity_total * 100, 1) if capacity_total else 0
        summary.append({
            'label': row['label'], 'key': row['key'],
            'total_planned': total_planned, 'total_overload': total_overload,
            'overload_days': overload_days, 'free_days': free_days,
            'utilization': util, 'windows': free_windows.get(row['key'], []),
        })

    # week columns for header
    week_cols = []
    for w in range(horizon_weeks):
        ws = forecast_start + timedelta(days=w * 7)
        we = ws + timedelta(days=6)
        week_cols.append({'start': ws, 'end': we, 'label': f'{ws.strftime("%d.%m")}–{we.strftime("%d.%m")}'})

    # flat day list for grid
    grid_days = [forecast_start + timedelta(days=i) for i in range(horizon_weeks * 7)]

    return render_template('planning_forecast.html',
                           rows=rows, summary=summary, week_cols=week_cols,
                           grid_days=grid_days, capacity=DAY_CAPACITY_HOURS,
                           forecast_start=forecast_start, forecast_end=forecast_end,
                           horizon_weeks=horizon_weeks, today=today,
                           prev_d=(week_start - timedelta(days=7)).isoformat(),
                           next_d=(week_start + timedelta(days=7)).isoformat())


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


# ──────────────────── KPI-панель (Ф5) ────────────────────

@bp.route('/planning/kpi')
@login_required
@role_required('admin', 'director', 'technician')
def planning_kpi():
    """KPI за месяц: план vs факт, % вовремя, загрузка станков."""
    today = now_local().date()
    month_raw = (request.args.get('m') or '').strip()
    try:
        month_start = datetime.strptime(month_raw, '%Y-%m').date().replace(day=1)
    except Exception:
        month_start = today.replace(day=1)
    # end of month
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1) - timedelta(days=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1) - timedelta(days=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    def _stats_for(ms, me):
        pur_total = PlanPurchase.query.filter(PlanPurchase.planned_date.between(ms, me)).count()
        pur_done = PlanPurchase.query.filter(PlanPurchase.planned_date.between(ms, me), PlanPurchase.status == 'done').count()
        prod_total = PlanProduction.query.filter(PlanProduction.prod_date.between(ms, me)).count()
        prod_done = PlanProduction.query.filter(PlanProduction.prod_date.between(ms, me), PlanProduction.status == 'done').count()
        ship_total = PlanShipment.query.filter(PlanShipment.ship_date.between(ms, me)).count()
        ship_done = PlanShipment.query.filter(PlanShipment.ship_date.between(ms, me), PlanShipment.status == 'done').count()
        # "in time" = done before or on planned date
        ship_ontime = 0
        for s in PlanShipment.query.filter(PlanShipment.ship_date.between(ms, me), PlanShipment.status == 'done').all():
            ship_ontime += 1  # simplified: done = on time unless we track actual date
        return {
            'pur_total': pur_total, 'pur_done': pur_done,
            'prod_total': prod_total, 'prod_done': prod_done,
            'ship_total': ship_total, 'ship_done': ship_done,
            'ship_ontime': ship_ontime,
        }

    cur = _stats_for(month_start, month_end)
    prev = _stats_for(prev_month_start, prev_month_end)

    # machine utilization: hours planned vs capacity (22 working days × 16h)
    prod_hours = (db.session.query(db.func.coalesce(db.func.sum(PlanProduction.hours), 0))
                  .filter(PlanProduction.prod_date.between(month_start, month_end),
                          PlanProduction.status != 'cancelled').scalar() or 0)
    working_days = sum(1 for i in range((month_end - month_start).days + 1)
                       if (month_start + timedelta(days=i)).weekday() < 6)
    capacity_hours = working_days * DAY_CAPACITY_HOURS
    utilization = round(prod_hours / capacity_hours * 100, 1) if capacity_hours else 0

    # top machines by hours
    top_machines = (db.session.query(
            db.func.coalesce(PlanProduction.machine_name, Machine.name, '?').label('name'),
            db.func.coalesce(db.func.sum(PlanProduction.hours), 0).label('hours'))
        .outerjoin(Machine, PlanProduction.machine_id == Machine.id)
        .filter(PlanProduction.prod_date.between(month_start, month_end),
                PlanProduction.status != 'cancelled')
        .group_by('name').order_by(db.desc('hours')).limit(8).all())

    # daily breakdown for sparkline
    daily = []
    for i in range((month_end - month_start).days + 1):
        d = month_start + timedelta(days=i)
        h = (db.session.query(db.func.coalesce(db.func.sum(PlanProduction.hours), 0))
             .filter(PlanProduction.prod_date == d, PlanProduction.status != 'cancelled').scalar() or 0)
        daily.append({'date': d.isoformat(), 'hours': float(h)})

    return render_template('planning_kpi.html',
                           month_start=month_start, month_end=month_end,
                           cur=cur, prev=prev,
                           prod_hours=prod_hours, capacity_hours=capacity_hours,
                           utilization=utilization,
                           top_machines=top_machines, daily=daily,
                           prev_m=(prev_month_start.strftime('%Y-%m')),
                           next_m=((month_end + timedelta(days=1)).strftime('%Y-%m')),
                           cur_m=month_start.strftime('%Y-%m'))


# ──────────────────── месячный вид (Ф6) ────────────────────

@bp.route('/planning/month')
@login_required
@role_required('admin', 'director', 'technician')
def planning_month():
    """Обзор месяца: сетка недель × 3 потока."""
    today = now_local().date()
    month_raw = (request.args.get('m') or '').strip()
    try:
        month_start = datetime.strptime(month_raw, '%Y-%m').date().replace(day=1)
    except Exception:
        month_start = today.replace(day=1)
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1) - timedelta(days=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1) - timedelta(days=1)

    # group by ISO week
    weeks = {}
    d = month_start
    while d <= month_end:
        wk = d.isocalendar()[1]
        if wk not in weeks:
            weeks[wk] = {'start': d, 'days': {}, 'purchases': [], 'production': [], 'shipments': []}
        weeks[wk]['days'][d] = {'purchases': [], 'production': [], 'shipments': []}
        d += timedelta(days=1)

    for p in PlanPurchase.query.filter(PlanPurchase.planned_date.between(month_start, month_end)).all():
        if p.planned_date in weeks.get(p.planned_date.isocalendar()[1], {}).get('days', {}):
            weeks[p.planned_date.isocalendar()[1]]['days'][p.planned_date]['purchases'].append(p)
            weeks[p.planned_date.isocalendar()[1]]['purchases'].append(p)
    for p in PlanProduction.query.filter(PlanProduction.prod_date.between(month_start, month_end)).all():
        if p.prod_date in weeks.get(p.prod_date.isocalendar()[1], {}).get('days', {}):
            weeks[p.prod_date.isocalendar()[1]]['days'][p.prod_date]['production'].append(p)
            weeks[p.prod_date.isocalendar()[1]]['production'].append(p)
    for p in PlanShipment.query.filter(PlanShipment.ship_date.between(month_start, month_end)).all():
        if p.ship_date in weeks.get(p.ship_date.isocalendar()[1], {}).get('days', {}):
            weeks[p.ship_date.isocalendar()[1]]['days'][p.ship_date]['shipments'].append(p)
            weeks[p.ship_date.isocalendar()[1]]['shipments'].append(p)

    week_list = [weeks[k] for k in sorted(weeks.keys())]

    return render_template('planning_month.html',
                           month_start=month_start, month_end=month_end,
                           weeks=week_list,
                           prev_m=(month_start - timedelta(days=1)).strftime('%Y-%m'),
                           next_m=((month_end + timedelta(days=1)).strftime('%Y-%m')),
                           cur_m=month_start.strftime('%Y-%m'),
                           today=today)


# ──────────────────── PDF-отчёт ────────────────────

@bp.route('/planning/report.pdf')
@login_required
@role_required('admin', 'director', 'technician')
def planning_report_pdf():
    """Недельная сводка PDF: планы по трём потокам + KPI."""
    from pdf_utils import find_pdf_font, ensure_fpdf
    FPDF = ensure_fpdf().FPDF
    anchor = _anchor_date()
    week_start, week_end = _week_bounds(anchor)

    purchases = PlanPurchase.query.filter(
        PlanPurchase.planned_date.between(week_start, week_end)).order_by(PlanPurchase.planned_date).all()
    production = PlanProduction.query.filter(
        PlanProduction.prod_date.between(week_start, week_end)).order_by(PlanProduction.prod_date, PlanProduction.priority.desc()).all()
    shipments = PlanShipment.query.filter(
        PlanShipment.ship_date.between(week_start, week_end)).order_by(PlanShipment.ship_date).all()

    font_path = find_pdf_font()
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    if font_path:
        pdf.add_font('DejaVu', '', font_path, uni=True)
        pdf.set_font('DejaVu', '', 10)
    else:
        pdf.set_font('Helvetica', '', 10)

    def _txt(s):
        """Coerce to str for fpdf2."""
        return str(s) if s is not None else ''

    # title
    pdf.set_font_size(16)
    pdf.cell(0, 10, _txt(f'Planning \u2014 {week_start.strftime("%d.%m")} \u2014 {week_end.strftime("%d.%m.%Y")}'),
             new_x='LMARGIN', new_y='NEXT', align='C')
    pdf.set_font_size(9)
    pdf.cell(0, 6, _txt(now_local().strftime('%d.%m.%Y %H:%M')), new_x='LMARGIN', new_y='NEXT', align='C')
    pdf.ln(4)

    def _section(title, items, cols):
        """cols = list of (header_label, width, value_fn) tuples."""
        pdf.set_font_size(12)
        pdf.set_fill_color(44, 62, 80)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 8, _txt(f'  {title} ({len(items)})'), new_x='LMARGIN', new_y='NEXT', fill=True)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font_size(9)
        if not items:
            pdf.cell(0, 6, '  \u2014', new_x='LMARGIN', new_y='NEXT')
            pdf.ln(2)
            return
        # header
        pdf.set_fill_color(230, 230, 230)
        for label, w, _fn in cols:
            pdf.cell(w, 6, _txt(label), border=1, fill=True)
        pdf.ln()
        for item in items:
            for _label, w, fn in cols:
                val = fn(item)
                pdf.cell(w, 6, _txt(val)[:40], border=1)
            pdf.ln()
        pdf.ln(2)

    _section(_('Purchases'), purchases, [
        (_('Date'), 22, lambda x: x.planned_date.strftime('%d.%m') if x.planned_date else ''),
        (_('Name'), 80, lambda x: x.article_name),
        (_('Qty'), 25, lambda x: f'{x.qty} {x.unit}'),
        (_('Status'), 25, lambda x: STATUS_LABELS.get(x.status, x.status)),
        (_('Supplier'), 38, lambda x: x.contractor.company_name if x.contractor else ''),
    ])

    _section(_('Production'), production, [
        (_('Date'), 22, lambda x: x.prod_date.strftime('%d.%m') if x.prod_date else ''),
        (_('Name'), 70, lambda x: x.article_name),
        (_('Qty'), 25, lambda x: f'{x.qty} {x.unit}'),
        (_('Machine'), 30, lambda x: x.machine.name if x.machine else (x.machine_name or '')),
        (_('Hours'), 15, lambda x: x.hours or ''),
        (_('Status'), 28, lambda x: STATUS_LABELS.get(x.status, x.status)),
    ])

    _section(_('Shipments'), shipments, [
        (_('Date'), 22, lambda x: x.ship_date.strftime('%d.%m') if x.ship_date else ''),
        (_('Name'), 75, lambda x: x.article_name),
        (_('Qty'), 25, lambda x: f'{x.qty} {x.unit}'),
        (_('Client'), 35, lambda x: x.client_name or ''),
        (_('Status'), 28, lambda x: STATUS_LABELS.get(x.status, x.status)),
    ])

    # summary
    pdf.ln(4)
    pdf.set_font_size(11)
    pdf.cell(0, 8, _txt(_('Summary')), new_x='LMARGIN', new_y='NEXT')
    pdf.set_font_size(9)
    total_h = sum((p.hours or 0) for p in production)
    done_p = sum(1 for p in production if p.status == 'done')
    done_s = sum(1 for p in shipments if p.status == 'done')
    pdf.cell(0, 6, _txt(f'{_("Production")}: {len(production)} ({done_p} {_("done")}), {total_h}h'), new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 6, _txt(f'{_("Shipments")}: {len(shipments)} ({done_s} {_("done")})'), new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 6, _txt(f'{_("Purchases")}: {len(purchases)}'), new_x='LMARGIN', new_y='NEXT')

    out = io.BytesIO()
    pdf.output(out)
    out.seek(0)
    return send_file(out, mimetype='application/pdf', as_attachment=True,
                     download_name=f'planning_{week_start.strftime("%Y%m%d")}.pdf')


# ──────────────────── уведомления (Ф7) ────────────────────

def check_planning_reminders():
    """Вызывается из maintenance_jobs / nightly_check.

    Отправляет уведомления о:
      - отгрузках на завтра / послезавтра
      - просроченных планах (закупки/производство/отгрузки)
    """
    from models import Notification
    from utils import create_notification
    today = now_local().date()
    sent = 0

    # shipments due tomorrow
    for s in PlanShipment.query.filter(
            PlanShipment.status == 'planned',
            PlanShipment.ship_date == today + timedelta(days=1)).all():
        users = _admin_users()
        for u in users:
            # ВАЖНО: у create_notification 3 позиционных аргумента (user_id,
            # title, message) и параметр называется ntype, не type — раньше
            # вызов падал с TypeError и напоминания не уходили вовсе.
            create_notification(
                u.id,
                f'🚚 {_("Shipment tomorrow")}: {s.article_name} ×{s.qty} {s.unit} → {s.client_name or "?"}',
                f'{s.ship_date} · {s.client_name or "?"}',
                'planning', '/planning/shipments')
            sent += 1

    # overdue items
    for model, date_field, icon, label in [
            (PlanPurchase, 'planned_date', '🛒', _('Purchase')),
            (PlanProduction, 'prod_date', '⚙️', _('Production')),
            (PlanShipment, 'ship_date', '🚚', _('Shipment'))]:
        col = getattr(model, date_field)
        overdue = model.query.filter(model.status == 'planned', col < today).all()
        if overdue:
            users = _admin_users()
            for u in users:
                create_notification(
                    u.id,
                    f'⚠️ {_("Overdue")} {label}: {len(overdue)}',
                    str(today),
                    'planning', '/planning')
                sent += 1
    return sent


def _admin_users():
    from models import User
    return User.query.filter(User.role.in_(['admin', 'director', 'technician'])).all()

