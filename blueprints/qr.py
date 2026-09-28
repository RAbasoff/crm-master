"""
qr blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json
import qrcode

from models import (db, Opdracht, Verantwoordelijke)
from utils import sanitize_like

bp = Blueprint('qr', __name__)

@bp.route('/qr/scan')
@login_required
def qr_scan():
    return render_template('qr_scan.html')


@bp.route('/warehouse/scan')
@login_required
def warehouse_scan_page():
    """Dedicated warehouse barcode scanner"""
    return render_template('warehouse_scan.html')


@bp.route('/qr/generate/<int:order_id>')
@login_required
def qr_generate(order_id):
    order = Opdracht.query.get_or_404(order_id)
    data = {
        'type': 'opdracht',
        'id': order.id,
        'nummer': order.nummer,
        'apparaat': order.apparaat,
        'model': order.model or '',
        'serienummer': order.serienummer or '',
        'klant': order.verantwoordelijke.naam if order.verantwoordelijke else '',
        'status': order.status
    }
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(json.dumps(data, ensure_ascii=False))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'QR_{order.nummer}.png')


@bp.route('/qr/product', methods=['POST'])
@login_required
def qr_product():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Geen data'}), 400
    klant = None
    if data.get('klant'):
        klant = Verantwoordelijke.query.filter(Verantwoordelijke.naam.ilike(f"%{sanitize_like(data['klant'])}%")).first()
    if not klant and data.get('telefoon'):
        klant = Verantwoordelijke.query.filter(Verantwoordelijke.telefoon.like(f"%{sanitize_like(data['telefoon'])}%")).first()
    result = {
        'apparaat': data.get('apparaat', ''),
        'model': data.get('model', ''),
        'serienummer': data.get('serienummer', ''),
        'probleem': data.get('probleem', ''),
        'klant_id': klant.id if klant else None,
        'klant_naam': klant.naam if klant else data.get('klant', '')
    }
    return jsonify(result)


@bp.route('/api/qr/lookup', methods=['POST'])
@login_required
def qr_lookup():
    data = request.get_json()
    code = data.get('code', '')
    try:
        parsed = json.loads(code)
        if parsed.get('type') == 'opdracht':
            order = Opdracht.query.get(parsed.get('id'))
            if order:
                return jsonify({
                    'found': True, 'type': 'opdracht',
                    'id': order.id, 'nummer': order.nummer,
                    'apparaat': order.apparaat, 'model': order.model,
                    'klant': order.verantwoordelijke.naam if order.verantwoordelijke else '', 'status': order.status,
                    'totaal': order.totaal
                })
    except (json.JSONDecodeError, AttributeError):
        pass
    order = Opdracht.query.filter_by(serienummer=code).order_by(Opdracht.aangemaakt.desc()).first()
    if order:
        return jsonify({
            'found': True, 'type': 'serienummer',
            'id': order.id, 'nummer': order.nummer,
            'apparaat': order.apparaat, 'model': order.model,
            'klant': order.verantwoordelijke.naam if order.verantwoordelijke else '', 'status': order.status
        })
    klant = Verantwoordelijke.query.filter(Verantwoordelijke.telefoon.like(f"%{sanitize_like(code)}%")).first()
    if klant:
        orders = Opdracht.query.filter_by(responsible_id=klant.id).order_by(Opdracht.aangemaakt.desc()).limit(5).all()
        return jsonify({
            'found': True, 'type': 'klant',
            'klant': klant.naam, 'telefoon': klant.telefoon,
            'orders': [{'id': o.id, 'nummer': o.nummer, 'apparaat': o.apparaat, 'status': o.status} for o in orders]
        })
    return jsonify({'found': False, 'code': code})
