# -*- coding: utf-8 -*-
"""
work_report blueprint — совместимость со старыми ссылками.

Отдельная страница «Рабочий журнал» убрана как дубль (решение владельца
2026-11-21): рабочие записи живут на карточках заявок (/faults/<id>).
Записи журнала (WorkReportEntry) продолжают сохраняться как история.
"""
from flask import Blueprint, redirect, url_for, request
from flask_login import login_required

bp = Blueprint('work_report', __name__)


@bp.route('/work-report')
@login_required
def work_report_page():
    return redirect(url_for('faults.faults_list'))


@bp.route('/work-report/add', methods=['POST'])
@login_required
def work_report_add():
    from models import db, WorkReportEntry
    from utils import safe_commit, resolve_user_id
    entry = (request.form.get('entry') or '').strip()
    if entry:
        db.session.add(WorkReportEntry(user_id=resolve_user_id(), entry=entry))
        safe_commit()
    return redirect(url_for('faults.faults_list'))


@bp.route('/work-report/delete/<int:entry_id>', methods=['POST'])
@login_required
def work_report_delete(entry_id):
    from models import db, WorkReportEntry
    from utils import safe_commit
    entry = WorkReportEntry.query.get_or_404(entry_id)
    db.session.delete(entry)
    safe_commit()
    return redirect(url_for('faults.faults_list'))
