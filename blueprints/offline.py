"""
Offline sync support — CSRF refresh + mutation idempotency helpers.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from flask_wtf.csrf import generate_csrf

from models import db, OfflineMutation
from utils import safe_commit

bp = Blueprint('offline', __name__)


@bp.route('/api/offline/csrf')
@login_required
def offline_csrf():
    """Fresh CSRF token for replaying queued mutations after a long offline period."""
    return jsonify({'csrf_token': generate_csrf()})


@bp.route('/api/offline/status')
@login_required
def offline_status():
    """Server-side view of replayed offline mutations (debug / support)."""
    limit = min(int(request.args.get('limit', 20)), 100)
    q = OfflineMutation.query
    if not current_user.has_role('admin', 'director'):
        q = q.filter_by(user_id=current_user.id)
    rows = q.order_by(OfflineMutation.created_at.desc()).limit(limit).all()
    return jsonify({
        'items': [{
            'client_id': r.client_id,
            'temp_number': r.temp_number,
            'title': r.title,
            'path': r.path,
            'status': r.status,
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'completed_at': r.completed_at.isoformat() if r.completed_at else None,
            'result': r.result_json,
        } for r in rows]
    })


def remember_mutation(client_id, path, method, temp_number, title, status, result_obj, user_id=None):
    """Persist the outcome of a replayed offline mutation (idempotency key)."""
    try:
        rec = OfflineMutation.query.filter_by(client_id=client_id).first()
        if rec is None:
            rec = OfflineMutation(client_id=client_id, path=path[:300], method=method[:10])
            db.session.add(rec)
        rec.user_id = user_id if user_id is not None else (current_user.id if current_user.is_authenticated else None)
        rec.temp_number = (temp_number or '')[:50] or None
        rec.title = (title or '')[:200] or None
        rec.status = status
        rec.result_json = result_obj
        rec.completed_at = datetime.utcnow()
        safe_commit()
    except Exception:
        db.session.rollback()


def find_mutation(client_id):
    try:
        return OfflineMutation.query.filter_by(client_id=client_id).first()
    except Exception:
        db.session.rollback()
        return None
