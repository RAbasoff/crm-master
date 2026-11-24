"""
Machines blueprint — machine CRUD, parts, consumables, documents, floor plan, reports
"""
import io
import tempfile
import uuid
from io import BytesIO

from flask import Blueprint
from flask_babel import gettext as _
from models import MachineTOChecklist, db
from utils import ensure_fpdf, find_pdf_font, safe_commit

bp = Blueprint('machines', __name__, url_prefix='/machines')


def _build_act_pdf(data, machine, photo_files, number):
    """Собрать компактный PDF акта передачи. Возвращает bytes или None."""
    try:
        FPDF = ensure_fpdf().FPDF
    except Exception:
        return None
    font_path = find_pdf_font()
    uni = bool(font_path)

    def t(s):
        s = '' if s is None else str(s)
        s = s.replace('₂', '2').replace('—', '-').replace('–', '-')
        return s if uni else s.encode('latin-1', 'replace').decode('latin-1')

    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=14)
    fn = 'AppFont' if uni else 'Helvetica'
    if uni:
        try:
            pdf.add_font('AppFont', '', font_path)
            pdf.add_font('AppFont', 'B', font_path)
        except Exception:
            uni = False
            fn = 'Helvetica'
    pdf.add_page()
    pdf.set_font(fn, 'B', 14)
    pdf.cell(0, 8, t('TRANSFER ACT OF MATERIAL ASSETS'), new_x='LMARGIN', new_y='NEXT')
    pdf.set_font(fn, '', 10)
    pdf.cell(0, 6, t(f"Act No: {number}    Date: {data.get('date') or ''}"), new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 5, t(f"Location: {data.get('location') or ''}"), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(3)

    pdf.set_font(fn, 'B', 11)
    pdf.cell(0, 6, t('MACHINE'), new_x='LMARGIN', new_y='NEXT')
    pdf.set_font(fn, '', 10)
    pdf.cell(0, 5, t(machine.name), new_x='LMARGIN', new_y='NEXT')
    spec = ' · '.join([x for x in [machine.machine_type, machine.manufacturer, machine.serial_number] if x])
    pdf.cell(0, 5, t(spec), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(2)

    pdf.set_font(fn, 'B', 11)
    pdf.cell(0, 6, t('PARTIES'), new_x='LMARGIN', new_y='NEXT')
    pdf.set_font(fn, '', 10)
    pdf.cell(0, 5, t(f"Giver: {data.get('giver') or ''} / {data.get('giver_pos') or ''}"), new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 5, t(f"Receiver: {data.get('receiver') or ''} / {data.get('receiver_pos') or ''}"), new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 5, t(f"Reason: {data.get('reason') or ''}"), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(2)

    pdf.set_font(fn, 'B', 11)
    pdf.cell(0, 6, t('ITEMS'), new_x='LMARGIN', new_y='NEXT')
    pdf.set_font(fn, 'B', 9)
    pdf.set_fill_color(44, 62, 80)
    pdf.set_text_color(255)
    for i, w in enumerate([10, 70, 45, 18, 15]):
        pdf.cell(w, 7, t(['#', 'Name', 'Spec', 'Qty', 'Unit'][i]), border=1, fill=True)
    pdf.ln()
    pdf.set_text_color(51)
    pdf.set_font(fn, '', 8)
    items = data.get('items') or []
    for i, it in enumerate(items, 1):
        pdf.cell(10, 6, t(i), border=1)
        pdf.cell(70, 6, t(str(it.get('name', ''))[:40]), border=1)
        pdf.cell(45, 6, t(str(it.get('spec', ''))[:30]), border=1)
        pdf.cell(18, 6, t(it.get('qty', '')), border=1)
        pdf.cell(15, 6, t(it.get('unit', '')), border=1)
        pdf.ln()
    pdf.ln(3)

    if data.get('notes'):
        pdf.set_font(fn, 'B', 11)
        pdf.cell(0, 6, t('NOTES'), new_x='LMARGIN', new_y='NEXT')
        pdf.set_font(fn, '', 9)
        pdf.multi_cell(0, 5, t(data.get('notes')))
        pdf.ln(2)

    if photo_files:
        pdf.add_page()
        pdf.set_font(fn, 'B', 12)
        pdf.cell(0, 8, t('PHOTOS (embedded in this Act)'), new_x='LMARGIN', new_y='NEXT')
        pdf.ln(2)
        col = 0
        y0 = 20.0
        for f in photo_files:
            try:
                raw = f.read()
                if not raw or len(raw) < 100:
                    continue
                # определяем тип для fpdf
                name = (f.filename or '').lower()
                fmt = 'JPEG' if name.endswith(('.jpg', '.jpeg')) or raw[:2] == b'\xff\xd8' else 'PNG'
                if col == 0:
                    y0 = pdf.get_y()
                    if y0 > 200:
                        pdf.add_page()
                        y0 = 20.0
                x = 12 + col * 62
                try:
                    pdf.image(BytesIO(raw), x=x, y=y0, w=58, h=42)
                except Exception:
                    try:
                        pdf.image(BytesIO(raw), x=x, y=y0, w=58)
                    except Exception as e2:
                        print('PDF IMG SKIP', e2)
                        continue
                col += 1
                if col >= 3:
                    col = 0
                    pdf.set_y(y0 + 44)
            except Exception as e:
                print('PDF PHOTO ERR', e)
                continue
    pdf.ln(8)
    pdf.set_font(fn, '', 9)
    pdf.cell(0, 6, t('Giver _______________     Receiver _______________     Date _______________'), new_x='LMARGIN', new_y='NEXT')
    try:
        out = pdf.output()
        return bytes(out) if out else None
    except Exception as e:
        print('PDF OUTPUT ERR', e)
        try:
            buf = io.BytesIO()
            pdf.output(buf)
            return buf.getvalue()
        except Exception as e2:
            print('PDF OUTPUT ERR2', e2)
            return None


TO_PERIODS = {
    'daily': 'Daily',
    'weekly': 'Weekly',
    'biweekly': 'Every 2 weeks',
    'monthly': 'Monthly',
    'quarterly': 'Quarterly',
    'semiannual': 'Semiannual',
    'yearly': 'Yearly',
}

DEFAULT_TO_CHECKLISTS = {
    'daily': [
        'Visual inspection of equipment',
        'Check oil / lubricant level',
        'Check for leaks (air, gas, water)',
        'Clean work area',
    ],
    'weekly': [
        'Clean filters',
        'Check bolts and fasteners',
        'Check drive belts / chains',
        'Lubricate bearings',
    ],
    'biweekly': [
        'Check knife / tool wear',
        'Check gaps and adjustments',
        'Check pneumatics / valves',
    ],
    'monthly': [
        'Full lubrication of assemblies',
        'Check electrical connections',
        'Calibrate sensors / instruments',
        'Check safety and emergency buttons',
    ],
    'quarterly': [
        'Replace consumables per schedule',
        'Check hydraulics / pneumatics',
        'OH act + photos',
    ],
    'semiannual': [
        'Replace oils / greases per schedule',
        'Check geometry / alignment',
        'Diagnose electric motors',
    ],
    'yearly': [
        'Full OH per manufacturer manual',
        'Replace worn assemblies',
        'Update documentation / acts',
    ],
}


def ensure_machine_to_checklists(machine_id):
    """Заполнить чек-листы ТО (переводятся по языку пользователя при создании)."""
    if MachineTOChecklist.query.filter_by(machine_id=machine_id).count() > 0:
        return
    for period, items in DEFAULT_TO_CHECKLISTS.items():
        for i, text in enumerate(items, 1):
            try:
                label = _(text)
            except Exception:
                label = text
            db.session.add(MachineTOChecklist(
                machine_id=machine_id, period=period, text=label, sort_order=i, is_active=True
            ))
    try:
        safe_commit()
    except Exception:
        db.session.rollback()


def _optf(value):
    """Optional float: empty/invalid -> None (0 stays 0)."""
    if value is None or str(value).strip() == '':
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# sub-modules attach their routes to bp on import; must run after bp is defined
from . import consumables, crud, parts, reports  # noqa: E402,F401
