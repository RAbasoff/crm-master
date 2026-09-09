"""
Electricity blueprint — electrical cabinets, breakers, schematic, switch log, documents
"""
import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename

from models import db, ElectricalCabinet, CircuitBreaker, ElectricalSwitchLog, ElectricalDocument
from utils import role_required

bp = Blueprint('electricity', __name__, url_prefix='/electricity')


@bp.before_request
def check_electricity_access():
    """Temporary lockdown — admin only while maintenance is in progress."""
    try:
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        if not current_user.has_role('admin'):
            return render_template('electricity_locked.html'), 403
    except Exception:
        return redirect(url_for('login'))


@bp.route('/')
@login_required
def electricity_list():
    cabinets = ElectricalCabinet.query.filter_by(is_active=True).order_by(ElectricalCabinet.name).all()
    cab_map = {c.id: c for c in cabinets}
    all_breakers = [b for cab in cabinets for b in cab.breakers]
    stats = {
        'total': len(all_breakers),
        'on': sum(1 for b in all_breakers if b.status == 'on'),
        'off': sum(1 for b in all_breakers if b.status == 'off'),
    }
    panels = [
        {'name': 'Panel 1 — VRACHTWAGEN / VERPAKKING / WERKVOORBEREIDING',
         'cabinet_ids': [3, 4, 5, 6]},
        {'name': 'Panel 2 — CANALIS / KOUDE / WATER / TECHNIEKEN',
         'cabinet_ids': [7, 8, 9, 10, 11]},
        {'name': 'Panel 3 — LOGISTIEK',
         'cabinet_ids': [12, 13]},
        {'name': 'Panel 4 — DISTRIBUTIE / UGL / ALGEMEEN / WB',
         'cabinet_ids': [14, 15, 16, 17, 18]},
    ]
    for p in panels:
        p['cabinets'] = [cab_map[cid] for cid in p['cabinet_ids'] if cid in cab_map]
        p['total_breakers'] = sum(len(c.breakers) for c in p['cabinets'])
        p['on_count'] = sum(1 for c in p['cabinets'] for b in c.breakers if b.status == 'on')
    switch_logs = ElectricalSwitchLog.query.order_by(ElectricalSwitchLog.created_at.desc()).limit(20).all()
    documents = ElectricalDocument.query.order_by(ElectricalDocument.uploaded_at.desc()).all()
    return render_template('electricity.html', cabinets=cabinets, stats=stats, panels=panels,
                           switch_logs=switch_logs, documents=documents)


@bp.route('/cabinet/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def cabinet_new():
    if request.method == 'POST':
        c = ElectricalCabinet(
            name=request.form['name'],
            location=request.form.get('location', ''),
            cabinet_type=request.form.get('cabinet_type', 'distribution'),
            description=request.form.get('description', ''),
            manufacturer=request.form.get('manufacturer', ''),
            serial_number=request.form.get('serial_number', ''),
            main_fuse_amps=int(request.form['main_fuse_amps']) if request.form.get('main_fuse_amps') else None,
            voltage=request.form.get('voltage', '400V/230V'),
            schematic_x=int(request.form.get('schematic_x', 0)),
            schematic_y=int(request.form.get('schematic_y', 0))
        )
        if 'photo' in request.files and request.files['photo'].filename:
            filename = secure_filename(f"cabinet_{request.files['photo'].filename}")
            request.files['photo'].save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
            c.photo = filename
        db.session.add(c)
        db.session.commit()
        flash(_('Cabinet created'), 'success')
        return redirect(url_for('electricity.cabinet_detail', cabinet_id=c.id))
    return render_template('cabinet_form.html', cabinet=None)


@bp.route('/cabinet/<int:cabinet_id>')
@login_required
def cabinet_detail(cabinet_id):
    c = ElectricalCabinet.query.get_or_404(cabinet_id)
    return render_template('cabinet_detail.html', cabinet=c)


@bp.route('/cabinet/<int:cabinet_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def cabinet_edit(cabinet_id):
    c = ElectricalCabinet.query.get_or_404(cabinet_id)
    if request.method == 'POST':
        c.name = request.form['name']
        c.location = request.form.get('location', '')
        c.cabinet_type = request.form.get('cabinet_type', 'distribution')
        c.description = request.form.get('description', '')
        c.manufacturer = request.form.get('manufacturer', '')
        c.serial_number = request.form.get('serial_number', '')
        c.main_fuse_amps = int(request.form['main_fuse_amps']) if request.form.get('main_fuse_amps') else None
        c.voltage = request.form.get('voltage', '400V/230V')
        c.schematic_x = int(request.form.get('schematic_x', 0))
        c.schematic_y = int(request.form.get('schematic_y', 0))
        if 'photo' in request.files and request.files['photo'].filename:
            filename = secure_filename(f"cabinet_{request.files['photo'].filename}")
            request.files['photo'].save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
            c.photo = filename
        db.session.commit()
        flash(_('Cabinet updated'), 'success')
        return redirect(url_for('electricity.cabinet_detail', cabinet_id=c.id))
    return render_template('cabinet_form.html', cabinet=c)


@bp.route('/cabinet/<int:cabinet_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def cabinet_delete(cabinet_id):
    c = ElectricalCabinet.query.get_or_404(cabinet_id)
    c.is_active = False
    db.session.commit()
    flash(_('Cabinet deleted'), 'success')
    return redirect(url_for('electricity.electricity_list'))


@bp.route('/cabinet/<int:cabinet_id>/breaker/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def breaker_new(cabinet_id):
    c = ElectricalCabinet.query.get_or_404(cabinet_id)
    if request.method == 'POST':
        b = CircuitBreaker(
            cabinet_id=c.id,
            label=request.form['label'],
            description=request.form.get('description', ''),
            breaker_type=request.form.get('breaker_type', 'MCB'),
            amperage=int(request.form['amperage']) if request.form.get('amperage') else None,
            poles=int(request.form.get('poles', 1)),
            curve_type=request.form.get('curve_type', 'C'),
            phase=request.form.get('phase', ''),
            status=request.form.get('status', 'on'),
            connected_to=request.form.get('connected_to', ''),
            notes=request.form.get('notes', ''),
            row=int(request.form.get('row', 1)),
            position=int(request.form.get('position', 1))
        )
        db.session.add(b)
        db.session.commit()
        flash(_('Breaker added'), 'success')
        return redirect(url_for('electricity.cabinet_detail', cabinet_id=c.id))
    return render_template('breaker_form.html', cabinet=c, breaker=None)


@bp.route('/breaker/<int:breaker_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def breaker_edit(breaker_id):
    b = CircuitBreaker.query.get_or_404(breaker_id)
    if request.method == 'POST':
        b.label = request.form['label']
        b.description = request.form.get('description', '')
        b.breaker_type = request.form.get('breaker_type', 'MCB')
        b.amperage = int(request.form['amperage']) if request.form.get('amperage') else None
        b.poles = int(request.form.get('poles', 1))
        b.curve_type = request.form.get('curve_type', 'C')
        b.phase = request.form.get('phase', '')
        b.status = request.form.get('status', 'on')
        b.connected_to = request.form.get('connected_to', '')
        b.notes = request.form.get('notes', '')
        b.row = int(request.form.get('row', 1))
        b.position = int(request.form.get('position', 1))
        db.session.commit()
        flash(_('Breaker updated'), 'success')
        return redirect(url_for('electricity.cabinet_detail', cabinet_id=b.cabinet_id))
    return render_template('breaker_form.html', cabinet=b.cabinet, breaker=b)


@bp.route('/breaker/<int:breaker_id>/toggle', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def breaker_toggle(breaker_id):
    b = CircuitBreaker.query.get_or_404(breaker_id)
    b.status = 'off' if b.status == 'on' else 'on'
    db.session.commit()
    return jsonify({'status': b.status})


@bp.route('/breaker/<int:breaker_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def breaker_delete(breaker_id):
    b = CircuitBreaker.query.get_or_404(breaker_id)
    cabinet_id = b.cabinet_id
    db.session.delete(b)
    db.session.commit()
    flash(_('Breaker deleted'), 'success')
    return redirect(url_for('electricity.cabinet_detail', cabinet_id=cabinet_id))


@bp.route('/schematic')
@login_required
def electricity_schematic():
    cabinets = ElectricalCabinet.query.filter_by(is_active=True).order_by(ElectricalCabinet.name).all()
    return render_template('electricity_schematic.html', cabinets=cabinets)


# ── SWITCH LOG ──────────────────────────────────────────────

@bp.route('/cabinet/<int:cabinet_id>/switch-log')
@login_required
def switch_log(cabinet_id):
    c = ElectricalCabinet.query.get_or_404(cabinet_id)
    logs = ElectricalSwitchLog.query.filter_by(cabinet_id=c.id).order_by(ElectricalSwitchLog.created_at.desc()).all()
    all_breakers = CircuitBreaker.query.join(ElectricalCabinet).filter(ElectricalCabinet.is_active == True).order_by(ElectricalCabinet.name, CircuitBreaker.label).all()
    return render_template('switch_log.html', cabinet=c, logs=logs, all_breakers=all_breakers)


@bp.route('/cabinet/<int:cabinet_id>/switch-log/add', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def switch_log_add(cabinet_id):
    c = ElectricalCabinet.query.get_or_404(cabinet_id)
    from_id = int(request.form['from_breaker_id']) if request.form.get('from_breaker_id') else None
    to_id = int(request.form['to_breaker_id']) if request.form.get('to_breaker_id') else None
    reason = request.form.get('reason', '').strip()
    if not reason:
        flash(_('Reason is required'), 'error')
        return redirect(url_for('electricity.switch_log', cabinet_id=c.id))
    log = ElectricalSwitchLog(
        cabinet_id=c.id,
        from_breaker_id=from_id,
        to_breaker_id=to_id,
        reason=reason,
        notes=request.form.get('notes', ''),
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    flash(_('Switch logged'), 'success')
    return redirect(url_for('electricity.switch_log', cabinet_id=c.id))


@bp.route('/switch-log/<int:log_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def switch_log_delete(log_id):
    log = ElectricalSwitchLog.query.get_or_404(log_id)
    db.session.delete(log)
    db.session.commit()
    flash(_('Log entry deleted'), 'success')
    return redirect(url_for('electricity.switch_log_global'))


# ── DOCUMENTS ───────────────────────────────────────────────

@bp.route('/cabinet/<int:cabinet_id>/document/upload', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def document_upload(cabinet_id):
    c = ElectricalCabinet.query.get_or_404(cabinet_id)
    if 'document' not in request.files or not request.files['document'].filename:
        flash(_('No file selected'), 'error')
        return redirect(url_for('electricity.cabinet_detail', cabinet_id=c.id))
    file = request.files['document']
    filename = secure_filename(f"elec_{c.id}_{file.filename}")
    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
    doc = ElectricalDocument(
        cabinet_id=c.id,
        doc_type=request.form.get('doc_type', 'schematic'),
        title=request.form.get('title', file.filename),
        filename=filename,
        uploaded_by=current_user.id
    )
    db.session.add(doc)
    db.session.commit()
    flash(_('Document uploaded'), 'success')
    return redirect(url_for('electricity.cabinet_detail', cabinet_id=c.id))


@bp.route('/document/<int:doc_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def document_delete(doc_id):
    doc = ElectricalDocument.query.get_or_404(doc_id)
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], doc.filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    db.session.delete(doc)
    db.session.commit()
    flash(_('Document deleted'), 'success')
    return redirect(url_for('electricity.electricity_list'))


# ── GLOBAL: upload + switch log from main page ─────────────

@bp.route('/document/upload', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def document_upload_global():
    cabinet_id = int(request.form.get('cabinet_id', 0))
    if not cabinet_id:
        flash(_('Select a cabinet'), 'error')
        return redirect(url_for('electricity.electricity_list'))
    c = ElectricalCabinet.query.get_or_404(cabinet_id)
    if 'document' not in request.files or not request.files['document'].filename:
        flash(_('No file selected'), 'error')
        return redirect(url_for('electricity.electricity_list'))
    file = request.files['document']
    filename = secure_filename(f"elec_{c.id}_{file.filename}")
    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
    doc = ElectricalDocument(
        cabinet_id=c.id,
        doc_type=request.form.get('doc_type', 'schematic'),
        title=request.form.get('title', file.filename),
        filename=filename,
        uploaded_by=current_user.id
    )
    db.session.add(doc)
    db.session.commit()
    flash(_('Document uploaded for {}').format(c.name), 'success')
    return redirect(url_for('electricity.electricity_list'))


@bp.route('/switch-log')
@login_required
def switch_log_global():
    logs = ElectricalSwitchLog.query.order_by(ElectricalSwitchLog.created_at.desc()).limit(100).all()
    all_breakers = CircuitBreaker.query.join(ElectricalCabinet).filter(ElectricalCabinet.is_active == True).order_by(ElectricalCabinet.name, CircuitBreaker.label).all()
    cabinets = ElectricalCabinet.query.filter_by(is_active=True).order_by(ElectricalCabinet.name).all()
    return render_template('switch_log.html', cabinet=None, logs=logs, all_breakers=all_breakers, cabinets=cabinets)


@bp.route('/switch-log/add', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def switch_log_add_global():
    cabinet_id = int(request.form.get('cabinet_id', 0))
    if not cabinet_id:
        flash(_('Select a cabinet'), 'error')
        return redirect(url_for('electricity.switch_log_global'))
    from_id = int(request.form['from_breaker_id']) if request.form.get('from_breaker_id') else None
    to_id = int(request.form['to_breaker_id']) if request.form.get('to_breaker_id') else None
    reason = request.form.get('reason', '').strip()
    if not reason:
        flash(_('Reason is required'), 'error')
        return redirect(url_for('electricity.switch_log_global'))
    log = ElectricalSwitchLog(
        cabinet_id=cabinet_id,
        from_breaker_id=from_id,
        to_breaker_id=to_id,
        reason=reason,
        notes=request.form.get('notes', ''),
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    flash(_('Switch logged'), 'success')
    return redirect(url_for('electricity.switch_log_global'))
