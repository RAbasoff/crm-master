"""Gas archive, cylinder returns and PDF report routes."""
import io
import json
from collections import OrderedDict
from datetime import datetime, timedelta

from flask import (request, redirect, url_for, flash, render_template,
                   jsonify, send_file)
from flask_login import login_required
from flask_babel import gettext as _

from models import (db, GasCylinder, CylinderLog, CylinderOrder, MonthlyArchive,
                    now_local, FaultReport, EquipmentRepair, MaintenanceRecord)
from utils import (resolve_user_id, role_required, log_audit, safe_commit,
                   safe_int, ensure_fpdf, find_pdf_font)

from . import bp, _inventory_balance, GAS_TYPES, GAS_LABELS


@bp.route('/archive', methods=['POST'])
@login_required
@role_required('admin')
def gas_archive():
    """Snapshot empty cylinders to MonthlyArchive (keep them in stock — balance identity)."""
    now = datetime.utcnow()
    month_key = now.strftime('%Y-%m')

    empty = GasCylinder.query.filter_by(status='empty').all()
    if not empty:
        flash(_('No empty cylinders to archive'), 'info')
        return redirect(url_for('gas.gas_dashboard'))

    # Save snapshot only — DO NOT delete (full + in_use + empty = total must hold)
    snapshot = []
    for c in empty:
        snapshot.append({
            'id': c.id, 'gas_type': c.gas_type,
            'cylinder_number': c.cylinder_number, 'barcode': c.barcode,
            'status': c.status,
            'received_at': c.received_at.strftime('%Y-%m-%d') if c.received_at else None,
            'installed_at': c.installed_at.strftime('%Y-%m-%d') if c.installed_at else None,
            'notes': c.notes or ''
        })

    archive = MonthlyArchive(
        archive_month=month_key,
        section='gas_cylinders',
        data_json=json.dumps(snapshot, ensure_ascii=False)
    )
    db.session.add(archive)

    count = len(empty)
    if not safe_commit():
        flash(_('Archive failed. Please try again.'), 'error')
        return redirect(url_for('gas.gas_dashboard'))
    log_audit('archive', 'gas_cylinders', 0, f'{count} empty cylinders snapshotted for {month_key}')
    flash(_('%(count)d empty cylinders snapshotted (kept in stock)', count=count), 'success')
    return redirect(url_for('gas.gas_dashboard'))


# ============================================================
# PDF REPORT — gas, by month, for a period
# ============================================================

@bp.route('/archive/view')
@login_required
def gas_archive_view():
    """Архив газовых баллонов по месяцам (все свёрнуты) + отчёт/статистика."""
    try:
        return _gas_archive_view_impl()
    except Exception as e:
        print('GAS ARCHIVE ERROR', e)
        flash(_('An error occurred. Please try again.'), 'error')
        return redirect(url_for('gas.gas_dashboard'))


def _gas_archive_view_impl():
    rows = (MonthlyArchive.query.filter_by(section='gas_cylinders')
            .order_by(MonthlyArchive.archive_month.desc()).all())
    months = OrderedDict()
    for a in rows:
        try:
            data = json.loads(a.data_json or '{}')
        except Exception:
            data = {}
        if isinstance(data, list):
            data = {}
        if not isinstance(data, dict):
            data = {}
        summary = data.get('summary') or {}
        if not isinstance(summary, dict):
            summary = {}
        empties = data.get('empty') or []
        if not isinstance(empties, list):
            empties = []
        empties = [e for e in empties if isinstance(e, dict)][:20]
        months[a.archive_month] = {
            'key': a.archive_month,
            'summary': summary,
            'empty_count': len(empties),
            'empty_sample': empties,
            'created_at': a.created_at,
        }

    def _ensure(mk):
        if mk not in months:
            months[mk] = {'key': mk, 'summary': {}, 'empty_count': 0, 'empty_sample': [], 'created_at': None}
        return months[mk]

    # Заказы и доставки по месяцам (реальные суммы)
    for o in CylinderOrder.query.all():
        if not o.ordered_at:
            continue
        k = o.ordered_at.strftime('%Y-%m')
        row = _ensure(k)
        q = int(o.quantity or 0)
        field = {'nitrogen': 'n2', 'co2': 'co2', 'mixed': 'mixed'}.get(o.gas_type, 'n2')
        row.setdefault('ordered_n2', 0)
        row.setdefault('ordered_co2', 0)
        row.setdefault('ordered_mixed', 0)
        row.setdefault('delivered_n2', 0)
        row.setdefault('delivered_co2', 0)
        row.setdefault('delivered_mixed', 0)
        row.setdefault('orders_list', [])
        row['ordered_' + field] = row.get('ordered_' + field, 0) + q
        if o.status == 'delivered':
            row['delivered_' + field] = row.get('delivered_' + field, 0) + q
        row['orders_list'].append({
            'id': o.id, 'gas': o.gas_type, 'qty': q, 'status': o.status,
            'date': o.ordered_at.strftime('%d-%m-%Y'),
            'delivered': o.delivered_at.strftime('%d-%m-%Y') if o.delivered_at else '',
        })

    # Расход (стало пустым) и возвраты по месяцам.
    # Расход — только опустевшие (уникальные баллоны, в старых данных
    # на каждое событие падало два лога). Возврат поставщику — НЕ расход.
    seen_used = set()
    for log in CylinderLog.query.all():
        if not log.date:
            continue
        k = log.date.strftime('%Y-%m')
        row = _ensure(k)
        act = (log.action or '')
        if '_to_empty' in act:
            mark = (k, log.cylinder_id)
            if mark not in seen_used:
                seen_used.add(mark)
                row['used_total'] = row.get('used_total', 0) + 1
        if act in ('return_full', 'return_empty'):
            row['returned_total'] = row.get('returned_total', 0) + 1
        if act in ('created', 'received'):
            row.setdefault('received_ops', 0)
            row['received_ops'] += 1

    # Неисправности / ремонты оборудования по месяцам
    for f in FaultReport.query.all():
        ts = f.created_at
        if not ts:
            continue
        k = ts.strftime('%Y-%m')
        row = _ensure(k)
        row.setdefault('faults', 0)
        row['faults'] += 1
    for r in EquipmentRepair.query.all():
        ts = r.date_broken or r.created_at
        if not ts:
            continue
        k = ts.strftime('%Y-%m')
        row = _ensure(k)
        row.setdefault('repairs', 0)
        row['repairs'] += 1

    months = OrderedDict(sorted(months.items(), key=lambda kv: kv[0], reverse=True))
    # normalize missing keys
    for row in months.values():
        for f in ('ordered_n2', 'ordered_co2', 'ordered_mixed',
                  'delivered_n2', 'delivered_co2', 'delivered_mixed',
                  'used_total', 'returned_total', 'faults', 'repairs', 'orders_list'):
            row.setdefault(f, 0 if f != 'orders_list' else [])
    balance = _inventory_balance()
    return render_template('gas/archive.html', months=months, balance=balance,
                           gas_labels=GAS_LABELS)


@bp.route('/returns', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def gas_returns():
    """Возврат баллонов поставщику (дата, тип, количество, причина).

    По факту обмена: при поставке новых пустые уезжают поставщику.
    Поэтому первыми списываются ПУСТЫЕ (ждут возврата), полные — только
    если пустых не хватило.
    """
    if request.method == 'POST':
        try:
            ret_date = datetime.strptime(request.form.get('return_date') or '', '%Y-%m-%d').date()
        except ValueError:
            ret_date = now_local().date()
        gas_type = request.form.get('gas_type') or 'nitrogen'
        if gas_type not in GAS_TYPES:
            gas_type = 'nitrogen'
        qty = safe_int(request.form.get('quantity'), 0)
        reason = (request.form.get('reason') or '').strip()
        if qty <= 0:
            flash(_('Quantity must be positive'), 'error')
            return redirect(url_for('gas.gas_returns'))
        # 1) пустые — их и возвращают поставщику при обмене
        n = 0
        empties = GasCylinder.query.filter_by(gas_type=gas_type, status='empty').order_by(
            GasCylinder.id.asc()).limit(qty).all()
        for c in empties:
            c.status = 'returned'
            c.side = None
            db.session.add(CylinderLog(
                cylinder_id=c.id, action='return_empty', performed_by=resolve_user_id(),
                notes=f'Returned empty {ret_date}: {reason}'
            ))
            n += 1
        # 2) не хватило пустых — отмечаем фактический возврат остальных
        if n < qty:
            rest = GasCylinder.query.filter(
                GasCylinder.gas_type == gas_type,
                GasCylinder.status.in_(('full', 'scanned'))
            ).order_by(GasCylinder.id.asc()).limit(qty - n).all()
            for c in rest:
                old = c.status
                c.status = 'returned'
                db.session.add(CylinderLog(
                    cylinder_id=c.id, action='return_full', performed_by=resolve_user_id(),
                    notes=f'Returned ({old}) {ret_date}: {reason}'
                ))
                n += 1
        if not safe_commit():
            flash(_('Save failed'), 'error')
        else:
            flash(_('Cylinders returned') + f': {n} ({GAS_LABELS.get(gas_type, gas_type)}), {ret_date}', 'success')
        log_audit('return', 'gas_cylinder', 0, f'{gas_type} x{qty} {ret_date} {reason}')
        return redirect(url_for('gas.gas_returns'))
    returns = (CylinderLog.query.filter(CylinderLog.action.in_(('return_full', 'return_empty')))
               .order_by(CylinderLog.date.desc()).limit(50).all())
    return render_template('gas/returns.html', returns=returns, gas_labels=GAS_LABELS, gas_types=GAS_TYPES)


@bp.route('/report/pdf')
@login_required
@role_required('admin', 'director')
def gas_report_pdf():
    """PDF: сколько заказано / израсходовано / неисправных — по месяцам за период."""
    date_from = request.args.get('from') or request.args.get('date_from') or ''
    date_to = request.args.get('to') or request.args.get('date_to') or ''
    try:
        d_from = datetime.strptime(date_from, '%Y-%m-%d') if date_from else datetime.utcnow().replace(day=1) - timedelta(days=180)
        d_to = datetime.strptime(date_to, '%Y-%m-%d') if date_to else datetime.utcnow() + timedelta(days=1)
    except ValueError:
        d_from = datetime.utcnow().replace(day=1) - timedelta(days=180)
        d_to = datetime.utcnow() + timedelta(days=1)

    # Собираем месяцы
    months = []
    cur = d_from.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_m = d_to.replace(day=1)
    while cur <= end_m:
        m_start = cur
        if cur.month == 12:
            m_end = cur.replace(year=cur.year + 1, month=1)
        else:
            m_end = cur.replace(month=cur.month + 1)
        key = cur.strftime('%Y-%m')
        row = {
            'month': key,
            'ordered_n2': 0, 'ordered_co2': 0, 'ordered_mixed': 0,
            'used_n2': 0, 'used_co2': 0, 'used_mixed': 0,
            'defect_n2': 0, 'defect_co2': 0,
            'received_n2': 0, 'received_co2': 0, 'received_mixed': 0,
        }
        for o in CylinderOrder.query.filter(
            CylinderOrder.ordered_at >= m_start, CylinderOrder.ordered_at < m_end
        ).all():
            q = int(o.quantity or 0)
            if o.gas_type == 'nitrogen':
                row['ordered_n2'] += q
            elif o.gas_type == 'mixed':
                row['ordered_mixed'] += q
            else:
                row['ordered_co2'] += q
        # Расход — уникальные баллоны, опустевшие за месяц
        used_seen = set()
        for log in CylinderLog.query.filter(
            CylinderLog.action.like('%_to_empty%'),
            CylinderLog.date >= m_start, CylinderLog.date < m_end
        ).all():
            if not log.cylinder_id or log.cylinder_id in used_seen:
                continue
            used_seen.add(log.cylinder_id)
            c = db.session.get(GasCylinder, log.cylinder_id)
            if not c:
                continue
            if c.gas_type == 'nitrogen':
                row['used_n2'] += 1
            elif c.gas_type == 'mixed':
                row['used_mixed'] += 1
            else:
                row['used_co2'] += 1
        # «Получено» = доставленные заказы за месяц (по дате доставки)
        for o in CylinderOrder.query.filter(
            CylinderOrder.status == 'delivered',
            db.func.coalesce(CylinderOrder.delivered_at, CylinderOrder.ordered_at) >= m_start,
            db.func.coalesce(CylinderOrder.delivered_at, CylinderOrder.ordered_at) < m_end
        ).all():
            q = int(o.quantity or 0)
            if o.gas_type == 'nitrogen':
                row['received_n2'] += q
            elif o.gas_type == 'mixed':
                row['received_mixed'] += q
            else:
                row['received_co2'] += q
        # неисправные: defect/maintenance на конец месяца
        for gt, field in (('nitrogen', 'defect_n2'), ('co2', 'defect_co2')):
            row[field] = GasCylinder.query.filter(
                GasCylinder.gas_type == gt,
                GasCylinder.status.in_(('defect', 'maintenance')),
            ).count()
        months.append(row)
        cur = m_end

    # PDF
    try:
        FPDF = ensure_fpdf().FPDF
    except Exception:
        return jsonify({'error': 'PDF library unavailable'}), 500
    font_path = find_pdf_font()
    unicode_font = bool(font_path)

    def pdf_text(s):
        s = str(s if s is not None else '')
        # ASCII-замены: N₂/CO₂ → N2/CO2, чтобы в PDF не было «?»
        s = (s.replace('₂', '2').replace('₃', '3')
               .replace('—', '-').replace('–', '-')
               .replace('№', 'No.'))
        return s if unicode_font else s.encode('latin-1', 'replace').decode('latin-1')

    title = _('Gas report')
    period = '%s — %s' % (d_from.strftime('%d.%m.%Y'), (d_to - timedelta(days=1)).strftime('%d.%m.%Y'))

    class GasPDF(FPDF):
        def header(self):
            fn = 'AppFont' if unicode_font else 'Helvetica'
            self.set_font(fn, 'B', 13)
            self.cell(0, 8, pdf_text(title), new_x='LMARGIN', new_y='NEXT')
            self.set_font(fn, '', 9)
            self.cell(0, 5, pdf_text(period), new_x='LMARGIN', new_y='NEXT')
            self.ln(3)

        def footer(self):
            fn = 'AppFont' if unicode_font else 'Helvetica'
            self.set_y(-12)
            self.set_font(fn, '', 7)
            self.set_text_color(128)
            self.cell(0, 8, pdf_text(f'ProMaster — {self.page_no()}/{{nb}}'), align='C')

    pdf = GasPDF(orientation='L', unit='mm', format='A4')
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

    headers = [
        _('Month'), 'N2 ' + _('Ordered'), 'N2 ' + _('Used'),
        'CO2 ' + _('Ordered'), 'CO2 ' + _('Used'),
        'Mix ' + _('Ordered'), 'Mix ' + _('Used'),
        _('Defects'), _('Received')
    ]
    col_w = [28, 28, 28, 28, 28, 28, 28, 28, 28]
    pdf.set_font(base_font, 'B', 8)
    pdf.set_fill_color(44, 62, 80)
    pdf.set_text_color(255)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, pdf_text(h), border=1, fill=True)
    pdf.ln()
    pdf.set_text_color(51)
    pdf.set_font(base_font, '', 8)

    tot = dict(ordered_n2=0, ordered_co2=0, ordered_mixed=0,
               used_n2=0, used_co2=0, used_mixed=0)
    for m in months:
        pdf.cell(col_w[0], 6, pdf_text(m['month']), border=1)
        pdf.cell(col_w[1], 6, pdf_text(m['ordered_n2']), border=1)
        pdf.cell(col_w[2], 6, pdf_text(m['used_n2']), border=1)
        pdf.cell(col_w[3], 6, pdf_text(m['ordered_co2']), border=1)
        pdf.cell(col_w[4], 6, pdf_text(m['used_co2']), border=1)
        pdf.cell(col_w[5], 6, pdf_text(m['ordered_mixed']), border=1)
        pdf.cell(col_w[6], 6, pdf_text(m['used_mixed']), border=1)
        pdf.cell(col_w[7], 6, pdf_text(m['defect_n2'] + m['defect_co2']), border=1)
        pdf.cell(col_w[8], 6, pdf_text(m['received_n2'] + m['received_co2'] + m['received_mixed']), border=1)
        pdf.ln()
        tot['ordered_n2'] += m['ordered_n2']
        tot['ordered_co2'] += m['ordered_co2']
        tot['ordered_mixed'] += m['ordered_mixed']
        tot['used_n2'] += m['used_n2']
        tot['used_co2'] += m['used_co2']
        tot['used_mixed'] += m['used_mixed']

    pdf.set_font(base_font, 'B', 8)
    pdf.cell(col_w[0], 7, pdf_text(_('Total')), border=1)
    pdf.cell(col_w[1], 7, pdf_text(tot['ordered_n2']), border=1)
    pdf.cell(col_w[2], 7, pdf_text(tot['used_n2']), border=1)
    pdf.cell(col_w[3], 7, pdf_text(tot['ordered_co2']), border=1)
    pdf.cell(col_w[4], 7, pdf_text(tot['used_co2']), border=1)
    pdf.cell(col_w[5], 7, pdf_text(tot['ordered_mixed']), border=1)
    pdf.cell(col_w[6], 7, pdf_text(tot['used_mixed']), border=1)
    pdf.cell(col_w[7], 7, pdf_text(''), border=1)
    pdf.cell(col_w[8], 7, pdf_text(''), border=1)

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    fname = f'gas_report_{d_from.strftime("%Y%m%d")}_{d_to.strftime("%Y%m%d")}.pdf'
    return send_file(buf, mimetype='application/pdf', download_name=fname, as_attachment=True)
