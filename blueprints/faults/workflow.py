from datetime import datetime
from flask import request, redirect, url_for, flash, render_template, jsonify, current_app
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import (db, FaultReport, FaultStatusHistory,
                    User, Contractor, FaultWorkSession, now_local)
from utils import resolve_user_id
from utils import role_required, log_audit, add_work_report, safe_commit, safe_int
from logs import create_notifications, mechanic_team_ids
from security import director_only

from . import bp
from . import _set_fault_status, _reporter_row, _notify_i18n_row, translate_status
from models import now_local


def _find_open_work_session(user_id):
    """Открытая (не завершённая) сессия работ пользователя — любая заявка."""
    return FaultWorkSession.query.filter_by(user_id=user_id, ended_at=None).first()


@bp.route('/<int:fault_id>/work-start', methods=['POST'])
@login_required
@role_required('technician', 'admin')
def fault_work_start(fault_id):
    """Начать работы по заявке. У механика может быть только одна открытая сессия."""
    f = FaultReport.query.get_or_404(fault_id)
    if f.status == 'closed':
        if request.is_json:
            return jsonify({'error': _('Fault is closed')}), 400
        flash(_('Fault is closed'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    existing = _find_open_work_session(current_user.id)
    if existing:
        if existing.fault_id == f.id:
            msg = _('Work already started on this fault')
        else:
            msg = _('Finish work on fault #{} before starting another').format(existing.fault_id)
        if request.is_json:
            return jsonify({
                'error': msg,
                'open_fault_id': existing.fault_id,
                'started_at': existing.started_at.isoformat() if existing.started_at else None,
            }), 409
        flash(msg, 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    sess = FaultWorkSession(fault_id=f.id, user_id=resolve_user_id(), started_at=now_local())
    db.session.add(sess)
    if f.status in ('open', 'accepted'):
        f.status = 'in_progress'
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        if request.is_json:
            return jsonify({'error': 'Save failed'}), 500
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    log_audit('work_start', 'fault', f.id,
              f'{f.title} — {current_user.display_name or current_user.username}')
    add_work_report(f"{_('Work started')}: #{f.id} {f.title} ({current_user.display_name or current_user.username})")
    if request.is_json:
        return jsonify({
            'ok': True, 'session_id': sess.id,
            'started_at': sess.started_at.isoformat(),
        })
    flash(_('Work started'), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/work-end', methods=['POST'])
@login_required
@role_required('technician', 'admin')
def fault_work_end(fault_id):
    """Окончить работы по заявке (снять таймер)."""
    f = FaultReport.query.get_or_404(fault_id)
    sess = FaultWorkSession.query.filter_by(
        fault_id=f.id, user_id=resolve_user_id(), ended_at=None
    ).first()
    if not sess:
        msg = _('No active work session on this fault')
        if request.is_json:
            return jsonify({'error': msg}), 404
        flash(msg, 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    data = request.get_json() if request.is_json else request.form
    notes = (data.get('notes') or '').strip() if data else ''
    sess.ended_at = now_local()
    minutes = round(sess.elapsed_seconds / 60.0, 2)
    sess.duration_minutes = minutes
    if notes:
        sess.notes = notes[:2000]
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        if request.is_json:
            return jsonify({'error': 'Save failed'}), 500
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    log_audit('work_end', 'fault', f.id,
              f'{f.title} — {minutes} мин ({current_user.display_name or current_user.username})')
    add_work_report(f"{_('Work finished')}: #{f.id} {f.title} ({minutes} {_('min')})")
    flash(_('Work finished') + f' — {minutes} ' + _('min'), 'success')
    if request.is_json:
        return jsonify({'ok': True, 'duration_minutes': minutes, 'session_id': sess.id})
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/accept', methods=['POST'])
@login_required
@role_required('technician', 'admin', 'director')
@director_only()
def fault_accept(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    _prev_tech = f.technician_id
    ok, err = _set_fault_status(f, 'accepted', current_user, reason='accepted')
    if not ok:
        flash(err, 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    f.technician_id = current_user.id
    f.accepted_at = f.accepted_at or now_local()
    f.first_response_at = f.first_response_at or now_local()
    # Смена механика (заявка перехвачена) — заявителю: НАЗНАЧЕН МЕХАНИК
    if _prev_tech and _prev_tech != current_user.id:
        try:
            _an = _reporter_row(f, 'assigned',
                                detail=current_user.display_name or current_user.username)
            if _an:
                create_notifications([_an])
        except Exception:
            pass
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    log_audit('accept', 'fault', f.id, f'{f.title} — {f.target_name}')
    flash(_('Fault report accepted'), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/assign', methods=['POST'])
@login_required
@role_required('admin', 'director')
@director_only()
def fault_assign(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    tech_ids = request.form.getlist('technician_ids')
    contractor_id = request.form.get('contractor_id', '')
    if not tech_ids and not contractor_id:
        flash(_('Select at least one technician or contractor'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    f.assigned_technicians = []
    names = []
    if tech_ids:
        for tid in tech_ids:
            tech = User.query.get(int(tid))
            if tech and tech.role == 'technician':
                f.assigned_technicians.append(tech)
                names.append(tech.display_name or tech.username)
        f.technician_id = int(tech_ids[0])
        lead_id = safe_int(request.form.get('lead_technician_id') or request.values.get('lead_technician_id'))
        if lead_id and any(t.id == lead_id for t in f.assigned_technicians):
            f.lead_technician_id = lead_id
        else:
            f.lead_technician_id = int(tech_ids[0])
    if contractor_id:
        f.contractor_id = int(contractor_id)
        c = Contractor.query.get(int(contractor_id))
        if c:
            names.append(f"🏢 {c.company_name}")
    _was_accepted = (f.status == 'accepted')
    f.status = 'accepted'
    f.accepted_at = f.accepted_at or now_local()
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    _a_rows = [_notify_i18n_row(tech.id, 'Fault assigned to you',
        lambda _f: f"{_f('Admin assigned fault to you')}: {f.title} ({_f('Machine')}: {f.target_name})",
        url_for('faults.fault_detail', fault_id=f.id)) for tech in f.assigned_technicians
        if tech.id != f.reporter_id]
    # Заявителю — строго из 5: ПРОЧТЕНА (один раз) и НАЗНАЧЕН МЕХАНИК (с именами)
    try:
        if not _was_accepted:
            _ar = _reporter_row(f, 'accepted')
            if _ar:
                _a_rows.append(_ar)
        _as = _reporter_row(f, 'assigned', detail=', '.join(names))
        if _as:
            _a_rows.append(_as)
    except Exception:
        pass
    create_notifications(_a_rows)
    log_audit('assign', 'fault', f.id, f'{f.title} → {", ".join(names)}')
    flash(_('Fault assigned to') + ' ' + (', '.join(names)), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/resolve', methods=['POST'])
@login_required
@role_required('technician', 'admin', 'director')
@director_only()
def fault_resolve(fault_id):
    """RESOLVED — механик после ремонта (закрыть может только начальство)."""
    f = FaultReport.query.get_or_404(fault_id)
    ok, err = _set_fault_status(f, 'resolved', current_user, reason='resolved by mechanic')
    if not ok:
        flash(err, 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    log_audit('resolve', 'fault', f.id, f'{f.title} — {f.target_name}')
    # Уведомить админов: заявка ждёт закрытия (директору идёт «РЕШЕНО» из _set_fault_status)
    _h_rows = [{'user_id': head.id, 'title': 'Fault resolved — awaiting close',
                'message': f"#{f.id} {f.title} — {_('Resolved by')} {current_user.display_name or current_user.username}. {_('Please verify and close')}.",
                'type': 'info',
                'link': url_for('faults.fault_detail', fault_id=f.id)}
               for head in User.query.filter(User.role == 'admin', User.is_active_user == True).all()
               if head.id != current_user.id and head.id != f.reporter_id]
    create_notifications(_h_rows)
    flash(_('Fault report resolved') + '. ' + _('Awaiting close by head'), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/status', methods=['POST'])
@login_required
@role_required('technician', 'admin', 'director')
@director_only()
def fault_status_change(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    data = request.get_json() or {}
    new_status = data.get('status')
    reason = data.get('reason', '')
    pause_reason = data.get('pause_reason', '')
    pause_comment = data.get('pause_comment', '')
    pause_until = data.get('pause_until') or None
    if pause_until:
        try:
            pause_until = datetime.strptime(pause_until, '%Y-%m-%d').date()
        except ValueError:
            pause_until = None

    old_status = f.status
    ok, err = _set_fault_status(
        f, new_status, current_user, reason=reason,
        pause_reason=pause_reason, pause_comment=pause_comment,
        pause_until=pause_until,
    )
    if not ok:
        return jsonify({'error': err}), 400
    # Подтверждённое время ремонта (с вопроса при «Opgelost»)
    wh_raw = data.get('work_hours')
    if wh_raw not in (None, ''):
        try:
            f.work_hours = float(str(wh_raw).replace(',', '.'))
        except ValueError:
            pass
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    log_audit('status_change', 'fault', f.id, f'{old_status} → {new_status}')
    add_work_report(f"#{f.id} {f.title}: {translate_status(old_status)} → {translate_status(new_status)}")
    return jsonify({'ok': True, 'old': old_status, 'new': new_status})


@bp.route('/<int:fault_id>/close', methods=['POST'])
@login_required
@role_required('admin', 'director')
def fault_close(fault_id):
    """Закрыть заявку — только админ / начальник ТС / главный механик."""
    f = FaultReport.query.get_or_404(fault_id)
    data = request.get_json() if request.is_json else request.form
    has_report = data.get('has_report', '')
    has_parts = data.get('has_parts', '')
    close_notes = data.get('close_notes', '')
    if has_report == 'no':
        return jsonify({'error': _('Work report is required to close this fault')}), 400
    # closed → уже закрыта
    if f.status == 'closed':
        return jsonify({'error': _('Fault is closed')}), 400
    ok, err = _set_fault_status(f, 'closed', current_user,
                                reason=close_notes or 'closed by head')
    if not ok:
        if request.is_json:
            return jsonify({'error': err}), 400
        flash(err, 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    if not safe_commit():
        if request.is_json:
            return jsonify({'error': 'Save failed'}), 500
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    try:
        from notify import notify_fault_done
        ru = User.query.get(f.reporter_id) if f.reporter_id else None
        heads = list(User.query.filter(User.role.in_(('admin','director')), User.is_active_user==True).all())
        notify_fault_done(f, ru, heads)
    except Exception as _ne:
        print('notify done', _ne)
    # Заявка закрыта — связанные TWO тоже закрываем
    try:
        from blueprints.two import close_linked_twos
        close_linked_twos(f.id)
        safe_commit()
    except Exception as _te:
        print('two close on fault-close', _te)
    log_audit('close', 'fault', f.id, close_notes)
    add_work_report(f"#{f.id} {f.title} — {_('Closed')}. {close_notes}")
    flash(_('Fault report closed'), 'success')
    if request.is_json:
        return jsonify({'ok': True})
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/reopen', methods=['POST'])
@login_required
@role_required('admin', 'director')
def fault_reopen(fault_id):
    """Переоткрыть заявку — только админ / главный механик / начальник ТС. Причина обязательна."""
    f = FaultReport.query.get_or_404(fault_id)
    data = request.get_json() if request.is_json else request.form
    reason = (data.get('reason') or '').strip()
    reopen_date = data.get('reopen_date', now_local().strftime('%Y-%m-%d'))
    if not reason:
        msg = _('Reason is required to reopen a fault')
        if request.is_json:
            return jsonify({'error': msg}), 400
        flash(msg, 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    old_status = f.status
    ok, err = _set_fault_status(
        f, 'reopened', current_user,
        reason=f'{reopen_date}: {reason}',
    )
    if not ok:
        if request.is_json:
            return jsonify({'error': err}), 400
        flash(err, 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    if not safe_commit():
        if request.is_json:
            return jsonify({'error': 'Save failed'}), 500
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    log_audit('reopen', 'fault', f.id, f'{old_status} → reopened: {reason} ({reopen_date})')
    add_work_report(f"#{f.id} {f.title} — {_('Reopened')}. {_('Reason')}: {reason}")
    # Обязательное оповещение ВСЕМ механикам (включая работавшего по заявке):
    # заявка снова открыта + причина
    try:
        link = url_for('faults.fault_detail', fault_id=f.id)
        mech_ids = set(mechanic_team_ids(exclude=current_user.id))
        if getattr(f, 'technician_id', None):
            mech_ids.add(f.technician_id)
        for t in (f.assigned_technicians or []):
            if getattr(t, 'id', None):
                mech_ids.add(t.id)
        mech_ids.discard(current_user.id)
        rows = [_notify_i18n_row(
            uid, 'Fault reopened',
            lambda _f: f"{_f('Fault reopened')}: {f.title} — {_f('Reason')}: {reason}",
            link) for uid in sorted(mech_ids)]
        create_notifications(rows)
    except Exception as _ne:
        print('reopen notify', _ne)
    if request.is_json:
        return jsonify({'ok': True})
    flash(_('Fault reopened'), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))
