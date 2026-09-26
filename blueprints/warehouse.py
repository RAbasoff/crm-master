"""
Warehouse blueprint — inventory, groups, movements, prices, import, labels, reports
"""
import os, csv, io
from datetime import datetime, timedelta
from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify, send_file, current_app
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import (db, VoorraadItem, VoorraadMutatie, WarehouseGroup, WarehouseReservation,
                    SupplierPrice, Machine, Contractor)
from utils import role_required, log_audit, sanitize_like, safe_commit, safe_int, safe_float, safe_date

bp = Blueprint('warehouse', __name__, url_prefix='/warehouse')


# ── GROUPS ──────────────────────────────────────────────────

@bp.route('/groups')
@login_required
@role_required('admin', 'director', 'technician')
def warehouse_groups():
    groups = WarehouseGroup.query.order_by(WarehouseGroup.name).all()
    manufacturers = [m[0] for m in db.session.query(Machine.manufacturer).distinct().all() if m[0]]
    return render_template('warehouse_groups.html', groups=groups, manufacturers=manufacturers)


@bp.route('/groups/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def warehouse_group_new():
    if request.method == 'POST':
        g = WarehouseGroup(
            name=request.form['name'],
            manufacturer=request.form.get('manufacturer', ''),
            description=request.form.get('description', '')
        )
        db.session.add(g)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('warehouse.warehouse_group_new'))
        flash(_('Group created'), 'success')
        return redirect(url_for('warehouse.warehouse_groups'))
    manufacturers = [m[0] for m in db.session.query(Machine.manufacturer).distinct().all() if m[0]]
    return render_template('warehouse_group_form.html', group=None, manufacturers=manufacturers)


@bp.route('/groups/<int:group_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def warehouse_group_edit(group_id):
    g = WarehouseGroup.query.get_or_404(group_id)
    if request.method == 'POST':
        g.name = request.form['name']
        g.manufacturer = request.form.get('manufacturer', '')
        g.description = request.form.get('description', '')
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('warehouse.warehouse_group_edit', group_id=group_id))
        flash(_('Group updated'), 'success')
        return redirect(url_for('warehouse.warehouse_groups'))
    manufacturers = [m[0] for m in db.session.query(Machine.manufacturer).distinct().all() if m[0]]
    return render_template('warehouse_group_form.html', group=g, manufacturers=manufacturers)


@bp.route('/groups/<int:group_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def warehouse_group_delete(group_id):
    g = WarehouseGroup.query.get_or_404(group_id)
    for item in g.items:
        item.group_id = None
    db.session.delete(g)
    if not safe_commit():
        flash(_('Delete failed. Please try again.'), 'error')
        return redirect(url_for('warehouse.warehouse_groups'))
    flash(_('Group deleted'), 'success')
    return redirect(url_for('warehouse.warehouse_groups'))


@bp.route('/groups/auto-create', methods=['POST'])
@login_required
@role_required('admin')
def warehouse_groups_auto():
    created = 0
    manufacturers = [m[0] for m in db.session.query(Machine.manufacturer).distinct().all() if m[0]]
    for mfg in manufacturers:
        existing = WarehouseGroup.query.filter_by(manufacturer=mfg).first()
        if not existing:
            g = WarehouseGroup(name=mfg, manufacturer=mfg, description=f'Auto-created from manufacturer: {mfg}')
            db.session.add(g)
            created += 1
    contractors = Contractor.query.filter(Contractor.company_name.isnot(None), Contractor.company_name != '').all()
    for c in contractors:
        existing = WarehouseGroup.query.filter_by(name=c.company_name).first()
        if not existing:
            g = WarehouseGroup(name=c.company_name, manufacturer=c.company_name, description=f'Contractor: {c.company_name} - {c.service_type or ""}')
            db.session.add(g)
            created += 1
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('warehouse.warehouse_groups'))
    flash(_('{} groups created').format(created), 'success')
    return redirect(url_for('warehouse.warehouse_groups'))


# ── ITEMS ───────────────────────────────────────────────────

@bp.route('/')
@login_required
@role_required('admin', 'director', 'technician')
def warehouse_list():
    page = request.args.get('page', 1, type=int)
    cat = request.args.get('categorie', '')
    group_id = request.args.get('group', '')
    q = VoorraadItem.query

    logistiek_group_id = _get_logistiek_warehouse_group_id()

    # Logistiek group: restrict to Oktopus warehouse group only
    if logistiek_group_id and not current_user.has_role('admin', 'director'):
        group_id = str(logistiek_group_id)

    if cat: q = q.filter_by(categorie=cat)
    if group_id:
        q = q.filter_by(group_id=int(group_id))
    else:
        # For admin/director: exclude Logistiek-Oktopus items from main list (shown separately)
        if logistiek_group_id and current_user.has_role('admin', 'director'):
            q = q.filter((VoorraadItem.group_id != logistiek_group_id) | (VoorraadItem.group_id.is_(None)))

    pagination = q.order_by(VoorraadItem.naam).paginate(page=page, per_page=25, error_out=False)
    items = pagination.items
    cats = [c[0] for c in db.session.query(VoorraadItem.categorie).distinct().all() if c[0]]
    groups = WarehouseGroup.query.order_by(WarehouseGroup.name).all()
    laag = [i for i in items if i.hoeveelheid <= i.minimum]
    # Logistiek-Oktopus items for admin/director view
    oktopus_items = []
    oktopus_low = []
    if logistiek_group_id and current_user.has_role('admin', 'director'):
        oktopus_items = VoorraadItem.query.filter_by(group_id=logistiek_group_id).order_by(VoorraadItem.naam).all()
        oktopus_low = [i for i in oktopus_items if i.minimum and i.hoeveelheid <= i.minimum]
    return render_template('warehouse.html', items=items, categories=cats, category_filter=cat,
        groups=groups, group_filter=int(group_id) if group_id else None, low_stock=laag, pagination=pagination,
        oktopus_items=oktopus_items, oktopus_low=oktopus_low, logistiek_group_id=logistiek_group_id)


def _get_logistiek_warehouse_group_id():
    """If current user belongs to Logistiek group, return the Oktopus warehouse group ID."""
    try:
        person = getattr(current_user, '_person', None) or getattr(current_user, 'person', None)
        if person and person.resp_group and person.resp_group.name == 'Logistiek - Oktopus':
            wg = WarehouseGroup.query.filter_by(name='Logistiek - Oktopus').first()
            return wg.id if wg else None
    except Exception:
        pass
    return None


@bp.route('/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def warehouse_new():
    logistiek_wh_id = _get_logistiek_warehouse_group_id()
    if request.method == 'POST':
        # Parse group_id which comes as "g_123" or "c_123" from the form
        raw_group = request.form.get('group_id', '')
        group_id = None
        contractor_id = None
        if raw_group.startswith('g_'):
            group_id = int(raw_group[2:])
        elif raw_group.startswith('c_'):
            contractor_id = int(raw_group[2:])
        # Logistiek users: force Oktopus group
        if logistiek_wh_id and not current_user.has_role('admin', 'director'):
            group_id = logistiek_wh_id
            contractor_id = None
        d_last = safe_date(request.form.get('last_replacement'))
        d_next = safe_date(request.form.get('next_replacement'))
        i = VoorraadItem(
            naam=request.form['naam'],
            description=request.form.get('description', ''),
            categorie=request.form.get('categorie',''),
            group_id=group_id,
            contractor_id=contractor_id,
            supplier_part_number=request.form.get('supplier_part_number','').strip() or None,
            eenheid=request.form.get('eenheid','st'),
            hoeveelheid=safe_float(request.form.get('hoeveelheid'), 0),
            minimum=safe_float(request.form.get('minimum'), 0),
            prijs=safe_float(request.form.get('prijs'), 0),
            locatie=request.form.get('locatie',''),
            consumable_type=request.form.get('consumable_type',''),
            consumable_subtype=request.form.get('consumable_subtype',''),
            volume=request.form.get('volume',''),
            compatible_machines=request.form.get('compatible_machines',''),
            replacement_interval=request.form.get('replacement_interval',''),
            last_replacement=d_last.date() if d_last else None,
            next_replacement=d_next.date() if d_next else None,
        )
        db.session.add(i)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('warehouse.warehouse_new'))
        flash(_('Item added') + f': {i.naam}', 'success')
        return redirect(url_for('warehouse.warehouse_list', new_qr=i.id))
    groups = WarehouseGroup.query.order_by(WarehouseGroup.name).all()
    contractors = Contractor.query.order_by(Contractor.company_name).all()
    prefill_barcode = request.args.get('barcode', '')
    return render_template('warehouse_form.html', item=None, groups=groups, contractors=contractors, prefill_barcode=prefill_barcode)


@bp.route('/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def warehouse_edit(item_id):
    item = VoorraadItem.query.get_or_404(item_id)
    from utils import acquire_lock, release_lock
    if request.method == 'POST':
        ok, lock_info = acquire_lock('warehouse', item_id, current_user.id, current_user.username)
        if not ok:
            flash(_('Record is being edited by %(user)s. Try again later.', user=lock_info.get('user_name', '?')), 'error')
            return redirect(url_for('warehouse.warehouse_list'))
        item.naam = request.form['naam']
        item.description = request.form.get('description', '')
        item.categorie = request.form.get('categorie','')
        raw_group = request.form.get('group_id', '')
        if raw_group.startswith('g_'):
            item.group_id = int(raw_group[2:])
            item.contractor_id = None
        elif raw_group.startswith('c_'):
            item.group_id = None
            item.contractor_id = int(raw_group[2:])
        else:
            item.group_id = None
            item.contractor_id = None
        item.supplier_part_number = request.form.get('supplier_part_number','').strip() or None
        item.eenheid = request.form.get('eenheid','st')
        item.hoeveelheid = safe_float(request.form.get('hoeveelheid'), 0)
        item.minimum = safe_float(request.form.get('minimum'), 0)
        item.prijs = safe_float(request.form.get('prijs'), 0)
        item.locatie = request.form.get('locatie','')
        item.consumable_type = request.form.get('consumable_type','')
        item.consumable_subtype = request.form.get('consumable_subtype','')
        item.volume = request.form.get('volume','')
        item.compatible_machines = request.form.get('compatible_machines','')
        item.replacement_interval = request.form.get('replacement_interval','')
        d = safe_date(request.form.get('last_replacement'))
        item.last_replacement = d.date() if d else None
        d = safe_date(request.form.get('next_replacement'))
        item.next_replacement = d.date() if d else None
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('warehouse.warehouse_edit', item_id=item_id))
        release_lock('warehouse', item_id, current_user.id)
        flash(_('Item updated'), 'success')
        return redirect(url_for('warehouse.warehouse_list'))
    ok, lock_info = acquire_lock('warehouse', item_id, current_user.id, current_user.username)
    if not ok:
        flash(_('⚠️ This record is being edited by %(user)s (since %(time)s). You cannot edit it now.', user=lock_info.get('user_name', '?'), time=lock_info.get('locked_at', '?')), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    groups = WarehouseGroup.query.order_by(WarehouseGroup.name).all()
    contractors = Contractor.query.order_by(Contractor.company_name).all()
    return render_template('warehouse_form.html', item=item, groups=groups, contractors=contractors)


@bp.route('/duplicates')
@login_required
@role_required('admin')
def warehouse_duplicates():
    from sqlalchemy import func
    dupes_name = db.session.query(
        func.lower(VoorraadItem.naam).label('name'),
        func.count().label('cnt')
    ).group_by(func.lower(VoorraadItem.naam)).having(func.count() > 1).all()
    dupe_groups = []
    for name, cnt in dupes_name:
        items = VoorraadItem.query.filter(func.lower(VoorraadItem.naam) == name).order_by(VoorraadItem.id).all()
        dupe_groups.append({'name': name, 'count': cnt, 'item_list': items})
    dupes_spn = db.session.query(
        VoorraadItem.supplier_part_number.label('spn'),
        func.count().label('cnt')
    ).filter(VoorraadItem.supplier_part_number.isnot(None), VoorraadItem.supplier_part_number != '').group_by(VoorraadItem.supplier_part_number).having(func.count() > 1).all()
    spn_groups = []
    for spn, cnt in dupes_spn:
        items = VoorraadItem.query.filter_by(supplier_part_number=spn).order_by(VoorraadItem.id).all()
        spn_groups.append({'spn': spn, 'count': cnt, 'item_list': items})
    return render_template('warehouse_duplicates.html', dupe_groups=dupe_groups, spn_groups=spn_groups)


@bp.route('/<int:item_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def warehouse_delete(item_id):
    item = VoorraadItem.query.get_or_404(item_id)
    name = item.naam
    if item.mutaties:
        flash(_('Cannot delete item with movement history. Deactivate instead.'), 'error')
        return redirect(url_for('warehouse.warehouse_edit', item_id=item.id))
    db.session.delete(item)
    if not safe_commit():
        flash(_('Delete failed. Please try again.'), 'error')
        return redirect(url_for('warehouse.warehouse_edit', item_id=item_id))
    log_audit('delete', 'warehouse_item', item_id, name)
    flash(_('Item deleted') + f': {name}', 'success')
    return redirect(url_for('warehouse.warehouse_duplicates'))


# ── MOVEMENTS ───────────────────────────────────────────────

@bp.route('/<int:item_id>/move', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def warehouse_move(item_id):
    item = VoorraadItem.query.get_or_404(item_id)
    # Logistiek users can only move items in their group
    logistiek_wh_id = _get_logistiek_warehouse_group_id()
    if logistiek_wh_id and not current_user.has_role('admin', 'director'):
        if item.group_id != logistiek_wh_id:
            flash(_('Access denied'), 'error')
            return redirect(url_for('warehouse.warehouse_list'))
    mt = request.form.get('type', '')
    if mt not in ('inkomend', 'uitgaand'):
        flash(_('Invalid movement type'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    try:
        qty = float(request.form.get('hoeveelheid', 0))
    except (ValueError, TypeError):
        flash(_('Invalid quantity'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    if qty <= 0:
        flash(_('Quantity must be positive'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    if mt == 'uitgaand' and qty > item.hoeveelheid:
        flash(_('Insufficient stock!'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    m = VoorraadMutatie(item_id=item_id, type=mt, hoeveelheid=qty,
                        opdracht_id=request.form.get('opdracht_id') or None,
                        opmerking=request.form.get('opmerking',''),
                        user_id=current_user.id)
    if mt == 'inkomend': item.hoeveelheid += qty
    else: item.hoeveelheid -= qty
    db.session.add(m)
    if not safe_commit():
        flash(_('Movement failed. Please try again.'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    # Low-stock notification for admin
    if item.minimum and item.hoeveelheid <= item.minimum:
        from utils import create_notification
        from models import User
        admins = User.query.filter_by(role='admin', is_active_user=True).all()
        level = 'critical' if item.hoeveelheid <= (item.minimum * 0.5) else 'low'
        msg = f'[{level.upper()}] {item.naam}: {item.hoeveelheid} {item.eenheid} (min: {item.minimum})'
        for a in admins:
            create_notification(a.id, msg, link='/warehouse/')
        _notify_logistiek_low_stock(item)
    flash(_('{} {} {} — {}').format(mt.capitalize(), qty, item.eenheid, item.naam), 'success')
    return redirect(url_for('warehouse.warehouse_list'))


@bp.route('/movements')
@login_required
@role_required('admin', 'director', 'technician')
def warehouse_movements():
    item_id = request.args.get('item', '')
    move_type = request.args.get('type', '')
    q = VoorraadMutatie.query
    if item_id: q = q.filter_by(item_id=int(item_id))
    if move_type: q = q.filter_by(type=move_type)
    movements = q.order_by(VoorraadMutatie.aangemaakt.desc()).limit(200).all()
    items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    return render_template('warehouse_movements.html', movements=movements, items=items,
                         item_filter=int(item_id) if item_id else None, type_filter=move_type)


# ── RESERVATIONS ────────────────────────────────────────────

@bp.route('/reserve/<int:item_id>', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def warehouse_reserve(item_id):
    item = VoorraadItem.query.get_or_404(item_id)
    qty = safe_float(request.form.get('quantity'), 1)
    if qty > item.hoeveelheid:
        flash(_('Insufficient stock for reservation!'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    r = WarehouseReservation(
        item_id=item_id, quantity=qty,
        reserved_for=request.form.get('reserved_for', ''),
        reserved_by=current_user.id,
        notes=request.form.get('notes', '')
    )
    db.session.add(r)
    if not safe_commit():
        flash(_('Reservation failed. Please try again.'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    flash(_('Reserved {} {} for {}').format(qty, item.eenheid, r.reserved_for), 'success')
    return redirect(url_for('warehouse.warehouse_list'))


@bp.route('/reserve/<int:res_id>/release', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def warehouse_release(res_id):
    r = WarehouseReservation.query.get_or_404(res_id)
    db.session.delete(r)
    if not safe_commit():
        flash(_('Release failed. Please try again.'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    flash(_('Reservation released'), 'success')
    return redirect(url_for('warehouse.warehouse_list'))


# ── INVENTORY ───────────────────────────────────────────────

@bp.route('/inventory')
@login_required
@role_required('admin', 'director')
def warehouse_inventory():
    items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    return render_template('warehouse_inventory.html', items=items)


@bp.route('/inventory/check', methods=['POST'])
@login_required
@role_required('admin', 'director')
def warehouse_inventory_check():
    data = request.get_json()
    item_id = data.get('item_id')
    actual_qty = safe_float(data.get('quantity'), 0)
    item = VoorraadItem.query.get(item_id)
    if not item:
        return jsonify({'error': 'Item not found'}), 404
    diff = actual_qty - item.hoeveelheid
    if diff != 0:
        m = VoorraadMutatie(
            item_id=item_id,
            type='inkomend' if diff > 0 else 'uitgaand',
            hoeveelheid=abs(diff),
            opmerking=f'Инвентаризация: было {item.hoeveelheid}, стало {actual_qty}',
            user_id=current_user.id
        )
        item.hoeveelheid = actual_qty
        db.session.add(m)
        if not safe_commit():
            return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'diff': diff})


# ── PRICES ──────────────────────────────────────────────────

@bp.route('/<int:item_id>/prices')
@login_required
@role_required('admin', 'director')
def warehouse_prices(item_id):
    item = VoorraadItem.query.get_or_404(item_id)
    prices = SupplierPrice.query.filter_by(item_id=item_id).order_by(SupplierPrice.price).all()
    return render_template('warehouse_prices.html', item=item, prices=prices)


@bp.route('/<int:item_id>/prices/add', methods=['POST'])
@login_required
@role_required('admin', 'director')
def warehouse_price_add(item_id):
    try:
        price = float(request.form.get('price', 0))
    except (ValueError, TypeError):
        flash(_('Invalid price'), 'error')
        return redirect(url_for('warehouse.warehouse_prices', item_id=item_id))
    p = SupplierPrice(
        item_id=item_id,
        supplier_name=request.form.get('supplier_name', ''),
        price=price,
        delivery_days=safe_int(request.form.get('delivery_days')) or None,
        min_order=safe_float(request.form.get('min_order')) or None,
        notes=request.form.get('notes', '')
    )
    db.session.add(p)
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('warehouse.warehouse_prices', item_id=item_id))
    flash(_('Price added'), 'success')
    return redirect(url_for('warehouse.warehouse_prices', item_id=item_id))


# ── IMPORT ──────────────────────────────────────────────────

@bp.route('/import', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def warehouse_import():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file:
            flash(_('No file'), 'error')
            return redirect(url_for('warehouse.warehouse_import'))
        content = file.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(content))
        count = 0
        for row in reader:
            naam = row.get('name', row.get('Name', '')).strip()
            if not naam: continue
            item = VoorraadItem(
                naam=naam, description=row.get('description', ''),
                categorie=row.get('category', ''),
                supplier_part_number=row.get('spn', ''),
                eenheid=row.get('unit', 'st'),
                hoeveelheid=float(row.get('quantity', 0)),
                minimum=float(row.get('minimum', 0)),
                prijs=float(row.get('price', 0)),
                locatie=row.get('location', ''),
                serial_number=row.get('serial', ''),
                barcode=row.get('barcode', '')
            )
            db.session.add(item)
            count += 1
        if not safe_commit():
            flash(_('Import failed. Please try again.'), 'error')
            return redirect(url_for('warehouse.warehouse_import'))
        flash(_('{} items imported').format(count), 'success')
        return redirect(url_for('warehouse.warehouse_list'))
    return render_template('warehouse_import.html')


# ── REPORT / SEARCH ─────────────────────────────────────────

@bp.route('/report')
@login_required
@role_required('admin', 'director', 'technician')
def warehouse_search_report():
    from sqlalchemy import func, and_, or_
    q_text = request.args.get('q', '').strip()
    spn = request.args.get('spn', '').strip()
    locatie = request.args.get('locatie', '').strip()
    categorie = request.args.get('categorie', '').strip()
    move_type = request.args.get('move_type', '').strip()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    group_id = request.args.get('group', '')
    report_mode = request.args.get('report', '')

    item_q = VoorraadItem.query
    if q_text:
        item_q = item_q.filter(or_(
            VoorraadItem.naam.ilike(f'%{sanitize_like(q_text)}%'),
            VoorraadItem.description.ilike(f'%{sanitize_like(q_text)}%'),
            VoorraadItem.supplier_part_number.ilike(f'%{sanitize_like(q_text)}%')
        ))
    if spn: item_q = item_q.filter(VoorraadItem.supplier_part_number.ilike(f'%{sanitize_like(spn)}%'))
    if locatie: item_q = item_q.filter(VoorraadItem.locatie.ilike(f'%{sanitize_like(locatie)}%'))
    if categorie: item_q = item_q.filter_by(categorie=categorie)
    if group_id: item_q = item_q.filter_by(group_id=int(group_id))
    items = item_q.order_by(VoorraadItem.naam).all()

    mov_q = VoorraadMutatie.query.join(VoorraadItem)
    if q_text:
        mov_q = mov_q.filter(or_(
            VoorraadItem.naam.ilike(f'%{sanitize_like(q_text)}%'),
            VoorraadItem.supplier_part_number.ilike(f'%{sanitize_like(q_text)}%'),
            VoorraadMutatie.opmerking.ilike(f'%{sanitize_like(q_text)}%')
        ))
    if spn: mov_q = mov_q.filter(VoorraadItem.supplier_part_number.ilike(f'%{sanitize_like(spn)}%'))
    if locatie: mov_q = mov_q.filter(VoorraadItem.locatie.ilike(f'%{sanitize_like(locatie)}%'))
    if categorie: mov_q = mov_q.filter(VoorraadItem.categorie == categorie)
    if group_id: mov_q = mov_q.filter(VoorraadItem.group_id == int(group_id))
    if move_type: mov_q = mov_q.filter(VoorraadMutatie.type == move_type)
    if date_from:
        try:
            dt = datetime.strptime(date_from, '%Y-%m-%d')
            mov_q = mov_q.filter(VoorraadMutatie.aangemaakt >= dt)
        except ValueError: flash(_('Invalid date format'), 'error')
    if date_to:
        try:
            dt = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            mov_q = mov_q.filter(VoorraadMutatie.aangemaakt < dt)
        except ValueError: flash(_('Invalid date format'), 'error')
    movements = mov_q.order_by(VoorraadMutatie.aangemaakt.desc()).limit(500).all()

    total_in = sum(m.hoeveelheid for m in movements if m.type == 'inkomend')
    total_out = sum(m.hoeveelheid for m in movements if m.type == 'uitgaand')
    total_in_value = sum(float(m.hoeveelheid) * float(m.item.prijs or 0) for m in movements if m.type == 'inkomend')
    total_out_value = sum(float(m.hoeveelheid) * float(m.item.prijs or 0) for m in movements if m.type == 'uitgaand')

    item_report = {}
    for m in movements:
        key = m.item_id
        if key not in item_report:
            item_report[key] = {'item': m.item, 'in_qty': 0, 'out_qty': 0, 'in_val': 0, 'out_val': 0}
        if m.type == 'inkomend':
            item_report[key]['in_qty'] += m.hoeveelheid
            item_report[key]['in_val'] += m.hoeveelheid * m.item.prijs
        else:
            item_report[key]['out_qty'] += m.hoeveelheid
            item_report[key]['out_val'] += m.hoeveelheid * m.item.prijs
    item_report = sorted(item_report.values(), key=lambda x: x['item'].naam)

    cats = [c[0] for c in db.session.query(VoorraadItem.categorie).distinct().all() if c[0]]
    groups = WarehouseGroup.query.order_by(WarehouseGroup.name).all()

    return render_template('warehouse_search.html',
        items=items, movements=movements, item_report=item_report,
        total_in=total_in, total_out=total_out,
        total_in_value=total_in_value, total_out_value=total_out_value,
        cats=cats, groups=groups,
        f_q=q_text, f_spn=spn, f_locatie=locatie, f_categorie=categorie,
        f_move_type=move_type, f_date_from=date_from, f_date_to=date_to,
        f_group=group_id, report_mode=report_mode)


# ── QUICK QTY EDIT ──────────────────────────────────────────

@bp.route('/api/qty', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def warehouse_qty_update():
    from flask import jsonify
    data = request.get_json()
    item_id = data.get('item_id')
    qty = safe_float(data.get('quantity'), 0)
    item = VoorraadItem.query.get(item_id)
    if not item:
        return jsonify({'error': 'Not found'}), 404
    old_qty = item.hoeveelheid
    item.hoeveelheid = qty
    # Log movement
    diff = qty - old_qty
    if diff != 0:
        m = VoorraadMutatie(
            item_id=item_id,
            type='inkomend' if diff > 0 else 'uitgaand',
            hoeveelheid=abs(diff),
            opmerking=f'Quick edit: {old_qty} → {qty}',
            user_id=current_user.id
        )
        db.session.add(m)
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    # Low-stock notification for admin
    if item.minimum and item.hoeveelheid <= item.minimum:
        from utils import create_notification
        from models import User
        admins = User.query.filter_by(role='admin', is_active_user=True).all()
        level = 'critical' if item.hoeveelheid <= (item.minimum * 0.5) else 'low'
        msg = f'[{level.upper()}] {item.naam}: {item.hoeveelheid} {item.eenheid} (min: {item.minimum})'
        for a in admins:
            create_notification(a.id, msg, link='/warehouse/')
        _notify_logistiek_low_stock(item)
    return jsonify({'ok': True, 'min_warning': qty <= item.minimum})


# ── LABELS ──────────────────────────────────────────────────

@bp.route('/labels')
@login_required
def warehouse_labels():
    ids = request.args.get('ids', '')
    if ids:
        item_ids = [safe_int(x) for x in ids.split(',') if x.strip()]
        items = VoorraadItem.query.filter(VoorraadItem.id.in_(item_ids)).all()
    else:
        items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    return render_template('warehouse_labels.html', items=items)


@bp.route('/transfer-print')
@login_required
def warehouse_transfer_print():
    """Print selected items for transfer to a responsible person."""
    ids = request.args.get('ids', '')
    if not ids:
        flash(_('Select items first'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    item_ids = [safe_int(x) for x in ids.split(',') if x.strip()]
    items = VoorraadItem.query.filter(VoorraadItem.id.in_(item_ids)).all()
    return render_template('warehouse_transfer.html', items=items, now=datetime.utcnow())


@bp.route('/<int:item_id>/transfer', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def warehouse_transfer(item_id):
    """Transfer item quantity to a responsible person."""
    from models import Verantwoordelijke
    item = VoorraadItem.query.get_or_404(item_id)
    persons = Verantwoordelijke.query.filter_by(actief=True).order_by(Verantwoordelijke.naam).all()

    if request.method == 'POST':
        person_id = safe_int(request.form.get('person_id'))
        qty = safe_float(request.form.get('quantity'), 0)
        notes = request.form.get('notes', '')

        if not person_id:
            flash(_('Select a responsible person'), 'error')
            return redirect(url_for('warehouse.warehouse_transfer', item_id=item_id))
        if qty <= 0:
            flash(_('Quantity must be positive'), 'error')
            return redirect(url_for('warehouse.warehouse_transfer', item_id=item_id))
        if qty > item.hoeveelheid:
            flash(_('Insufficient stock! Available: {} {}').format(item.hoeveelheid, item.eenheid), 'error')
            return redirect(url_for('warehouse.warehouse_transfer', item_id=item_id))

        person = Verantwoordelijke.query.get(person_id)
        if not person:
            flash(_('Person not found'), 'error')
            return redirect(url_for('warehouse.warehouse_transfer', item_id=item_id))

        # Create outgoing movement
        m = VoorraadMutatie(
            item_id=item_id, type='uitgaand', hoeveelheid=qty,
            opmerking=f'Transfer to: {person.naam}' + (f' — {notes}' if notes else ''),
            user_id=current_user.id
        )
        item.hoeveelheid -= qty

        # Create reservation (tracks who has it)
        r = WarehouseReservation(
            item_id=item_id, quantity=qty,
            reserved_for=person.naam,
            reserved_by=current_user.id,
            notes=notes or f'Transferred to {person.naam}'
        )
        db.session.add(m)
        db.session.add(r)
        if not safe_commit():
            flash(_('Transfer failed. Please try again.'), 'error')
            return redirect(url_for('warehouse.warehouse_transfer', item_id=item_id))

        # Low-stock notification
        if item.minimum and item.hoeveelheid <= item.minimum:
            from utils import create_notification
            from models import User
            admins = User.query.filter_by(role='admin', is_active_user=True).all()
            level = 'critical' if item.hoeveelheid <= (item.minimum * 0.5) else 'low'
            msg = f'[{level.upper()}] {item.naam}: {item.hoeveelheid} {item.eenheid} (min: {item.minimum})'
            for a in admins:
                create_notification(a.id, msg, link='/warehouse/')

        flash(_('Transferred {} {} {} to {}').format(qty, item.eenheid, item.naam, person.naam), 'success')
        return redirect(url_for('warehouse.warehouse_list'))

    return render_template('warehouse_transfer_form.html', item=item, persons=persons)


def _get_logistiek_group_id():
    """Return the Logistiek - Oktopus WarehouseGroup ID."""
    wg = WarehouseGroup.query.filter_by(name='Logistiek - Oktopus').first()
    return wg.id if wg else None


def _notify_logistiek_low_stock(item):
    """Notify Logistiek-Oktopus responsible persons about low stock."""
    from models import Verantwoordelijke, User
    from utils import create_notification
    logistiek_group_id = _get_logistiek_group_id()
    if not logistiek_group_id or item.group_id != logistiek_group_id:
        return
    # Find responsible persons in Logistiek group
    persons = Verantwoordelijke.query.filter_by(group_id=None).all()
    from models import ResponsibleGroup
    logistiek_resp = ResponsibleGroup.query.filter_by(name='Logistiek - Oktopus').first()
    if not logistiek_resp:
        return
    persons = Verantwoordelijke.query.filter_by(group_id=logistiek_resp.id).all()
    level = 'critical' if item.hoeveelheid <= (item.minimum * 0.5) else 'low'
    msg = f'[{level.upper()}] {item.naam}: {item.hoeveelheid} {item.eenheid} (min: {item.minimum})'
    for p in persons:
        if p.username:
            user = User.query.filter_by(username=p.username).first()
            if user:
                create_notification(user.id, f'⚠️ Склад Oktopus: {msg}', link='/warehouse/')


@bp.route('/transfer-to-oktopus', methods=['POST'])
@login_required
@role_required('admin', 'director')
def warehouse_transfer_to_oktopus():
    """Transfer items from main warehouse to Logistiek-Oktopus."""
    ids = request.form.get('ids', '')
    if not ids:
        flash(_('Select items first'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    logistiek_id = _get_logistiek_group_id()
    if not logistiek_id:
        flash(_('Logistiek-Oktopus group not found'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    item_ids = [safe_int(x) for x in ids.split(',') if x.strip()]
    count = 0
    for item_id in item_ids:
        item = VoorraadItem.query.get(item_id)
        if item and item.group_id != logistiek_id:
            item.group_id = logistiek_id
            count += 1
    if not safe_commit():
        flash(_('Transfer failed. Please try again.'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    flash(_('{} items transferred to Logistiek-Oktopus').format(count), 'success')
    return redirect(url_for('warehouse.warehouse_list'))


@bp.route('/transfer-from-oktopus', methods=['POST'])
@login_required
@role_required('admin', 'director')
def warehouse_transfer_from_oktopus():
    """Transfer items from Logistiek-Oktopus back to main warehouse."""
    ids = request.form.get('ids', '')
    if not ids:
        flash(_('Select items first'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    logistiek_id = _get_logistiek_group_id()
    item_ids = [safe_int(x) for x in ids.split(',') if x.strip()]
    count = 0
    for item_id in item_ids:
        item = VoorraadItem.query.get(item_id)
        if item and item.group_id == logistiek_id:
            item.group_id = None
            count += 1
    if not safe_commit():
        flash(_('Transfer failed. Please try again.'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    flash(_('{} items returned to main warehouse').format(count), 'success')
    return redirect(url_for('warehouse.warehouse_list'))
