"""Machine parts, documents, maintenance records, transfer acts."""
import json
import os
from datetime import datetime

from flask import (Response, current_app, flash, jsonify, redirect,
                   render_template, request, send_file, send_from_directory,
                   url_for)
from flask_babel import gettext as _
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from models import (Machine, MachineDocument, MachinePart, MaintenancePhoto,
                    MaintenanceRecord, SavedDocument, User, Verantwoordelijke,
                    db)
from security import director_only
from utils import (log_audit, next_document_number, resolve_user_id,
                   role_required, safe_commit, safe_date, safe_float,
                   safe_int, save_uploaded_file)
from utils import next_document_number as _next_no

from . import _build_act_pdf, bp

@bp.route('/<int:machine_id>/parts')
@login_required
def machine_parts(machine_id):
    m = Machine.query.get_or_404(machine_id)
    parts = MachinePart.query.filter_by(machine_id=m.id).all()
    return render_template('machine_parts.html', machine=m, parts=parts)


def _part_form_ctx(m, p=None):
    users = User.query.filter(User.is_active_user == True).order_by(User.display_name).all()
    return render_template('part_form.html', machine=m, part=p, users=users)


@bp.route('/<int:machine_id>/parts/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def machine_new_part(machine_id):
    m = Machine.query.get_or_404(machine_id)
    if request.method == 'POST':
        p = MachinePart(
            machine_id=m.id,
            name=request.form.get('name', '').strip() or 'Part',
            part_number=request.form.get('part_number', ''),
            description=request.form.get('description', ''),
            category=request.form.get('category', 'mechanical'),
            status=request.form.get('status', 'ok'),
            installed_date=safe_date(request.form.get('installed_date')),
            replacement_interval_days=safe_int(request.form.get('replacement_interval_days')) or None,
            maintenance_interval_days=safe_int(request.form.get('maintenance_interval_days')) or None,
            responsible_user_id=safe_int(request.form.get('responsible_user_id')) or None,
            notes=request.form.get('notes', ''),
        )
        db.session.add(p)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return _part_form_ctx(m)
        flash(_('Part added'), 'success')
        return redirect(url_for('machines.machine_parts', machine_id=m.id))
    return _part_form_ctx(m)


@bp.route('/<int:machine_id>/parts/add', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def machine_add_part(machine_id):
    """Legacy POST endpoint — same as parts/new."""
    return machine_new_part(machine_id)


@bp.route('/<int:machine_id>/parts/<int:part_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def machine_edit_part(machine_id, part_id):
    m = Machine.query.get_or_404(machine_id)
    p = MachinePart.query.get_or_404(part_id)
    if request.method == 'POST':
        p.name = request.form.get('name', '').strip() or p.name
        p.part_number = request.form.get('part_number', '')
        p.description = request.form.get('description', '')
        p.category = request.form.get('category', p.category)
        p.status = request.form.get('status', p.status)
        p.installed_date = safe_date(request.form.get('installed_date'))
        p.replacement_interval_days = safe_int(request.form.get('replacement_interval_days')) or None
        p.maintenance_interval_days = safe_int(request.form.get('maintenance_interval_days')) or None
        p.responsible_user_id = safe_int(request.form.get('responsible_user_id')) or None
        p.notes = request.form.get('notes', '')
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
        else:
            flash(_('Part updated'), 'success')
        return redirect(url_for('machines.machine_parts', machine_id=m.id))
    return _part_form_ctx(m, p)


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
    filename = save_uploaded_file(file, prefix=f"doc_{m.id}_")
    if not filename:
        flash(_('File type not allowed'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
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
            performed_by=resolve_user_id(),
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
                    fn = save_uploaded_file(photo, prefix=f"maint_{mr.id}_")
                    if fn:
                        mp = MaintenancePhoto(maintenance_id=mr.id, filename=fn)
                        db.session.add(mp)
            safe_commit()
        flash(_('Maintenance record added'), 'success')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    return render_template('maintenance_form.html', machine=m, now=datetime.utcnow())


@bp.route('/<int:machine_id>/transfer-act')
@login_required
def machine_transfer_act(machine_id):
    """Printable transfer act of material assets (ТМЦ) for a machine."""
    m = Machine.query.get_or_404(machine_id)
    users = User.query.filter(User.is_active_user == True).order_by(User.username).all()
    persons = Verantwoordelijke.query.filter_by(is_active=True).order_by(Verantwoordelijke.naam).all()
    items_total = 1 + len(m.parts) + len([sp for sp in m.spare_parts if sp.warehouse_item]) \
                  + len([mc for mc in m.consumables if mc.warehouse_item])
    act_no = next_document_number('ACT')
    return render_template('machine_transfer_act.html',
                           machine=m, users=users, persons=persons,
                           items_total=items_total, now=datetime.utcnow(),
                           act_no=act_no)


@bp.route('/<int:machine_id>/transfer-act/save', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only('tim', 'peter')
def machine_transfer_act_save(machine_id):
    """Сохранить заполненный акт передачи (form-data + CSRF)."""
    m = Machine.query.get_or_404(machine_id)
    if request.is_json:
        data = request.get_json(silent=True) or {}
        html = (data.get('html') or '').strip()
        number = (data.get('number') or '').strip()
        title = (data.get('title') or '').strip()
    else:
        html = (request.form.get('html') or '').strip()
        number = (request.form.get('number') or '').strip()
        title = (request.form.get('title') or '').strip()
    if not html and not request.form.get('act_no') and not request.files.get('photo_0') and not request.form.get('items_json'):
        return jsonify({'ok': False, 'error': 'empty'}), 400
    # Структурированные поля → PDF (маленький, открывается везде)
    try:
        items = json.loads(request.form.get('items_json') or '[]')
    except Exception:
        items = []
    data = {
        'date': request.form.get('act_date') or '',
        'location': request.form.get('act_location') or '',
        'giver': request.form.get('giver') or '',
        'giver_pos': request.form.get('giver_pos') or '',
        'receiver': request.form.get('receiver') or '',
        'receiver_pos': request.form.get('receiver_pos') or '',
        'reason': request.form.get('reason') or '',
        'notes': request.form.get('notes') or '',
        'items': items,
    }
    photo_files = []
    for key in list(request.files.keys()):
        if key.startswith('photo_'):
            photo_files.append(request.files[key])

    if number and SavedDocument.query.filter_by(number=number).first():
        number = next_document_number('ACT')
    elif not number:
        number = next_document_number('ACT')
    title = title or f"Transfer Act {m.name}"

    # PDF — путь: static/uploads/docs/ГГГГ/НОМЕР.pdf
    year = datetime.utcnow().strftime('%Y')
    abs_dir = os.path.join(current_app.config.get('UPLOAD_FOLDER', 'static/uploads'), 'docs', year)
    os.makedirs(abs_dir, exist_ok=True)
    pdf_name = f'{number}.pdf'
    pdf_rel = os.path.join('uploads', 'docs', year, pdf_name)  # URL: /static/uploads/docs/...
    pdf_path = os.path.join(abs_dir, pdf_name)
    pdf_bytes = None
    try:
        pdf_bytes = _build_act_pdf(data, m, photo_files, number)
        if pdf_bytes:
            with open(pdf_path, 'wb') as f:
                f.write(pdf_bytes)
            print(f'ACT PDF saved {pdf_path} size={len(pdf_bytes)}')
        else:
            print('ACT PDF EMPTY')
    except Exception as e:
        print('ACT PDF ERROR', e)
        pdf_bytes = None
    # компактный HTML (без base64) как дубликат для просмотра
    if not html:
        html = f"<html><body><h1>{number}</h1><p>{m.name}</p></body></html>"

    # Гонка номеров: при дубле пересчитываем номер и переименовываем PDF
    doc = None
    for _try in range(5):
        doc = SavedDocument(
            number=number,
            doc_type='transfer_act',
            module='machines',
            title=title,
            machine_id=m.id,
            content_html=html,
            pdf_file=pdf_rel if pdf_bytes else None,
            created_by=resolve_user_id(),
        )
        db.session.add(doc)
        try:
            db.session.flush()
            break
        except IntegrityError:
            db.session.rollback()
            doc = None
            old_path = pdf_path
            number = _next_no('ACT')
            pdf_name = f'{number}.pdf'
            pdf_path = os.path.join(abs_dir, pdf_name)
            pdf_rel = os.path.join('uploads', 'docs', year, pdf_name)
            if pdf_bytes:
                try:
                    if os.path.exists(old_path):
                        os.replace(old_path, pdf_path)
                    else:
                        with open(pdf_path, 'wb') as f:
                            f.write(pdf_bytes)
                except OSError:
                    pass
    if doc is None:
        return jsonify({'ok': False, 'error': 'duplicate number'}), 409
    try:
        if not safe_commit():
            return jsonify({'ok': False, 'error': 'save failed'}), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500
    log_audit('save_document', 'machine', m.id, f'{number} transfer_act')
    return jsonify({'ok': True, 'number': number, 'id': doc.id,
                    'pdf': f'/static/{pdf_rel}' if pdf_bytes else None,
                    'file': doc.pdf_file})


@bp.route('/documents/act/<int:doc_id>/pdf')
@login_required
def saved_act_pdf(doc_id):
    """Отдать PDF акта (надёжный роут)."""
    d = SavedDocument.query.get_or_404(doc_id)
    if not d.pdf_file:
        return jsonify({'error': 'no pdf'}), 404
    # pdf_file = uploads/docs/ГГГГ/файл.pdf
    rel = d.pdf_file
    if rel.startswith('uploads/'):
        rel = rel[len('uploads/'):]
    directory = os.path.join(current_app.config.get('UPLOAD_FOLDER', 'static/uploads'), rel)
    return send_file(directory, mimetype='application/pdf', download_name=f'{d.number}.pdf', as_attachment=False)


@bp.route('/<int:machine_id>/documents/acts')
@login_required
def machine_saved_acts(machine_id):
    """Список сохранённых актов по станку."""
    m = Machine.query.get_or_404(machine_id)
    acts = SavedDocument.query.filter_by(
        machine_id=m.id, doc_type='transfer_act'
    ).order_by(SavedDocument.created_at.desc()).all()
    return render_template('machine_acts.html', machine=m, acts=acts)


@bp.route('/documents/act/<int:doc_id>')
@login_required
def saved_act_view(doc_id):
    d = SavedDocument.query.get_or_404(doc_id)
    html = d.content_html or '<html><body>empty</body></html>'
    return Response(html, mimetype='text/html; charset=utf-8')

