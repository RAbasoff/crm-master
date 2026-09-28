"""
work_report blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from models import (db, WorkReportEntry)
from utils import role_required, safe_commit

bp = Blueprint('work_report', __name__)

@bp.route('/work-report')
@login_required
@role_required('admin', 'director', 'technician')
def work_report_page():
    entries = WorkReportEntry.query.order_by(WorkReportEntry.created_at.desc()).limit(500).all()
    return render_template('work_report_page.html', entries=entries)


@bp.route('/work-report/add', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def work_report_add():
    entry_text = request.form.get('entry', '').strip()
    if not entry_text:
        flash(_('Entry cannot be empty'), 'error')
        return redirect(url_for('work_report.work_report_page'))
    entry = WorkReportEntry(
        user_id=current_user.id,
        entry=entry_text
    )
    db.session.add(entry)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('work_report.work_report_page'))
    flash(_('Entry added'), 'success')
    return redirect(url_for('work_report.work_report_page'))


@bp.route('/work-report/delete/<int:entry_id>', methods=['POST'])
@login_required
@role_required('admin')
def work_report_delete(entry_id):
    entry = WorkReportEntry.query.get_or_404(entry_id)
    db.session.delete(entry)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('work_report.work_report_page'))
    flash(_('Entry deleted'), 'success')
    return redirect(url_for('work_report.work_report_page'))
