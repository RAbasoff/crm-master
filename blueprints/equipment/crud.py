"""
Equipment maintenance CRUD routes, MRO photos and part orders.
"""
import os
from datetime import datetime

from flask import (render_template, request, redirect, url_for, flash,
                   current_app)
from flask_login import login_required
from flask_babel import gettext as _

from models import (db, EquipmentMaintenance, EquipmentPart, EquipmentComponent,
                    EquipmentPartOrder, EquipmentMROPhoto, VoorraadItem,
                    VoorraadMutatie, MuleDevice)
from utils import (resolve_user_id, log_audit, role_required, safe_commit, adjust_stock,
                   safe_date, safe_float, safe_int, sanitize_like,
                   save_uploaded_file)

from . import bp
from . import (gen_eq_number, gen_eq_order_number, mule_machines,
               mule_machines_json, mule_devices_json, split_mule_name,
               _get_or_create_device)


@bp.route('/equipment')
@login_required
@role_required('admin', 'director', 'technician')
def equipment_list():
    q = EquipmentMaintenance.query.filter(EquipmentMaintenance.record_type.isnot('repair'))
    serial = request.args.get('serial', '').strip()
    if serial:
        q = q.filter(EquipmentMaintenance.serial.ilike(f'%{sanitize_like(serial)}%'))
    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(EquipmentMaintenance.date.desc()).paginate(page=page, per_page=25, error_out=False)
    records = pagination.items
    # раздел «Ремонты» — та же информация, что и в обслуживании
    repairs_q = EquipmentMaintenance.query.filter(EquipmentMaintenance.record_type == 'repair')
    if serial:
        repairs_q = repairs_q.filter(EquipmentMaintenance.serial.ilike(f'%{sanitize_like(serial)}%'))
    repairs_count = repairs_q.count()
    repairs = repairs_q.order_by(EquipmentMaintenance.date.desc()).limit(25).all()
    orders = EquipmentPartOrder.query.order_by(EquipmentPartOrder.created_at.desc()).limit(20).all()
    machines = mule_machines()
    # Find warehouse items linked to mule maintenance parts that are low or out of stock
    used_wh_ids = db.session.query(EquipmentPart.warehouse_item_id).filter(
        EquipmentPart.warehouse_item_id.isnot(None)).distinct().all()
    used_wh_ids = [w[0] for w in used_wh_ids]
    low_stock_parts = VoorraadItem.query.filter(
        VoorraadItem.id.in_(used_wh_ids),
        VoorraadItem.hoeveelheid <= VoorraadItem.minimum
    ).order_by(VoorraadItem.naam).all() if used_wh_ids else []
    return render_template('equipment.html', pagination=pagination, records=records, orders=orders, machines=machines, serial=serial, low_stock_parts=low_stock_parts, repairs=repairs, repairs_count=repairs_count)


@bp.route('/equipment/<int:eq_id>')
@login_required
@role_required('admin', 'director', 'technician')
def equipment_detail(eq_id):
    eq = EquipmentMaintenance.query.get_or_404(eq_id)
    return render_template('equipment_detail.html', eq=eq)


@bp.route('/equipment/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def equipment_new():
    if request.method == 'POST':
        try:
            date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid date format'), 'error')
            return redirect(url_for('equipment.equipment_new'))
        rec_type = request.form.get('record_type') or ('repair' if request.args.get('type') == 'repair' else 'maintenance')
        # Если в имени смешаны имя и модель ('V9 II COM 1799') — модель
        # автоматически уходит в своё поле (пока поле модели пустое).
        _nm = (request.form.get('name') or '').strip()
        _md = (request.form.get('model_name') or '').strip()
        if not _md and _nm:
            _sn, _sm = split_mule_name(_nm)
            if _sm:
                _nm, _md = _sn, _sm
        # Номер генерируем через retry_unique_number: при одновременном
        # сохранении двух записей раньше падал IntegrityError и форма терялась.
        from utils import retry_unique_number

        def _make_eq():
            return EquipmentMaintenance(
                number=gen_eq_number(rec_type, date),
                record_type=rec_type,
                name=_nm,
                serial=request.form.get('serial', ''),
                model_name=_md,
                machine_id=safe_int(request.form.get('machine_id')) or None,
                date=date,
                reason=request.form['reason'],
                next_date=(d := safe_date(request.form.get('next_date'))) and d.date() or None,
                periodicity=request.form.get('periodicity', ''),
                notes=request.form.get('notes', ''),
                created_by=resolve_user_id()
            )

        eq = retry_unique_number(_make_eq, number_attr='number')
        # реестр муле: данные вводятся один раз, дальше только выбираются.
        # Ремонты реестр НЕ пополняют (иначе в чек-листе ТО появляются мусорные
        # устройства с вечным «не сделано»).
        if rec_type != 'repair':
            _dev = _get_or_create_device(_nm, _md, eq.serial, eq.machine_id)
            if _dev is not None:
                eq.device_id = _dev.id
        db.session.add(eq)
        db.session.flush()
        # Add parts + deduct from warehouse
        wh_ids = request.form.getlist('part_warehouse_id')
        for i, pname in enumerate(request.form.getlist('part_name')):
            if pname.strip():
                wh_id = safe_int(wh_ids[i]) if i < len(wh_ids) and wh_ids[i] else None
                qty = safe_float(request.form.getlist('part_qty')[i], 1) if i < len(request.form.getlist('part_qty')) and request.form.getlist('part_qty')[i] else 1
                db.session.add(EquipmentPart(
                    equipment_id=eq.id,
                    warehouse_item_id=wh_id,
                    name=pname.strip(),
                    number=request.form.getlist('part_number')[i] if i < len(request.form.getlist('part_number')) else '',
                    quantity=qty
                ))
                # Deduct from warehouse (with stock check)
                if wh_id:
                    wi = VoorraadItem.query.get(wh_id)
                    if wi:
                        if (wi.hoeveelheid or 0) < qty:
                            flash(_('Not enough stock. Available: %(qty)s %(unit)s', qty=wi.hoeveelheid, unit=wi.eenheid), 'error')
                            return redirect(url_for('equipment.equipment_detail', eq_id=eq.id))
                        adjust_stock(wh_id, -qty, mt='uitgaand',
                                     opmerking=f'Mule Maintenance {eq.number}: {pname.strip()}',
                                     user_id=resolve_user_id())
        # Add components
        comp_types = request.form.getlist('comp_type')
        comp_models = request.form.getlist('comp_model')
        comp_sizes = request.form.getlist('comp_size')
        comp_lengths = request.form.getlist('comp_length')
        comp_qtys = request.form.getlist('comp_qty')
        for i, ctype in enumerate(comp_types):
            if ctype:
                db.session.add(EquipmentComponent(
                    equipment_id=eq.id,
                    component_type=ctype,
                    model=comp_models[i] if i < len(comp_models) else '',
                    size=comp_sizes[i] if i < len(comp_sizes) else '',
                    length=comp_lengths[i] if i < len(comp_lengths) else '',
                    quantity=float(comp_qtys[i]) if i < len(comp_qtys) and comp_qtys[i] else 1
                ))
        # Add photos
        for photo in request.files.getlist('photos'):
            if photo and photo.filename:
                fn = save_uploaded_file(photo, prefix=f"mro_{eq.id}_")
                if fn:
                    db.session.add(EquipmentMROPhoto(equipment_id=eq.id, filename=fn))
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('equipment.equipment_list'))
        log_audit('create', 'equipment', eq.id, f'{eq.number} — {eq.name}')
        flash(_('Equipment maintenance recorded'), 'success')
        return redirect(url_for('equipment.equipment_list'))
    machines = mule_machines()
    warehouse_items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    mule_devices = MuleDevice.query.order_by(MuleDevice.name).all()
    # prefill из реестра: /equipment/new?device_id=N (ТО за месяц из чек-листа)
    prefill = None
    _did = safe_int(request.args.get('device_id'))
    if _did:
        for d in mule_devices:
            if d.id == _did:
                prefill = d
                break
    return render_template('equipment_form.html', eq=None, machines=machines, machines_json=mule_machines_json(machines), warehouse_items=warehouse_items, mule_devices=mule_devices, mule_devices_json=mule_devices_json(mule_devices), prefill_device=prefill, record_type=request.args.get('type') or request.form.get('record_type') or 'maintenance')


@bp.route('/equipment/<int:eq_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def equipment_edit(eq_id):
    eq = EquipmentMaintenance.query.get_or_404(eq_id)
    if request.method == 'POST':
        try:
            eq.date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid date format'), 'error')
            return redirect(url_for('equipment.equipment_edit', eq_id=eq.id))
        _nm = (request.form.get('name') or '').strip()
        _md = (request.form.get('model_name') or '').strip()
        if not _md and _nm:
            _sn, _sm = split_mule_name(_nm)
            if _sm:
                _nm, _md = _sn, _sm
        eq.name = _nm
        eq.serial = request.form.get('serial', '')
        eq.model_name = _md
        eq.machine_id = safe_int(request.form.get('machine_id')) or None
        if (eq.record_type or 'maintenance') != 'repair':
            _dev = _get_or_create_device(_nm, _md, eq.serial, eq.machine_id)
            eq.device_id = _dev.id if _dev is not None else None
        eq.reason = request.form['reason']
        eq.next_date = (d := safe_date(request.form.get('next_date'))) and d.date() or None
        eq.periodicity = request.form.get('periodicity', '')
        eq.notes = request.form.get('notes', '')
        EquipmentPart.query.filter_by(equipment_id=eq.id).delete()
        wh_ids = request.form.getlist('part_warehouse_id')
        for i, pname in enumerate(request.form.getlist('part_name')):
            if pname.strip():
                wh_id = safe_int(wh_ids[i]) if i < len(wh_ids) and wh_ids[i] else None
                qty = safe_float(request.form.getlist('part_qty')[i], 1) if i < len(request.form.getlist('part_qty')) and request.form.getlist('part_qty')[i] else 1
                db.session.add(EquipmentPart(
                    equipment_id=eq.id,
                    warehouse_item_id=wh_id,
                    name=pname.strip(),
                    number=request.form.getlist('part_number')[i] if i < len(request.form.getlist('part_number')) else '',
                    quantity=qty
                ))
                if wh_id:
                    wi = VoorraadItem.query.get(wh_id)
                    if wi:
                        adjust_stock(wh_id, -qty, mt='uitgaand',
                                     opmerking=f'Mule Maintenance {eq.number}: {pname.strip()}',
                                     user_id=resolve_user_id())
        # Update components
        EquipmentComponent.query.filter_by(equipment_id=eq.id).delete()
        comp_types = request.form.getlist('comp_type')
        comp_models = request.form.getlist('comp_model')
        comp_sizes = request.form.getlist('comp_size')
        comp_lengths = request.form.getlist('comp_length')
        comp_qtys = request.form.getlist('comp_qty')
        for i, ctype in enumerate(comp_types):
            if ctype:
                db.session.add(EquipmentComponent(
                    equipment_id=eq.id,
                    component_type=ctype,
                    model=comp_models[i] if i < len(comp_models) else '',
                    size=comp_sizes[i] if i < len(comp_sizes) else '',
                    length=comp_lengths[i] if i < len(comp_lengths) else '',
                    quantity=float(comp_qtys[i]) if i < len(comp_qtys) and comp_qtys[i] else 1
                ))
        # Add new photos (existing ones are kept)
        for photo in request.files.getlist('photos'):
            if photo and photo.filename:
                fn = save_uploaded_file(photo, prefix=f"mro_{eq.id}_")
                if fn:
                    db.session.add(EquipmentMROPhoto(equipment_id=eq.id, filename=fn))
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('equipment.equipment_list'))
        flash(_('Equipment maintenance updated'), 'success')
        return redirect(url_for('equipment.equipment_list'))
    machines = mule_machines()
    warehouse_items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    mule_devices = MuleDevice.query.order_by(MuleDevice.name).all()
    return render_template('equipment_form.html', eq=eq, machines=machines, machines_json=mule_machines_json(machines), warehouse_items=warehouse_items, mule_devices=mule_devices, mule_devices_json=mule_devices_json(mule_devices), prefill_device=eq.device, record_type=eq.record_type or 'maintenance')


@bp.route('/equipment/<int:eq_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def equipment_delete(eq_id):
    eq = EquipmentMaintenance.query.get_or_404(eq_id)
    db.session.delete(eq)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('equipment.equipment_list'))
    flash(_('Equipment maintenance deleted'), 'success')
    return redirect(url_for('equipment.equipment_list'))


@bp.route('/equipment/photo/<int:photo_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def equipment_photo_delete(photo_id):
    photo = EquipmentMROPhoto.query.get_or_404(photo_id)
    eq_id = photo.equipment_id
    try:
        os.remove(os.path.join(current_app.config['UPLOAD_FOLDER'], photo.filename))
    except OSError:
        pass
    db.session.delete(photo)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        flash(_('Photo deleted'), 'success')
    return redirect(url_for('equipment.equipment_edit', eq_id=eq_id))


@bp.route('/equipment/order', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def equipment_order():
    o = EquipmentPartOrder(
        order_number=gen_eq_order_number(),
        equipment_name=request.form.get('equipment_name', ''),
        part_name=request.form['part_name'],
        part_number=request.form.get('part_number', ''),
        quantity=safe_float(request.form.get('quantity'), 1),
        supplier=request.form.get('supplier', ''),
        urgency=request.form.get('urgency', 'normal'),
        notes=request.form.get('notes', ''),
        created_by=resolve_user_id()
    )
    db.session.add(o)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('equipment.equipment_list'))
    flash(_('Part order created'), 'success')
    return redirect(url_for('equipment.equipment_list'))


@bp.route('/equipment/order/<int:order_id>/status', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def equipment_order_status(order_id):
    o = EquipmentPartOrder.query.get_or_404(order_id)
    new_status = request.form.get('status')
    if new_status in ('pending', 'ordered', 'delivered', 'cancelled'):
        o.status = new_status
        if new_status == 'delivered':
            o.delivered_at = datetime.utcnow()
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('equipment.equipment_list'))
    return redirect(url_for('equipment.equipment_list'))
