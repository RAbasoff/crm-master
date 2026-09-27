"""
Messages blueprint — internal messages
"""
from flask import Blueprint, request, redirect, url_for, flash, render_template
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import db, Message, User
from utils import role_required, safe_commit, log_audit

bp = Blueprint('messages', __name__)


@bp.route('/messages')
@login_required
def messages_list():
    received = Message.query.filter_by(receiver_id=current_user.id).order_by(Message.created_at.desc()).all()
    sent = Message.query.filter_by(sender_id=current_user.id).order_by(Message.created_at.desc()).all()
    all_messages = []
    if current_user.has_role('admin'):
        all_messages = Message.query.order_by(Message.created_at.desc()).limit(200).all()
    return render_template('messages.html', received=received, sent=sent, all_messages=all_messages)

@bp.route('/messages/new', methods=['GET', 'POST'])
@login_required
def message_new():
    if request.method == 'POST':
        receiver_ids = request.form.getlist('receiver_ids')
        verant_ids = request.form.getlist('verant_ids')
        subject = request.form.get('subject', '')
        body = request.form['body']
        fault_id = request.form.get('fault_id') or None
        # Resolve verantwoordelijke to linked users
        skipped = []
        for vid in verant_ids:
            v = Verantwoordelijke.query.get(int(vid))
            if v:
                linked_user = User.query.filter_by(person_id=v.id, is_active_user=True).first()
                if linked_user and str(linked_user.id) not in receiver_ids:
                    receiver_ids.append(str(linked_user.id))
                elif not linked_user:
                    skipped.append(v.naam)
        # If nobody selected — send to ALL active users
        if not receiver_ids:
            all_users = User.query.filter(User.id != current_user.id, User.is_active_user == True).all()
            receiver_ids = [str(u.id) for u in all_users]
        sent = 0
        for rid in receiver_ids:
            m = Message(
                sender_id=current_user.id,
                receiver_id=int(rid),
                subject=subject,
                body=body,
                fault_id=fault_id
            )
            db.session.add(m)
            create_notification(
                int(rid),
                _('New message'),
                f"{_('From')}: {current_user.display_name} - {subject}",
                'message',
                url_for('messages.messages_list')
            )
            sent += 1
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(request.referrer or '/')
        msg = _('Message sent to') + f' {sent} ' + _('users')
        if skipped:
            msg += f'. {_("No user account for")}: {", ".join(skipped)}'
        flash(msg, 'success')
        return redirect(url_for('messages.messages_list'))
    
    users = User.query.filter(User.id != current_user.id, User.is_active_user == True).all()
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    fault_id = request.args.get('fault_id')
    return render_template('message_form.html', users=users, fault_id=fault_id, verantwoordelijken=verantwoordelijken)

@bp.route('/messages/<int:message_id>')
@login_required
def message_detail(message_id):
    m = Message.query.get_or_404(message_id)
    if m.sender_id != current_user.id and m.receiver_id != current_user.id:
        from flask import abort
        abort(403)
    if m.receiver_id == current_user.id:
        m.is_read = True
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(request.referrer or '/')
    return render_template('message_detail.html', message=m)

@bp.route('/messages/<int:message_id>/delete', methods=['POST'])
@login_required
def message_delete(message_id):
    m = Message.query.get_or_404(message_id)
    if m.sender_id != current_user.id and m.receiver_id != current_user.id:
        from flask import abort
        abort(403)
    db.session.delete(m)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('messages.messages_list'))
    flash(_('Message deleted'), 'success')
    return redirect(url_for('messages.messages_list'))

