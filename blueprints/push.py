"""Web Push subscription API — /api/push/*
"""
from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

from models import db, PushSubscription
from utils import safe_commit
from push import get_public_key

bp = Blueprint('push_api', __name__, url_prefix='/api/push')


@bp.route('/vapid-public-key')
@login_required
def vapid_public_key():
    return jsonify({'publicKey': get_public_key()})


@bp.route('/subscribe', methods=['POST'])
@login_required
def push_subscribe():
    data = request.get_json() or {}
    sub = data.get('subscription') or data
    endpoint = (sub.get('endpoint') or '').strip()
    keys = sub.get('keys') or {}
    p256dh = (keys.get('p256dh') or '').strip()
    auth = (keys.get('auth') or '').strip()
    if not endpoint or not p256dh or not auth:
        return jsonify({'error': 'Bad subscription'}), 400

    existing = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if existing:
        existing.user_id = current_user.id
        existing.p256dh = p256dh
        existing.auth = auth
        existing.user_agent = str(request.user_agent)[:300]
        existing.last_used_at = datetime.utcnow()
    else:
        db.session.add(PushSubscription(
            user_id=current_user.id, endpoint=endpoint,
            p256dh=p256dh, auth=auth,
            user_agent=str(request.user_agent)[:300],
        ))
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})


@bp.route('/unsubscribe', methods=['POST'])
@login_required
def push_unsubscribe():
    data = request.get_json() or {}
    endpoint = (data.get('endpoint') or '').strip()
    if not endpoint:
        return jsonify({'error': 'Bad request'}), 400
    PushSubscription.query.filter_by(endpoint=endpoint, user_id=current_user.id).delete()
    safe_commit()
    return jsonify({'ok': True})


@bp.route('/status')
@login_required
def push_status():
    count = PushSubscription.query.filter_by(user_id=current_user.id).count()
    return jsonify({'subscribed': count > 0, 'publicKey': bool(get_public_key())})
