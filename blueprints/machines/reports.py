"""Machine reports, CSV/XLSX/PDF export, bulk actions, QR labels."""
import csv
import io
import qrcode
from datetime import date as _date
from datetime import datetime, timedelta

from flask import (flash, redirect, render_template, request, send_file,
                   url_for)
from flask_babel import gettext as _
from flask_login import login_required

from models import (FaultReport, Machine, MaintenancePlan, MaintenanceRecord,
                    db)
from security import director_only
from utils import (ensure_fpdf, find_pdf_font, log_audit, resolve_user_id,
                   role_required, safe_commit, safe_int)

from . import bp

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
@director_only('tim', 'peter')
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
                created_by=resolve_user_id(),
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

