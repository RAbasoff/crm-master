"""
Mule device registry routes: list, card, checklist, parts, list PDF.
"""
import re as _re
from datetime import date as _date, datetime

from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required
from flask_babel import gettext as _

from models import (db, EquipmentMaintenance, MuleDevice, MuleDevicePart,
                    VoorraadItem, now_local)
from utils import (ensure_fpdf, find_pdf_font, log_audit, log_system,
                   role_required, safe_commit, safe_float, safe_int)

from . import bp
from . import _mule_devices, _norm_serial, _pdf_response


@bp.route('/equipment/mule-list')
@login_required
@role_required('admin', 'director', 'technician')
def equipment_mule_list():
    """Список Mule: №, название, модель, серийник, состав, история перемещений."""
    return render_template('equipment_mule_list.html', devices=_mule_devices())


@bp.route('/equipment/mule/<int:device_id>')
@login_required
@role_required('admin', 'director', 'technician')
def equipment_mule_card(device_id):
    """Карточка муле: паспорт + история ТО + миграции + дата следующего ТО."""
    dev = MuleDevice.query.get_or_404(device_id)
    recs = (EquipmentMaintenance.query
            .filter(EquipmentMaintenance.record_type.isnot('repair'),
                    db.not_(EquipmentMaintenance.number.like('OJ-%')))
            .order_by(EquipmentMaintenance.date.desc(), EquipmentMaintenance.id.desc())
            .all())
    # записи этого устройства: device_id или совпадение серийника
    ns = dev.normalized_serial()
    mine, timeline = [], []
    for r in recs:
        if r.device_id == dev.id or (not r.device_id and ns and _norm_serial(r.serial) == ns):
            mine.append(r)
    for r in reversed(mine):
        mname = r.machine.name if r.machine else '—'
        if not timeline or timeline[-1][1] != mname:
            timeline.append((r.date, mname))
    last_to = mine[0].date if mine else None
    next_to = None
    for r in mine:
        if r.next_date:
            nxt = r.next_date.date() if isinstance(r.next_date, datetime) else r.next_date
            next_to = nxt
            break
    parts_catalog = (MuleDevicePart.query
                     .filter_by(device_id=dev.id)
                     .order_by(MuleDevicePart.name).all())
    warehouse_items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    return render_template('equipment_mule_card.html', dev=dev, records=mine,
                           timeline=timeline, last_to=last_to, next_to=next_to,
                           parts_catalog=parts_catalog, warehouse_items=warehouse_items)


@bp.route('/equipment/mule-checklist')
@login_required
@role_required('admin', 'director', 'technician')
def equipment_mule_checklist():
    """Чек-лист месяца «ТО за <месяц>»: все муле — сделано/не сделано."""
    ym = (request.args.get('month') or '').strip()
    try:
        y, m = int(ym[:4]), int(ym[5:7])
        if not (1 <= m <= 12):
            raise ValueError
    except Exception:
        t = now_local().date()
        y, m = t.year, t.month
    month_start = _date(y, m, 1)
    month_end = _date(y + (m == 12), (m % 12) + 1, 1)
    devices = _mule_devices()
    recs = (EquipmentMaintenance.query
            .filter(EquipmentMaintenance.record_type.isnot('repair'),
                    db.not_(EquipmentMaintenance.number.like('OJ-%')),
                    EquipmentMaintenance.date >= month_start,
                    EquipmentMaintenance.date < month_end)
            .order_by(EquipmentMaintenance.date.asc()).all())
    # соответствие записей ↔ строк реестра: device_id → серийник → имя;
    # как в карточке, чтобы legacy-записи не терялись
    dev_by_id = {d.id: d for d in MuleDevice.query.all()}

    def _rec_key(r):
        dev = dev_by_id.get(r.device_id) if r.device_id else None
        if dev is None:
            ns = _norm_serial(r.serial)
            if ns:
                for d2 in dev_by_id.values():
                    if d2.normalized_serial() == ns:
                        dev = d2
                        break
        if dev is not None:
            return 'd:%s' % dev.id
        ns = _norm_serial(r.serial)
        if ns:
            return 's:' + ns
        nn = _re.sub(r'\s+', ' ', (r.name or '').strip().lower())
        return ('n:' + nn) if nn else ''

    done_map = {}
    for r in recs:
        key = _rec_key(r)
        if key:
            done_map.setdefault(key, []).append(r)
    rows = []
    for d in devices:
        if d.get('device_id'):
            key = 'd:%s' % d['device_id']
        else:
            ns = _norm_serial(d.get('serial'))
            if ns:
                key = 's:' + ns
            else:
                key = 'n:' + _re.sub(r'\s+', ' ', (d.get('name') or '').strip().lower())
        rows.append({'device': d, 'records': done_map.get(key, []),
                     'done': bool(done_map.get(key))})
    month_label = f'{y}-{m:02d}'
    prev_month = f'{y - 1}-12' if m == 1 else f'{y}-{m - 1:02d}'
    next_month = f'{y + 1}-01' if m == 12 else f'{y}-{m + 1:02d}'
    return render_template('equipment_mule_checklist.html', rows=rows,
                           month=month_label, month_start=month_start,
                           prev_month=prev_month, next_month=next_month,
                           done_count=sum(1 for r in rows if r['done']),
                           total=len(rows))


@bp.route('/equipment/mule/<int:device_id>/parts', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def equipment_mule_part_add(device_id):
    """Закрепить запчасть/расходник за мулой (типовой набор для формы ТО)."""
    dev = MuleDevice.query.get_or_404(device_id)
    name = (request.form.get('name') or '').strip()
    if not name:
        flash(_('Part name required'), 'error')
        return redirect(url_for('equipment.equipment_mule_card', device_id=dev.id))
    p = MuleDevicePart(
        device_id=dev.id,
        warehouse_item_id=safe_int(request.form.get('warehouse_item_id')) or None,
        name=name,
        number=(request.form.get('number') or '').strip(),
        unit=(request.form.get('unit') or 'st').strip(),
        default_qty=safe_float(request.form.get('default_qty'), 1) or 1,
        notes=(request.form.get('notes') or '').strip(),
    )
    db.session.add(p)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('equipment.equipment_mule_card', device_id=dev.id))
    log_audit('create', 'mule_device_part', p.id, f'{dev.name}: {p.name}')
    flash(_('Part attached to mule'), 'success')
    return redirect(url_for('equipment.equipment_mule_card', device_id=dev.id))


@bp.route('/equipment/mule/part/<int:part_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def equipment_mule_part_delete(part_id):
    """Открепить запчасть от муле (только POST)."""
    p = MuleDevicePart.query.get_or_404(part_id)
    dev_id = p.device_id
    db.session.delete(p)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('equipment.equipment_mule_card', device_id=dev_id))
    log_audit('delete', 'mule_device_part', part_id, p.name)
    flash(_('Part detached'), 'success')
    return redirect(url_for('equipment.equipment_mule_card', device_id=dev_id))


@bp.route('/equipment/mule-list/pdf')
@login_required
@role_required('admin', 'director', 'technician')
def equipment_mule_list_pdf():
    """PDF-экспорт списка Mule (?composition=1&history=1)."""
    devices = _mule_devices()
    with_comp = request.args.get('composition', '1') != '0'
    with_hist = request.args.get('history', '0') == '1'
    try:
        fpdf_mod = ensure_fpdf()
        FPDF = fpdf_mod.FPDF
    except Exception as e:
        log_system('error', 'export', f'Mule list PDF failed: fpdf unavailable: {e}')
        flash(_('PDF export is temporarily unavailable'), 'error')
        return redirect(url_for('equipment.equipment_mule_list'))
    font_path = find_pdf_font()
    unicode_font = bool(font_path)
    if unicode_font:
        try:
            _probe = FPDF()
            _probe.add_font('AppFont', '', font_path)
        except Exception:
            unicode_font = False

    def pdf_text(s):
        s = str(s if s is not None else '')
        return s if unicode_font else s.encode('latin-1', 'replace').decode('latin-1')

    date_str = datetime.now().strftime('%d-%m-%Y %H:%M')

    class ListPDF(FPDF):
        def header(self):
            fn = 'AppFont' if unicode_font else 'Helvetica'
            self.set_font(fn, 'B', 12)
            self.cell(0, 8, pdf_text(f"Mule list - ProMaster"), new_x='LMARGIN', new_y='NEXT')
            self.set_font(fn, '', 8)
            self.cell(0, 5, pdf_text(f'{date_str} - {len(devices)}'),
                      new_x='LMARGIN', new_y='NEXT')
            self.ln(2)

        def footer(self):
            fn = 'AppFont' if unicode_font else 'Helvetica'
            self.set_y(-12)
            self.set_font(fn, '', 7)
            self.set_text_color(128)
            self.cell(0, 8, pdf_text(f'ProMaster CRM - {_("page")} {self.page_no()}/{{nb}}'), align='C')

    pdf = ListPDF(orientation='L', unit='mm', format='A4')
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=14)
    if unicode_font:
        pdf.add_font('AppFont', '', font_path)
        pdf.add_font('AppFont', 'B', font_path)
        base = 'AppFont'
    else:
        base = 'Helvetica'
    pdf.add_page()

    # шапка таблицы
    headers = [('#', 10), (_('Name'), 42), (_('Model'), 34), (_('Serial Number'), 32)]
    if with_comp:
        headers.append((_('Components'), 78))
    headers.append((_('Installed on'), 40))
    pdf.set_font(base, 'B', 9)
    pdf.set_fill_color(44, 62, 80)
    pdf.set_text_color(255)
    for label, w in headers:
        pdf.cell(w, 7, pdf_text(label), border=1, fill=True)
    pdf.ln()
    pdf.set_text_color(0)

    for d in devices:
        pdf.set_font(base, '', 8)
        comp_txt = '; '.join(
            f"{c['type']}{(' ' + c['model']) if c['model'] else ''}"
            f"{(' ' + c['size']) if c['size'] else ''} x{c['qty']:g}"
            for c in d['components']) if d['components'] else '—'
        row = [(str(d['no']), 10), (d['name'], 42), (d['model'] or '—', 34), (d['serial'] or '—', 32)]
        if with_comp:
            row.append((comp_txt, 78))
        row.append((d['machine'] or '—', 40))
        # высота строки — по самому длинному тексту
        line_h = 5
        for txt, w in row:
            lines = pdf.multi_cell(w, line_h, pdf_text(txt), border=0, dry_run=True, output='LINES')
            line_h_needed = max(line_h, line_h * max(1, len(lines)))
        h = line_h_needed
        x0, y0 = pdf.get_x(), pdf.get_y()
        if y0 + h > pdf.h - 14:
            pdf.add_page()
            x0, y0 = pdf.get_x(), pdf.get_y()
        x = x0
        for txt, w in row:
            pdf.set_xy(x, y0)
            pdf.multi_cell(w, h, pdf_text(txt), border=1)
            x += w
        pdf.set_xy(x0, y0 + h)
        if with_hist and d['history']:
            hist = ' | '.join(f'{dt.strftime("%d.%m.%Y")}: {mm}' for dt, mm in d['history'])
            pdf.set_font(base, '', 7)
            pdf.set_text_color(90)
            total_w = sum(w for _, w in row)
            hh = 5
            pdf.multi_cell(total_w, hh, pdf_text(f"{_('History')}: {hist}"), border=1)
            pdf.set_text_color(0)
            pdf.set_font(base, '', 8)

    return _pdf_response(pdf, f'mule-list-{datetime.now():%Y%m%d}.pdf')
