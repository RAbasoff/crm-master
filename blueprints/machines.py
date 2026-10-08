"""
Machines blueprint — machine CRUD, parts, consumables, documents, floor plan, reports
"""
from datetime import datetime, timedelta
from flask import (Blueprint, request, redirect, url_for, flash, render_template,
                   jsonify, send_file, current_app)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json
import qrcode

from models import (db, Machine, MachinePart, PartMaintenanceLog, MachineDocument,
                    MachineSparePart, MachineConsumable, MaintenanceRecord, MaintenancePhoto,
                    MaintenancePlan, FactorySection, User, Contractor, Verantwoordelijke, FaultReport,
                    TechnicalWorkOrder, PurchaseRequest, MachinePassword,
                    VoorraadItem, VoorraadMutatie, now_local)
from utils import (role_required, log_audit, save_uploaded_file, safe_commit,
                   safe_int, safe_float, safe_date, sanitize_like,
                   find_pdf_font, ensure_fpdf)

bp = Blueprint('machines', __name__, url_prefix='/machines')


def _optf(value):
    """Optional float: empty/invalid -> None (0 stays 0)."""
    if value is None or str(value).strip() == '':
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


@bp.route('/')
@login_required
def machines_list():
    page = request.args.get('page', 1, type=int)
    if current_user.has_role('admin', 'director'):
        pagination = Machine.query.order_by(Machine.id).paginate(page=page, per_page=25, error_out=False)
    elif current_user.has_role('technician'):
        pagination = Machine.query.order_by(Machine.id).paginate(page=page, per_page=25, error_out=False)
    else:
        machines = current_user.assigned_machines
        return render_template('machines.html', machines=machines, pagination=None)
    return render_template('machines.html', machines=pagination.items, pagination=pagination)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def machine_new():
    if request.method == 'POST':
        m = Machine(
            name=request.form['name'],
            description=request.form.get('description', ''),
            serial_number=request.form.get('serial_number', ''),
            machine_type=request.form.get('machine_type', ''),
            manufacturer=request.form.get('manufacturer', ''),
            year_of_manufacture=safe_int(request.form.get('year_of_manufacture')) or None,
            installation_location=request.form.get('installation_location', ''),
            contractor_id=safe_int(request.form.get('contractor_id')) or None,
            responsible_user_id=safe_int(request.form.get('responsible_user_id')) or None,
            responsible_person_id=safe_int(request.form.get('responsible_person_id')) or None,
            section_id=safe_int(request.form.get('section_id')) or None,
            marker_size=safe_int(request.form.get('marker_size'), 45),
            marker_shape=request.form.get('marker_shape', 'circle'),
            floor_x=safe_float(request.form.get('floor_x'), 50),
            floor_y=safe_float(request.form.get('floor_y'), 50),
            dim_length_mm=_optf(request.form.get('dim_length_mm')),
            dim_width_mm=_optf(request.form.get('dim_width_mm')),
            dim_height_mm=_optf(request.form.get('dim_height_mm')),
            power_voltage=request.form.get('power_voltage', '').strip() or None,
            power_kw=_optf(request.form.get('power_kw')),
            air_usage_m3h=_optf(request.form.get('air_usage_m3h')),
            water_usage_lmin=_optf(request.form.get('water_usage_lmin')),
            gas_usage_m3h=_optf(request.form.get('gas_usage_m3h')),
            gas_natural=bool(request.form.get('gas_natural')),
            gas_nitrogen=bool(request.form.get('gas_nitrogen')),
            gas_co2=bool(request.form.get('gas_co2')),
            nitrogen_usage_m3h=_optf(request.form.get('nitrogen_usage_m3h')),
            co2_usage_m3h=_optf(request.form.get('co2_usage_m3h')),
        )
        db.session.add(m)
        db.session.flush()
        if 'photo' in request.files and request.files['photo'].filename:
            filename = save_uploaded_file(request.files['photo'], prefix=f"machine_{m.id}_")
            if filename:
                m.photo = filename
        for uid in request.form.getlist('assigned_users'):
            u = User.query.get(int(uid))
            if u:
                m.assigned_users.append(u)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('machines.machine_new'))
        flash(_('Machine created'), 'success')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    users = User.query.filter(User.is_active_user == True).all()
    sections = FactorySection.query.all()
    contractors = Contractor.query.filter_by(is_active=True).order_by(Contractor.company_name).all()
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    return render_template('machine_form.html', machine=None, users=users, sections=sections,
                           contractors=contractors, verantwoordelijken=verantwoordelijken)


@bp.route('/<int:machine_id>')
@login_required
def machine_detail(machine_id):
    m = Machine.query.get_or_404(machine_id)
    faults = FaultReport.query.filter_by(machine_id=m.id).order_by(FaultReport.created_at.desc()).all()
    maintenance = MaintenanceRecord.query.filter_by(machine_id=m.id).order_by(MaintenanceRecord.date_performed.desc()).all()
    warehouse_items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    timeline = _build_machine_timeline(m.id, limit=80)
    return render_template('machine_detail.html', machine=m, faults=faults, maintenance=maintenance,
                           warehouse_items=warehouse_items, timeline=timeline)


def _build_machine_timeline(machine_id, limit=100):
    """Unified event feed for one machine: faults, TO, plans, TWO, parts, purchases."""
    events = []

    for f in FaultReport.query.filter_by(machine_id=machine_id).all():
        ts = f.created_at
        events.append({
            'ts': ts, 'kind': 'fault', 'icon': '⚠️', 'color': '#e74c3c',
            'title': f.title or f'#{f.id}',
            'status': f.status, 'priority': getattr(f, 'priority', None),
            'url': f'/faults/{f.id}',
            'detail': (f.description or '')[:180],
        })

    for p in MaintenancePlan.query.filter_by(machine_id=machine_id).all():
        ts = p.planned_start or p.created_at
        events.append({
            'ts': ts, 'kind': 'plan', 'icon': '📋', 'color': '#3498db',
            'title': p.title or f'Plan #{p.id}',
            'status': p.status,
            'url': f'/maintenance-plans/{p.id}',
            'detail': (p.description or '')[:180],
        })

    for r in MaintenanceRecord.query.filter_by(machine_id=machine_id).all():
        ts = r.date_performed or r.created_at
        events.append({
            'ts': ts, 'kind': 'maintenance', 'icon': '🔧', 'color': '#27ae60',
            'title': r.description or f'TO #{r.id}',
            'status': r.maintenance_type or 'done',
            'url': None,
            'detail': (r.notes or '')[:180],
        })

    for t in TechnicalWorkOrder.query.filter_by(machine_id=machine_id).all():
        ts = t.planned_date or t.created_at
        events.append({
            'ts': ts, 'kind': 'two', 'icon': '📝', 'color': '#9b59b6',
            'title': f'{getattr(t, "number", "") or "TWO"} — {(t.description or "")[:60]}',
            'status': t.status,
            'url': f'/two/{t.id}',
            'detail': (t.description or '')[:180],
        })

    for pr in PurchaseRequest.query.filter_by(machine_id=machine_id).all():
        ts = pr.created_at
        events.append({
            'ts': ts, 'kind': 'purchase', 'icon': '🛒', 'color': '#e67e22',
            'title': pr.part_name or f'PR #{pr.id}',
            'status': pr.status,
            'url': f'/purchase-requests/{pr.id}',
            'detail': (getattr(pr, 'reason', '') or getattr(pr, 'notes', '') or '')[:180],
        })

    for mp in MachinePart.query.filter_by(machine_id=machine_id).all():
        ts = mp.installed_date or mp.created_at
        if not ts:
            continue
        events.append({
            'ts': ts, 'kind': 'part', 'icon': '⚙️', 'color': '#16a085',
            'title': mp.name or f'Part #{mp.id}',
            'status': mp.status,
            'url': f'/machines/{machine_id}/parts',
            'detail': (mp.category or '')[:180],
        })

    # normalize ts to datetime for sorting
    def _ts(e):
        v = e['ts']
        if v is None:
            return datetime.min
        if hasattr(v, 'year') and not hasattr(v, 'hour'):
            return datetime(v.year, v.month, v.day)
        return v

    events.sort(key=_ts, reverse=True)

    # serialize
    out = []
    for e in events[:limit]:
        ts = e['ts']
        if ts is None:
            date_s = None
        elif hasattr(ts, 'strftime'):
            date_s = ts.strftime('%Y-%m-%d %H:%M') if hasattr(ts, 'hour') else ts.strftime('%Y-%m-%d')
        else:
            date_s = str(ts)
        out.append({**e, 'ts': date_s, 'ts_raw': str(_ts(e))})
    return out


@bp.route('/<int:machine_id>/timeline')
@login_required
def machine_timeline(machine_id):
    m = Machine.query.get_or_404(machine_id)
    limit = min(max(request.args.get('limit', 100, type=int), 1), 500)
    events = _build_machine_timeline(m.id, limit=limit)
    if request.args.get('format') == 'html':
        return render_template('machine_timeline.html', machine=m, timeline=events)
    return jsonify({'machine_id': m.id, 'machine': m.name, 'count': len(events), 'events': events})


@bp.route('/<int:machine_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def machine_edit(machine_id):
    m = Machine.query.get_or_404(machine_id)
    if request.method == 'POST':
        # Verify lock on save
        from utils import acquire_lock, release_lock
        ok, lock_info = acquire_lock('machine', machine_id, current_user.id, current_user.username)
        if not ok:
            flash(_('Record is being edited by %(user)s. Try again later.', user=lock_info.get('user_name', '?')), 'error')
            return redirect(url_for('machines.machine_detail', machine_id=m.id))
        m.name = request.form['name']
        m.description = request.form.get('description', '')
        m.serial_number = request.form.get('serial_number', '')
        m.machine_type = request.form.get('machine_type', '')
        m.manufacturer = request.form.get('manufacturer', '')
        m.year_of_manufacture = safe_int(request.form.get('year_of_manufacture')) or None
        m.installation_location = request.form.get('installation_location', '')
        m.contractor_id = safe_int(request.form.get('contractor_id')) or None
        m.responsible_user_id = safe_int(request.form.get('responsible_user_id')) or None
        m.responsible_person_id = safe_int(request.form.get('responsible_person_id')) or None
        m.section_id = safe_int(request.form.get('section_id')) or None
        m.marker_size = safe_int(request.form.get('marker_size'), m.marker_size or 45)
        m.marker_shape = request.form.get('marker_shape', m.marker_shape or 'circle')
        m.status = request.form.get('status', m.status)
        m.floor_x = safe_float(request.form.get('floor_x'), m.floor_x)
        m.floor_y = safe_float(request.form.get('floor_y'), m.floor_y)
        m.dim_length_mm = _optf(request.form.get('dim_length_mm'))
        m.dim_width_mm = _optf(request.form.get('dim_width_mm'))
        m.dim_height_mm = _optf(request.form.get('dim_height_mm'))
        m.power_voltage = request.form.get('power_voltage', '').strip() or None
        m.power_kw = _optf(request.form.get('power_kw'))
        m.air_usage_m3h = _optf(request.form.get('air_usage_m3h'))
        m.water_usage_lmin = _optf(request.form.get('water_usage_lmin'))
        m.gas_usage_m3h = _optf(request.form.get('gas_usage_m3h'))
        m.gas_natural = bool(request.form.get('gas_natural'))
        m.gas_nitrogen = bool(request.form.get('gas_nitrogen'))
        m.gas_co2 = bool(request.form.get('gas_co2'))
        m.nitrogen_usage_m3h = _optf(request.form.get('nitrogen_usage_m3h'))
        m.co2_usage_m3h = _optf(request.form.get('co2_usage_m3h'))
        if 'photo' in request.files and request.files['photo'].filename:
            filename = save_uploaded_file(request.files['photo'], prefix=f"machine_{m.id}_")
            if filename:
                if m.photo:
                    old_path = os.path.join(current_app.config['UPLOAD_FOLDER'], m.photo)
                    try:
                        if os.path.isfile(old_path):
                            os.remove(old_path)
                    except OSError:
                        pass
                m.photo = filename
        m.assigned_users = []
        for uid in request.form.getlist('assigned_users'):
            u = User.query.get(int(uid))
            if u:
                m.assigned_users.append(u)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('machines.machine_edit', machine_id=m.id))
        release_lock('machine', machine_id, current_user.id)
        flash(_('Machine updated'), 'success')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    # GET: acquire lock
    from utils import acquire_lock, release_lock
    ok, lock_info = acquire_lock('machine', machine_id, current_user.id, current_user.username)
    if not ok:
        flash(_('⚠️ This record is being edited by %(user)s (since %(time)s). You cannot edit it now.', user=lock_info.get('user_name', '?'), time=lock_info.get('locked_at', '?')), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    users = User.query.filter(User.is_active_user == True).all()
    sections = FactorySection.query.all()
    contractors = Contractor.query.filter_by(is_active=True).order_by(Contractor.company_name).all()
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    return render_template('machine_form.html', machine=m, users=users, sections=sections,
                           contractors=contractors, verantwoordelijken=verantwoordelijken)


@bp.route('/<int:machine_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def machine_delete(machine_id):
    m = Machine.query.get_or_404(machine_id)
    name = m.name
    open_faults = FaultReport.query.filter_by(machine_id=m.id).filter(
        FaultReport.status.in_(['open', 'accepted', 'in_progress'])
    ).count()
    if open_faults > 0:
        flash(_('Cannot delete machine with {} open fault reports').format(open_faults), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    FaultReport.query.filter_by(machine_id=m.id).update({'machine_id': None})
    MaintenanceRecord.query.filter_by(machine_id=m.id).delete()
    MachinePart.query.filter_by(machine_id=m.id).delete()
    MachineConsumable.query.filter_by(machine_id=m.id).delete()
    MachineDocument.query.filter_by(machine_id=m.id).delete()
    MachineSparePart.query.filter_by(machine_id=m.id).delete()
    m.assigned_users = []
    db.session.delete(m)
    if not safe_commit():
        flash(_('Delete failed. Please try again.'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    log_audit('delete', 'machine', machine_id, name)
    flash(_('Machine deleted') + f': {name}', 'success')
    return redirect(url_for('machines.machines_list'))


@bp.route('/<int:machine_id>/parts')
@login_required
def machine_parts(machine_id):
    m = Machine.query.get_or_404(machine_id)
    parts = MachinePart.query.filter_by(machine_id=m.id).all()
    return render_template('machine_parts.html', machine=m, parts=parts)


@bp.route('/<int:machine_id>/parts/add', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def machine_add_part(machine_id):
    m = Machine.query.get_or_404(machine_id)
    p = MachinePart(
        machine_id=m.id,
        name=request.form['name'],
        description=request.form.get('description', ''),
        category=request.form.get('category', ''),
        quantity=safe_float(request.form.get('quantity'), 1),
        min_quantity=safe_float(request.form.get('min_quantity'), 0),
        cost=safe_float(request.form.get('cost'), 0),
        supplier=request.form.get('supplier', ''),
        supplier_part_number=request.form.get('supplier_part_number', ''),
        location=request.form.get('location', ''),
        replacement_interval=safe_int(request.form.get('replacement_interval')) or None,
        last_replacement=safe_date(request.form.get('last_replacement')),
        next_replacement=safe_date(request.form.get('next_replacement'))
    )
    db.session.add(p)
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
    else:
        flash(_('Part added'), 'success')
    return redirect(url_for('machines.machine_parts', machine_id=m.id))


@bp.route('/<int:machine_id>/parts/<int:part_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def machine_edit_part(machine_id, part_id):
    m = Machine.query.get_or_404(machine_id)
    p = MachinePart.query.get_or_404(part_id)
    if request.method == 'POST':
        p.name = request.form['name']
        p.description = request.form.get('description', '')
        p.category = request.form.get('category', '')
        p.quantity = safe_float(request.form.get('quantity'), 1)
        p.min_quantity = safe_float(request.form.get('min_quantity'), 0)
        p.cost = safe_float(request.form.get('cost'), 0)
        p.supplier = request.form.get('supplier', '')
        p.supplier_part_number = request.form.get('supplier_part_number', '')
        p.location = request.form.get('location', '')
        p.replacement_interval = safe_int(request.form.get('replacement_interval')) or None
        p.last_replacement = safe_date(request.form.get('last_replacement'))
        p.next_replacement = safe_date(request.form.get('next_replacement'))
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
        else:
            flash(_('Part updated'), 'success')
        return redirect(url_for('machines.machine_parts', machine_id=m.id))
    return render_template('machine_part_form.html', machine=m, part=p)


@bp.route('/<int:machine_id>/parts/<int:part_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def machine_delete_part(machine_id, part_id):
    m = Machine.query.get_or_404(machine_id)
    p = MachinePart.query.get_or_404(part_id)
    db.session.delete(p)
    if not safe_commit():
        flash(_('Delete failed. Please try again.'), 'error')
    else:
        flash(_('Part deleted'), 'success')
    return redirect(url_for('machines.machine_parts', machine_id=m.id))


@bp.route('/<int:machine_id>/upload-document', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def machine_upload_document(machine_id):
    m = Machine.query.get_or_404(machine_id)
    if 'document' not in request.files or not request.files['document'].filename:
        flash(_('No file selected'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    file = request.files['document']
    filename = save_uploaded_file(file, prefix=f"doc_{m.id}_")
    if not filename:
        flash(_('File type not allowed'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    doc = MachineDocument(
        machine_id=m.id,
        doc_type=request.form.get('doc_type', 'other'),
        title=request.form.get('title', file.filename),
        filename=filename,
        uploaded_by=current_user.id
    )
    db.session.add(doc)
    if not safe_commit():
        flash(_('Upload failed. Please try again.'), 'error')
    else:
        flash(_('Document uploaded'), 'success')
    return redirect(url_for('machines.machine_detail', machine_id=m.id))


@bp.route('/<int:machine_id>/add-maintenance', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def machine_add_maintenance(machine_id):
    m = Machine.query.get_or_404(machine_id)
    if request.method == 'POST':
        try:
            date_performed = datetime.strptime(request.form['date_performed'], '%Y-%m-%d')
        except (ValueError, KeyError):
            flash(_('Invalid date format'), 'error')
            return redirect(url_for('machines.machine_add_maintenance', machine_id=m.id))
        mr = MaintenanceRecord(
            machine_id=m.id,
            maintenance_type=request.form['maintenance_type'],
            description=request.form['description'],
            performed_by=current_user.id,
            date_performed=date_performed,
            next_maintenance=safe_date(request.form.get('next_maintenance')),
            cost=safe_float(request.form.get('cost'), 0),
            parts_used=request.form.get('parts_used', '[]'),
            notes=request.form.get('notes', '')
        )
        db.session.add(mr)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('machines.machine_add_maintenance', machine_id=m.id))
        if 'photos' in request.files:
            for photo in request.files.getlist('photos'):
                if photo.filename:
                    fn = save_uploaded_file(photo, prefix=f"maint_{mr.id}_")
                    if fn:
                        mp = MaintenancePhoto(maintenance_id=mr.id, filename=fn)
                        db.session.add(mp)
            safe_commit()
        flash(_('Maintenance record added'), 'success')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    return render_template('maintenance_form.html', machine=m, now=datetime.utcnow())


@bp.route('/<int:machine_id>/transfer-act')
@login_required
def machine_transfer_act(machine_id):
    """Printable transfer act of material assets (ТМЦ) for a machine."""
    m = Machine.query.get_or_404(machine_id)
    users = User.query.filter(User.is_active_user == True).order_by(User.username).all()
    persons = Verantwoordelijke.query.filter_by(is_active=True).order_by(Verantwoordelijke.naam).all()
    items_total = 1 + len(m.parts) + len([sp for sp in m.spare_parts if sp.warehouse_item]) \
                  + len([mc for mc in m.consumables if mc.warehouse_item])
    return render_template('machine_transfer_act.html',
                           machine=m, users=users, persons=persons,
                           items_total=items_total, now=datetime.utcnow())


@bp.route('/report', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def machines_report():
    machine_ids = request.args.getlist('machine_ids') or request.form.getlist('machine_ids')
    date_from = request.args.get('date_from') or request.form.get('date_from')
    date_to = request.args.get('date_to') or request.form.get('date_to')
    if not date_from:
        date_from = (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d')
    if not date_to:
        date_to = datetime.utcnow().strftime('%Y-%m-%d')
    d_from = datetime.strptime(date_from, '%Y-%m-%d')
    d_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
    if machine_ids:
        machines = Machine.query.filter(Machine.id.in_([int(i) for i in machine_ids])).all()
    else:
        machines = Machine.query.all()
    machine_ids_list = [m.id for m in machines]
    all_faults = FaultReport.query.filter(
        FaultReport.machine_id.in_(machine_ids_list),
        FaultReport.created_at >= d_from,
        FaultReport.created_at < d_to
    ).all()
    all_maintenance = MaintenanceRecord.query.filter(
        MaintenanceRecord.machine_id.in_(machine_ids_list),
        MaintenanceRecord.date_performed >= d_from,
        MaintenanceRecord.date_performed < d_to
    ).all()
    faults_by_machine = {}
    for f in all_faults:
        faults_by_machine.setdefault(f.machine_id, []).append(f)
    maint_by_machine = {}
    for mr in all_maintenance:
        maint_by_machine.setdefault(mr.machine_id, []).append(mr)
    report_data = []
    for m in machines:
        faults = faults_by_machine.get(m.id, [])
        maintenance = maint_by_machine.get(m.id, [])
        report_data.append({
            'machine': m,
            'faults_total': len(faults),
            'faults_open': len([f for f in faults if f.status in ['open', 'accepted', 'in_progress']]),
            'faults_resolved': len([f for f in faults if f.status in ['resolved', 'closed']]),
            'maintenance_total': len(maintenance),
            'maintenance_cost': sum(rec.cost for rec in maintenance),
            'fault_cost': sum(sum(wr.time_spent_hours or 0 for wr in f.work_report) * 50 for f in faults if f.work_report)
        })
    return render_template('machines_report.html', report_data=report_data,
                           date_from=date_from, date_to=date_to, all_machines=Machine.query.all(),
                           selected_machines=machine_ids)


MACHINE_EXPORT_FIELDS = {
    'name':              lambda m: m.name,
    'machine_type':      lambda m: m.machine_type or '',
    'serial_number':     lambda m: m.serial_number or '',
    'year_of_manufacture': lambda m: m.year_of_manufacture or '',
    'manufacturer':      lambda m: m.manufacturer or '',
    'status':            lambda m: m.status or 'active',
    'installation_location': lambda m: m.installation_location or '',
    'section':           lambda m: m.section.name if m.section else '',
    'responsible':       lambda m: (m.responsible_user.display_name or m.responsible_user.username) if m.responsible_user else (m.responsible_person.naam if m.responsible_person else ''),
    'contractor':        lambda m: m.contractor_rel.company_name if m.contractor_rel else '',
}

MACHINE_EXPORT_FIELD_LABELS = {
    'name': 'Name', 'machine_type': 'Type', 'serial_number': 'Serial Number',
    'year_of_manufacture': 'Year of Manufacture', 'manufacturer': 'Manufacturer',
    'status': 'Status', 'installation_location': 'Location', 'section': 'Section',
    'responsible': 'Responsible', 'contractor': 'Contractor',
}

# All fields in original export order
MACHINE_EXPORT_ALL_FIELDS = ['name', 'machine_type', 'serial_number', 'manufacturer',
                              'year_of_manufacture', 'installation_location', 'section',
                              'responsible', 'contractor', 'status']


@bp.route('/export/<format_type>')
@login_required
@role_required('admin', 'director')
def machines_export(format_type):
    ids_raw = request.args.get('ids', '')
    id_list = [safe_int(x) for x in ids_raw.split(',') if x.strip()]
    if id_list:
        machines = Machine.query.filter(Machine.id.in_(id_list)).order_by(Machine.id).all()
    else:
        machines = Machine.query.order_by(Machine.id).all()

    fields_raw = request.args.get('fields', '')
    field_keys = [f.strip() for f in fields_raw.split(',') if f.strip() in MACHINE_EXPORT_FIELDS] if fields_raw else MACHINE_EXPORT_ALL_FIELDS
    if not field_keys:
        field_keys = MACHINE_EXPORT_ALL_FIELDS

    status_labels = {
        'active': _('Active'), 'maintenance': _('Maintenance'), 'broken': _('Broken'),
        'offline': _('Offline'), 'retired': _('Retired'), 'disposed': _('Disposed'),
    }

    def get_val(m, key):
        if key == 'status':
            return status_labels.get(m.status or 'active', m.status or 'active')
        return MACHINE_EXPORT_FIELDS[key](m)

    header_map = {k: _(MACHINE_EXPORT_FIELD_LABELS[k]) for k in MACHINE_EXPORT_FIELDS}
    headers = [header_map[k] for k in field_keys]

    rows = []
    for m in machines:
        rows.append([str(get_val(m, k)) for k in field_keys])
    if format_type == 'xlsx':
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = 'Machines'
            ws.append(headers)
            for row in rows:
                ws.append(row)
            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                             download_name=f'machines_{datetime.now().strftime("%Y%m%d")}.xlsx', as_attachment=True)
        except ImportError:
            pass
    elif format_type == 'csv':
        import csv
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers)
        writer.writerows(rows)
        buf.seek(0)
        return send_file(io.BytesIO(buf.getvalue().encode('utf-8-sig')), mimetype='text/csv',
                         download_name=f'machines_{datetime.now().strftime("%Y%m%d")}.csv', as_attachment=True)
    elif format_type == 'pdf':
        try:
            fpdf_mod = ensure_fpdf()
            FPDF = fpdf_mod.FPDF
        except Exception as e:
            flash(_('PDF export is temporarily unavailable'), 'error')
            return redirect(url_for('machines.machines_list'))
        date_str = datetime.now().strftime('%d-%m-%Y %H:%M')
        title = _('Machines')
        subtitle = f'{date_str} - {len(machines)} {_("machines")}'
        footer_txt = _('Generated by ProMaster CRM')
        page_txt = _('page')

        font_path = find_pdf_font()
        unicode_font = bool(font_path)

        def pdf_text(s):
            s = str(s if s is not None else '')
            if unicode_font:
                return s
            return s.encode('latin-1', 'replace').decode('latin-1')

        try:
            class MachinesPDF(FPDF):
                def header(self):
                    fn = 'AppFont' if unicode_font else 'Helvetica'
                    self.set_font(fn, 'B', 12)
                    self.cell(0, 8, pdf_text(f'{title} - ProMaster'), new_x='LMARGIN', new_y='NEXT')
                    self.set_font(fn, '', 8)
                    self.cell(0, 5, pdf_text(subtitle), new_x='LMARGIN', new_y='NEXT')
                    self.ln(2)

                def footer(self):
                    fn = 'AppFont' if unicode_font else 'Helvetica'
                    self.set_y(-12)
                    self.set_font(fn, '', 7)
                    self.set_text_color(128)
                    self.cell(0, 8, pdf_text(f'{footer_txt} - {page_txt} {self.page_no()}/{{nb}}'), align='C')

            pdf = MachinesPDF(orientation='L', unit='mm', format='A4')
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

            # Dynamic column widths: distribute available space evenly
            n_cols = len(headers)
            page_w = 277  # A4 landscape usable width in mm
            col_w = [page_w / n_cols] * n_cols
            pdf.set_font(base_font, 'B', 7)
            pdf.set_fill_color(44, 62, 80)
            pdf.set_text_color(255)
            for i, h in enumerate(headers):
                pdf.cell(col_w[i], 7, pdf_text(h), border=1, fill=True)
            pdf.ln()
            pdf.set_font(base_font, '', 6.5)
            pdf.set_text_color(51)
            for idx, row in enumerate(rows):
                if idx % 2 == 1:
                    pdf.set_fill_color(249, 249, 249)
                    fill = True
                else:
                    fill = False
                for i, val in enumerate(row):
                    pdf.cell(col_w[i], 6, pdf_text(val), border=1, fill=fill)
                pdf.ln()

            buf = io.BytesIO()
            pdf.output(buf)
            buf.seek(0)
            return send_file(buf, mimetype='application/pdf',
                             download_name=f'machines_{datetime.now().strftime("%Y%m%d")}.pdf', as_attachment=True)
        except Exception as e:
            flash(_('PDF export failed: %(err)s', err=str(e)), 'error')
            return redirect(url_for('machines.machines_list'))
    flash(_('Unsupported format'), 'error')
    return redirect(url_for('machines.machines_list'))


@bp.route('/bulk', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def machines_bulk():
    """Bulk actions for selected machines: plan TO, set status, export."""
    action = request.form.get('action', '')
    ids = [safe_int(x) for x in request.form.getlist('ids') if x]
    if not ids:
        flash(_('No machines selected'), 'error')
        return redirect(url_for('machines.machines_list'))
    machines = Machine.query.filter(Machine.id.in_(ids)).all()
    if not machines:
        flash(_('No machines selected'), 'error')
        return redirect(url_for('machines.machines_list'))

    if action == 'set_status':
        status = request.form.get('status', '')
        if status not in ('active', 'maintenance', 'broken', 'offline', 'retired', 'disposed'):
            flash(_('Invalid status'), 'error')
            return redirect(url_for('machines.machines_list'))
        for m in machines:
            m.status = status
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('machines.machines_list'))
        log_audit('bulk_update', 'machine', 0, f'Status={status} for {len(machines)} machines')
        flash(_('Updated %(n)d machines', n=len(machines)), 'success')

    elif action == 'plan_to':
        title = request.form.get('title') or _('Preventive maintenance')
        try:
            planned = datetime.strptime(request.form.get('planned_start', ''), '%Y-%m-%d').date()
        except ValueError:
            from datetime import date as _date
            planned = _date.today()
        # skip weekend
        if planned.weekday() == 5:
            planned += timedelta(days=2)
        elif planned.weekday() == 6:
            planned += timedelta(days=1)
        rec = request.form.get('recurrence') or None
        created = 0
        for m in machines:
            p = MaintenancePlan(
                machine_id=m.id,
                title=title,
                description=title,
                maintenance_type='preventive',
                status='planned',
                planned_start=planned,
                recurrence=rec,
                created_by=current_user.id,
            )
            db.session.add(p)
            created += 1
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('machines.machines_list'))
        log_audit('bulk_create', 'maintenance_plan', 0, f'{created} plans for {len(machines)} machines')
        flash(_('Created %(n)d maintenance plans', n=created), 'success')

    elif action == 'export_ids':
        id_csv = ','.join(str(m.id) for m in machines)
        fmt = request.form.get('format', 'xlsx')
        return redirect(url_for('machines.machines_export', format_type=fmt, ids=id_csv))

    else:
        flash(_('Unknown action'), 'error')

    return redirect(url_for('machines.machines_list'))


@bp.route('/qr/<int:machine_id>')
@login_required
def machine_qr(machine_id):
    m = Machine.query.get_or_404(machine_id)
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(f'{request.host_url}machines/{m.id}')
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


@bp.route('/qr-labels')
@login_required
def machines_qr_labels():
    ids = request.args.get('ids', '')
    if ids:
        item_ids = [safe_int(x) for x in ids.split(',') if x.strip()]
        machines = Machine.query.filter(Machine.id.in_(item_ids)).all()
    else:
        machines = Machine.query.order_by(Machine.name).all()
    return render_template('machines_qr_labels.html', machines=machines)


@bp.route('/scan')
@login_required
def machine_scan_page():
    return render_template('machine_scan.html')


# ============================================================
# CONSUMABLES — link, write-off (consume), update-date, unlink
# ============================================================

@bp.route('/<int:machine_id>/consumables/link', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def machine_consumables_link(machine_id):
    m = Machine.query.get_or_404(machine_id)
    warehouse_item_id = safe_int(request.form.get('warehouse_item_id'))
    if not warehouse_item_id:
        flash(_('Select a material'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    wi = VoorraadItem.query.get_or_404(warehouse_item_id)
    # Check if already linked
    existing = MachineConsumable.query.filter_by(machine_id=machine_id, warehouse_item_id=warehouse_item_id).first()
    if existing:
        flash(_('This material is already linked'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    mc = MachineConsumable(
        machine_id=machine_id,
        warehouse_item_id=warehouse_item_id,
        quantity_per_use=safe_float(request.form.get('quantity_per_use'), 1),
        notes=request.form.get('notes', '')
    )
    db.session.add(mc)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    flash(_('Consumable linked'), 'success')
    return redirect(url_for('machines.machine_detail', machine_id=machine_id))


@bp.route('/<int:machine_id>/consumables/<int:mc_id>/consume', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def machine_consumables_consume(machine_id, mc_id):
    mc = MachineConsumable.query.get_or_404(mc_id)
    if mc.machine_id != machine_id:
        flash(_('Not found'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    qty = safe_float(request.form.get('quantity'), mc.quantity_per_use or 1)
    if qty <= 0:
        qty = mc.quantity_per_use or 1
    wi = mc.warehouse_item
    if not wi:
        flash(_('Warehouse item not found'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    if wi.hoeveelheid < qty:
        flash(_('Not enough stock. Available: %(qty)s %(unit)s', qty=wi.hoeveelheid, unit=wi.eenheid), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    # Deduct from warehouse
    old_qty = wi.hoeveelheid
    wi.hoeveelheid = old_qty - qty
    # Record warehouse movement
    mut = VoorraadMutatie(
        item_id=wi.id,
        type='uitgaand',
        hoeveelheid=qty,
        user_id=current_user.id if current_user.is_authenticated else None,
        opmerking=f'Consumable used on machine #{machine_id}'
    )
    db.session.add(mut)
    mc.last_issued_at = now_local()
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    flash(_('%(name)s written off: %(qty)s %(unit)s', name=wi.naam, qty=qty, unit=wi.eenheid), 'success')
    return redirect(url_for('machines.machine_detail', machine_id=machine_id))


@bp.route('/<int:machine_id>/consumables/<int:mc_id>/update-date', methods=['POST'])
@login_required
@role_required('admin')
def machine_consumables_update_date(machine_id, mc_id):
    mc = MachineConsumable.query.get_or_404(mc_id)
    if mc.machine_id != machine_id:
        flash(_('Not found'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    d = safe_date(request.form.get('last_issued_at'))
    mc.last_issued_at = datetime.combine(d, datetime.min.time()) if d else None
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    flash(_('Date updated'), 'success')
    return redirect(url_for('machines.machine_detail', machine_id=machine_id))


@bp.route('/<int:machine_id>/consumables/<int:mc_id>/unlink', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def machine_consumables_unlink(machine_id, mc_id):
    mc = MachineConsumable.query.get_or_404(mc_id)
    if mc.machine_id != machine_id:
        flash(_('Not found'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    db.session.delete(mc)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    flash(_('Consumable unlinked'), 'success')
    return redirect(url_for('machines.machine_detail', machine_id=machine_id))

# ── ПАРОЛИ станков (admin + механики) ────────────────────────────

@bp.route('/passwords')
@login_required
@role_required('admin', 'technician')
def passwords_list():
    q = MachinePassword.query.order_by(MachinePassword.title)
    mid = safe_int(request.args.get('machine_id'))
    if mid:
        q = q.filter_by(machine_id=mid)
    items = q.all()
    machines = Machine.query.order_by(Machine.name).all()
    return render_template('machine_passwords.html', items=items, machines=machines, mid=mid or '')


@bp.route('/passwords/new', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def password_new():
    title = (request.form.get('title') or '').strip()
    password = (request.form.get('password') or '').strip()
    if not title or not password:
        flash(_('Title and password are required'), 'error')
        return redirect(url_for('machines.passwords_list'))
    mp = MachinePassword(
        machine_id=safe_int(request.form.get('machine_id')) or None,
        title=title,
        login=(request.form.get('login') or '').strip(),
        password=password,
        kind=request.form.get('kind') or 'other',
        notes=(request.form.get('notes') or '').strip(),
        created_by=current_user.id,
    )
    db.session.add(mp)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('machines.passwords_list'))
    log_audit('create', 'machine_password', mp.id, title)
    flash(_('Password saved'), 'success')
    return redirect(url_for('machines.passwords_list'))


@bp.route('/passwords/<int:pid>/update', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def password_update(pid):
    mp = MachinePassword.query.get_or_404(pid)
    mp.title = (request.form.get('title') or mp.title).strip()
    mp.login = (request.form.get('login') or '').strip()
    if request.form.get('password'):
        mp.password = request.form.get('password').strip()
    mp.kind = request.form.get('kind') or mp.kind
    mp.notes = (request.form.get('notes') or '').strip()
    mp.machine_id = safe_int(request.form.get('machine_id')) or None
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('update', 'machine_password', mp.id, mp.title)
        flash(_('Password saved'), 'success')
    return redirect(url_for('machines.passwords_list'))


@bp.route('/passwords/<int:pid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def password_delete(pid):
    mp = MachinePassword.query.get_or_404(pid)
    title = mp.title
    db.session.delete(mp)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('delete', 'machine_password', pid, title)
        flash(_('Deleted'), 'success')
    return redirect(url_for('machines.passwords_list'))
