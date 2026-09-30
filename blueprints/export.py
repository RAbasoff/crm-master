"""
export blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from models import (db, FaultReport, Machine, MaintenanceRecord, User, VoorraadItem, VoorraadMutatie)
from utils import role_required

bp = Blueprint('export', __name__)

@bp.route('/export/machines')
@login_required
@role_required('admin', 'director')
def export_machines():
    import csv
    import io
    machines = Machine.query.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Name', 'Type', 'Manufacturer', 'Serial', 'Status', 'Section', 'Contractor'])
    for m in machines:
        writer.writerow([m.id, m.name, m.machine_type, m.manufacturer, m.serial_number,
                         m.status, m.section.name if m.section else '',
                         m.contractor_rel.company_name if m.contractor_rel else ''])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                     mimetype='text/csv', as_attachment=True,
                     download_name=f'machines_{datetime.utcnow().strftime("%Y%m%d")}.csv')


@bp.route('/export/warehouse')
@login_required
@role_required('admin', 'director')
def export_warehouse():
    import csv
    import io
    items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Name', 'Category', 'Group', 'Quantity', 'Unit', 'Min', 'Price', 'Location'])
    for i in items:
        writer.writerow([i.id, i.naam, i.categorie, i.group.name if i.group else '',
                         i.hoeveelheid, i.eenheid, i.minimum, i.prijs, i.locatie])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                     mimetype='text/csv', as_attachment=True,
                     download_name=f'warehouse_{datetime.utcnow().strftime("%Y%m%d")}.csv')


@bp.route('/export/movements')
@login_required
@role_required('admin', 'director')
def export_movements():
    import csv
    import io
    item_id = request.args.get('item', '')
    move_type = request.args.get('type', '')
    q = VoorraadMutatie.query
    if item_id:
        q = q.filter_by(item_id=int(item_id))
    if move_type:
        q = q.filter_by(type=move_type)
    movements = q.order_by(VoorraadMutatie.aangemaakt.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Type', 'Item', 'Quantity', 'Unit', 'Order', 'Comment'])
    for m in movements:
        writer.writerow([
            m.aangemaakt.strftime('%Y-%m-%d %H:%M'),
            m.type,
            m.item.naam if m.item else '',
            m.hoeveelheid,
            m.item.eenheid if m.item else '',
            m.opdracht.nummer if m.opdracht else '',
            m.opmerking or ''
        ])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                     mimetype='text/csv', as_attachment=True,
                     download_name=f'movements_{datetime.utcnow().strftime("%Y%m%d")}.csv')


@bp.route('/export/maintenance')
@login_required
@role_required('admin', 'director')
def export_maintenance():
    import csv
    import io
    records = MaintenanceRecord.query.order_by(MaintenanceRecord.date_performed.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Machine', 'Type', 'Description', 'Date', 'Cost', 'Next', 'Performed By'])
    for r in records:
        writer.writerow([r.id, r.machine.name, r.maintenance_type, r.description,
                         r.date_performed.strftime('%Y-%m-%d'), r.cost,
                         r.next_maintenance.strftime('%Y-%m-%d') if r.next_maintenance else '',
                         r.performer.display_name if r.performer else ''])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                     mimetype='text/csv', as_attachment=True,
                     download_name=f'maintenance_{datetime.utcnow().strftime("%Y%m%d")}.csv')


@bp.route('/export/faults')
@login_required
@role_required('admin', 'director')
def export_faults():
    import csv
    import io
    faults = FaultReport.query.order_by(FaultReport.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Title', 'Machine', 'Priority', 'Status', 'Reporter', 'Technician', 'Created', 'Resolved'])
    for f in faults:
        tech = User.query.get(f.technician_id) if f.technician_id else None
        writer.writerow([f.id, f.title, f.target_name, f.priority, f.status,
                         f.reporter_label,
                         tech.display_name if tech else '',
                         f.created_at.strftime('%Y-%m-%d'),
                         f.resolved_at.strftime('%Y-%m-%d') if f.resolved_at else ''])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                     mimetype='text/csv', as_attachment=True,
                     download_name=f'faults_{datetime.utcnow().strftime("%Y%m%d")}.csv')
