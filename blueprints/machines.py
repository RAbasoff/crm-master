"""
Machines blueprint — machine CRUD, parts, consumables, documents, floor plan, reports
"""
from datetime import datetime, timedelta
from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify, send_file
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json
import qrcode

from models import (db, Machine, MachinePart, PartMaintenanceLog, MachineDocument,
                    MachineSparePart, MachineConsumable, MaintenanceRecord, MaintenancePhoto,
                    FactorySection, User, Contractor, Verantwoordelijke, FaultReport,
                    VoorraadItem, VoorraadMutatie)
from utils import (role_required, log_audit, save_uploaded_file, safe_commit,
                   safe_int, safe_float, safe_date, sanitize_like)

bp = Blueprint('machines', __name__, url_prefix='/machines')


@bp.route('/')
@login_required
def machines_list():
    page = request.args.get('page', 1, type=int)
    if current_user.has_role('admin', 'director'):
        pagination = Machine.query.order_by(Machine.name).paginate(page=page, per_page=25, error_out=False)
    elif current_user.has_role('technician'):
        pagination = Machine.query.order_by(Machine.name).paginate(page=page, per_page=25, error_out=False)
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
            floor_y=safe_float(request.form.get('floor_y'), 50)
        )
        if 'photo' in request.files and request.files['photo'].filename:
            filename = secure_filename(f"machine_{request.files['photo'].filename}")
            request.files['photo'].save(os.path.join('static/uploads', filename))
            m.photo = filename
        db.session.add(m)
        db.session.flush()
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
    return render_template('machine_detail.html', machine=m, faults=faults, maintenance=maintenance,
                           warehouse_items=warehouse_items)


@bp.route('/<int:machine_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def machine_edit(machine_id):
    m = Machine.query.get_or_404(machine_id)
    if request.method == 'POST':
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
        if 'photo' in request.files and request.files['photo'].filename:
            filename = secure_filename(f"machine_{m.id}_{request.files['photo'].filename}")
            request.files['photo'].save(os.path.join('static/uploads', filename))
            m.photo = filename
        m.assigned_users = []
        for uid in request.form.getlist('assigned_users'):
            u = User.query.get(int(uid))
            if u:
                m.assigned_users.append(u)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('machines.machine_edit', machine_id=m.id))
        flash(_('Machine updated'), 'success')
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
    filename = secure_filename(f"doc_{m.id}_{file.filename}")
    file.save(os.path.join('static/uploads', filename))
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
                    fn = secure_filename(f"maint_{mr.id}_{photo.filename}")
                    photo.save(os.path.join('static/uploads', fn))
                    mp = MaintenancePhoto(maintenance_id=mr.id, filename=fn)
                    db.session.add(mp)
            safe_commit()
        flash(_('Maintenance record added'), 'success')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    return render_template('maintenance_form.html', machine=m, now=datetime.utcnow())


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


@bp.route('/export/<format_type>')
@login_required
@role_required('admin', 'director')
def machines_export(format_type):
    machines = Machine.query.order_by(Machine.name).all()
    headers = ['ID', 'Name', 'Type', 'Serial', 'Manufacturer', 'Year',
               'Location', 'Section', 'Responsible', 'Contractor', 'Status']
    rows = []
    for m in machines:
        rows.append([
            m.id, m.name, m.machine_type or '', m.serial_number or '',
            m.manufacturer or '', m.year_of_manufacture or '',
            m.installation_location or '',
            m.section.name if m.section else '',
            m.responsible_user.display_name if m.responsible_user else '',
            m.contractor.company_name if m.contractor else '',
            m.status or 'active'
        ])
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
        date_str = datetime.now().strftime('%d-%m-%Y %H:%M')
        html = '<!DOCTYPE html><html><head><meta charset="utf-8">'
        html += '<style>body{font-family:Arial,sans-serif;font-size:9px;padding:20px;color:#333}'
        html += 'h1{font-size:16px;margin-bottom:4px;color:#2c3e50}'
        html += 'table{width:100%;border-collapse:collapse;font-size:8px}'
        html += 'th{background:#2c3e50;color:white;padding:5px 6px;text-align:left;font-weight:bold;border:1px solid #1a252f}'
        html += 'td{padding:4px 6px;border:1px solid #ddd}'
        html += 'tr:nth-child(even){background:#f9f9f9}'
        html += '@media print{body{padding:10px;font-size:7px}th{padding:3px 4px}td{padding:2px 4px}}'
        html += '</style></head><body>'
        html += f'<h1>Machines — ProMaster</h1>'
        html += f'<p>{date_str} — {len(machines)} machines</p>'
        html += '<table><tr>'
        for h in headers:
            html += f'<th>{h}</th>'
        html += '</tr>'
        for row in rows:
            html += '<tr>'
            for i, val in enumerate(row):
                cls = ''
                if i == 10:
                    cls = f' class="{"ok" if val == "active" else "err" if val == "broken" else "warn"}"'
                html += f'<td{cls}>{val}</td>'
            html += '</tr>'
        html += '</table>'
        html += '<p style="margin-top:16px;font-size:8px;color:#999">Generated by ProMaster CRM</p>'
        html += '</body></html>'
        buf = io.BytesIO(html.encode('utf-8'))
        return send_file(buf, mimetype='text/html',
                         download_name=f'machines_{datetime.now().strftime("%Y%m%d")}.html', as_attachment=True)
    flash(_('Unsupported format'), 'error')
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
