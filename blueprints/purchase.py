"""
purchase blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from models import (db, FaultReport, Machine, PurchaseRequest, User)
from utils import add_work_report, create_notification, log_audit, role_required, safe_commit, safe_float

bp = Blueprint('purchase', __name__)

@bp.route('/purchase-requests')
@login_required
@role_required('admin', 'director')
def purchase_requests_list():
    if current_user.has_role('admin', 'director'):
        requests = PurchaseRequest.query.order_by(PurchaseRequest.created_at.desc()).all()
    else:
        requests = PurchaseRequest.query.filter_by(requester_id=current_user.id).order_by(PurchaseRequest.created_at.desc()).all()
    return render_template('purchase_requests.html', requests=requests)


@bp.route('/purchase-requests/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def purchase_request_new():
    if request.method == 'POST':
        try:
            machine_id = int(request.form['machine_id'])
        except (ValueError, KeyError):
            flash(_('Invalid machine'), 'error')
            return redirect(url_for('purchase.purchase_request_new'))
        pr = PurchaseRequest(
            fault_id=request.form.get('fault_id') or None,
            machine_id=machine_id,
            requester_id=current_user.id,
            machine_serial=request.form.get('machine_serial', ''),
            fault_number=request.form.get('fault_number', ''),
            fault_description=request.form.get('fault_description', ''),
            part_name=request.form['part_name'],
            part_catalog=request.form.get('part_catalog', ''),
            quantity=safe_float(request.form.get('quantity'), 1),
            unit=request.form.get('unit', 'st'),
            urgency=request.form.get('urgency', 'normal'),
            reason=request.form.get('reason', '')
        )
        db.session.add(pr)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('purchase.purchase_request_new'))
        
        log_audit('create', 'purchase_request', pr.id, f'{pr.part_name} x{pr.quantity} — {pr.machine.name} (срочность: {pr.urgency}, заявитель: {pr.requester.name if pr.requester else "?"})')
        add_work_report(f'🛒 Новая заявка: {pr.part_name} x{pr.quantity} — {pr.machine.name} (срочность: {pr.urgency}, заявитель: {pr.requester.name if pr.requester else "?"})')

        admins = User.query.filter(User.role.in_(['admin', 'director']), User.is_active_user == True).all()
        for admin in admins:
            create_notification(
                admin.id,
                _('New purchase request'),
                f"{pr.part_name} x{pr.quantity} — {pr.machine.name} ({_('Requester')}: {pr.requester.name if pr.requester else '?'})",
                'fault',
                url_for('purchase.purchase_request_detail', request_id=pr.id)
            )
        
        flash(_('Purchase request created'), 'success')
        return redirect(url_for('purchase.purchase_requests_list'))
    
    if current_user.has_role('admin', 'director', 'technician'):
        machines = Machine.query.all()
    else:
        machines = current_user.assigned_machines
    faults = FaultReport.query.filter(
        (FaultReport.reporter_id == current_user.id) | (FaultReport.technician_id == current_user.id)
    ).order_by(FaultReport.created_at.desc()).limit(20).all()
    return render_template('purchase_request_form.html', machines=machines, faults=faults)


@bp.route('/purchase-requests/<int:request_id>')
@login_required
@role_required('admin', 'director')
def purchase_request_detail(request_id):
    pr = PurchaseRequest.query.get_or_404(request_id)
    return render_template('purchase_request_detail.html', pr=pr)


@bp.route('/purchase-requests/<int:request_id>/approve', methods=['POST'])
@login_required
@role_required('admin', 'director')
def purchase_request_approve(request_id):
    pr = PurchaseRequest.query.get_or_404(request_id)
    pr.status = 'approved'
    pr.reviewed_at = datetime.utcnow()
    pr.reviewer_id = current_user.id
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(request.referrer or '/')
    
    log_audit('approve', 'purchase_request', pr.id, f'{pr.part_name} x{pr.quantity} — {pr.machine.name}')
    add_work_report(f'✅ Заявка одобрена: {pr.part_name} x{pr.quantity} — {pr.machine.name}')
    
    create_notification(
        pr.requester_id,
        _('Purchase request approved'),
        f"{pr.part_name} x{pr.quantity} — {pr.machine.name}",
        'info',
        url_for('purchase.purchase_request_detail', request_id=pr.id)
    )
    
    flash(_('Purchase request approved'), 'success')
    return redirect(url_for('purchase.purchase_request_detail', request_id=pr.id))


@bp.route('/purchase-requests/<int:request_id>/reject', methods=['POST'])
@login_required
@role_required('admin', 'director')
def purchase_request_reject(request_id):
    pr = PurchaseRequest.query.get_or_404(request_id)
    pr.status = 'rejected'
    pr.reviewed_at = datetime.utcnow()
    pr.reviewer_id = current_user.id
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(request.referrer or '/')
    
    log_audit('reject', 'purchase_request', pr.id, f'{pr.part_name} x{pr.quantity} — {pr.machine.name}')
    add_work_report(f'❌ Заявка отклонена: {pr.part_name} x{pr.quantity} — {pr.machine.name}')
    
    create_notification(
        pr.requester_id,
        _('Purchase request rejected'),
        f"{pr.part_name} x{pr.quantity} — {pr.machine.name}",
        'warning',
        url_for('purchase.purchase_request_detail', request_id=pr.id)
    )
    
    flash(_('Purchase request rejected'), 'error')
    return redirect(url_for('purchase.purchase_request_detail', request_id=pr.id))
