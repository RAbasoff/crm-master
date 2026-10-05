"""
timekeeping blueprint
"""
from datetime import datetime, timedelta, date
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from models import (db, TimeEntry, User, Vacation, now_local)
from utils import create_notification, role_required, safe_commit, safe_int

bp = Blueprint('timekeeping', __name__)

@bp.route('/time-tracking')
@login_required
def time_tracking():
    if current_user.has_role('admin', 'director'):
        users = User.query.filter(User.is_active_user == True, User.role.in_(['technician', 'user'])).all()
    else:
        users = [current_user]
    
    today = now_local().date()
    month_start = today.replace(day=1)
    
    entries = TimeEntry.query.filter(
        TimeEntry.user_id.in_([u.id for u in users]),
        TimeEntry.date >= month_start,
        TimeEntry.date <= today
    ).order_by(TimeEntry.date.desc()).all()
    
    return render_template('time_tracking.html', users=users, entries=entries, today=today, month_start=month_start)


def _clock_redirect():
    """Возврат: откуда пришли (дашборд / time-tracking)."""
    nxt = (request.form.get('next') or request.args.get('next') or '').strip()
    if nxt in ('/', '/index', 'index', 'dashboard'):
        return redirect(url_for('index'))
    return redirect(url_for('timekeeping.time_tracking'))


@bp.route('/time-tracking/clock-in', methods=['POST'])
@login_required
def clock_in():
    today = now_local().date()
    existing = TimeEntry.query.filter_by(user_id=current_user.id, date=today).first()
    if existing and existing.clock_in:
        flash(_('Already clocked in today'), 'error')
        return _clock_redirect()

    if existing:
        existing.clock_in = now_local()
        existing.status = 'present'
    else:
        entry = TimeEntry(
            user_id=current_user.id,
            date=today,
            clock_in=now_local(),
            status='present'
        )
        db.session.add(entry)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return _clock_redirect()
    flash(_('Clocked in at') + ' ' + now_local().strftime('%H:%M'), 'success')
    return _clock_redirect()


@bp.route('/time-tracking/clock-out', methods=['POST'])
@login_required
def clock_out():
    today = now_local().date()
    entry = TimeEntry.query.filter_by(user_id=current_user.id, date=today).first()
    if not entry or not entry.clock_in:
        flash(_('Not clocked in today'), 'error')
        return _clock_redirect()
    if entry.clock_out:
        flash(_('Already clocked out today'), 'error')
        return _clock_redirect()

    entry.clock_out = now_local()
    delta = entry.clock_out - entry.clock_in
    hours = delta.total_seconds() / 3600
    entry.hours_worked = max(0, round(hours - (entry.break_minutes or 0) / 60, 2))

    # Overtime (standard 8h) — always recalculate
    entry.overtime_hours = round(max(0, entry.hours_worked - 8), 2) or 0

    if not safe_commit():
        flash(_('Save failed'), 'error')
        return _clock_redirect()
    flash(_('Clocked out at') + ' ' + entry.clock_out.strftime('%H:%M') + '. ' + _('Hours worked') + ': ' + str(entry.hours_worked), 'success')
    return _clock_redirect()


@bp.route('/time-tracking/manual', methods=['POST'])
@login_required
@role_required('admin', 'director')
def time_tracking_manual():
    try:
        user_id = int(request.form['user_id'])
        date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
    except (ValueError, KeyError):
        flash(_('Invalid user or date'), 'error')
        return redirect(url_for('timekeeping.time_tracking'))
    status = request.form.get('status', 'present')
    
    entry = TimeEntry.query.filter_by(user_id=user_id, date=date).first()
    if not entry:
        entry = TimeEntry(user_id=user_id, date=date, status=status)
        db.session.add(entry)
    
    entry.status = status
    entry.notes = request.form.get('notes', '')
    
    if status == 'present':
        try:
            entry.clock_in = datetime.combine(date, datetime.strptime(request.form['clock_in'], '%H:%M').time())
            entry.clock_out = datetime.combine(date, datetime.strptime(request.form['clock_out'], '%H:%M').time())
        except (ValueError, KeyError):
            flash(_('Invalid time format (use HH:MM)'), 'error')
            return redirect(url_for('timekeeping.time_tracking'))
        delta = entry.clock_out - entry.clock_in
        hours = delta.total_seconds() / 3600
        entry.break_minutes = safe_int(request.form.get('break_minutes'), 60)
        entry.hours_worked = round(hours - (entry.break_minutes / 60), 2)
        if entry.hours_worked > 8:
            entry.overtime_hours = round(entry.hours_worked - 8, 2)
    
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('timekeeping.time_tracking'))
    flash(_('Time entry saved'), 'success')
    return redirect(url_for('timekeeping.time_tracking'))


@bp.route('/vacations')
@login_required
@role_required('admin', 'director', 'technician')
def vacations_list():
    if current_user.has_role('admin', 'director'):
        vacations = Vacation.query.order_by(Vacation.created_at.desc()).all()
    else:
        vacations = Vacation.query.filter_by(user_id=current_user.id).order_by(Vacation.created_at.desc()).all()
    return render_template('vacations.html', vacations=vacations)


@bp.route('/vacations/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def vacation_new():
    if request.method == 'POST':
        try:
            d_from = datetime.strptime(request.form['date_from'], '%Y-%m-%d').date()
            d_to = datetime.strptime(request.form['date_to'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid date format'), 'error')
            return redirect(url_for('timekeeping.vacation_new'))
        days = (d_to - d_from).days + 1
        v = Vacation(
            user_id=current_user.id,
            vacation_type=request.form['vacation_type'],
            date_from=d_from,
            date_to=d_to,
            days_count=days,
            reason=request.form.get('reason', '')
        )
        db.session.add(v)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('timekeeping.vacation_new'))
        
        admins = User.query.filter(User.role.in_(['admin', 'director']), User.is_active_user == True).all()
        for admin in admins:
            create_notification(admin.id, 'New vacation request',
                f"{current_user.display_name}: {v.vacation_type} {v.date_from} - {v.date_to}",
                'info',
                url_for('timekeeping.vacations_list')
            )
        
        flash(_('Vacation request submitted'), 'success')
        return redirect(url_for('timekeeping.vacations_list'))
    return render_template('vacation_form.html')


@bp.route('/vacations/<int:vacation_id>/approve', methods=['POST'])
@login_required
@role_required('admin', 'director')
def vacation_approve(vacation_id):
    v = Vacation.query.get_or_404(vacation_id)
    v.status = 'approved'
    v.approved_by = current_user.id
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('timekeeping.vacations_list'))
    create_notification(v.user_id, 'Vacation approved', f"{v.vacation_type} {v.date_from} - {v.date_to}", 'info')
    flash(_('Vacation approved'), 'success')
    return redirect(url_for('timekeeping.vacations_list'))


@bp.route('/vacations/<int:vacation_id>/reject', methods=['POST'])
@login_required
@role_required('admin', 'director')
def vacation_reject(vacation_id):
    v = Vacation.query.get_or_404(vacation_id)
    v.status = 'rejected'
    v.approved_by = current_user.id
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('timekeeping.vacations_list'))
    create_notification(v.user_id, 'Vacation rejected', f"{v.vacation_type} {v.date_from} - {v.date_to}", 'warning')
    flash(_('Vacation rejected'), 'error')
    return redirect(url_for('timekeeping.vacations_list'))


@bp.route('/time-report/<int:user_id>')
@login_required
def time_report(user_id):
    if not current_user.has_role('admin', 'director') and current_user.id != user_id:
        flash(_('Access denied'), 'error')
        return redirect(url_for('timekeeping.time_tracking'))
    
    user = User.query.get_or_404(user_id)
    month = request.args.get('month', now_local().strftime('%Y-%m'))
    year, mon = map(int, month.split('-'))
    start = datetime(year, mon, 1).date()
    if mon == 12:
        end = datetime(year + 1, 1, 1).date()
    else:
        end = datetime(year, mon + 1, 1).date()
    
    entries = TimeEntry.query.filter(
        TimeEntry.user_id == user_id,
        TimeEntry.date >= start,
        TimeEntry.date < end
    ).order_by(TimeEntry.date).all()
    
    total_hours = sum(e.hours_worked for e in entries)
    total_overtime = sum(e.overtime_hours for e in entries)
    days_present = len([e for e in entries if e.status == 'present'])
    days_absent = len([e for e in entries if e.status in ['absent', 'sick']])
    
    vacations = Vacation.query.filter(
        Vacation.user_id == user_id,
        Vacation.status == 'approved',
        Vacation.date_from < end,
        Vacation.date_to >= start
    ).all()
    
    return render_template('time_report.html', user=user, entries=entries, month=month,
                         total_hours=total_hours, total_overtime=total_overtime,
                         days_present=days_present, days_absent=days_absent, vacations=vacations)


@bp.route('/time-report/team')
@login_required
@role_required('admin', 'director')
def team_hours_report():
    """Отчёт по часам команды за месяц."""
    month = request.args.get('month', now_local().strftime('%Y-%m'))
    try:
        year, mon = map(int, month.split('-'))
    except ValueError:
        year, mon = now_local().year, now_local().month
        month = f'{year:04d}-{mon:02d}'
    start = date(year, mon, 1)
    end = (date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1))

    users = User.query.filter(
        User.is_active_user == True,
        User.role.in_(['technician', 'user'])
    ).order_by(User.display_name, User.username).all()

    rows = []
    for u in users:
        entries = TimeEntry.query.filter(
            TimeEntry.user_id == u.id,
            TimeEntry.date >= start,
            TimeEntry.date < end
        ).all()
        total_h = sum((e.hours_worked or 0) for e in entries)
        total_ot = sum((e.overtime_hours or 0) for e in entries)
        days = len([e for e in entries if e.status == 'present'])
        absent = len([e for e in entries if e.status in ('absent', 'sick')])
        rows.append({
            'user': u,
            'days': days,
            'absent': absent,
            'hours': round(total_h, 2),
            'overtime': round(total_ot, 2),
            'entries': entries,
        })
    rows.sort(key=lambda r: r['user'].display_name or r['user'].username)
    totals = {
        'hours': round(sum(r['hours'] for r in rows), 2),
        'overtime': round(sum(r['overtime'] for r in rows), 2),
        'days': sum(r['days'] for r in rows),
    }
    return render_template('team_hours_report.html', rows=rows, month=month, totals=totals)


@bp.route('/time-report/team/export')
@login_required
@role_required('admin', 'director')
def team_hours_export():
    """CSV-выгрузка часов команды."""
    import csv, io
    month = request.args.get('month', now_local().strftime('%Y-%m'))
    try:
        year, mon = map(int, month.split('-'))
    except ValueError:
        year, mon = now_local().year, now_local().month
        month = f'{year:04d}-{mon:02d}'
    start = date(year, mon, 1)
    end = (date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1))
    users = User.query.filter(User.is_active_user == True, User.role.in_(['technician', 'user'])).order_by(User.display_name).all()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    w.writerow(['User', 'Days', 'Absent', 'Hours', 'Overtime'])
    for u in users:
        entries = TimeEntry.query.filter(TimeEntry.user_id == u.id, TimeEntry.date >= start, TimeEntry.date < end).all()
        w.writerow([
            u.display_name or u.username,
            len([e for e in entries if e.status == 'present']),
            len([e for e in entries if e.status in ('absent', 'sick')]),
            round(sum((e.hours_worked or 0) for e in entries), 2),
            round(sum((e.overtime_hours or 0) for e in entries), 2),
        ])
    out = buf.getvalue().encode('utf-8-sig')
    return send_file(
        __import__('io').BytesIO(out),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'team-hours-{month}.csv'
    )
