"""
Notifications blueprint — notifications + reminders pages
"""
from datetime import datetime, timedelta

from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import db, Notification, MachinePart, VoorraadItem, User, UserReminder, now_local
from utils import role_required, safe_commit, create_notification

bp = Blueprint('notifications', __name__)


@bp.route('/notifications')
@login_required
def notifications_list():
    notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(50).all()
    return render_template('notifications.html', notifications=notifications)

@bp.route('/notifications/unread')
@login_required
def notifications_unread():
    count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({'count': count})

@bp.route('/notifications/<int:notif_id>/read', methods=['POST'])
@login_required
def notification_read(notif_id):
    n = Notification.query.get_or_404(notif_id)
    if n.user_id == current_user.id:
        n.is_read = True
        if not safe_commit():
            return jsonify({'error': 'Save failed'}), 500
    return jsonify({'success': True})

@bp.route('/notifications/read-all', methods=['POST'])
@login_required
def notifications_read_all():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'success': True})

@bp.route('/reminders')
@login_required
def reminders():
    """Unified reminders page — maintenance + consumables + overdue."""
    from sqlalchemy.orm import joinedload
    today = now_local().date()
    soon_7 = today + timedelta(days=7)
    soon_30 = today + timedelta(days=30)

    # Machine parts with upcoming maintenance
    part_q = MachinePart.query.options(joinedload(MachinePart.machine))
    if not current_user.has_role('admin', 'director', 'technician'):
        machine_ids = [m.id for m in current_user.assigned_machines]
        part_q = part_q.filter(MachinePart.machine_id.in_(machine_ids))

    overdue_parts = []
    upcoming_parts = []
    for p in part_q.all():
        if p.next_replacement and p.next_replacement <= soon_30:
            entry = {'type': 'replacement', 'part': p, 'machine': p.machine,
                     'date': p.next_replacement, 'days': (p.next_replacement - today).days}
            if p.next_replacement < today:
                overdue_parts.append(entry)
            else:
                upcoming_parts.append(entry)
        if p.next_maintenance and p.next_maintenance <= soon_30:
            entry = {'type': 'maintenance', 'part': p, 'machine': p.machine,
                     'date': p.next_maintenance, 'days': (p.next_maintenance - today).days}
            if p.next_maintenance < today:
                overdue_parts.append(entry)
            else:
                upcoming_parts.append(entry)

    # Consumables
    consumables = VoorraadItem.query.filter(
        VoorraadItem.consumable_type.isnot(None),
        VoorraadItem.next_replacement.isnot(None),
        VoorraadItem.next_replacement <= soon_30
    ).order_by(VoorraadItem.next_replacement).all()

    overdue_consumables = [c for c in consumables if c.next_replacement < today]
    upcoming_consumables = [c for c in consumables if today <= c.next_replacement <= soon_30]

    # Low stock
    low_stock = VoorraadItem.query.filter(
        VoorraadItem.hoeveelheid <= VoorraadItem.minimum
    ).all()

    overdue_parts.sort(key=lambda x: x['date'])
    upcoming_parts.sort(key=lambda x: x['date'])

    # Личные / общие напоминания
    from sqlalchemy import or_
    from models import user_reminder_target
    mine_q = UserReminder.query.filter(
        or_(UserReminder.created_by == current_user.id,
            UserReminder.targets.any(User.id == current_user.id))
    ).order_by(UserReminder.is_done.asc(), UserReminder.due_at.asc().nullslast(),
               UserReminder.created_at.desc()).all()
    all_users = User.query.filter(User.is_active_user == True).order_by(User.display_name, User.username).all()

    return render_template('reminders.html',
        overdue_parts=overdue_parts, upcoming_parts=upcoming_parts,
        overdue_consumables=overdue_consumables, upcoming_consumables=upcoming_consumables,
        low_stock=low_stock, today=today,
        my_reminders=mine_q, all_users=all_users)


@bp.route('/reminders/new', methods=['POST'])
@login_required
def reminder_new():
    """Создать напоминание: себе (видит только автор) или выбранным пользователям."""
    title = (request.form.get('title') or '').strip()
    body = (request.form.get('body') or '').strip()
    due_raw = (request.form.get('due_at') or '').strip()
    if not title:
        flash(_('Title is required'), 'error')
        return redirect(url_for('notifications.reminders'))
    due_at = None
    if due_raw:
        try:
            due_at = datetime.strptime(due_raw, '%Y-%m-%dT%H:%M')
        except ValueError:
            try:
                due_at = datetime.strptime(due_raw, '%Y-%m-%d')
            except ValueError:
                due_at = None
    r = UserReminder(created_by=current_user.id, title=title, body=body, due_at=due_at)
    target_ids = request.form.getlist('target_ids')
    for tid in target_ids:
        try:
            u = db.session.get(User, int(tid))
        except (ValueError, TypeError):
            continue
        if u:
            r.targets.append(u)
    db.session.add(r)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('notifications.reminders'))

    # Уведомления получателям (заголовок «Напоминание»)
    for u in r.targets:
        if u.id != current_user.id:
            create_notification(u.id, 'Reminder', title, 'info', '/reminders')
    flash(_('Reminder created'), 'success')
    return redirect(url_for('notifications.reminders'))


@bp.route('/reminders/<int:rem_id>/done', methods=['POST'])
@login_required
def reminder_done(rem_id):
    r = UserReminder.query.get_or_404(rem_id)
    # отметить может автор или любой, кому адресовано
    allowed = r.created_by == current_user.id or any(t.id == current_user.id for t in r.targets)
    if not allowed and not current_user.has_role('admin', 'director'):
        flash(_('Access denied'), 'error')
        return redirect(url_for('notifications.reminders'))
    r.is_done = not r.is_done
    if not safe_commit():
        flash(_('Save failed'), 'error')
    return redirect(url_for('notifications.reminders'))


@bp.route('/reminders/<int:rem_id>/delete', methods=['POST'])
@login_required
def reminder_delete(rem_id):
    r = UserReminder.query.get_or_404(rem_id)
    if r.created_by != current_user.id and not current_user.has_role('admin'):
        flash(_('Access denied'), 'error')
        return redirect(url_for('notifications.reminders'))
    db.session.delete(r)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    return redirect(url_for('notifications.reminders'))


@bp.route('/consumable-reminders')
@login_required
@role_required('admin', 'director', 'technician')
def consumable_reminders():
    """Show upcoming consumable replacements"""
    today = now_local().date()
    soon = today + timedelta(days=30)
    
    # Get all consumables with replacement dates
    upcoming = VoorraadItem.query.filter(
        VoorraadItem.consumable_type.isnot(None),
        VoorraadItem.consumable_type != '',
        VoorraadItem.next_replacement.isnot(None),
        VoorraadItem.next_replacement <= soon
    ).order_by(VoorraadItem.next_replacement).all()
    
    overdue = [c for c in upcoming if c.next_replacement < today]
    soon_list = [c for c in upcoming if today <= c.next_replacement <= soon]
    
    return render_template('consumable_reminders.html', 
        overdue=overdue, soon=soon_list, today=today)

