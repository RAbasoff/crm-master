"""
api blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json
import qrcode

from models import (db, Contractor, FaultReport, Machine, Monteur, Opdracht, TechnicalWorkOrder, User, Verantwoordelijke, VoorraadItem)
from utils import role_required, sanitize_like, translate_text
from sqlalchemy import func, case

bp = Blueprint('api', __name__)

@bp.route('/machines/<int:machine_id>/qr')
@login_required
def machine_qr_generate(machine_id):
    """Generate QR code for a machine — links to /mobile/qr/<id>."""
    m = Machine.query.get_or_404(machine_id)
    url = request.host_url.rstrip('/') + url_for('mobile.machine_qr_page', machine_id=m.id)
    qr = qrcode.QRCode(version=None, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'QR_{m.name.replace(" ", "_")}.png')


@bp.route('/machines/qr/batch')
@login_required
@role_required('admin', 'director')
def machines_qr_batch():
    """Generate printable page with QR codes for all active machines."""
    machines = Machine.query.filter_by(status='active').order_by(Machine.name).all()
    return render_template('machines_qr_batch.html', machines=machines)


@bp.route('/api/machines/search')
@login_required
def api_machines_search():
    """Search machines by name, serial number, or barcode"""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    machines = Machine.query.filter(
        db.or_(
            Machine.name.ilike(f'%{sanitize_like(q)}%'),
            Machine.serial_number.ilike(f'%{sanitize_like(q)}%'),
            Machine.machine_type.ilike(f'%{sanitize_like(q)}%')
        )
    ).limit(10).all()
    return jsonify([{
        'id': m.id,
        'name': m.name,
        'type': m.machine_type or '',
        'serial': m.serial_number or '',
        'status': m.status
    } for m in machines])


@bp.route('/api/machine/<int:machine_id>/faults')
@login_required
def api_machine_faults(machine_id):
    """Return faults and stats for a machine (used by floor plan)"""
    m = Machine.query.get_or_404(machine_id)
    faults = FaultReport.query.filter_by(machine_id=m.id).order_by(FaultReport.created_at.desc()).limit(10).all()

    # Single aggregated query for stats
    stats = db.session.query(
        func.count().label('total'),
        func.sum(case((FaultReport.status.in_(['open', 'accepted', 'in_progress']), 1), else_=0)).label('open'),
        func.sum(case((FaultReport.status == 'resolved', 1), else_=0)).label('resolved'),
        func.sum(case((FaultReport.priority == 'critical', 1), else_=0)).label('critical'),
    ).filter(FaultReport.machine_id == m.id).first()

    faults_data = [{
        'id': f.id,
        'title': f.title or '',
        'status': f.status or '',
        'priority': f.priority or '',
        'date': f.created_at.strftime('%d-%m-%Y'),
    } for f in faults]

    return jsonify({
        'total': stats.total if stats else 0,
        'open': int(stats.open or 0) if stats else 0,
        'resolved': int(stats.resolved or 0) if stats else 0,
        'critical': int(stats.critical or 0) if stats else 0,
        'faults': faults_data
    })


@bp.route('/api/machines/faults-batch')
@login_required
def api_machines_faults_batch():
    """Return fault stats for all machines in one query (replaces N+1 calls on floor plan)."""
    stats = db.session.query(
        FaultReport.machine_id,
        func.count().label('total'),
        func.sum(case((FaultReport.status.in_(['open', 'accepted', 'in_progress']), 1), else_=0)).label('open'),
        func.sum(case((FaultReport.status == 'resolved', 1), else_=0)).label('resolved'),
        func.sum(case((FaultReport.priority == 'critical', 1), else_=0)).label('critical'),
    ).group_by(FaultReport.machine_id).all()
    result = {}
    for row in stats:
        result[str(row.machine_id)] = {
            'total': row.total,
            'open': int(row.open or 0),
            'resolved': int(row.resolved or 0),
            'critical': int(row.critical or 0),
        }
    return jsonify(result)


@bp.route('/api/machines/<int:machine_id>/qr')
@login_required
def machine_qr(machine_id):
    m = Machine.query.get_or_404(machine_id)
    data = {
        'type': 'machine',
        'id': m.id,
        'name': m.name,
        'serial': m.serial_number or '',
        'manufacturer': m.manufacturer or '',
        'location': m.installation_location or '',
        'section': m.section.name if m.section else ''
    }
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(json.dumps(data, ensure_ascii=False))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'QR_{m.name}.png')


@bp.route('/api/machines/<int:machine_id>/barcode')
@login_required
def machine_barcode(machine_id):
    from PIL import Image, ImageDraw, ImageFont
    m = Machine.query.get_or_404(machine_id)
    code_str = f"M{m.id:05d}"
    
    try:
        import barcode
        from barcode.writer import ImageWriter
        code128 = barcode.get('code128', code_str, writer=ImageWriter())
        buf = io.BytesIO()
        code128.write(buf, options={'module_width': 0.3, 'module_height': 8, 'font_size': 8, 'text_distance': 2, 'quiet_zone': 2})
        buf.seek(0)
        return send_file(buf, mimetype='image/png', download_name=f'BAR_{m.name}.png')
    except ImportError:
        pass
    
    # Fallback: generate with Pillow
    def code128_encode(text):
        chars = ' !"#$%&\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~'
        codes = [104]
        checksum = 104
        for i, c in enumerate(text):
            if c in chars:
                val = chars.index(c) + 32
                codes.append(val)
                checksum += val * (i + 1)
        codes.append(checksum % 103)
        codes.append(106)
        patterns = [
            '11011001100','11001101100','11001100110','10010011000','10010001100',
            '10001001100','10011001000','10011000100','10001100100','11001001000',
            '11001000100','11000100100','10110011100','10011011100','10011001110',
            '10111001100','10011101100','10011100110','11001110010','11001011100',
            '11001001110','11011100100','11001110100','11101101110','11101001100',
            '11100101100','11100100110','11101100100','11100110100','11100110010',
            '11011011000','11011000110','11000110110','10100011000','10001011000',
            '10001000110','10110001000','10001101000','10001100010','11010001000',
            '11000101000','11000100010','10110111000','10110001110','10001101110',
            '10111011000','10111000110','10001110110','11101110110','11010001110',
            '11000101110','11011101000','11011100010','11011101110','11101011000',
            '11101000110','11100010110','11101101000','11101100010','11100011010',
            '11101111010','11001000010','11110001010','10100110000','10100001100',
            '10010110000','10010000110','10000101100','10000100110','10110010000',
            '10110000100','10011010000','10011000010','10000110100','10000110010',
            '11000010010','11001010000','11110111010','11000010100','10001111010',
            '10100111100','10010111100','10010011110','10111100100','10011110100',
            '10011110010','11110100100','11110010100','11110010010','11011011110',
            '11011110110','11110110110','10101111000','10100011110','10001011110',
            '10111101000','10111100010','11110101000','11110100010','10111011110',
            '10111101110','11101011110','11110101110','11010000100','11010010000',
            '11010011100','1100011101011'
        ]
        bars = []
        for c in codes:
            if c < len(patterns):
                bars.append(patterns[c])
        return bars
    
    bar_width = 2
    height = 60
    text_height = 16
    bars = code128_encode(code_str)
    total_width = sum(len(b) for b in bars) * bar_width + 20
    img = Image.new('RGB', (total_width, height + text_height + 4), 'white')
    draw = ImageDraw.Draw(img)
    x = 10
    for bar_pattern in bars:
        for bit in bar_pattern:
            if bit == '1':
                draw.rectangle([x, 0, x + bar_width - 1, height - 1], fill='black')
            x += bar_width
    try:
        font = ImageFont.truetype("arial.ttf", 12)
    except (OSError, IOError):
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), code_str, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((total_width - tw) // 2, height + 2), code_str, fill='black', font=font)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'BAR_{m.name}.png')


@bp.route('/machines/<int:machine_id>/qr-label')
@login_required
def machine_qr_label(machine_id):
    m = Machine.query.get_or_404(machine_id)
    return render_template('machine_qr_label.html', machine=m)


@bp.route('/api/warehouse/search')
@login_required
def warehouse_search():
    q = request.args.get('q', '')
    if not q:
        items = VoorraadItem.query.order_by(VoorraadItem.naam).limit(50).all()
    else:
        items = VoorraadItem.query.filter(
            (VoorraadItem.naam.ilike(f'%{sanitize_like(q)}%')) | 
            (VoorraadItem.categorie.ilike(f'%{sanitize_like(q)}%'))
        ).order_by(VoorraadItem.naam).all()
    return jsonify([{
        'id': i.id, 'name': i.naam, 'category': i.categorie or '',
        'quantity': i.hoeveelheid, 'unit': i.eenheid, 'price': i.prijs,
        'available': i.hoeveelheid > 0
    } for i in items])


@bp.route('/api/warehouse/qr/<int:item_id>')
@login_required
def warehouse_qr(item_id):
    item = VoorraadItem.query.get_or_404(item_id)
    data = {
        'type': 'warehouse_item',
        'id': item.id,
        'name': item.naam,
        'category': item.categorie or '',
        'location': item.locatie or '',
        'quantity': item.hoeveelheid,
        'unit': item.eenheid
    }
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(json.dumps(data, ensure_ascii=False))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'QR_{item.naam}.png')


@bp.route('/api/warehouse/barcode/<int:item_id>')
@login_required
def warehouse_barcode(item_id):
    from PIL import Image, ImageDraw, ImageFont
    item = VoorraadItem.query.get_or_404(item_id)
    code_str = item.supplier_part_number if item.supplier_part_number else f"W{item.id:05d}"
    
    try:
        import barcode
        from barcode.writer import ImageWriter
        code128 = barcode.get('code128', code_str, writer=ImageWriter())
        buf = io.BytesIO()
        code128.write(buf, options={'module_width': 0.3, 'module_height': 8, 'font_size': 8, 'text_distance': 2, 'quiet_zone': 2})
        buf.seek(0)
        return send_file(buf, mimetype='image/png', download_name=f'BAR_{item.naam}.png')
    except ImportError:
        # Fallback: generate barcode with Pillow using Code128B encoding
        pass
    
    # Code128 encoding table (subset for alphanumeric)
    def code128_encode(text):
        # Code128B character set
        chars = ' !"#$%&\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~'
        # Start code B = 104, Stop = 106
        codes = [104]  # Start B
        checksum = 104
        for i, c in enumerate(text):
            if c in chars:
                val = chars.index(c) + 32
                codes.append(val)
                checksum += val * (i + 1)
            else:
                codes.append(0)  # fallback
        codes.append(checksum % 103)
        codes.append(106)  # Stop
        
        # Code128 bar patterns (widths of bars and spaces)
        patterns = [
            '11011001100','11001101100','11001100110','10010011000','10010001100',
            '10001001100','10011001000','10011000100','10001100100','11001001000',
            '11001000100','11000100100','10110011100','10011011100','10011001110',
            '10111001100','10011101100','10011100110','11001110010','11001011100',
            '11001001110','11011100100','11001110100','11101101110','11101001100',
            '11100101100','11100100110','11101100100','11100110100','11100110010',
            '11011011000','11011000110','11000110110','10100011000','10001011000',
            '10001000110','10110001000','10001101000','10001100010','11010001000',
            '11000101000','11000100010','10110111000','10110001110','10001101110',
            '10111011000','10111000110','10001110110','11101110110','11010001110',
            '11000101110','11011101000','11011100010','11011101110','11101011000',
            '11101000110','11100010110','11101101000','11101100010','11100011010',
            '11101111010','11001000010','11110001010','10100110000','10100001100',
            '10010110000','10010000110','10000101100','10000100110','10110010000',
            '10110000100','10011010000','10011000010','10000110100','10000110010',
            '11000010010','11001010000','11110111010','11000010100','10001111010',
            '10100111100','10010111100','10010011110','10111100100','10011110100',
            '10011110010','11110100100','11110010100','11110010010','11011011110',
            '11011110110','11110110110','10101111000','10100011110','10001011110',
            '10111101000','10111100010','11110101000','11110100010','10111011110',
            '10111101110','11101011110','11110101110','11010000100','11010010000',
            '11010011100','1100011101011'
        ]
        bars = []
        for c in codes:
            if c < len(patterns):
                bars.append(patterns[c])
        return bars
    
    # Generate image
    bar_width = 2
    height = 60
    text_height = 16
    total_height = height + text_height + 4
    
    bars = code128_encode(code_str)
    total_width = sum(len(b) for b in bars) * bar_width + 20  # margins
    
    img = Image.new('RGB', (total_width, total_height), 'white')
    draw = ImageDraw.Draw(img)
    
    x = 10
    for bar_pattern in bars:
        for i, bit in enumerate(bar_pattern):
            if bit == '1':
                draw.rectangle([x, 0, x + bar_width - 1, height - 1], fill='black')
            x += bar_width
    
    # Draw text
    try:
        font = ImageFont.truetype("arial.ttf", 12)
    except (OSError, IOError):
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), code_str, font=font)
    text_width = bbox[2] - bbox[0]
    text_x = (total_width - text_width) // 2
    draw.text((text_x, height + 2), code_str, fill='black', font=font)
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'BAR_{item.naam}.png')


@bp.route('/api/warehouse/scan', methods=['POST'])
@login_required
def warehouse_scan():
    data = request.get_json()
    code = data.get('code', '')
    try:
        parsed = json.loads(code)
        if parsed.get('type') == 'warehouse_item':
            item = VoorraadItem.query.get(parsed.get('id'))
            if item:
                return jsonify({
                    'found': True, 'id': item.id, 'name': item.naam,
                    'category': item.categorie, 'quantity': item.hoeveelheid,
                    'unit': item.eenheid, 'price': item.prijs,
                    'available': item.hoeveelheid > 0
                })
    except (json.JSONDecodeError, AttributeError):
        pass
    # Search by supplier part number (barcode), then by name
    item = VoorraadItem.query.filter(
        db.or_(
            VoorraadItem.supplier_part_number == code,
            VoorraadItem.naam.ilike(f'%{sanitize_like(code)}%')
        )
    ).first()
    if item:
        return jsonify({
            'found': True, 'id': item.id, 'name': item.naam,
            'category': item.categorie, 'quantity': item.hoeveelheid,
            'unit': item.eenheid, 'price': item.prijs,
            'available': item.hoeveelheid > 0
        })
    return jsonify({'found': False, 'code': code})


@bp.route('/api/translate', methods=['POST'])
@login_required
def api_translate():
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400
    text = data['text']
    target = data.get('target', g.lang)
    translated = translate_text(text, target)
    return jsonify({'translated': translated, 'original': text})


@bp.route('/api/search')
@login_required
def api_search():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'results': []})
    
    results = []
    limit = 20
    
    # Search machines
    machines = Machine.query.filter(
        (Machine.name.ilike(f'%{sanitize_like(q)}%')) | 
        (Machine.serial_number.ilike(f'%{sanitize_like(q)}%')) |
        (Machine.description.ilike(f'%{sanitize_like(q)}%'))
    ).limit(5).all()
    for m in machines:
        results.append({
            'type': 'machine',
            'icon': '⚙️',
            'title': m.name,
            'subtitle': f'{m.machine_type or ""} {m.serial_number or ""}'.strip(),
            'url': f'/machines/{m.id}',
            'status': m.status
        })
    
    # Search faults
    faults = FaultReport.query.filter(
        (FaultReport.title.ilike(f'%{sanitize_like(q)}%')) | 
        (FaultReport.description.ilike(f'%{sanitize_like(q)}%'))
    ).limit(5).all()
    for f in faults:
        results.append({
            'type': 'fault',
            'icon': '⚠️',
            'title': f.title,
            'subtitle': f'{getattr(f, "target_name", None) or (f.machine.name if f.machine else "")} - {f.priority}',
            'url': f'/faults/{f.id}',
            'status': f.status
        })
    
    # Search work orders
    orders = Opdracht.query.filter(
        (Opdracht.nummer.ilike(f'%{sanitize_like(q)}%')) | 
        (Opdracht.apparaat.ilike(f'%{sanitize_like(q)}%')) |
        (Opdracht.model.ilike(f'%{sanitize_like(q)}%')) |
        (Opdracht.serienummer.ilike(f'%{sanitize_like(q)}%'))
    ).limit(5).all()
    for o in orders:
        results.append({
            'type': 'order',
            'icon': '📋',
            'title': f'{o.nummer} - {o.apparaat}',
            'subtitle': f'{o.model or ""} | {o.verantwoordelijke.naam if o.verantwoordelijke else ""}',
            'url': f'/orders/{o.id}',
            'status': o.status
        })
    
    # Search warehouse
    items = VoorraadItem.query.filter(
        (VoorraadItem.naam.ilike(f'%{sanitize_like(q)}%')) | 
        (VoorraadItem.categorie.ilike(f'%{sanitize_like(q)}%')) |
        (VoorraadItem.locatie.ilike(f'%{sanitize_like(q)}%'))
    ).limit(5).all()
    for i in items:
        results.append({
            'type': 'warehouse',
            'icon': '📦',
            'title': i.naam,
            'subtitle': f'{i.categorie or ""} | {i.hoeveelheid} {i.eenheid}',
            'url': f'/warehouse/{i.id}/edit',
            'status': 'low' if i.hoeveelheid <= i.minimum else 'ok'
        })
    
    # Search clients/responsible
    clients = Verantwoordelijke.query.filter(
        (Verantwoordelijke.naam.ilike(f'%{sanitize_like(q)}%')) | 
        (Verantwoordelijke.company.ilike(f'%{sanitize_like(q)}%')) |
        (Verantwoordelijke.telefoon.ilike(f'%{sanitize_like(q)}%')) |
        (Verantwoordelijke.email.ilike(f'%{sanitize_like(q)}%'))
    ).limit(5).all()
    for c in clients:
        results.append({
            'type': 'client',
            'icon': '👤',
            'title': c.naam,
            'subtitle': f'{c.company or ""} {c.telefoon or ""}'.strip(),
            'url': f'/responsible/{c.id}',
            'status': 'active'
        })
    
    # Search workers
    workers = Monteur.query.filter(
        (Monteur.naam.ilike(f'%{sanitize_like(q)}%')) | 
        (Monteur.specialisatie.ilike(f'%{sanitize_like(q)}%'))
    ).limit(3).all()
    for w in workers:
        results.append({
            'type': 'worker',
            'icon': '🔧',
            'title': w.naam,
            'subtitle': w.specialisatie or '',
            'url': f'/workers/{w.id}/edit',
            'status': 'active' if w.actief else 'inactive'
        })
    
    # Search contractors
    contractors = Contractor.query.filter(
        (Contractor.company_name.ilike(f'%{sanitize_like(q)}%')) | 
        (Contractor.service_type.ilike(f'%{sanitize_like(q)}%'))
    ).limit(3).all()
    for c in contractors:
        results.append({
            'type': 'contractor',
            'icon': '🏢',
            'title': c.company_name,
            'subtitle': c.service_type or '',
            'url': f'/contractors/{c.id}',
            'status': 'active' if c.is_active else 'inactive'
        })
    
    # Search TWO
    twos = TechnicalWorkOrder.query.filter(
        (TechnicalWorkOrder.number.ilike(f'%{sanitize_like(q)}%')) | 
        (TechnicalWorkOrder.description.ilike(f'%{sanitize_like(q)}%'))
    ).limit(3).all()
    for t in twos:
        results.append({
            'type': 'two',
            'icon': '🔧',
            'title': t.number,
            'subtitle': t.description[:50] if t.description else '',
            'url': f'/two/{t.id}',
            'status': t.status
        })
    
    # Search users (admin only)
    if current_user.has_role('admin'):
        users = User.query.filter(
            (User.username.ilike(f'%{sanitize_like(q)}%')) | 
            (User.display_name.ilike(f'%{sanitize_like(q)}%')) |
            (User.first_name.ilike(f'%{sanitize_like(q)}%')) |
            (User.last_name.ilike(f'%{sanitize_like(q)}%'))
        ).limit(3).all()
        for u in users:
            results.append({
                'type': 'user',
                'icon': '👥',
                'title': u.display_name or u.username,
                'subtitle': f'{u.role} | {u.username}',
                'url': f'/users/{u.id}',
                'status': 'active' if u.is_active_user else 'inactive'
            })
    
    return jsonify({'results': results[:limit], 'total': len(results)})
