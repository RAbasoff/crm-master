"""Gas quality checks (O₂ / CO₂) routes."""
from datetime import datetime, timedelta

from flask import request, redirect, url_for, flash, render_template
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import db, GasQualityCheck, User, now_local
from utils import (resolve_user_id, role_required, safe_commit, safe_int,
                   safe_date, save_uploaded_file)
from security import director_only, is_head_of_ts

from . import bp


# ── Контроль качества газа (O₂ / CO₂) ─────────────────────────────

@bp.route('/quality')
@login_required
@role_required('admin', 'director', 'technician', 'user', 'responsible')
def gas_quality_list():
    today = datetime.utcnow().date()
    month = request.args.get('month', today.strftime('%Y-%m'))
    try:
        y, m = map(int, month.split('-'))
        m_start = datetime(y, m, 1).date()
        m_end = datetime(y + 1, 1, 1).date() if m == 12 else datetime(y, m + 1, 1).date()
    except ValueError:
        m_start = today.replace(day=1)
        m_end = (m_start + timedelta(days=32)).replace(day=1)
    checks = GasQualityCheck.query.filter(
        GasQualityCheck.check_date >= m_start,
        GasQualityCheck.check_date < m_end
    ).order_by(GasQualityCheck.check_date.desc(), GasQualityCheck.check_time.desc()).all()
    last = GasQualityCheck.query.order_by(GasQualityCheck.check_time.desc()).first()
    today_checks = GasQualityCheck.query.filter_by(check_date=today).all()
    mechanics = User.query.filter(User.role == 'technician', User.is_active_user == True).order_by(User.display_name).all()
    prev_m = (m_start - timedelta(days=1)).strftime('%Y-%m')
    next_m = m_end.strftime('%Y-%m')
    return render_template('gas_quality.html',
        checks=checks, last=last, today_checks=today_checks,
        mechanics=mechanics, month=month,
        month_start=m_start, month_end=m_end,
        prev_month=prev_m, next_month=next_m)


@bp.route('/quality/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only()
def gas_quality_new():
    if not (current_user.has_role('admin', 'technician') or is_head_of_ts(current_user)):
        flash(_('Access denied. Insufficient rights.'), 'error')
        return redirect(url_for('gas.gas_quality_list'))
    if request.method == 'POST':
        try:
            o2 = float(request.form.get('o2_percent') or '')
            co2 = float(request.form.get('co2_percent') or '')
        except ValueError:
            flash(_('O2 and CO2 must be numbers'), 'error')
            return redirect(url_for('gas.gas_quality_new'))
        if not (0 <= o2 <= 100) or not (0 <= co2 <= 100):
            flash(_('Percent must be 0–100'), 'error')
            return redirect(url_for('gas.gas_quality_new'))
        now = now_local()
        d = safe_date(request.form.get('check_date')) or now
        # safe_date возвращает datetime — для Date-колонки нужен date
        d = d.date() if isinstance(d, datetime) else d
        # защита от дублей: повторный POST тех же значений (двойное нажатие, медленная сеть).
        # сравниваем в Python — Numeric-колонки в SQLite хранятся как текст, float в SQL не матчит
        recent = GasQualityCheck.query.filter(
            GasQualityCheck.check_date == d,
            GasQualityCheck.created_at >= now - timedelta(seconds=120),
        ).order_by(GasQualityCheck.id.desc()).first()
        if recent is not None and \
                float(recent.o2_percent if recent.o2_percent is not None else -1) == o2 and \
                float(recent.co2_percent if recent.co2_percent is not None else -1) == co2:
            flash(_('This measurement was already saved'), 'info')
            return redirect(url_for('gas.gas_quality_list', month=d.strftime('%Y-%m')))
        chk = GasQualityCheck(
            check_date=d,
            check_time=now,
            sampled_by_id=safe_int(request.form.get('sampled_by_id'), default=None),
            o2_percent=o2,
            co2_percent=co2,
            notes=request.form.get('notes', ''),
            mixed_gas_used=bool(request.form.get('mixed_gas_used')),
            created_by=resolve_user_id(),
        )
        if 'photo' in request.files and request.files['photo'].filename:
            fn = save_uploaded_file(request.files['photo'], prefix='gq_')
            if fn:
                chk.photo = fn
        db.session.add(chk)
        if not safe_commit():
            flash(_('Save failed'), 'error')
        else:
            flash(_('Gas quality check saved'), 'success')
        return redirect(url_for('gas.gas_quality_list', month=d.strftime('%Y-%m')))
    mechanics = User.query.filter(User.role == 'technician', User.is_active_user == True).order_by(User.display_name).all()
    now = now_local()
    return render_template('gas_quality_form.html', mechanics=mechanics, now=now)


@bp.route('/quality/<int:check_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def gas_quality_edit(check_id):
    """Администратор может править замер качества газа."""
    chk = GasQualityCheck.query.get_or_404(check_id)
    if request.method == 'POST':
        try:
            o2 = float(request.form.get('o2_percent') or '')
            co2 = float(request.form.get('co2_percent') or '')
        except ValueError:
            flash(_('O2 and CO2 must be numbers'), 'error')
            return redirect(url_for('gas.gas_quality_edit', check_id=check_id))
        chk.o2_percent = o2
        chk.co2_percent = co2
        chk.notes = request.form.get('notes', '') or ''
        chk.mixed_gas_used = bool(request.form.get('mixed_gas_used'))
        chk.sampled_by_id = safe_int(request.form.get('sampled_by_id'), default=None)
        d = safe_date(request.form.get('check_date'))
        if d:
            chk.check_date = d
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('gas.gas_quality_edit', check_id=check_id))
        flash(_('Saved'), 'success')
        return redirect(url_for('gas.gas_quality_list'))
    users = User.query.filter(User.is_active_user == True).order_by(User.display_name).all()
    return render_template('gas_quality_form.html', check=chk, users=users, mechanics=users, editing=True, now=now_local())


@bp.route('/quality/<int:check_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director')
@director_only()
def gas_quality_delete(check_id):
    chk = GasQualityCheck.query.get_or_404(check_id)
    month = chk.check_date.strftime('%Y-%m')
    db.session.delete(chk)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        flash(_('Deleted'), 'success')
    return redirect(url_for('gas.gas_quality_list', month=month))
