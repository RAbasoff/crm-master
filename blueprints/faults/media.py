import os, json
from flask import request, redirect, url_for, flash, render_template, jsonify, current_app
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import (db, FaultReport, FaultPhoto,
                    WorkReport, WorkReportPhoto, VoorraadItem, VoorraadMutatie)
from utils import resolve_user_id
from utils import role_required, log_audit, add_work_report, safe_commit, safe_float, save_uploaded_file, adjust_stock
from security import director_only, is_head_of_ts

from . import bp


@bp.route('/<int:fault_id>/photo/<int:photo_id>/delete', methods=['POST'])
@login_required
def fault_photo_delete(fault_id, photo_id):
    """Удаление фото заявки админом/начальником ТС."""
    f = FaultReport.query.get_or_404(fault_id)
    if not (current_user.has_role('admin') or is_head_of_ts(current_user)):
        flash(_('Access denied'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    photo = FaultPhoto.query.filter_by(id=photo_id, fault_id=f.id).first()
    if photo:
        try:
            p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'static', 'uploads', photo.filename)
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
        db.session.delete(photo)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('faults.fault_detail', fault_id=f.id))
        log_audit('delete', 'fault_photo', photo_id, f'#{f.id}')
        flash(_('Photo deleted'), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/work-report/photo/<int:photo_id>/delete', methods=['POST'])
@login_required
def work_report_photo_delete(fault_id, photo_id):
    """Удаление фото рабочего отчёта: админ/начальник ТС или автор отчёта."""
    f = FaultReport.query.get_or_404(fault_id)
    photo = WorkReportPhoto.query.filter_by(id=photo_id).first_or_404()
    wr = WorkReport.query.get(photo.report_id)
    allowed = (current_user.has_role('admin') or is_head_of_ts(current_user)
               or (wr and wr.technician_id == current_user.id))
    if not allowed:
        flash(_('Access denied'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    try:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'static', 'uploads', photo.filename)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass
    db.session.delete(photo)
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
    else:
        log_audit('delete', 'work_report_photo', photo_id, f'#{f.id}')
        flash(_('Photo deleted'), 'success')
    # назад на форму редактирования отчёта, если удаляли оттуда
    back = request.referrer or ''
    if '/work-report' in back and wr is not None:
        return redirect(url_for('faults.work_report_edit', fault_id=f.id, report_id=wr.id))
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/work-report', methods=['GET', 'POST'])
@login_required
@role_required('technician', 'admin', 'director')
@director_only()
def work_report_new(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    # Каждый механик ведёт свой отчёт (диагностика, ход работ, результат)
    if request.method == 'POST':
        wr = WorkReport(
            fault_id=f.id,
            technician_id=resolve_user_id(),
            work_description=request.form['work_description'],
            parts_used=request.form.get('parts_used', '[]'),
            time_spent_hours=safe_float(request.form.get('time_spent_hours'), 0)
        )
        db.session.add(wr)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('faults.fault_detail', fault_id=f.id))

        if 'photos' in request.files:
            for photo in request.files.getlist('photos'):
                if photo.filename:
                    filename = save_uploaded_file(photo, prefix=f"work_{wr.id}_")
                    if filename:
                        wp = WorkReportPhoto(report_id=wr.id, filename=filename, description=request.form.get('photo_desc', ''))
                        db.session.add(wp)
            if not safe_commit():
                flash(_('Work report saved but photo upload failed'), 'warning')

        # Deduct parts from warehouse (non-fatal — work report already saved)
        try:
            parts = json.loads(wr.parts_used)
            for part in parts:
                adjust_stock(part['part_id'], -part['quantity'], mt='uitgaand',
                             opmerking=f"Work report #{wr.id} for fault #{f.id}")
            if not safe_commit():
                db.session.rollback()
        except (ValueError, KeyError, TypeError):
            db.session.rollback()

        # Статус заявки НЕ меняем при сохранении рапорта/фото.
        # Отметка «Решено» ставится только кнопкой Resolve самим механиком.

        log_audit('create', 'work_report', wr.id, f'Отчёт по поломке #{f.id}: {f.title} ({wr.time_spent_hours}ч)')
        add_work_report(f"{_('Work report')}: #{f.id} {f.title} ({wr.time_spent_hours}h)")
        flash(_('Work report created'), 'success')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    return render_template('work_report_form.html', fault=f, warehouse_items=VoorraadItem.query.all(), report=None)


@bp.route('/<int:fault_id>/work-report/<int:report_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('technician', 'admin', 'director')
@director_only()
def work_report_edit(fault_id, report_id):
    f = FaultReport.query.get_or_404(fault_id)
    wr = WorkReport.query.get_or_404(report_id)
    if request.method == 'POST':
        try:
            old_parts = json.loads(wr.parts_used) if wr.parts_used else []
            for part in old_parts:
                adjust_stock(part['part_id'], part['quantity'], mt='inkomend',
                             opmerking=f"Reversed: work report #{wr.id} edit")
            if not safe_commit():
                db.session.rollback()
        except (ValueError, KeyError, TypeError):
            db.session.rollback()

        wr.work_description = request.form['work_description']
        wr.parts_used = request.form.get('parts_used', '[]')
        wr.time_spent_hours = safe_float(request.form.get('time_spent_hours'), 0)

        if 'photos' in request.files:
            for photo in request.files.getlist('photos'):
                if photo.filename:
                    filename = save_uploaded_file(photo, prefix=f"work_{wr.id}_")
                    if filename:
                        wp = WorkReportPhoto(report_id=wr.id, filename=filename, description=request.form.get('photo_desc', ''))
                        db.session.add(wp)

        try:
            new_parts = json.loads(wr.parts_used)
            for part in new_parts:
                adjust_stock(part['part_id'], -part['quantity'], mt='uitgaand',
                             opmerking=f"Work report #{wr.id} (edited) for fault #{f.id}")
            if not safe_commit():
                db.session.rollback()
        except (ValueError, KeyError, TypeError):
            db.session.rollback()

        log_audit('update', 'work_report', wr.id, f'Отчёт по поломке #{f.id}: {f.title}')
        flash(_('Work report updated'), 'success')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    return render_template('work_report_form.html', fault=f, warehouse_items=VoorraadItem.query.all(), report=wr)
