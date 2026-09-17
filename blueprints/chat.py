"""
Chat blueprint — real-time messaging with delivery/read status
WhatsApp-style: gray checks (delivered), blue checks (read)
"""
import json
from datetime import datetime
from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import db, User, ChatRoom, ChatParticipant, ChatMessage
from utils import role_required, safe_commit, safe_int, create_notification

bp = Blueprint('chat', __name__, url_prefix='/chat')


@bp.before_request
@login_required
def check_auth():
    pass


def get_or_create_dm(user1_id, user2_id):
    """Get or create a 1:1 chat room between two users."""
    # Check if DM already exists
    rooms = ChatRoom.query.filter_by(is_group=False).all()
    for room in rooms:
        pids = [p.user_id for p in room.participants]
        if user1_id in pids and user2_id in pids and len(pids) == 2:
            return room
    # Create new DM
    room = ChatRoom(is_group=False, created_by=user1_id)
    db.session.add(room)
    db.session.flush()
    db.session.add(ChatParticipant(room_id=room.id, user_id=user1_id))
    db.session.add(ChatParticipant(room_id=room.id, user_id=user2_id))
    safe_commit()
    return room


def get_unread_count(user_id):
    """Count unread messages across all rooms for a user."""
    total = 0
    participations = ChatParticipant.query.filter_by(user_id=user_id).all()
    for p in participations:
        unread = ChatMessage.query.filter(
            ChatMessage.room_id == p.room_id,
            ChatMessage.sender_id != user_id,
            ChatMessage.created_at > (p.last_read_at or datetime.min)
        ).count()
        total += unread
    return total


@bp.route('/')
@login_required
def chat_list():
    """Chat list — all conversations."""
    participations = ChatParticipant.query.filter_by(user_id=current_user.id).all()
    rooms = []
    for p in participations:
        room = p.room
        last_msg = ChatMessage.query.filter_by(room_id=room.id).order_by(ChatMessage.created_at.desc()).first()
        unread = ChatMessage.query.filter(
            ChatMessage.room_id == room.id,
            ChatMessage.sender_id != current_user.id,
            ChatMessage.created_at > (p.last_read_at or datetime.min)
        ).count()
        # Get other participants
        others = [pp.user for pp in room.participants if pp.user_id != current_user.id]
        rooms.append({
            'room': room,
            'last_msg': last_msg,
            'unread': unread,
            'others': others
        })
    rooms.sort(key=lambda x: x['last_msg'].created_at if x['last_msg'] else datetime.min, reverse=True)

    users = User.query.filter(User.id != current_user.id, User.is_active_user == True).order_by(User.display_name).all()
    return render_template('chat/list.html', rooms=rooms, users=users)


@bp.route('/room/<int:room_id>')
@login_required
def chat_room(room_id):
    """Chat room — view messages."""
    room = ChatRoom.query.get_or_404(room_id)
    # Check membership
    participant = ChatParticipant.query.filter_by(room_id=room_id, user_id=current_user.id).first()
    if not participant:
        flash(_('Not a member of this chat'), 'error')
        return redirect(url_for('chat.chat_list'))

    # Mark as read
    participant.last_read_at = datetime.utcnow()
    safe_commit()

    messages = ChatMessage.query.filter_by(room_id=room_id).order_by(ChatMessage.created_at.asc()).limit(200).all()
    others = [p.user for p in room.participants if p.user_id != current_user.id]
    all_participants = [p.user for p in room.participants]
    users = User.query.filter(User.id != current_user.id, User.is_active_user == True).order_by(User.display_name).all()

    return render_template('chat/room.html', room=room, messages=messages, others=others,
                          all_participants=all_participants, users=users, participant=participant)


@bp.route('/start/<int:user_id>')
@login_required
def chat_start(user_id):
    """Start a 1:1 chat with a user."""
    if user_id == current_user.id:
        flash(_('Cannot chat with yourself'), 'error')
        return redirect(url_for('chat.chat_list'))
    other = User.query.get_or_404(user_id)
    room = get_or_create_dm(current_user.id, user_id)
    return redirect(url_for('chat.chat_room', room_id=room.id))


@bp.route('/group/new', methods=['POST'])
@login_required
def chat_group_new():
    """Create a group chat."""
    name = (request.form.get('name') or '').strip()
    member_ids = request.form.getlist('members')
    if not name:
        flash(_('Group name required'), 'error')
        return redirect(url_for('chat.chat_list'))
    if len(member_ids) < 1:
        flash(_('Select at least one member'), 'error')
        return redirect(url_for('chat.chat_list'))

    room = ChatRoom(name=name, is_group=True, created_by=current_user.id)
    db.session.add(room)
    db.session.flush()

    # Add creator
    db.session.add(ChatParticipant(room_id=room.id, user_id=current_user.id))
    # Add members
    for mid in member_ids:
        mid = safe_int(mid)
        if mid and mid != current_user.id:
            db.session.add(ChatParticipant(room_id=room.id, user_id=mid))
    safe_commit()

    flash(_('Group created'), 'success')
    return redirect(url_for('chat.chat_room', room_id=room.id))


@bp.route('/api/send', methods=['POST'])
@login_required
def api_send():
    """Send a message via AJAX."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    room_id = safe_int(data.get('room_id'))
    body = (data.get('body') or '').strip()
    if not room_id or not body:
        return jsonify({'error': 'Missing room_id or body'}), 400

    # Verify membership
    participant = ChatParticipant.query.filter_by(room_id=room_id, user_id=current_user.id).first()
    if not participant:
        return jsonify({'error': 'Not a member'}), 403

    msg = ChatMessage(
        room_id=room_id,
        sender_id=current_user.id,
        body=body,
        status_json='{}'
    )
    db.session.add(msg)
    db.session.flush()

    # Set delivery status for all other participants
    status = {}
    for p in ChatParticipant.query.filter_by(room_id=room_id).all():
        if p.user_id != current_user.id:
            status[str(p.user_id)] = 'delivered'
            # Create notification
            create_notification(
                p.user_id,
                _('New message'),
                f"{current_user.display_name}: {body[:50]}",
                'chat',
                url_for('chat.chat_room', room_id=room_id)
            )
    msg.status_json = json.dumps(status)
    safe_commit()

    return jsonify({
        'ok': True,
        'msg_id': msg.id,
        'created_at': msg.created_at.strftime('%H:%M'),
        'status': status
    })


@bp.route('/api/messages/<int:room_id>')
@login_required
def api_messages(room_id):
    """Get new messages for a room (polling)."""
    after_id = safe_int(request.args.get('after', 0))
    participant = ChatParticipant.query.filter_by(room_id=room_id, user_id=current_user.id).first()
    if not participant:
        return jsonify({'error': 'Not a member'}), 403

    query = ChatMessage.query.filter_by(room_id=room_id)
    if after_id:
        query = query.filter(ChatMessage.id > after_id)
    messages = query.order_by(ChatMessage.created_at.asc()).limit(50).all()

    # Mark as read
    participant.last_read_at = datetime.utcnow()
    # Update status to 'read' for messages from others
    for m in messages:
        if m.sender_id != current_user.id:
            try:
                status = json.loads(m.status_json or '{}')
                if status.get(str(current_user.id)) != 'read':
                    status[str(current_user.id)] = 'read'
                    m.status_json = json.dumps(status)
            except (json.JSONDecodeError, ValueError):
                pass
    safe_commit()

    result = []
    for m in messages:
        try:
            status = json.loads(m.status_json or '{}')
        except (json.JSONDecodeError, ValueError):
            status = {}
        result.append({
            'id': m.id,
            'sender_id': m.sender_id,
            'sender_name': m.sender.display_name if m.sender else '?',
            'body': m.body,
            'created_at': m.created_at.strftime('%H:%M'),
            'status': status
        })

    return jsonify({'messages': result})


@bp.route('/api/unread')
@login_required
def api_unread():
    """Get total unread count for badge."""
    count = get_unread_count(current_user.id)
    return jsonify({'count': count})


@bp.route('/api/rooms/<int:room_id>/add', methods=['POST'])
@login_required
def api_add_member(room_id):
    """Add member to group chat."""
    room = ChatRoom.query.get_or_404(room_id)
    if not room.is_group:
        return jsonify({'error': 'Not a group chat'}), 400
    data = request.get_json()
    user_id = safe_int(data.get('user_id'))
    if not user_id:
        return jsonify({'error': 'No user_id'}), 400
    existing = ChatParticipant.query.filter_by(room_id=room_id, user_id=user_id).first()
    if existing:
        return jsonify({'ok': True, 'msg': 'Already a member'})
    db.session.add(ChatParticipant(room_id=room_id, user_id=user_id))
    safe_commit()
    return jsonify({'ok': True})
