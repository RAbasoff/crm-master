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

from models import (db, Monteur, User, WeekendShift, WorkSchedule, now_local)
from utils import (get_belgian_holidays, role_required, safe_commit, safe_int,
                   log_audit, WORK_SHIFT_TYPES, OFF_SHIFT_TYPES, get_day_shift)

bp = Blueprint('schedule', __name__)


def _is_schedule_head(user):
    """Начальник технической службы / руководство: admin и director."""
    return user.has_role('admin', 'director')


def _tech_monteurs():
    """Активные механики с привязанным User (техслужба)."""
    monteurs = Monteur.query.filter_by(actief=True).order_by(Monteur.naam).all()
    return [m for m in monteurs if m.user_id]

@bp.route('/schedule')
@login_required
@role_required('admin', 'director', 'technician')
def schedule_list():
    monteurs = Monteur.query.filter_by(actief=True).order_by(Monteur.naam).all()
    # Only monteurs with linked user accounts can have schedules
    monteur_user_ids = [m.user_id for m in monteurs if m.user_id]
    schedules = WorkSchedule.query.filter(WorkSchedule.user_id.in_(monteur_user_ids)).all() if monteur_user_ids else []
    return render_template('schedule.html', monteurs=monteurs, schedules=schedules)


@bp.route('/schedule/test-push', methods=['POST'])
@login_required
@role_required('admin')
def schedule_test_push():
    """Тестовое оповещение (только админ): конкретному механику или всем."""
    from logs import create_notification
    data = request.get_json() if request.is_json else request.form
    target = (data.get('target') or 'all').strip()
    message = (data.get('message') or '').strip() or _('Test notification from ProMaster')

    monteurs = Monteur.query.filter_by(actief=True).order_by(Monteur.naam).all()
    targets = []
    if target == 'all':
        for m in monteurs:
            if m.user_id and m.user and m.user.is_active_user:
                targets.append(m.user)
    else:
        uid = safe_int(target)
        u = User.query.get(uid) if uid else None
        if u:
            targets.append(u)

    if not targets:
        if request.is_json:
            return jsonify({'error': _('Select a mechanic')}), 400
        flash(_('Select a mechanic'), 'error')
        return redirect(url_for('schedule.schedule_list'))

    for u in targets:
        create_notification(
            u.id,
            _('Test push'),
            message,
            'info',
            '/schedule',
        )
    log_audit('test_push', 'schedule', None,
              f'to {len(targets)} user(s): {message[:80]}')
    if request.is_json:
        return jsonify({'ok': True, 'sent': len(targets)})
    flash(_('Test notification sent') + f' ({len(targets)})', 'success')
    return redirect(url_for('schedule.schedule_list'))


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
    year = safe_int(request.args.get('year'), now_local().year)
    month = safe_int(request.args.get('month'), now_local().month)
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


# ── Рабочие субботы: ставит начальник ТС / админ; механик только смотрит ──

@bp.route('/schedule/saturdays')
@login_required
@role_required('admin', 'director', 'technician')
def saturdays():
    """Страница рабочих суббот.

    Начальник ТС / админ (admin, director) — назначает и снимает субботы.
    Механик — только просмотр своих назначенных суббот.
    """
    is_head = _is_schedule_head(current_user)
    filter_user = request.args.get('user', '')
    monteurs = _tech_monteurs()

    # Target user: head can pick anyone; mechanic sees only self (read-only)
    if is_head:
        all_users = [m.user for m in monteurs if m.user]
        if filter_user:
            target = next((u for u in all_users if str(u.id) == filter_user), None)
        else:
            target = None
    else:
        target = current_user
        all_users = [current_user]

    # Next 12 Saturdays from today
    today = now_local().date()
    days_ahead = (5 - today.weekday()) % 7  # 5=Sat
    first_sat = today + timedelta(days=days_ahead)
    saturdays_list = []
    for i in range(12):
        d = first_sat + timedelta(days=7 * i)
        saturdays_list.append(d)

    def _build_rows(u):
        rows = []
        for d in saturdays_list:
            shift = get_day_shift(u.id, d)
            rows.append({
                'date': d,
                'shift': shift,
                'is_working': bool(shift and shift.shift_type in WORK_SHIFT_TYPES),
            })
        return rows

    rows_by_user = {}
    if is_head and target:
        rows_by_user[target.id] = _build_rows(target)
    elif is_head:
        for u in all_users:
            rows_by_user[u.id] = _build_rows(u)
    else:
        rows_by_user[current_user.id] = _build_rows(current_user)

    return render_template('schedule_saturdays.html',
                           is_head=is_head,
                           target=target,
                           all_users=all_users,
                           filter_user=filter_user,
                           saturdays_list=saturdays_list,
                           rows_by_user=rows_by_user)


@bp.route('/schedule/saturdays/set', methods=['POST'])
@login_required
@role_required('admin', 'director')
def saturdays_set():
    """Установить/снять рабочую субботу. Только начальник ТС / админ.

    JSON: {user_id, date, action: 'add'|'remove', shift_type?}
    """
    data = request.get_json() or {}
    user_id = safe_int(data.get('user_id'))
    date_str = (data.get('date') or '').strip()
    action = data.get('action')
    shift_type = data.get('shift_type') or 'full'
    if not user_id or not date_str or action not in ('add', 'remove'):
        return jsonify({'error': 'Bad request'}), 400
    if shift_type not in WORK_SHIFT_TYPES and action == 'add':
        return jsonify({'error': 'Bad shift type'}), 400

    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Bad date'}), 400
    if date.weekday() != 5:  # only Saturdays
        return jsonify({'error': 'Not a Saturday'}), 400

    existing = get_day_shift(user_id, date)
    if action == 'add':
        if existing:
            existing.shift_type = shift_type
            existing.created_by = current_user.id
        else:
            db.session.add(WeekendShift(
                user_id=user_id, date=date, shift_type=shift_type,
                notes=_('Working Saturday'), created_by=current_user.id))
    else:  # remove
        if existing:
            db.session.delete(existing)

    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    log_audit('saturday_shift', 'weekend_shift', user_id,
              f"{action} {date_str} {shift_type if action == 'add' else ''}")
    return jsonify({'ok': True, 'action': action, 'date': date_str})
