import threading
import time as _tmod
from collections import OrderedDict
from datetime import datetime, date, timedelta
from flask import request, redirect, url_for, flash, render_template, jsonify, current_app
from flask_login import login_required, current_user
from flask_babel import gettext as _
from sqlalchemy.orm import joinedload, subqueryload

from models import (db, FaultReport, FaultPhoto, FaultVideo, FaultStatusHistory,
                    WorkReport, WorkReportPhoto, User, Machine, Equipment, Contractor,
                    VoorraadItem, VoorraadMutatie, FaultWorkSession, now_local)
from utils import resolve_user_id
from utils import role_required, log_audit, create_notification, add_work_report, safe_commit, safe_int, safe_float, safe_date, save_uploaded_file, sanitize_like
from logs import create_notifications
from security import is_head_of_ts
from notify import technician_recipients, email_configured, notify_fault_created

import logging as _lg_faults

from . import bp
from . import (STATUS_LABELS, STATUS_MSGIDS, translate_status, sla_info,
               ALLOWED_TRANSITIONS, PAUSE_REASONS, PAUSE_REASON_MSGIDS,
               SLA_MINUTES, SLA_LABELS, ACTIVE_WORK_STATUSES, WAIT_STATUSES,
               fault_work_hours, _notify_i18n_row, _reporter_row, _director_rows)


@bp.route('/status-history')
@login_required
@role_required('admin', 'director', 'technician')
def status_history_report():
    """Отчёт по истории статусов заявок (фильтры: период, пользователь, статус)."""
    d_from = request.args.get('from', '') or (now_local() - timedelta(days=30)).strftime('%Y-%m-%d')
    d_to = request.args.get('to', '') or now_local().strftime('%Y-%m-%d')
    user_f = safe_int(request.args.get('user_id')) or None
    status_f = (request.args.get('status') or '').strip()
    fault_f = safe_int(request.args.get('fault_id')) or None

    try:
        date_from = datetime.strptime(d_from, '%Y-%m-%d').date()
        date_to = datetime.strptime(d_to, '%Y-%m-%d').date()
    except ValueError:
        date_from = (now_local() - timedelta(days=30)).date()
        date_to = now_local().date()

    q = FaultStatusHistory.query.options(
        joinedload(FaultStatusHistory.fault),
        joinedload(FaultStatusHistory.changer),
    ).filter(
        FaultStatusHistory.changed_at >= datetime.combine(date_from, datetime.min.time()),
        FaultStatusHistory.changed_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()),
    )
    if user_f:
        q = q.filter(FaultStatusHistory.changed_by == user_f)
    if status_f:
        q = q.filter(FaultStatusHistory.new_status == status_f)
    if fault_f:
        q = q.filter(FaultStatusHistory.fault_id == fault_f)

    history = q.order_by(FaultStatusHistory.changed_at.desc()).limit(500).all()

    # Сводка по статусам
    summary = {}
    for h in history:
        key = h.new_status or '?'
        summary[key] = summary.get(key, 0) + 1

    users = User.query.filter(User.is_active_user == True).order_by(User.display_name).all()
    return render_template(
        'status_history_report.html',
        history=history, summary=summary, users=users,
        date_from=date_from, date_to=date_to,
        user_f=user_f, status_f=status_f, fault_f=fault_f,
        status_labels=STATUS_LABELS,
        status_msgs=STATUS_MSGIDS,
    )


_FAULTS_EAGER = (
    joinedload(FaultReport.machine),
    joinedload(FaultReport.reporter),
    joinedload(FaultReport.technician),
    subqueryload(FaultReport.assigned_technicians),
)

@bp.route('/')
@login_required
def faults_list():
    page = request.args.get('page', 1, type=int)
    q_txt = (request.args.get('q') or '').strip()
    base = FaultReport.query.options(*_FAULTS_EAGER)
    if q_txt:
        _like = f'%{sanitize_like(q_txt)}%'
        base = base.filter(db.or_(
            FaultReport.title.ilike(_like),
            FaultReport.description.ilike(_like),
            FaultReport.machine.has(Machine.name.ilike(_like)),
        ))
    if current_user.has_role('admin', 'director'):
        q = base
    elif current_user.has_role('technician'):
        # Механик видит ВСЕ незакрытые заявки (открытые, в работе, решённые до
        # закрытия) + свои собственные в любом статусе.
        q = base.filter(
            (FaultReport.status.is_(None)) |
            (FaultReport.status.notin_(('closed', 'rejected'))) |
            (FaultReport.technician_id == current_user.id) |
            (FaultReport.reporter_id == current_user.id) |
            (FaultReport.assigned_technicians.any(User.id == current_user.id))
        )
    else:
        uid = current_user.id
        if not isinstance(uid, int):
            person_id = getattr(current_user, 'person_id', None)
            linked = User.query.filter_by(person_id=person_id).first() if person_id else None
            uid = linked.id if linked else -1
        q = base.filter_by(reporter_id=uid)

    # Сортировка по порядковому номеру: desc (новые первыми, по умолчанию) / asc
    CLOSED = ('closed', 'rejected')
    sort_dir = (request.args.get('sort') or 'desc').lower()
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'
    reverse = (sort_dir == 'desc')

    def _num(f):
        return f.serial_no or f.id

    all_f = q.order_by(FaultReport.id.asc()).all()
    active_list = sorted([f for f in all_f if (f.status or '').lower() not in CLOSED],
                         key=_num, reverse=reverse)
    closed_list = sorted([f for f in all_f if (f.status or '').lower() in CLOSED],
                         key=_num, reverse=reverse)

    # Месяцы закрытых: все свёрнуты (включая текущий), внутри — по номеру
    month_groups = OrderedDict()
    for f in closed_list:
        d = f.created_at.date() if f.created_at else now_local().date()
        key = d.strftime('%Y-%m')
        month_groups.setdefault(key, {'label': d.strftime('%m.%Y'), 'orders': []})
        month_groups[key]['orders'].append(f)
    current_key = now_local().strftime('%Y-%m')

    return render_template('faults.html',
                           faults=active_list,
                           closed_list=closed_list,
                           month_groups=month_groups,
                           current_month=current_key,
                           sort_dir=sort_dir,
                           q=q_txt,
                           pagination=None,
                           status_msgs=STATUS_MSGIDS)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def fault_new():
    if request.method == 'POST':
        try:
            machine_id = request.form.get('machine_id', '').strip()
            equipment_id = request.form.get('equipment_id', '').strip()
            if not request.form.get('title') or not request.form.get('description'):
                flash(_('Title and description are required'), 'error')
                return redirect(url_for('faults.fault_new'))
            f = FaultReport(
                title=request.form['title'],
                description=request.form['description'],
                priority=request.form.get('priority', 'normal'),
                machine_id=int(machine_id) if machine_id else None,
                equipment_id=int(equipment_id) if equipment_id else None,
                reporter_id=resolve_user_id()
            )
            # ResponsibleAuth (id «r_N») → связанный User
            if f.reporter_id is None:
                person_id = getattr(current_user, 'person_id', None)
                linked = User.query.filter_by(person_id=person_id).first() if person_id else None
                if not linked and person_id:
                    # авто-связь: User для ответственного person_id
                    try:
                        uname = (getattr(current_user, 'username', None) or f'user{person_id}')[:80]
                        linked = User(username=uname,
                                      display_name=getattr(current_user, 'display_name', None) or uname,
                                      role='user', is_active_user=True)
                        linked.person_id = person_id
                        db.session.add(linked)
                        db.session.flush()
                    except Exception:
                        linked = User.query.filter_by(person_id=person_id).first()
                f.reporter_id = linked.id if linked else None
            # Всегда есть reporter_id — иначе заявка не сохранится (NOT NULL)
            if not f.reporter_id:
                fallback = User.query.filter_by(username='admin').first() or User.query.first()
                f.reporter_id = fallback.id if fallback else None
                if not f.reporter_name:
                    f.reporter_name = getattr(current_user, 'display_name', None) or 'user'
            # Заявка от текущего; админ / начальник ТС — от любого
            who = current_user.display_name or current_user.username
            if not who or who.lower() in ('admin', 'administrator', 'director', 'technicus'):
                who = ' '.join(filter(None, [getattr(current_user, 'first_name', ''), getattr(current_user, 'last_name', '')])) or current_user.username
            f.reporter_name = who
            if current_user.has_role('admin', 'technician') or is_head_of_ts(current_user):
                rid = (request.form.get('reporter_id') or '').strip()
                rname = (request.form.get('reporter_name') or '').strip()
                if rid and rid.isdigit() and User.query.get(int(rid)):
                    f.reporter_id = int(rid)
                    ru = User.query.get(int(rid))
                    if ru:
                        who = ru.display_name or ru.username
                if rname:
                    f.reporter_name = rname[:200]
                elif rid == 'other':
                    f.reporter_name = who

            # Защита от дублей (двойной клик / повторная отправка)
            recent = FaultReport.query.filter(
                FaultReport.title == f.title,
                FaultReport.description == f.description,
                FaultReport.reporter_id == f.reporter_id,
                FaultReport.created_at >= now_local() - timedelta(seconds=30),
            ).first()
            if recent:
                flash(_('This fault report was already created'), 'info')
                return redirect(url_for('faults.fault_detail', fault_id=recent.id))
            db.session.add(f)
            db.session.flush()

            # Механика при создании НЕ выбирают: уведомление получают все
            # механики, назначает начальник ТС (маршрут /assign).

            if not safe_commit():
                db.session.rollback()
                flash(_('Save failed. Please try again.'), 'error')
                return redirect(url_for('faults.fault_new'))

            target = f.target_name
            who = f.reporter_label

            if 'photos' in request.files:
                for photo in request.files.getlist('photos'):
                    if photo.filename:
                        filename = save_uploaded_file(photo, prefix=f"fault_{f.id}_")
                        if filename:
                            db.session.add(FaultPhoto(fault_id=f.id, filename=filename))

            if 'videos' in request.files:
                for video in request.files.getlist('videos'):
                    if video.filename:
                        filename = save_uploaded_file(video, prefix=f"fault_{f.id}_video_")
                        if filename:
                            db.session.add(FaultVideo(fault_id=f.id, filename=filename))
                if not safe_commit():
                    flash(_('Fault created but video upload failed'), 'warning')

            _link = url_for('faults.fault_detail', fault_id=f.id)
            # Все уведомления события — одной пачкой (один commit на диск)
            _notif_rows = []
            for tech in f.assigned_technicians:
                if tech.id == f.reporter_id:
                    continue
                _notif_rows.append(_notify_i18n_row(tech.id, 'Fault assigned to you',
                    lambda _f: f"{_f('Machine')}: {target} - {f.title} ({_f('Priority')}: {f.priority}, {_f('Reporter')}: {who})",
                    _link))
            # Уведомляем: механиков + админ (+ Peter/Ruslan по имени).
            # Директорам идёт отдельное «ЗАЯВКА СОЗДАНА» — без лишних сообщений.
            try:
                notify_ids = set(technician_recipients())
            except Exception:
                notify_ids = set()
                for tech in User.query.filter(User.is_active_user == True, User.role.in_(('technician', 'admin'))).all():
                    notify_ids.add(tech.id)
            for u in User.query.filter(User.is_active_user == True).all():
                if u.role == 'director':
                    continue
                n = ((u.display_name or '') + ' ' + (u.username or '')).lower()
                if 'peter' in n or 'petr' in n:
                    notify_ids.add(u.id)
                if 'ruslan' in n or 'abasoff' in n:
                    notify_ids.add(u.id)
            for tid in notify_ids:
                if tid == f.reporter_id:
                    continue  # заявитель получает только свои 5 сообщений
                _notif_rows.append(_notify_i18n_row(tid, 'New fault report',
                    lambda _f: f"{_f('Machine')}: {target} - {f.title} ({_f('Reporter')}: {who})",
                    _link))
            # Заявитель: «ЗАЯВКА СОЗДАНА: … — время»; директору — то же
            _r_row = _reporter_row(f, 'created')
            if _r_row:
                _notif_rows.append(_r_row)
            _notif_rows += _director_rows(f, 'created')
            create_notifications(_notif_rows)

            log_audit('create', 'fault', f.id, f'{f.title} — {target} (приоритет: {f.priority}, заявитель: {who})')
            try:
                # Email — в фоне; если SMTP не настроен — не трогаем БД из другого потока
                if not email_configured():
                    raise RuntimeError('skip-notify')
                from notify import notify_fault_created
                from security import is_head_of_ts
                _tech_ids = [getattr(u, 'id', None) for u in (f.assigned_technicians or []) if getattr(u, 'id', None)]
                _head_ids = [u.id for u in User.query.filter(User.role.in_(('admin','director')), User.is_active_user==True).all()]
                _fid = f.id
                def _send_notify(_fid=_fid, _tids=_tech_ids, _hids=_head_ids):
                    try:
                        _tmod.sleep(0.5)  # дать запросу завершиться и освободить сессию
                        from app import app as _app
                        with _app.app_context():
                            from models import User as _U, FaultReport as _FR, db as _db
                            _f = _FR.query.get(_fid)
                            _t = _U.query.filter(_U.id.in_(_tids)).all() if _tids else []
                            _h = _U.query.filter(_U.id.in_(_hids)).all() if _hids else []
                            if _f:
                                notify_fault_created(_f, _t, _h)
                            _db.session.remove()
                    except Exception as _e:
                        _lg_faults.warning('notify bg: %s', _e)
                threading.Thread(target=_send_notify, daemon=True).start()
            except Exception as _ne:
                _lg_faults.warning('notify_fault_created: %s', _ne)
            add_work_report(f"{_('New fault')}: {f.title} — {target} ({_('Priority')}: {f.priority}, {_('Reporter')}: {who})")

            if f.priority == 'critical':
                from app import send_email
                admins = User.query.filter(User.role.in_(['admin', 'director']), User.is_active_user == True).all()
                for admin in admins:
                    admin_email = getattr(admin, 'email', None)
                    if admin_email:
                        send_email(
                            admin_email,
                            f"{_('Critical fault')}: {f.title}",
                            f"<h2>{_('Critical fault')} #{f.id}</h2>"
                            f"<p><strong>{_('Machine / Equipment')}:</strong> {target}</p>"
                            f"<p><strong>{_('Reporter')}:</strong> {who}</p>"
                            f"<p><strong>{_('Description')}:</strong> {f.description[:200]}</p>"
                            f"<p><a href=\"https://rabasoff.pythonanywhere.com/faults/{f.id}\">{_('View')}</a></p>"
                        )

            # Авто-TWO на заявку (не low): наряд + подпись как приёмка работы
            try:
                from blueprints.two import auto_two_for_fault
                if auto_two_for_fault(f) is not None:
                    safe_commit()
            except Exception as _te:
                _lg_faults.warning('auto TWO: %s', _te)

            flash(_('Fault report created'), 'success')
            return redirect(url_for('faults.faults_list'))
        except Exception as e:
            db.session.rollback()
            flash(_('Error creating fault report: {}').format(str(e)), 'error')
            return redirect(url_for('faults.fault_new'))

    technicians = User.query.filter_by(role='technician', is_active_user=True).order_by(User.display_name).all()
    machines = Machine.query.order_by(Machine.name).all()
    equipment_list = Equipment.query.order_by(Equipment.name).all()
    users = User.query.filter(User.is_active_user == True).order_by(User.username).all()
    return render_template('fault_form.html', fault=None, machines=machines, equipment_list=equipment_list, technicians=technicians, users=users)


@bp.route('/<int:fault_id>')
@login_required
def fault_detail(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    # Цеховой пользователь — только свои заявки
    if not current_user.has_role('admin', 'director', 'technician'):
        uid = resolve_user_id()
        if uid is None:
            person_id = getattr(current_user, 'person_id', None)
            linked = User.query.filter_by(person_id=person_id).first() if person_id else None
            uid = linked.id if linked else -1
        if f.reporter_id != uid:
            flash(_('Access denied. Insufficient rights.'), 'error')
            return redirect(url_for('faults.faults_list'))
    technicians = User.query.filter_by(role='technician', is_active_user=True).order_by(User.display_name).all()
    contractors = Contractor.query.filter_by(is_active=True).order_by(Contractor.company_name).all()
    my_session = FaultWorkSession.query.filter_by(
        fault_id=f.id, user_id=resolve_user_id(), ended_at=None
    ).first()
    open_session = FaultWorkSession.query.filter_by(
        user_id=resolve_user_id(), ended_at=None
    ).first()
    sessions = FaultWorkSession.query.filter_by(fault_id=f.id).order_by(
        FaultWorkSession.started_at.desc()
    ).all()
    total_minutes = sum((s.duration_minutes or 0) for s in sessions if s.ended_at)
    sla = sla_info(f)
    # Сколько заявка «ждёт» (сумма пауз)
    wait_minutes = 0
    if f.pause_started_at:
        end = now_local()
        wait_minutes = round((end - f.pause_started_at).total_seconds() / 60.0, 1)
    allowed_next = ALLOWED_TRANSITIONS.get(f.status or 'open', ())
    # ── Полная хронология: создание → приёмка → работы → статусы → решение → закрытие ──
    timeline = []
    timeline.append({
        'at': f.created_at, 'kind': 'created',
        'title': _('Fault created'),
        'detail': f.reporter_label, 'user': f.reporter,
    })
    if f.accepted_at:
        timeline.append({
            'at': f.accepted_at, 'kind': 'accepted',
            'title': _('Accepted'),
            'detail': f.technician.display_name if f.technician else '',
            'user': f.technician,
        })
    for s in sessions:
        who = s.user.display_name if s.user else ''
        timeline.append({
            'at': s.started_at, 'kind': 'work_start',
            'title': _('Work started'),
            'detail': who, 'user': s.user,
        })
        if s.ended_at:
            mins = s.duration_minutes or 0
            timeline.append({
                'at': s.ended_at, 'kind': 'work_end',
                'title': _('Work finished'),
                'detail': f"{who} · {mins:.0f} {_('min')}",
                'user': s.user,
            })
    for h in f.status_history:
        label = translate_status(h.new_status)
        kind = h.new_status
        if h.new_status == 'resolved':
            kind = 'resolved'
            label = _('Resolved') + f' — {_("by mechanic")}'
        elif h.new_status == 'closed':
            kind = 'closed'
            label = _('Closed')
        elif h.new_status == 'reopened':
            kind = 'reopened'
            label = _('Reopened')
        elif h.new_status == 'paused':
            kind = 'paused'
            label = _('Paused')
        elif h.new_status == 'rejected':
            kind = 'rejected'
            label = _('Rejected')
        timeline.append({
            'at': h.changed_at, 'kind': kind,
            'title': label,
            'detail': h.reason or '',
            'user': h.changer,
        })
    if f.resolved_at and not any(t['kind'] == 'resolved' for t in timeline):
        timeline.append({
            'at': f.resolved_at, 'kind': 'resolved',
            'title': _('Resolved'), 'detail': '', 'user': None,
        })
    timeline.sort(key=lambda t: (t['at'] or datetime.min))

    try:
        _lead = ((f.resolved_at or now_local()) - f.created_at).total_seconds() / 3600.0 if f.created_at else None
        lead_hours = round(_lead, 1) if _lead is not None else None
    except Exception:
        lead_hours = None
    return render_template(
        'fault_detail.html', fault=f, technicians=technicians, contractors=contractors,
        can_close=is_head_of_ts(current_user),
        my_session=my_session, open_session=open_session,
        work_sessions=sessions, total_work_minutes=total_minutes,
        pause_reasons=PAUSE_REASONS, allowed_next=allowed_next,
        status_labels=STATUS_LABELS, wait_minutes=wait_minutes,
        active_work_statuses=ACTIVE_WORK_STATUSES, wait_statuses=WAIT_STATUSES,
        timeline=timeline,
        status_msgs=STATUS_MSGIDS, pause_reason_msgs=PAUSE_REASON_MSGIDS,
        sla=sla, sla_minutes=SLA_MINUTES, sla_labels=SLA_LABELS,
        work_hours=fault_work_hours(f), lead_hours=lead_hours,
    )


@bp.route('/<int:fault_id>/edit-info', methods=['POST'])
@login_required
def fault_edit_info(fault_id):
    """Корректировка заявки админом/начальником ТС: заголовок и описание."""
    f = FaultReport.query.get_or_404(fault_id)
    if not (current_user.has_role('admin') or is_head_of_ts(current_user)):
        flash(_('Access denied'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    title = (request.form.get('title') or '').strip()
    description = (request.form.get('description') or '').strip()
    if title:
        f.title = title[:200]
    if description:
        f.description = description
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    log_audit('edit', 'fault', f.id, f'Корректировка описания: {f.title}')
    add_work_report(f"#{f.id} {f.title} — {_('Fault updated')} ({current_user.display_name or current_user.username})")
    flash(_('Fault updated'), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def fault_delete(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    title = f.title
    db.session.delete(f)
    if not safe_commit():
        if request.is_json:
            return jsonify({'error': 'Delete failed'}), 500
        flash(_('Delete failed. Please try again.'), 'error')
        return redirect(url_for('faults.faults_list'))
    log_audit('delete', 'fault', fault_id, title)
    if request.is_json:
        return jsonify({'ok': True})
    flash(_('Fault deleted'), 'success')
    return redirect(url_for('faults.faults_list'))
