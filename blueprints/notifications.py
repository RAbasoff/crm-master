"""
Notifications blueprint — notifications + reminders pages
"""
from datetime import datetime, timedelta

from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import db, Notification, MachinePart, VoorraadItem
from utils import role_required, safe_commit

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
    today = datetime.utcnow().date()
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

    return render_template('reminders.html',
        overdue_parts=overdue_parts, upcoming_parts=upcoming_parts,
        overdue_consumables=overdue_consumables, upcoming_consumables=upcoming_consumables,
        low_stock=low_stock, today=today)


@bp.route('/consumable-reminders')
@login_required
@role_required('admin', 'director', 'technician')
def consumable_reminders():
    """Show upcoming consumable replacements"""
    today = datetime.utcnow().date()
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

