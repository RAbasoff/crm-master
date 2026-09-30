"""
mobile blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from models import (db, FaultReport, Machine, User, VoorraadItem)
from utils import add_work_report, create_notification, log_audit, safe_commit, safe_int

bp = Blueprint('mobile', __name__)

@bp.route('/mobile')
@login_required
def mobile_dashboard():
    """Lightweight mobile dashboard for workers."""
    # My assigned faults
    my_faults = FaultReport.query.filter(
        (FaultReport.technician_id == current_user.id) |
        (FaultReport.reporter_id == current_user.id)
    ).filter(
        FaultReport.status.in_(['open', 'accepted', 'in_progress'])
    ).order_by(FaultReport.created_at.desc()).limit(10).all()

    # My assigned machines
    my_machines = current_user.assigned_machines[:12] if current_user.assigned_machines else []

    # Quick stats
    active_faults = FaultReport.query.filter(
        FaultReport.status.in_(['open', 'accepted', 'in_progress'])
    ).count()
    critical_faults = FaultReport.query.filter(
        FaultReport.status.in_(['open', 'accepted', 'in_progress']),
        FaultReport.priority == 'critical'
    ).count()
    low_stock = VoorraadItem.query.filter(VoorraadItem.hoeveelheid <= VoorraadItem.minimum).count()

    return render_template('mobile_dashboard.html',
        my_faults=my_faults,
        my_machines=my_machines,
        active_faults=active_faults,
        critical_faults=critical_faults,
        low_stock=low_stock)


@bp.route('/mobile/fault', methods=['GET', 'POST'])
@login_required
def mobile_fault_new():
    """Quick fault report from mobile — minimal form."""
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        description = (request.form.get('description') or '').strip()
        machine_id = safe_int(request.form.get('machine_id'))
        priority = request.form.get('priority', 'normal')
        if priority not in ('normal', 'high', 'critical'):
            priority = 'normal'

        if not title or not description:
            flash(_('Title and description required'), 'error')
            return redirect(url_for('mobile.mobile_fault_new'))

        fault = FaultReport(
            title=title, description=description,
            machine_id=machine_id or None,
            priority=priority,
            status='open',
            reporter_id=current_user.id
        )
        rname = (request.form.get('reporter_name') or '').strip()
        if rname:
            fault.reporter_name = rname[:200]
        db.session.add(fault)
        if not safe_commit():
            flash(_('Error saving fault report. Please try again.'), 'error')
            return redirect(url_for('mobile.mobile_fault_new'))

        target = fault.target_name
        who = fault.reporter_label
        log_audit('create', 'fault_report', fault.id, f'Mobile: {title} — {target} (заявитель: {who})')
        add_work_report(f'⚠️ Новая поломка (моб.): {title} — {target} (приоритет: {priority}, заявитель: {who})')

        # Notify all technicians
        for tech in User.query.filter_by(role='technician', is_active_user=True).all():
            create_notification(
                tech.id,
                _('New fault report'),
                f"{_('Machine')}: {target} - {title} ({_('Reporter')}: {who})",
                'fault',
                url_for('faults.fault_detail', fault_id=fault.id)
            )

        flash(_('Fault reported'), 'success')
        return redirect(url_for('mobile.mobile_dashboard'))

    machines = Machine.query.order_by(Machine.name).all()
    return render_template('mobile_fault_form.html', machines=machines)


@bp.route('/mobile/qr/<int:machine_id>')
@login_required
def machine_qr_page(machine_id):
    """Machine info page via QR code scan."""
    m = Machine.query.get_or_404(machine_id)
    faults = FaultReport.query.filter(
        FaultReport.machine_id == m.id,
        FaultReport.status.in_(['open', 'accepted', 'in_progress'])
    ).order_by(FaultReport.created_at.desc()).limit(5).all()

    return render_template('machine_qr.html', machine=m, faults=faults)
