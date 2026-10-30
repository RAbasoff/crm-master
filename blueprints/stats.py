"""
Stats blueprint — statistics pages and export
"""
from datetime import datetime, timedelta

from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify, send_file, Response
from flask_login import login_required, current_user
from flask_babel import gettext as _
from sqlalchemy import func, case

import io

from models import (db, FaultReport, Machine, MachinePart, VoorraadItem, VoorraadMutatie,
                    User, TechnicalWorkOrder, WorkReport, TimeEntry, MaintenancePlan,
                    Opdracht, Monteur, FactorySection, GasCylinder, ResponsibleGroup,
                    UserActivityLog)
from utils import role_required, safe_int, safe_date

bp = Blueprint('stats', __name__)


@bp.route('/stats/faults')
@login_required
@role_required('admin', 'director', 'technician')
def stats_faults():
    total = FaultReport.query.count()
    open_count = FaultReport.query.filter_by(status='open').count()
    in_progress = FaultReport.query.filter(FaultReport.status.in_(['accepted', 'in_progress'])).count()
    resolved = FaultReport.query.filter_by(status='resolved').count()
    
    by_machine = db.session.query(
        Machine.name, db.func.count(FaultReport.id)
    ).join(FaultReport).group_by(Machine.name).all()
    
    by_priority = db.session.query(
        FaultReport.priority, db.func.count(FaultReport.id)
    ).group_by(FaultReport.priority).all()
    
    return render_template('stats_faults.html',
        total=total, open_count=open_count, in_progress=in_progress, resolved=resolved,
        by_machine=by_machine, by_priority=by_priority)

@bp.route('/stats/full')
@login_required
@role_required('admin', 'director')
def stats_full():
    """Full statistics page with all metrics"""
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    if date_from and date_to:
        d_from = datetime.strptime(date_from, '%Y-%m-%d')
        d_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
        period = (d_to - d_from).days
    else:
        period = safe_int(request.args.get('period'), 30)
        d_from = datetime.utcnow() - timedelta(days=period)
        d_to = datetime.utcnow() + timedelta(days=1)
        date_from = d_from.strftime('%Y-%m-%d')
        date_to = (d_to - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # Faults stats
    faults_total = FaultReport.query.count()
    faults_period = FaultReport.query.filter(FaultReport.created_at >= d_from).count()
    faults_open = FaultReport.query.filter(FaultReport.status.in_(['open', 'accepted', 'in_progress'])).count()
    faults_resolved = FaultReport.query.filter(FaultReport.status == 'resolved').count()
    faults_critical = FaultReport.query.filter(FaultReport.priority == 'critical').count()
    
    faults_by_priority = db.session.query(
        FaultReport.priority, db.func.count(FaultReport.id)
    ).filter(FaultReport.created_at >= d_from).group_by(FaultReport.priority).all()
    
    faults_by_status = db.session.query(
        FaultReport.status, db.func.count(FaultReport.id)
    ).group_by(FaultReport.status).all()
    
    # Top machines with faults
    top_machines = db.session.query(
        Machine.name, db.func.count(FaultReport.id).label('cnt')
    ).join(FaultReport).filter(FaultReport.created_at >= d_from).group_by(Machine.name).order_by(db.desc('cnt')).limit(10).all()
    
    # Warehouse stats
    warehouse_total = VoorraadItem.query.count()
    warehouse_low = VoorraadItem.query.filter(VoorraadItem.hoeveelheid <= VoorraadItem.minimum).count()
    warehouse_incoming = VoorraadMutatie.query.filter(VoorraadMutatie.type == 'inkomend', VoorraadMutatie.aangemaakt >= d_from).count()
    warehouse_outgoing = VoorraadMutatie.query.filter(VoorraadMutatie.type == 'uitgaand', VoorraadMutatie.aangemaakt >= d_from).count()
    
    # Users stats
    users_total = User.query.filter(User.is_active_user == True).count()
    users_online = UserActivityLog.query.filter(UserActivityLog.action == 'login', UserActivityLog.created_at >= datetime.utcnow() - timedelta(hours=1)).count()
    
    # TWO stats
    two_total = TechnicalWorkOrder.query.count()
    two_active = TechnicalWorkOrder.query.filter(TechnicalWorkOrder.status.in_(['draft', 'assigned', 'in_progress', 'pending_approval'])).count()
    two_completed = TechnicalWorkOrder.query.filter_by(status='completed').count()
    two_cancelled = TechnicalWorkOrder.query.filter_by(status='cancelled').count()
    # TWO по механикам
    from models import Monteur, two_workers
    two_by_mechanic = []
    for m in Monteur.query.order_by(Monteur.naam).all():
        twos = (TechnicalWorkOrder.query
                .join(two_workers, two_workers.c.two_id == TechnicalWorkOrder.id)
                .filter(two_workers.c.worker_id == m.id)
                .all())
        two_by_mechanic.append({
            'name': m.naam,
            'open': sum(1 for t in twos if (t.status or '') not in ('completed', 'cancelled', 'done', 'closed')),
            'completed': sum(1 for t in twos if t.status == 'completed'),
            'cancelled': sum(1 for t in twos if t.status == 'cancelled'),
            'total': len(twos),
        })
    
    # Gas cylinder stats
    cyl_n2_full = GasCylinder.query.filter_by(gas_type='nitrogen', status='full').count()
    cyl_n2_in_use = GasCylinder.query.filter_by(gas_type='nitrogen', status='in_use').count()
    cyl_co2_full = GasCylinder.query.filter_by(gas_type='co2', status='full').count()
    cyl_co2_in_use = GasCylinder.query.filter_by(gas_type='co2', status='in_use').count()
    
    # Machine report with filtering
    section_filter = request.args.get('section', '')
    group_filter = request.args.get('group', '')
    
    machines_q = Machine.query.order_by(Machine.name)
    if section_filter:
        machines_q = machines_q.filter_by(section_id=int(section_filter))
    
    all_machines = machines_q.all()
    sections = FactorySection.query.order_by(FactorySection.name).all()
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    
    # Bulk-fetch fault stats — single query instead of 5N
    machine_report = []
    fault_stats = db.session.query(
        FaultReport.machine_id,
        func.count().label('total_faults'),
        func.sum(case((FaultReport.created_at >= d_from, 1), else_=0)).label('period_faults'),
        func.sum(case((FaultReport.status.in_(['open', 'accepted', 'in_progress']), 1), else_=0)).label('open_faults'),
        func.sum(case((FaultReport.priority == 'critical', 1), else_=0)).label('critical_faults')
    ).group_by(FaultReport.machine_id).all()
    fault_map = {mid: (tf, pf, of_, cf) for mid, tf, pf, of_, cf in fault_stats}

    # Bulk-fetch last fault per machine
    last_faults = {}
    for mid in fault_map:
        lf = FaultReport.query.filter(FaultReport.machine_id == mid).order_by(FaultReport.created_at.desc()).first()
        last_faults[mid] = lf

    machine_map = {m.id: m for m in all_machines}
    for mid, (tf, pf, of_, cf) in fault_map.items():
        m = machine_map.get(mid)
        if m:
            machine_report.append({
                'machine': m,
                'total_faults': tf,
                'period_faults': pf or 0,
                'open_faults': of_ or 0,
                'critical_faults': cf or 0,
                'last_fault': last_faults.get(mid),
            })
    # Add machines with no faults
    fault_machine_ids = set(fault_map.keys())
    for m in all_machines:
        if m.id not in fault_machine_ids:
            machine_report.append({
                'machine': m, 'total_faults': 0, 'period_faults': 0,
                'open_faults': 0, 'critical_faults': 0, 'last_fault': None,
            })
    
    # Sort by period faults descending
    machine_report.sort(key=lambda x: x['period_faults'], reverse=True)
    
    return render_template('stats_full.html',
        period=period, d_from=d_from, date_from=date_from, date_to=date_to,
        faults_total=faults_total, faults_period=faults_period, faults_open=faults_open,
        faults_resolved=faults_resolved, faults_critical=faults_critical,
        faults_by_priority=faults_by_priority, faults_by_status=faults_by_status,
        top_machines=top_machines,
        warehouse_total=warehouse_total, warehouse_low=warehouse_low,
        warehouse_incoming=warehouse_incoming, warehouse_outgoing=warehouse_outgoing,
        users_total=users_total, users_online=users_online,
        cyl_n2_full=cyl_n2_full, cyl_n2_in_use=cyl_n2_in_use,
        cyl_co2_full=cyl_co2_full, cyl_co2_in_use=cyl_co2_in_use,
        two_total=two_total, two_active=two_active, two_completed=two_completed,
        two_cancelled=two_cancelled, two_by_mechanic=two_by_mechanic,
        machine_report=machine_report, sections=sections, groups=groups,
        section_filter=section_filter, group_filter=group_filter)

@bp.route('/stats/full/export')
@login_required
@role_required('admin', 'director')
def stats_export():
    """Export full statistics in various formats"""
    format_type = request.args.get('format', 'pdf')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    import io
    from flask import send_file
    
    if date_from and date_to:
        d_from = datetime.strptime(date_from, '%Y-%m-%d')
        d_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
        period = (d_to - d_from).days
    else:
        period = safe_int(request.args.get('period'), 30)
        d_from = datetime.utcnow() - timedelta(days=period)
        d_to = datetime.utcnow() + timedelta(days=1)
        date_from = d_from.strftime('%Y-%m-%d')
        date_to = (d_to - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # Collect all stats
    stats = {
        'period': f'{date_from} — {date_to}',
        'date': datetime.utcnow().strftime('%d.%m.%Y %H:%M'),
        'faults': {
            'total': FaultReport.query.count(),
            'period': FaultReport.query.filter(FaultReport.created_at >= d_from).count(),
            'open': FaultReport.query.filter(FaultReport.status.in_(['open', 'accepted', 'in_progress'])).count(),
            'resolved': FaultReport.query.filter(FaultReport.status == 'resolved').count(),
            'critical': FaultReport.query.filter(FaultReport.priority == 'critical').count(),
        },
        'warehouse': {
            'total': VoorraadItem.query.count(),
            'low_stock': VoorraadItem.query.filter(VoorraadItem.hoeveelheid <= VoorraadItem.minimum).count(),
            'incoming': VoorraadMutatie.query.filter(VoorraadMutatie.type == 'inkomend', VoorraadMutatie.aangemaakt >= d_from).count(),
            'outgoing': VoorraadMutatie.query.filter(VoorraadMutatie.type == 'uitgaand', VoorraadMutatie.aangemaakt >= d_from).count(),
        },
        'users': {
            'total': User.query.filter(User.is_active_user == True).count(),
        },
        'two': {
            'total': TechnicalWorkOrder.query.count(),
            'active': TechnicalWorkOrder.query.filter(TechnicalWorkOrder.status.in_(['draft', 'assigned', 'in_progress'])).count(),
            'completed': TechnicalWorkOrder.query.filter_by(status='completed').count(),
        }
    }
    
    # Machine report
    section_filter = request.args.get('section', '')
    machines_q = Machine.query.order_by(Machine.name)
    if section_filter:
        machines_q = machines_q.filter_by(section_id=int(section_filter))
    
    machine_data = []
    for m in machines_q.all():
        total_f = FaultReport.query.filter(FaultReport.machine_id == m.id).count()
        period_f = FaultReport.query.filter(FaultReport.machine_id == m.id, FaultReport.created_at >= d_from).count()
        open_f = FaultReport.query.filter(FaultReport.machine_id == m.id, FaultReport.status.in_(['open', 'accepted', 'in_progress'])).count()
        critical_f = FaultReport.query.filter(FaultReport.machine_id == m.id, FaultReport.priority == 'critical').count()
        machine_data.append({
            'name': m.name, 'type': m.machine_type or '', 'serial': m.serial_number or '',
            'section': m.section.name if m.section else '',
            'total': total_f, 'period': period_f, 'open': open_f, 'critical': critical_f
        })
    machine_data.sort(key=lambda x: x['period'], reverse=True)
    
    # Top machines
    top_machines = db.session.query(
        Machine.name, db.func.count(FaultReport.id).label('cnt')
    ).join(FaultReport).filter(FaultReport.created_at >= d_from).group_by(Machine.name).order_by(db.desc('cnt')).limit(10).all()
    
    # By priority
    by_priority = db.session.query(
        FaultReport.priority, db.func.count(FaultReport.id)
    ).filter(FaultReport.created_at >= d_from).group_by(FaultReport.priority).all()
    
    filename = f'statistics_{period}days_{datetime.utcnow().strftime("%Y%m%d")}'
    
    # === TXT ===
    if format_type == 'txt':
        lines = []
        lines.append('=' * 60)
        lines.append('СТАТИСТИКА — CRM МАСТЕРСКАЯ')
        lines.append(f'Период: {stats["period"]}')
        lines.append(f'Дата: {stats["date"]}')
        lines.append('=' * 60)
        lines.append('')
        lines.append('--- ЗАЯВКИ О НЕИСПРАВНОСТИ ---')
        lines.append(f'Всего: {stats["faults"]["total"]}')
        lines.append(f'За период: {stats["faults"]["period"]}')
        lines.append(f'Открытых: {stats["faults"]["open"]}')
        lines.append(f'Решённых: {stats["faults"]["resolved"]}')
        lines.append(f'Критичных: {stats["faults"]["critical"]}')
        lines.append('')
        lines.append('По приоритету:')
        for p, c in by_priority:
            lines.append(f'  {p}: {c}')
        lines.append('')
        lines.append('Топ станков по заявкам:')
        for name, cnt in top_machines:
            lines.append(f'  {name}: {cnt}')
        lines.append('')
        lines.append('--- СКЛАД ---')
        lines.append(f'Всего позиций: {stats["warehouse"]["total"]}')
        lines.append(f'Низкий запас: {stats["warehouse"]["low_stock"]}')
        lines.append(f'Приход за период: {stats["warehouse"]["incoming"]}')
        lines.append(f'Расход за период: {stats["warehouse"]["outgoing"]}')
        lines.append('')
        lines.append('--- TWO (НАРЯДЫ) ---')
        lines.append(f'Всего: {stats["two"]["total"]}')
        lines.append(f'Активных: {stats["two"]["active"]}')
        lines.append(f'Завершённых: {stats["two"]["completed"]}')
        lines.append('')
        lines.append('--- ПОЛЬЗОВАТЕЛИ ---')
        lines.append(f'Активных: {stats["users"]["total"]}')
        lines.append('')
        lines.append('--- ОТЧЁТ ПО СТАНКАМ ---')
        lines.append(f'Всего станков: {len(machine_data)}')
        lines.append('')
        lines.append(f'{"Станок":<25} {"Тип":<15} {"Отдел":<15} {"Всего":>6} {"Период":>7} {"Откр":>5} {"Крит":>5}')
        lines.append('-' * 80)
        for m in machine_data:
            lines.append(f'{m["name"]:<25} {m["type"]:<15} {m["section"]:<15} {m["total"]:>6} {m["period"]:>7} {m["open"]:>5} {m["critical"]:>5}')
        lines.append('')
        lines.append('=' * 60)
        lines.append('ProMaster CRM - TechnicheDienst — Статистика')
        
        from flask import Response
        return Response('\n'.join(lines), mimetype='text/plain',
            headers={'Content-Disposition': f'attachment;filename={filename}.txt'})
    
    # === CSV ===
    elif format_type == 'csv':
        import csv, io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['ProMaster CRM - TechnicheDienst — Статистика', stats['period'], stats['date']])
        writer.writerow([])
        writer.writerow(['ЗАЯВКИ', 'Количество'])
        writer.writerow(['Всего', stats['faults']['total']])
        writer.writerow(['За период', stats['faults']['period']])
        writer.writerow(['Открытых', stats['faults']['open']])
        writer.writerow(['Решённых', stats['faults']['resolved']])
        writer.writerow(['Критичных', stats['faults']['critical']])
        writer.writerow([])
        writer.writerow(['Приоритет', 'Количество'])
        for p, c in by_priority:
            writer.writerow([p, c])
        writer.writerow([])
        writer.writerow(['Станок', 'Заявок'])
        for name, cnt in top_machines:
            writer.writerow([name, cnt])
        writer.writerow([])
        writer.writerow(['СКЛАД', 'Количество'])
        writer.writerow(['Всего позиций', stats['warehouse']['total']])
        writer.writerow(['Низкий запас', stats['warehouse']['low_stock']])
        writer.writerow(['Приход', stats['warehouse']['incoming']])
        writer.writerow(['Расход', stats['warehouse']['outgoing']])
        writer.writerow([])
        writer.writerow(['ОТЧЁТ ПО СТАНКАМ'])
        writer.writerow(['Станок', 'Тип', 'Серийный номер', 'Отдел', 'Всего заявок', 'За период', 'Открытых', 'Критичных'])
        for m in machine_data:
            writer.writerow([m['name'], m['type'], m['serial'], m['section'], m['total'], m['period'], m['open'], m['critical']])
        output.seek(0)
        from flask import Response
        return Response(output.getvalue(), mimetype='text/csv',
            headers={'Content-Disposition': f'attachment;filename={filename}.csv'})
    
    # === EXCEL ===
    elif format_type in ('excel', 'xlsx'):
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        wb = Workbook()
        ws = wb.active
        ws.title = 'Статистика'
        
        header_fill = PatternFill(start_color='2C3E50', end_color='2C3E50', fill_type='solid')
        header_font = Font(bold=True, color='FFFFFF', size=11)
        title_font = Font(bold=True, size=14)
        section_font = Font(bold=True, size=12, color='2C3E50')
        thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
        
        ws['A1'] = 'Статистика — ProMaster CRM - TechnicheDienst'
        ws['A1'].font = title_font
        ws['A2'] = f'Период: {stats["period"]}'
        ws['A3'] = f'Дата: {stats["date"]}'
        
        row = 5
        ws.cell(row=row, column=1, value='ЗАЯВКИ О НЕИСПРАВНОСТИ').font = section_font
        row += 1
        for label, val in [('Всего', stats['faults']['total']), ('За период', stats['faults']['period']),
                           ('Открытых', stats['faults']['open']), ('Решённых', stats['faults']['resolved']),
                           ('Критичных', stats['faults']['critical'])]:
            ws.cell(row=row, column=1, value=label).border = thin_border
            ws.cell(row=row, column=2, value=val).border = thin_border
            row += 1
        
        row += 1
        ws.cell(row=row, column=1, value='По приоритету').font = section_font
        row += 1
        for p, c in by_priority:
            ws.cell(row=row, column=1, value=p).border = thin_border
            ws.cell(row=row, column=2, value=c).border = thin_border
            row += 1
        
        row += 1
        ws.cell(row=row, column=1, value='Топ станков').font = section_font
        row += 1
        for name, cnt in top_machines:
            ws.cell(row=row, column=1, value=name).border = thin_border
            ws.cell(row=row, column=2, value=cnt).border = thin_border
            row += 1
        
        row += 1
        ws.cell(row=row, column=1, value='СКЛАД').font = section_font
        row += 1
        for label, val in [('Всего позиций', stats['warehouse']['total']), ('Низкий запас', stats['warehouse']['low_stock']),
                           ('Приход', stats['warehouse']['incoming']), ('Расход', stats['warehouse']['outgoing'])]:
            ws.cell(row=row, column=1, value=label).border = thin_border
            ws.cell(row=row, column=2, value=val).border = thin_border
            row += 1
        
        row += 1
        ws.cell(row=row, column=1, value='ОТЧЁТ ПО СТАНКАМ').font = section_font
        row += 1
        machine_headers = ['Станок', 'Тип', 'Серийный номер', 'Отдел', 'Всего заявок', 'За период', 'Открытых', 'Критичных']
        for col, h in enumerate(machine_headers, 1):
            cell = ws.cell(row=row, column=col, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.border = thin_border
        row += 1
        for m in machine_data:
            ws.cell(row=row, column=1, value=m['name']).border = thin_border
            ws.cell(row=row, column=2, value=m['type']).border = thin_border
            ws.cell(row=row, column=3, value=m['serial']).border = thin_border
            ws.cell(row=row, column=4, value=m['section']).border = thin_border
            ws.cell(row=row, column=5, value=m['total']).border = thin_border
            ws.cell(row=row, column=6, value=m['period']).border = thin_border
            ws.cell(row=row, column=7, value=m['open']).border = thin_border
            ws.cell(row=row, column=8, value=m['critical']).border = thin_border
            row += 1
        
        ws.column_dimensions['A'].width = 25
        ws.column_dimensions['B'].width = 15
        ws.column_dimensions['C'].width = 18
        ws.column_dimensions['D'].width = 15
        
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            download_name=f'{filename}.xlsx', as_attachment=True)
    
    # === WORD ===
    elif format_type == 'word':
        from docx import Document
        from docx.shared import Pt, RGBColor
        doc = Document()
        doc.add_heading('Статистика — ProMaster CRM - TechnicheDienst', level=1)
        doc.add_paragraph(f'Период: {stats["period"]}')
        doc.add_paragraph(f'Дата: {stats["date"]}')
        
        doc.add_heading('Заявки о неисправности', level=2)
        table = doc.add_table(rows=6, cols=2)
        table.style = 'Light Grid Accent 1'
        table.rows[0].cells[0].text = 'Показатель'
        table.rows[0].cells[1].text = 'Количество'
        for i, (label, val) in enumerate([('Всего', stats['faults']['total']), ('За период', stats['faults']['period']),
                                           ('Открытых', stats['faults']['open']), ('Решённых', stats['faults']['resolved']),
                                           ('Критичных', stats['faults']['critical'])], 1):
            table.rows[i].cells[0].text = label
            table.rows[i].cells[1].text = str(val)
        
        doc.add_heading('Топ станков по заявкам', level=2)
        table2 = doc.add_table(rows=len(top_machines)+1, cols=2)
        table2.style = 'Light Grid Accent 1'
        table2.rows[0].cells[0].text = 'Станок'
        table2.rows[0].cells[1].text = 'Заявок'
        for i, (name, cnt) in enumerate(top_machines, 1):
            table2.rows[i].cells[0].text = name
            table2.rows[i].cells[1].text = str(cnt)
        
        doc.add_heading('Склад', level=2)
        table3 = doc.add_table(rows=5, cols=2)
        table3.style = 'Light Grid Accent 1'
        table3.rows[0].cells[0].text = 'Показатель'
        table3.rows[0].cells[1].text = 'Количество'
        for i, (label, val) in enumerate([('Всего позиций', stats['warehouse']['total']), ('Низкий запас', stats['warehouse']['low_stock']),
                                           ('Приход', stats['warehouse']['incoming']), ('Расход', stats['warehouse']['outgoing'])], 1):
            table3.rows[i].cells[0].text = label
            table3.rows[i].cells[1].text = str(val)
        
        doc.add_heading('Отчёт по станкам', level=2)
        table5 = doc.add_table(rows=len(machine_data)+1, cols=8)
        table5.style = 'Light Grid Accent 1'
        for i, h in enumerate(['Станок', 'Тип', 'Серийный номер', 'Отдел', 'Всего', 'Период', 'Открытых', 'Критичных']):
            table5.rows[0].cells[i].text = h
        for i, m in enumerate(machine_data, 1):
            table5.rows[i].cells[0].text = m['name']
            table5.rows[i].cells[1].text = m['type']
            table5.rows[i].cells[2].text = m['serial']
            table5.rows[i].cells[3].text = m['section']
            table5.rows[i].cells[4].text = str(m['total'])
            table5.rows[i].cells[5].text = str(m['period'])
            table5.rows[i].cells[6].text = str(m['open'])
            table5.rows[i].cells[7].text = str(m['critical'])
        
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            download_name=f'{filename}.docx', as_attachment=True)
    
    # === PDF ===
    elif format_type == 'pdf':
        from utils import ensure_fpdf, find_pdf_font
        try:
            FPDF = ensure_fpdf().FPDF
        except Exception:
            return jsonify({'error': 'PDF unavailable'}), 500
        font_path = find_pdf_font()
        unicode_font = bool(font_path)

        def pdf_text(s):
            s = str(s if s is not None else '')
            return s if unicode_font else s.encode('latin-1', 'replace').decode('latin-1')

        class StatsPDF(FPDF):
            def header(self):
                fn = 'AppFont' if unicode_font else 'Helvetica'
                self.set_font(fn, 'B', 14)
                self.cell(0, 8, pdf_text('Статистика — ProMaster CRM - TechnicheDienst'), new_x='LMARGIN', new_y='NEXT')
                self.set_font(fn, '', 9)
                self.cell(0, 5, pdf_text(f'{stats["period"]} | {stats["date"]}'), new_x='LMARGIN', new_y='NEXT')
                self.ln(2)

            def footer(self):
                fn = 'AppFont' if unicode_font else 'Helvetica'
                self.set_y(-12)
                self.set_font(fn, '', 7)
                self.set_text_color(128)
                self.cell(0, 8, pdf_text(f'CRM — {self.page_no()}/{{nb}}'), align='C')

        pdf = StatsPDF(orientation='L', unit='mm', format='A4')
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

        def section(t):
            pdf.set_font(base_font, 'B', 11)
            pdf.set_text_color(44, 62, 80)
            pdf.cell(0, 8, pdf_text(t), new_x='LMARGIN', new_y='NEXT')
            pdf.set_text_color(51)

        def kv_table(pairs):
            pdf.set_font(base_font, '', 9)
            for label, val in pairs:
                pdf.cell(80, 6, pdf_text(label), border=1)
                pdf.cell(30, 6, pdf_text(str(val)), border=1, new_x='LMARGIN', new_y='NEXT')
            pdf.ln(2)

        def data_table(headers, rows, widths):
            pdf.set_font(base_font, 'B', 8)
            pdf.set_fill_color(44, 62, 80)
            pdf.set_text_color(255)
            for i, h in enumerate(headers):
                pdf.cell(widths[i], 7, pdf_text(h), border=1, fill=True)
            pdf.ln()
            pdf.set_text_color(51)
            pdf.set_font(base_font, '', 8)
            for row in rows:
                for i, val in enumerate(row):
                    pdf.cell(widths[i], 6, pdf_text(val), border=1)
                pdf.ln()
            pdf.ln(2)

        section('Заявки о неисправности')
        kv_table([
            ('Всего', stats['faults']['total']),
            ('За период', stats['faults']['period']),
            ('Открытых', stats['faults']['open']),
            ('Решённых', stats['faults']['resolved']),
            ('Критичных', stats['faults']['critical']),
        ])
        section('По приоритету')
        data_table(['Приоритет', 'Количество'], [(str(p), str(c)) for p, c in by_priority], [80, 40])
        section('Топ станков по заявкам')
        data_table(['Станок', 'Заявок'], [(str(n), str(c)) for n, c in top_machines], [120, 40])
        section('Склад')
        kv_table([
            ('Всего позиций', stats['warehouse']['total']),
            ('Низкий запас', stats['warehouse']['low_stock']),
            ('Приход', stats['warehouse']['incoming']),
            ('Расход', stats['warehouse']['outgoing']),
        ])
        section('TWO (наряды)')
        kv_table([
            ('Всего', stats['two']['total']),
            ('Активных', stats['two']['active']),
            ('Завершённых', stats['two']['completed']),
        ])
        section('Отчёт по станкам')
        data_table(
            ['Станок', 'Тип', 'Отдел', 'Всего', 'Период', 'Открытых', 'Критичных'],
            [(m['name'], m['type'], m['section'], str(m['total']), str(m['period']),
              str(m['open']), str(m['critical'])) for m in machine_data],
            [55, 35, 40, 25, 25, 30, 30],
        )

        import io
        buf = io.BytesIO()
        pdf.output(buf)
        buf.seek(0)
        return send_file(buf, mimetype='application/pdf',
                         download_name=f'{filename}.pdf', as_attachment=True)

    return jsonify({'error': 'Unknown format'}), 400

@bp.route('/stats/parts')
@login_required
@role_required('admin', 'director')
def stats_parts():
    low_stock = VoorraadItem.query.filter(VoorraadItem.hoeveelheid <= VoorraadItem.minimum).all()
    recent_usage = VoorraadMutatie.query.filter_by(type='uitgaand').order_by(VoorraadMutatie.aangemaakt.desc()).limit(20).all()
    return render_template('stats_parts.html', low_stock=low_stock, recent_usage=recent_usage)

@bp.route('/api/stats')
@login_required
def api_stats():
    days = []
    for i in range(29, -1, -1):
        dag = datetime.utcnow().date() - timedelta(days=i)
        c = Opdracht.query.filter(Opdracht.aangemaakt >= datetime.combine(dag, datetime.min.time()),
            Opdracht.aangemaakt < datetime.combine(dag + timedelta(days=1), datetime.min.time())).count()
        days.append({'date': dag.strftime('%d.%m'), 'count': c})
    statuses = {}
    for s in ['aangenomen','diagnose','in behandeling','gereed','afgeleverd','geannuleerd']:
        statuses[s] = Opdracht.query.filter_by(status=s).count()
    wdata = []
    for w in Monteur.query.filter_by(actief=True).all():
        cnt = Opdracht.query.filter_by(monteur_id=w.id, status='afgeleverd').count()
        rev = db.session.query(db.func.sum(Opdracht.totaal)).filter_by(monteur_id=w.id, status='afgeleverd').scalar() or 0
        wdata.append({'id': w.id, 'name': w.naam, 'orders': cnt, 'revenue': float(rev)})
    return jsonify({'days': days, 'statuses': statuses, 'workers': wdata})

