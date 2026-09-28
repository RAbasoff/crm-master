"""
schedule blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from models import (db, Monteur, User, WeekendShift, WorkSchedule)
from utils import get_belgian_holidays, role_required, safe_commit, safe_int

bp = Blueprint('schedule', __name__)

@bp.route('/schedule')
@login_required
@role_required('admin', 'director', 'technician')
def schedule_list():
    monteurs = Monteur.query.filter_by(actief=True).order_by(Monteur.naam).all()
    # Only monteurs with linked user accounts can have schedules
    monteur_user_ids = [m.user_id for m in monteurs if m.user_id]
    schedules = WorkSchedule.query.filter(WorkSchedule.user_id.in_(monteur_user_ids)).all() if monteur_user_ids else []
    return render_template('schedule.html', monteurs=monteurs, schedules=schedules)


@bp.route('/schedule/<int:user_id>', methods=['GET', 'POST'])
@login_required
def schedule_user(user_id):
    if not current_user.has_role('admin', 'director') and current_user.id != user_id:
        flash(_('Access denied'), 'error')
        return redirect(url_for('schedule.schedule_list'))
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        work_days = ','.join(request.form.getlist('work_days'))
        s = WorkSchedule(
            user_id=user.id,
            name=request.form['name'],
            shift_start=request.form['shift_start'],
            shift_end=request.form['shift_end'],
            break_minutes=safe_int(request.form.get('break_minutes'), 60),
            work_days=work_days or '1,2,3,4,5'
        )
        db.session.add(s)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('schedule.schedule_list'))
        flash(_('Schedule created'), 'success')
    schedules = WorkSchedule.query.filter_by(user_id=user.id).all()
    return render_template('schedule_user.html', user=user, schedules=schedules)


@bp.route('/schedule/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director')
def schedule_delete(user_id):
    user = User.query.get_or_404(user_id)
    deleted = WorkSchedule.query.filter_by(user_id=user.id).delete()
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('schedule.schedule_list'))
    flash(_('Schedule deleted for') + ' ' + (user.display_name or user.username) + f' ({deleted})', 'success')
    return redirect(url_for('schedule.schedule_list'))


@bp.route('/schedule/monthly')
@login_required
@role_required('admin', 'director')
def schedule_monthly():
    year = safe_int(request.args.get('year'), datetime.utcnow().year)
    month = safe_int(request.args.get('month'), datetime.utcnow().month)
    filter_user = request.args.get('user', '')
    if month < 1: month = 12; year -= 1
    if month > 12: month = 1; year += 1

    first_day = datetime(year, month, 1).date()
    if month == 12:
        last_day = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        last_day = datetime(year, month + 1, 1).date() - timedelta(days=1)

    all_monteurs = Monteur.query.filter_by(actief=True).order_by(Monteur.naam).all()
    # Only monteurs with linked user accounts can appear in schedule
    # Exclude workers fired before the start of the displayed month
    all_users = [m.user for m in all_monteurs if m.user and m.user.is_active_user
                 and not (m.fire_date and m.fire_date < first_day)]
    if filter_user:
        users = [u for u in all_users if str(u.id) == filter_user]
    else:
        users = all_users

    # Get all weekend shifts for this month
    shifts = WeekendShift.query.filter(
        WeekendShift.date >= first_day,
        WeekendShift.date <= last_day
    ).all()
    shift_map = {}
    for s in shifts:
        key = (s.user_id, s.date)
        shift_map[key] = s

    # Get work schedules for each user
    user_schedules = {}
    all_schedules = WorkSchedule.query.filter(WorkSchedule.user_id.in_([u.id for u in users]), WorkSchedule.is_active == True).all()
    for s in all_schedules:
        if s.user_id not in user_schedules:
            user_schedules[s.user_id] = s

    # Get Belgian holidays
    holidays = get_belgian_holidays(year)

    # Build days list
    days = []
    current = first_day
    while current <= last_day:
        days.append({
            'date': current,
            'day': current.day,
            'weekday': current.weekday(),  # 0=Mon, 6=Sun
            'is_weekend': current.weekday() >= 5,
            'is_holiday': current in holidays,
            'holiday_name': holidays.get(current, ''),
        })
        current += timedelta(days=1)

    return render_template('schedule_monthly.html',
        users=users, days=days, year=year, month=month,
        shift_map=shift_map, holidays=holidays, user_schedules=user_schedules,
        all_users=all_users, filter_user=filter_user)


@bp.route('/schedule/monthly/shift', methods=['POST'])
@login_required
@role_required('admin', 'director')
def schedule_monthly_shift():
    data = request.get_json()
    user_id = data.get('user_id')
    date_str = data.get('date')
    action = data.get('action')  # 'add' or 'remove'
    shift_type = data.get('shift_type', 'full')

    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    existing = WeekendShift.query.filter_by(user_id=user_id, date=date).first()

    if action == 'add':
        if existing:
            existing.shift_type = shift_type
        else:
            s = WeekendShift(user_id=user_id, date=date, shift_type=shift_type, created_by=current_user.id)
            db.session.add(s)
    elif action == 'remove' and existing:
        db.session.delete(existing)

    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})


@bp.route('/schedule/monthly/delete', methods=['POST'])
@login_required
@role_required('admin', 'director')
def schedule_monthly_delete():
    data = request.get_json()
    user_id = data.get('user_id')
    year = data.get('year')
    month = data.get('month')
    first_day = datetime(year, month, 1).date()
    if month == 12:
        last_day = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        last_day = datetime(year, month + 1, 1).date() - timedelta(days=1)
    deleted = WeekendShift.query.filter(
        WeekendShift.user_id == user_id,
        WeekendShift.date >= first_day,
        WeekendShift.date <= last_day
    ).delete()
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'deleted': deleted})
