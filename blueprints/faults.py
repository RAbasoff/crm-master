"""
Faults blueprint — fault reports, work reports, status management
"""
import os, json
from datetime import datetime
from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify, current_app
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename

from models import (db, FaultReport, FaultPhoto, FaultVideo, FaultStatusHistory,
                    WorkReport, WorkReportPhoto, User, Machine, Contractor,
                    VoorraadItem, VoorraadMutatie)
from utils import role_required, log_audit, create_notification, add_work_report

bp = Blueprint('faults', __name__, url_prefix='/faults')


@bp.route('/')
@login_required
def faults_list():
    if current_user.has_role('admin', 'director'):
        faults = FaultReport.query.order_by(FaultReport.created_at.desc()).all()
    elif current_user.has_role('technician'):
        faults = FaultReport.query.filter(
            (FaultReport.technician_id == current_user.id) |
            (FaultReport.status == 'open')
        ).order_by(FaultReport.created_at.desc()).all()
    else:
        faults = FaultReport.query.filter_by(reporter_id=current_user.id).order_by(FaultReport.created_at.desc()).all()
    return render_template('faults.html', faults=faults)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def fault_new():
    if request.method == 'POST':
        f = FaultReport(
            title=request.form['title'],
            description=request.form['description'],
            priority=request.form.get('priority', 'normal'),
            machine_id=int(request.form['machine_id']),
            reporter_id=current_user.id
        )
        db.session.add(f)
        db.session.flush()

        tech_ids = request.form.getlist('technician_ids')
        for tid in tech_ids:
            tech = User.query.get(int(tid))
            if tech:
                f.assigned_technicians.append(tech)
        if tech_ids:
            f.technician_id = int(tech_ids[0])
            f.status = 'accepted'
            f.accepted_at = datetime.utcnow()

        db.session.commit()

        if 'photos' in request.files:
            for photo in request.files.getlist('photos'):
                if photo.filename:
                    filename = secure_filename(f"fault_{f.id}_{photo.filename}")
                    photo.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                    fp = FaultPhoto(fault_id=f.id, filename=filename)
                    db.session.add(fp)

        if 'videos' in request.files:
            for video in request.files.getlist('videos'):
                if video.filename:
                    filename = secure_filename(f"fault_{f.id}_video_{video.filename}")
                    video.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                    fv = FaultVideo(fault_id=f.id, filename=filename)
                    db.session.add(fv)
            db.session.commit()

        for tech in f.assigned_technicians:
            create_notification(
                tech.id,
                _('Fault assigned to you'),
                f"{_('Machine')}: {f.machine.name} - {f.title} ({_('Priority')}: {f.priority})",
                'fault',
                url_for('faults.fault_detail', fault_id=f.id)
            )
        if not f.assigned_technicians:
            for tech in User.query.filter_by(role='technician', is_active_user=True).all():
                create_notification(
                    tech.id,
                    _('New fault report'),
                    f"{_('Machine')}: {f.machine.name} - {f.title}",
                    'fault',
                    url_for('faults.fault_detail', fault_id=f.id)
                )

        log_audit('create', 'fault', f.id, f'{f.title} — {f.machine.name} (приоритет: {f.priority})')
        add_work_report(f'⚠️ Новая поломка: {f.title} — {f.machine.name} (приоритет: {f.priority})')

        if f.priority == 'critical':
            from app import send_email
            admins = User.query.filter(User.role.in_(['admin', 'director']), User.is_active_user == True).all()
            for admin in admins:
                if admin.email:
                    send_email(
                        admin.email,
                        f'🔴 КРИТИЧЕСКАЯ ЗАЯВКА: {f.title}',
                        f'<h2>Критическая заявка #{f.id}</h2>'
                        f'<p><strong>Станок:</strong> {f.machine.name}</p>'
                        f'<p><strong>Описание:</strong> {f.description[:200]}</p>'
                        f'<p><a href="https://rabasoff.pythonanywhere.com/faults/{f.id}">Открыть заявку</a></p>'
                    )

        flash(_('Fault report created'), 'success')
        return redirect(url_for('faults.faults_list'))

    technicians = User.query.filter_by(role='technician', is_active_user=True).order_by(User.display_name).all()
    machines = Machine.query.order_by(Machine.name).all()
    return render_template('fault_form.html', fault=None, machines=machines, technicians=technicians)


@bp.route('/<int:fault_id>')
@login_required
def fault_detail(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    technicians = User.query.filter_by(role='technician', is_active_user=True).order_by(User.display_name).all()
    contractors = Contractor.query.filter_by(is_active=True).order_by(Contractor.company_name).all()
    return render_template('fault_detail.html', fault=f, technicians=technicians, contractors=contractors)


@bp.route('/<int:fault_id>/accept', methods=['POST'])
@login_required
@role_required('technician', 'admin')
def fault_accept(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    f.status = 'accepted'
    f.technician_id = current_user.id
    f.accepted_at = datetime.utcnow()
    db.session.commit()
    log_audit('accept', 'fault', f.id, f'{f.title} — {f.machine.name}')
    create_notification(
        f.reporter_id,
        _('Fault accepted'),
        f"{_('Technician')} {current_user.display_name} {_('accepted your fault report')}: {f.title}",
        'info',
        url_for('faults.fault_detail', fault_id=f.id)
    )
    flash(_('Fault report accepted'), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/assign', methods=['POST'])
@login_required
@role_required('admin', 'director')
def fault_assign(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    tech_ids = request.form.getlist('technician_ids')
    contractor_id = request.form.get('contractor_id', '')
    if not tech_ids and not contractor_id:
        flash(_('Select at least one technician or contractor'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    f.assigned_technicians = []
    names = []
    if tech_ids:
        for tid in tech_ids:
            tech = User.query.get(int(tid))
            if tech and tech.role == 'technician':
                f.assigned_technicians.append(tech)
                names.append(tech.display_name or tech.username)
        f.technician_id = int(tech_ids[0])
    if contractor_id:
        f.contractor_id = int(contractor_id)
        c = Contractor.query.get(int(contractor_id))
        if c:
            names.append(f"🏢 {c.company_name}")
    f.status = 'accepted'
    f.accepted_at = datetime.utcnow()
    db.session.commit()

    for tech in f.assigned_technicians:
        create_notification(
            tech.id,
            _('Fault assigned to you'),
            f"{_('Admin assigned fault to you')}: {f.title} ({_('Machine')}: {f.machine.name})",
            'fault',
            url_for('faults.fault_detail', fault_id=f.id)
        )
    create_notification(
        f.reporter_id,
        _('Fault assigned'),
        f"{_('Your fault assigned to')} {', '.join(names)}: {f.title}",
        'info',
        url_for('faults.fault_detail', fault_id=f.id)
    )
    log_audit('assign', 'fault', f.id, f'{f.title} → {", ".join(names)}')
    flash(_('Fault assigned to') + ' ' + (', '.join(names)), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/resolve', methods=['POST'])
@login_required
@role_required('technician', 'admin')
def fault_resolve(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    f.status = 'resolved'
    f.resolved_at = datetime.utcnow()
    db.session.commit()
    log_audit('resolve', 'fault', f.id, f'{f.title} — {f.machine.name}')
    create_notification(
        f.reporter_id,
        _('Fault resolved'),
        f"{_('Your fault report has been resolved')}: {f.title}",
        'info',
        url_for('faults.fault_detail', fault_id=f.id)
    )
    flash(_('Fault report resolved'), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/status', methods=['POST'])
@login_required
@role_required('technician', 'admin')
def fault_status_change(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    data = request.get_json()
    new_status = data.get('status')
    reason = data.get('reason', '')
    allowed = ['open', 'accepted', 'in_progress', 'parts_ordered', 'waiting_parts', 'resolved', 'reopened']
    if new_status not in allowed:
        return jsonify({'error': 'Invalid status'}), 400
    old_status = f.status
    f.status = new_status
    if new_status == 'reopened':
        f.resolved_at = None
    history = FaultStatusHistory(
        fault_id=f.id, old_status=old_status, new_status=new_status,
        reason=reason, changed_by=current_user.id
    )
    db.session.add(history)
    db.session.commit()
    log_audit('status_change', 'fault', f.id, f'{old_status} → {new_status}')
    add_work_report(f'🔄 Поломка #{f.id} "{f.title}": статус {old_status} → {new_status}')
    return jsonify({'ok': True, 'old': old_status, 'new': new_status})


@bp.route('/<int:fault_id>/close', methods=['POST'])
@login_required
@role_required('technician', 'admin')
def fault_close(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    data = request.get_json() if request.is_json else request.form
    has_report = data.get('has_report', '')
    has_parts = data.get('has_parts', '')
    close_notes = data.get('close_notes', '')
    if has_report == 'no':
        return jsonify({'error': _('Work report is required to close this fault')}), 400
    f.status = 'closed'
    f.resolved_at = datetime.utcnow()
    db.session.commit()
    create_notification(
        f.reporter_id,
        _('Fault closed'),
        f"{_('Your fault report has been closed')}: {f.title}. {close_notes}",
        'success',
        url_for('faults.fault_detail', fault_id=f.id)
    )
    log_audit('close', 'fault', f.id, close_notes)
    add_work_report(f'🔒 Поломка #{f.id} "{f.title}" закрыта. {close_notes}')
    flash(_('Fault report closed'), 'success')
    if request.is_json:
        return jsonify({'ok': True})
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/reopen', methods=['POST'])
@login_required
@role_required('technician', 'admin')
def fault_reopen(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    data = request.get_json() if request.is_json else request.form
    reason = data.get('reason', '')
    reopen_date = data.get('reopen_date', datetime.utcnow().strftime('%Y-%m-%d'))
    old_status = f.status
    f.status = 'reopened'
    f.resolved_at = None
    history = FaultStatusHistory(
        fault_id=f.id, old_status=old_status, new_status='reopened',
        reason=f'{reopen_date}: {reason}', changed_by=current_user.id
    )
    db.session.add(history)
    db.session.commit()
    create_notification(
        f.reporter_id,
        _('Fault reopened'),
        f"{_('Fault reopened')}: {f.title}. {reason}",
        'warning',
        url_for('faults.fault_detail', fault_id=f.id)
    )
    log_audit('reopen', 'fault', f.id, f'{old_status} → reopened: {reason} ({reopen_date})')
    add_work_report(f'🔓 Поломка #{f.id} "{f.title}" переоткрыта. Причина: {reason}')
    if request.is_json:
        return jsonify({'ok': True})
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/work-report', methods=['GET', 'POST'])
@login_required
@role_required('technician', 'admin')
def work_report_new(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    if request.method == 'POST':
        wr = WorkReport(
            fault_id=f.id,
            technician_id=current_user.id,
            work_description=request.form['work_description'],
            parts_used=request.form.get('parts_used', '[]'),
            time_spent_hours=float(request.form.get('time_spent_hours', 0))
        )
        db.session.add(wr)
        db.session.commit()

        if 'photos' in request.files:
            for photo in request.files.getlist('photos'):
                if photo.filename:
                    filename = secure_filename(f"work_{wr.id}_{photo.filename}")
                    photo.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                    wp = WorkReportPhoto(report_id=wr.id, filename=filename, description=request.form.get('photo_desc', ''))
                    db.session.add(wp)
            db.session.commit()

        try:
            parts = json.loads(wr.parts_used)
            for part in parts:
                item = VoorraadItem.query.get(part['part_id'])
                if item:
                    item.hoeveelheid -= part['quantity']
                    mutatie = VoorraadMutatie(
                        item_id=item.id, type='uitgaand',
                        hoeveelheid=part['quantity'],
                        opmerking=f"Work report #{wr.id} for fault #{f.id}"
                    )
                    db.session.add(mutatie)
            db.session.commit()
        except (ValueError, KeyError, TypeError):
            db.session.rollback()

        f.status = 'resolved'
        f.resolved_at = datetime.utcnow()
        db.session.commit()

        log_audit('create', 'work_report', wr.id, f'Отчёт по поломке #{f.id}: {f.title} ({wr.time_spent_hours}ч)')
        add_work_report(f'📝 Отчёт о работе по поломке #{f.id}: {f.title} ({wr.time_spent_hours}ч)')
        flash(_('Work report created'), 'success')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    return render_template('work_report_form.html', fault=f, warehouse_items=VoorraadItem.query.all(), report=None)


@bp.route('/<int:fault_id>/work-report/<int:report_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('technician', 'admin')
def work_report_edit(fault_id, report_id):
    f = FaultReport.query.get_or_404(fault_id)
    wr = WorkReport.query.get_or_404(report_id)
    if request.method == 'POST':
        try:
            old_parts = json.loads(wr.parts_used) if wr.parts_used else []
            for part in old_parts:
                item = VoorraadItem.query.get(part['part_id'])
                if item:
                    item.hoeveelheid += part['quantity']
                    mutatie = VoorraadMutatie(
                        item_id=item.id, type='inkomend',
                        hoeveelheid=part['quantity'],
                        opmerking=f"Reversed: work report #{wr.id} edit"
                    )
                    db.session.add(mutatie)
            db.session.commit()
        except (ValueError, KeyError, TypeError):
            db.session.rollback()

        wr.work_description = request.form['work_description']
        wr.parts_used = request.form.get('parts_used', '[]')
        wr.time_spent_hours = float(request.form.get('time_spent_hours', 0))

        if 'photos' in request.files:
            for photo in request.files.getlist('photos'):
                if photo.filename:
                    filename = secure_filename(f"work_{wr.id}_{photo.filename}")
                    photo.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                    wp = WorkReportPhoto(report_id=wr.id, filename=filename, description=request.form.get('photo_desc', ''))
                    db.session.add(wp)

        try:
            new_parts = json.loads(wr.parts_used)
            for part in new_parts:
                item = VoorraadItem.query.get(part['part_id'])
                if item:
                    item.hoeveelheid -= part['quantity']
                    mutatie = VoorraadMutatie(
                        item_id=item.id, type='uitgaand',
                        hoeveelheid=part['quantity'],
                        opmerking=f"Work report #{wr.id} (edited) for fault #{f.id}"
                    )
                    db.session.add(mutatie)
            db.session.commit()
        except (ValueError, KeyError, TypeError):
            db.session.rollback()

        log_audit('update', 'work_report', wr.id, f'Отчёт по поломке #{f.id}: {f.title}')
        flash(_('Work report updated'), 'success')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    return render_template('work_report_form.html', fault=f, warehouse_items=VoorraadItem.query.all(), report=wr)


@bp.route('/<int:fault_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def fault_delete(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    title = f.title
    db.session.delete(f)
    db.session.commit()
    log_audit('delete', 'fault', fault_id, title)
    if request.is_json:
        return jsonify({'ok': True})
    flash(_('Fault deleted'), 'success')
    return redirect(url_for('faults.faults_list'))
