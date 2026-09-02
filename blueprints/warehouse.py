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
from utils import role_required, log_audit

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
        db.session.add(g); db.session.commit()
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
        db.session.commit()
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
    db.session.delete(g); db.session.commit()
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
    db.session.commit()
    flash(_('{} groups created').format(created), 'success')
    return redirect(url_for('warehouse.warehouse_groups'))


# ── ITEMS ───────────────────────────────────────────────────

@bp.route('/')
@login_required
@role_required('admin', 'director', 'technician')
def warehouse_list():
    cat = request.args.get('categorie', '')
    group_id = request.args.get('group', '')
    q = VoorraadItem.query
    if cat: q = q.filter_by(categorie=cat)
    if group_id: q = q.filter_by(group_id=int(group_id))
    items = q.order_by(VoorraadItem.naam).all()
    cats = [c[0] for c in db.session.query(VoorraadItem.categorie).distinct().all() if c[0]]
    groups = WarehouseGroup.query.order_by(WarehouseGroup.name).all()
    laag = [i for i in items if i.hoeveelheid <= i.minimum]
    return render_template('warehouse.html', items=items, categories=cats, category_filter=cat,
        groups=groups, group_filter=int(group_id) if group_id else None, low_stock=laag)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def warehouse_new():
    if request.method == 'POST':
        i = VoorraadItem(
            naam=request.form['naam'],
            description=request.form.get('description', ''),
            categorie=request.form.get('categorie',''),
            group_id=int(request.form['group_id']) if request.form.get('group_id') else None,
            contractor_id=int(request.form['contractor_id']) if request.form.get('contractor_id') else None,
            supplier_part_number=request.form.get('supplier_part_number','').strip() or None,
            eenheid=request.form.get('eenheid','st'),
            hoeveelheid=float(request.form.get('hoeveelheid',0)),
            minimum=float(request.form.get('minimum',0)),
            prijs=float(request.form.get('prijs',0)),
            locatie=request.form.get('locatie',''),
            consumable_type=request.form.get('consumable_type',''),
            consumable_subtype=request.form.get('consumable_subtype',''),
            volume=request.form.get('volume',''),
            compatible_machines=request.form.get('compatible_machines',''),
            replacement_interval=request.form.get('replacement_interval',''),
            last_replacement=datetime.strptime(request.form['last_replacement'], '%Y-%m-%d').date() if request.form.get('last_replacement') else None,
            next_replacement=datetime.strptime(request.form['next_replacement'], '%Y-%m-%d').date() if request.form.get('next_replacement') else None,
        )
        db.session.add(i); db.session.commit()
        flash(_('Item added') + f': {i.naam}', 'success')
        return redirect(url_for('warehouse.warehouse_list'))
    groups = WarehouseGroup.query.order_by(WarehouseGroup.name).all()
    contractors = Contractor.query.order_by(Contractor.company_name).all()
    return render_template('warehouse_form.html', item=None, groups=groups, contractors=contractors)


@bp.route('/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def warehouse_edit(item_id):
    item = VoorraadItem.query.get_or_404(item_id)
    if request.method == 'POST':
        item.naam = request.form['naam']
        item.description = request.form.get('description', '')
        item.categorie = request.form.get('categorie','')
        item.group_id = int(request.form['group_id']) if request.form.get('group_id') else None
        item.contractor_id = int(request.form['contractor_id']) if request.form.get('contractor_id') else None
        item.supplier_part_number = request.form.get('supplier_part_number','').strip() or None
        item.eenheid = request.form.get('eenheid','st')
        item.hoeveelheid = float(request.form.get('hoeveelheid',0))
        item.minimum = float(request.form.get('minimum',0))
        item.prijs = float(request.form.get('prijs',0))
        item.locatie = request.form.get('locatie','')
        item.consumable_type = request.form.get('consumable_type','')
        item.consumable_subtype = request.form.get('consumable_subtype','')
        item.volume = request.form.get('volume','')
        item.compatible_machines = request.form.get('compatible_machines','')
        item.replacement_interval = request.form.get('replacement_interval','')
        item.last_replacement = datetime.strptime(request.form['last_replacement'], '%Y-%m-%d').date() if request.form.get('last_replacement') else None
        item.next_replacement = datetime.strptime(request.form['next_replacement'], '%Y-%m-%d').date() if request.form.get('next_replacement') else None
        db.session.commit()
        flash(_('Item updated'), 'success')
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
    db.session.commit()
    log_audit('delete', 'warehouse_item', item_id, name)
    flash(_('Item deleted') + f': {name}', 'success')
    return redirect(url_for('warehouse.warehouse_duplicates'))


# ── MOVEMENTS ───────────────────────────────────────────────

@bp.route('/<int:item_id>/move', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def warehouse_move(item_id):
    item = VoorraadItem.query.get_or_404(item_id)
    mt = request.form['type']
    qty = float(request.form['hoeveelheid'])
    if mt == 'uitgaand' and qty > item.hoeveelheid:
        flash(_('Insufficient stock!'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    m = VoorraadMutatie(item_id=item_id, type=mt, hoeveelheid=qty,
                        opdracht_id=request.form.get('opdracht_id') or None,
                        opmerking=request.form.get('opmerking',''),
                        user_id=current_user.id)
    if mt == 'inkomend': item.hoeveelheid += qty
    else: item.hoeveelheid -= qty
    db.session.add(m); db.session.commit()
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
@role_required('admin', 'director', 'technician')
def warehouse_reserve(item_id):
    item = VoorraadItem.query.get_or_404(item_id)
    qty = float(request.form.get('quantity', 1))
    if qty > item.hoeveelheid:
        flash(_('Insufficient stock for reservation!'), 'error')
        return redirect(url_for('warehouse.warehouse_list'))
    r = WarehouseReservation(
        item_id=item_id, quantity=qty,
        reserved_for=request.form.get('reserved_for', ''),
        reserved_by=current_user.id,
        notes=request.form.get('notes', '')
    )
    db.session.add(r); db.session.commit()
    flash(_('Reserved {} {} for {}').format(qty, item.eenheid, r.reserved_for), 'success')
    return redirect(url_for('warehouse.warehouse_list'))


@bp.route('/reserve/<int:res_id>/release', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def warehouse_release(res_id):
    r = WarehouseReservation.query.get_or_404(res_id)
    db.session.delete(r); db.session.commit()
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
    actual_qty = float(data.get('quantity', 0))
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
        db.session.add(m); db.session.commit()
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
    p = SupplierPrice(
        item_id=item_id,
        supplier_name=request.form['supplier_name'],
        price=float(request.form['price']),
        delivery_days=int(request.form['delivery_days']) if request.form.get('delivery_days') else None,
        min_order=float(request.form['min_order']) if request.form.get('min_order') else None,
        notes=request.form.get('notes', '')
    )
    db.session.add(p); db.session.commit()
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
        db.session.commit()
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
            VoorraadItem.naam.ilike(f'%{q_text}%'),
            VoorraadItem.description.ilike(f'%{q_text}%'),
            VoorraadItem.supplier_part_number.ilike(f'%{q_text}%')
        ))
    if spn: item_q = item_q.filter(VoorraadItem.supplier_part_number.ilike(f'%{spn}%'))
    if locatie: item_q = item_q.filter(VoorraadItem.locatie.ilike(f'%{locatie}%'))
    if categorie: item_q = item_q.filter_by(categorie=categorie)
    if group_id: item_q = item_q.filter_by(group_id=int(group_id))
    items = item_q.order_by(VoorraadItem.naam).all()

    mov_q = VoorraadMutatie.query.join(VoorraadItem)
    if q_text:
        mov_q = mov_q.filter(or_(
            VoorraadItem.naam.ilike(f'%{q_text}%'),
            VoorraadItem.supplier_part_number.ilike(f'%{q_text}%'),
            VoorraadMutatie.opmerking.ilike(f'%{q_text}%')
        ))
    if spn: mov_q = mov_q.filter(VoorraadItem.supplier_part_number.ilike(f'%{spn}%'))
    if locatie: mov_q = mov_q.filter(VoorraadItem.locatie.ilike(f'%{locatie}%'))
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


# ── LABELS ──────────────────────────────────────────────────

@bp.route('/labels')
@login_required
def warehouse_labels():
    ids = request.args.get('ids', '')
    if ids:
        item_ids = [int(x) for x in ids.split(',') if x.strip()]
        items = VoorraadItem.query.filter(VoorraadItem.id.in_(item_ids)).all()
    else:
        items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    return render_template('warehouse_labels.html', items=items)
