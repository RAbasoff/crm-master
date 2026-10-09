"""
Faults blueprint — fault reports, work reports, status management
"""
import os, json
from datetime import datetime, date, timedelta
from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify, current_app
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload, subqueryload

from models import (db, FaultReport, FaultPhoto, FaultVideo, FaultStatusHistory,
                    WorkReport, WorkReportPhoto, User, Machine, Equipment, Contractor,
                    VoorraadItem, VoorraadMutatie, FaultWorkSession, now_local)
from utils import role_required, log_audit, create_notification, add_work_report, safe_commit, safe_int, safe_float, safe_date, save_uploaded_file

bp = Blueprint('faults', __name__, url_prefix='/faults')


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

# ── Статусы заявки (CMMS-модель ProMaster) ──────────────────────────
# open=open/NEW, diagnosis, in_progress, paused, waiting_parts,
# parts_ordered, testing, resolved, rejected, closed.
# reopened — только как переход из resolved/closed/rejected.
FAULT_STATUSES = (
    'open', 'accepted', 'diagnosis', 'in_progress', 'paused',
    'waiting_parts', 'parts_ordered', 'testing', 'resolved', 'rejected',
    'closed', 'reopened',
)

STATUS_LABELS = {
    'open': 'Новая',
    'accepted': 'Принята',
    'diagnosis': 'Диагностика',
    'in_progress': 'В работе',
    'paused': 'Приостановлена',
    'waiting_parts': 'Ожидание запчасти',
    'parts_ordered': 'Запчасть заказана',
    'testing': 'Тестирование',
    'resolved': 'Устранена',
    'rejected': 'Отказано',
    'closed': 'Закрыта',
    'reopened': 'Переоткрыта',
}

# msgid для перевода при показе (см. translate_status)
STATUS_MSGIDS = {
    'open': 'New',
    'accepted': 'Accepted',
    'diagnosis': 'Diagnosis',
    'in_progress': 'In progress',
    'paused': 'Paused',
    'waiting_parts': 'Waiting for part',
    'parts_ordered': 'Part ordered',
    'testing': 'Testing',
    'resolved': 'Resolved',
    'rejected': 'Rejected',
    'closed': 'Closed',
    'reopened': 'Reopened',
}


def translate_status(status):
    """Подпись статуса на языке текущего пользователя."""
    key = STATUS_MSGIDS.get(status)
    return _(key) if key else (STATUS_LABELS.get(status, status))

# Строгая цепочка: Новая → Принята → Диагностика → В работе → Тестирование → Устранена → Закрыта
# Из «В работе» — Пауза / Ожидание / Заказ; после причин — снова «В работе».
ALLOWED_TRANSITIONS = {
    'open':          ('accepted',),
    'accepted':      ('diagnosis', 'paused', 'waiting_parts'),
    'diagnosis':     ('in_progress', 'paused', 'waiting_parts', 'rejected'),
    'in_progress':   ('diagnosis', 'testing', 'paused', 'waiting_parts', 'parts_ordered'),
    'paused':        ('in_progress', 'waiting_parts', 'parts_ordered', 'rejected'),
    'waiting_parts': ('parts_ordered', 'in_progress'),
    'parts_ordered': ('in_progress', 'waiting_parts'),
    'testing':       ('in_progress', 'resolved', 'diagnosis'),
    'resolved':      ('closed', 'testing'),
    'rejected':      ('reopened', 'accepted'),
    'closed':        ('reopened',),
    'reopened':      ('diagnosis', 'accepted', 'in_progress'),
}

# SLA: реакция (минуты); время допустимой паузы не входит в рабочее время
SLA_MINUTES = {'critical': 15, 'high': 60, 'normal': 240, 'low': 1440}
SLA_LABELS = {
    'critical': 'КРИТИЧЕСКИЙ < 15 мин',
    'high': 'ВЫСОКИЙ < 1 час',
    'normal': 'СРЕДНИЙ < 4 часа',
    'low': 'НИЗКИЙ < 24 часа',
}


def sla_info(f):
    limit = SLA_MINUTES.get(f.priority or 'normal', 240)
    created = f.created_at
    reacted = f.first_response_at or f.accepted_at
    react_min = None
    if created and reacted:
        react_min = round((reacted - created).total_seconds() / 60.0, 1)
    return {
        'limit_min': limit,
        'label': SLA_LABELS.get(f.priority or 'normal', str(limit) + ' min'),
        'reacted_min': react_min,
        'ok': (react_min <= limit) if react_min is not None else None,
        'expired': created is not None and reacted is None and
                   (now_local() - created).total_seconds() / 60.0 > limit,
    }


# Пауза: статус один, причины — отдельно (для аналитики)
PAUSE_REASONS = {
    'waiting_production': 'Машину нельзя остановить / ожидание производства',
    'waiting_approval': 'Ожидание согласования',
    'waiting_specialist': 'Ожидание другого специалиста',
    'no_access': 'Нет доступа к оборудованию',
    'no_tools': 'Нет необходимых инструментов',
    'need_info': 'Требуется дополнительная информация',
    'scheduled_other': 'Работа запланирована на другую дату',
    'other': 'Другая причина',
}

PAUSE_REASON_MSGIDS = {
    'waiting_production': 'Machine cannot be stopped / waiting for production',
    'waiting_approval': 'Waiting for approval',
    'waiting_specialist': 'Waiting for another specialist',
    'no_access': 'No access to equipment',
    'no_tools': 'Missing tools',
    'need_info': 'Additional information required',
    'scheduled_other': 'Work scheduled for another date',
    'other': 'Other reason',
}


def translate_pause_reason(code):
    key = PAUSE_REASON_MSGIDS.get(code)
    return _(key) if key else (PAUSE_REASONS.get(code, code))

# Статусы «работа идёт» — таймер имеет смысл
ACTIVE_WORK_STATUSES = ('in_progress', 'diagnosis', 'testing')
# Статусы «ожидание» — таймер активной работы должен быть закрыт
WAIT_STATUSES = ('paused', 'waiting_parts', 'parts_ordered')
# Статусы «не трогать таймер»
TERMINAL_STATUSES = ('closed', 'resolved', 'open')


def _end_open_work_sessions(user_id, fault_id, note=''):
    """Закрыть открытую сессию работ (при паузе/ожидании/закрытии)."""
    sessions = FaultWorkSession.query.filter_by(user_id=user_id, ended_at=None).all()
    closed = 0
    for s in sessions:
        if fault_id is not None and s.fault_id != fault_id:
            continue
        s.ended_at = now_local()
        s.duration_minutes = round(s.elapsed_seconds / 60.0, 2)
        if note and not s.notes:
            s.notes = note[:500]
        closed += 1
    return closed


def _role_can_set_status(user, new_status):
    """Кто может выставить статус.

    RESOLVED — механик после ремонта.
    CLOSED — только админ / главный механик / начальник ТС (admin, director).
    """
    if new_status == 'closed':
        return user.has_role('admin', 'director')
    if new_status == 'reopened':
        return user.has_role('admin', 'director', 'technician')
    # остальные рабочие переходы — механик / админ
    return user.has_role('admin', 'director', 'technician')


def _set_fault_status(f, new_status, user, reason='', pause_reason='', pause_comment='',
                      pause_until=None, allow_illegal=False):
    """Смена статуса с проверкой переходов, ролей и обслуживанием паузы/таймера.

    Возвращает (ok, error_message).
    """
    old_status = f.status or 'open'
    if new_status not in FAULT_STATUSES:
        return False, _('Invalid status')

    if not _role_can_set_status(user, new_status):
        if new_status == 'closed':
            return False, _('Only head of technical service or admin can close a fault')
        return False, _('Access denied')

    if not allow_illegal and new_status != old_status:
        allowed = ALLOWED_TRANSITIONS.get(old_status, ())
        # Начальство может закрыть заявку из любого статуса (кроме уже закрытой)
        if new_status == 'closed' and user.has_role('admin', 'director'):
            pass
        elif new_status not in allowed:
            return False, _('Transition not allowed: {} → {}').format(
                translate_status(old_status),
                translate_status(new_status))

    if new_status == 'paused':
        # Описание причины обязательно
        comment = (pause_comment or '').strip()
        if pause_reason not in PAUSE_REASONS:
            return False, _('Select a pause reason')
        if not comment:
            return False, _('Describe the reason for pause')
        f.pause_reason = pause_reason
        f.pause_comment = comment
        f.pause_started_at = now_local()
        f.pause_until = pause_until
        _end_open_work_sessions(user.id, f.id, note=_('Auto-stopped: fault paused'))
    elif new_status == 'rejected':
        # Отказано — причина обязательна
        if not (reason or '').strip():
            return False, _('Reason is required for rejection')
        f.pause_reason = None
        f.pause_comment = (reason or '').strip()
        f.pause_started_at = None
        f.pause_until = None
        _end_open_work_sessions(user.id, f.id, note=_('Auto-stopped: fault rejected'))
    elif new_status in WAIT_STATUSES:
        comment = (pause_comment or '').strip()
        if pause_reason:
            f.pause_reason = pause_reason
        elif not f.pause_reason:
            f.pause_reason = 'need_info' if new_status != 'waiting_parts' else 'waiting_production'
        if comment:
            f.pause_comment = comment
        elif not f.pause_comment:
            return False, _('Describe the reason for pause')
        if pause_until:
            f.pause_until = pause_until
        if not f.pause_started_at:
            f.pause_started_at = now_local()
        _end_open_work_sessions(user.id, f.id, note=_('Auto-stopped: waiting'))
    else:
        # выход из паузы / рабочий статус
        if old_status in WAIT_STATUSES or f.pause_reason:
            # фиксируем причину в истории
            if f.pause_reason and not reason:
                reason = f"{_('Pause reason')}: {PAUSE_REASONS.get(f.pause_reason, f.pause_reason)}"
        f.pause_reason = None
        f.pause_comment = None
        f.pause_started_at = None
        f.pause_until = None

    if new_status == 'testing' and f.resolved_at:
        f.resolved_at = None
    if new_status == 'resolved':
        f.resolved_at = now_local()
        _end_open_work_sessions(user.id, f.id, note=_('Auto-stopped: resolved'))
    if new_status == 'closed':
        f.resolved_at = f.resolved_at or now_local()
        _end_open_work_sessions(user.id, f.id, note=_('Auto-stopped: closed'))
    if new_status == 'reopened':
        f.resolved_at = None
        # reopened — транзитом дальше в diagnosis/in_progress
    if new_status == 'accepted' and not f.accepted_at:
        f.accepted_at = now_local()
        if not f.technician_id:
            f.technician_id = user.id

    f.status = new_status
    history = FaultStatusHistory(
        fault_id=f.id, old_status=old_status, new_status=new_status,
        reason=reason, changed_by=user.id,
    )
    db.session.add(history)
    return True, ''

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
    base = FaultReport.query.options(*_FAULTS_EAGER)
    if current_user.has_role('admin', 'director'):
        pagination = base.order_by(FaultReport.created_at.desc()).paginate(page=page, per_page=25, error_out=False)
    elif current_user.has_role('technician'):
        # Механик видит: свои назначения, созданные им, свободные (open)
        pagination = base.filter(
            (FaultReport.technician_id == current_user.id) |
            (FaultReport.reporter_id == current_user.id) |
            (FaultReport.assigned_technicians.any(User.id == current_user.id)) |
            (FaultReport.status == 'open')
        ).order_by(FaultReport.created_at.desc()).paginate(page=page, per_page=25, error_out=False)
    else:
        # Обычный пользователь / ответственный: свои заявки
        uid = current_user.id
        if not isinstance(uid, int):
            person_id = getattr(current_user, 'person_id', None)
            linked = User.query.filter_by(person_id=person_id).first() if person_id else None
            uid = linked.id if linked else -1
        pagination = base.filter_by(reporter_id=uid).order_by(FaultReport.created_at.desc()).paginate(page=page, per_page=25, error_out=False)
    faults = pagination.items
    return render_template('faults.html', faults=faults, pagination=pagination)


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
                reporter_id=current_user.id if isinstance(current_user.id, int) else None
            )
            # ResponsibleAuth (id «r_N») → связанный User
            if f.reporter_id is None:
                person_id = getattr(current_user, 'person_id', None)
                linked = User.query.filter_by(person_id=person_id).first() if person_id else None
                f.reporter_id = linked.id if linked else None
            # Actual reporter: selected user, or free-text name (someone without an account)
            rid = (request.form.get('reporter_id') or '').strip()
            rname = (request.form.get('reporter_name') or '').strip()
            if rid and rid.isdigit() and int(rid) != current_user.id:
                if User.query.get(int(rid)):
                    f.reporter_id = int(rid)
            if rname:
                f.reporter_name = rname[:200]
            else:
                # Имя заявителя: ФИО / display_name, а не роль
                who = current_user.display_name or current_user.username
                if not who or who.lower() in ('admin', 'administrator', 'director', 'technicus'):
                    who = ' '.join(filter(None, [current_user.first_name, current_user.last_name])) or current_user.username
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

            tech_ids = request.form.getlist('technician_ids')
            for tid in tech_ids:
                tech = User.query.get(int(tid))
                if tech:
                    f.assigned_technicians.append(tech)
            if tech_ids:
                f.technician_id = int(tech_ids[0])
                f.status = 'accepted'
                f.accepted_at = now_local()

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

            for tech in f.assigned_technicians:
                create_notification(tech.id, 'Fault assigned to you',
                    f"{_('Machine')}: {target} - {f.title} ({_('Priority')}: {f.priority}, {_('Reporter')}: {who})",
                    'fault',
                    url_for('faults.fault_detail', fault_id=f.id)
                )
            if not f.assigned_technicians:
                for tech in User.query.filter_by(role='technician', is_active_user=True).all():
                    create_notification(tech.id, 'New fault report',
                        f"{_('Machine')}: {target} - {f.title} ({_('Reporter')}: {who})",
                        'fault',
                        url_for('faults.fault_detail', fault_id=f.id)
                    )

            log_audit('create', 'fault', f.id, f'{f.title} — {target} (приоритет: {f.priority}, заявитель: {who})')
            add_work_report(f'⚠️ Новая поломка: {f.title} — {target} (приоритет: {f.priority}, заявитель: {who})')

            if f.priority == 'critical':
                from app import send_email
                admins = User.query.filter(User.role.in_(['admin', 'director']), User.is_active_user == True).all()
                for admin in admins:
                    if admin.email:
                        send_email(
                            admin.email,
                            f'🔴 КРИТИЧЕСКАЯ ЗАЯВКА: {f.title}',
                            f'<h2>Критическая заявка #{f.id}</h2>'
                            f'<p><strong>Станок/Оборудование:</strong> {target}</p>'
                            f'<p><strong>Заявитель:</strong> {who}</p>'
                            f'<p><strong>Описание:</strong> {f.description[:200]}</p>'
                            f'<p><a href="https://rabasoff.pythonanywhere.com/faults/{f.id}">Открыть заявку</a></p>'
                        )

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
        uid = current_user.id if isinstance(current_user.id, int) else None
        if uid is None:
            person_id = getattr(current_user, 'person_id', None)
            linked = User.query.filter_by(person_id=person_id).first() if person_id else None
            uid = linked.id if linked else -1
        if f.reporter_id != uid:
            flash(_('ДОСТУП ЗАКРЫТ. НЕ ДОСТАТОЧНО ПРАВ.'), 'error')
            return redirect(url_for('faults.faults_list'))
    technicians = User.query.filter_by(role='technician', is_active_user=True).order_by(User.display_name).all()
    contractors = Contractor.query.filter_by(is_active=True).order_by(Contractor.company_name).all()
    my_session = FaultWorkSession.query.filter_by(
        fault_id=f.id, user_id=current_user.id, ended_at=None
    ).first()
    open_session = FaultWorkSession.query.filter_by(
        user_id=current_user.id, ended_at=None
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
        label = STATUS_LABELS.get(h.new_status, h.new_status)
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

    return render_template(
        'fault_detail.html', fault=f, technicians=technicians, contractors=contractors,
        my_session=my_session, open_session=open_session,
        work_sessions=sessions, total_work_minutes=total_minutes,
        pause_reasons=PAUSE_REASONS, allowed_next=allowed_next,
        status_labels=STATUS_LABELS, wait_minutes=wait_minutes,
        active_work_statuses=ACTIVE_WORK_STATUSES, wait_statuses=WAIT_STATUSES,
        timeline=timeline,
        status_msgs=STATUS_MSGIDS, pause_reason_msgs=PAUSE_REASON_MSGIDS,
        sla=sla, sla_minutes=SLA_MINUTES, sla_labels=SLA_LABELS,
    )


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

    sess = FaultWorkSession(fault_id=f.id, user_id=current_user.id, started_at=now_local())
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
    add_work_report(f'▶️ Начаты работы по поломке #{f.id}: {f.title} ({current_user.display_name or current_user.username})')
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
        fault_id=f.id, user_id=current_user.id, ended_at=None
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
    add_work_report(f'⏹ Окончены работы по поломке #{f.id}: {f.title} ({minutes} мин)')
    flash(_('Work finished') + f' — {minutes} ' + _('min'), 'success')
    if request.is_json:
        return jsonify({'ok': True, 'duration_minutes': minutes, 'session_id': sess.id})
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/accept', methods=['POST'])
@login_required
@role_required('technician', 'admin', 'director')
def fault_accept(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    ok, err = _set_fault_status(f, 'accepted', current_user, reason='accepted')
    if not ok:
        flash(err, 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    f.technician_id = current_user.id
    f.accepted_at = f.accepted_at or now_local()
    f.first_response_at = f.first_response_at or now_local()
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))
    log_audit('accept', 'fault', f.id, f'{f.title} — {f.target_name}')
    create_notification(f.reporter_id, 'Fault accepted',
        f"{_('Technician')} {current_user.display_name} {_('accepted your fault report')}: {f.title}",
        'info',
        url_for('faults.fault_detail', fault_id=f.id)
    )
    flash(_('Fault report accepted'), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/assign', methods=['POST'])
@login_required
@role_required('admin', 'director')
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
    f.status = 'accepted'
    f.accepted_at = now_local()
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    for tech in f.assigned_technicians:
        create_notification(tech.id, 'Fault assigned to you',
            f"{_('Admin assigned fault to you')}: {f.title} ({_('Machine')}: {f.target_name})",
            'fault',
            url_for('faults.fault_detail', fault_id=f.id)
        )
    create_notification(f.reporter_id, 'Fault assigned',
        f"{_('Your fault assigned to')} {', '.join(names)}: {f.title}",
        'info',
        url_for('faults.fault_detail', fault_id=f.id)
    )
    log_audit('assign', 'fault', f.id, f'{f.title} → {", ".join(names)}')
    flash(_('Fault assigned to') + ' ' + (', '.join(names)), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/resolve', methods=['POST'])
@login_required
@role_required('technician', 'admin', 'director')
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
    create_notification(f.reporter_id, 'Fault resolved',
        f"{_('Your fault report has been resolved')}: {f.title}",
        'info',
        url_for('faults.fault_detail', fault_id=f.id)
    )
    # Уведомить начальство: заявка ждёт закрытия
    for head in User.query.filter(User.role.in_(['admin', 'director']), User.is_active_user == True).all():
        if head.id != current_user.id:
            create_notification(head.id, 'Fault resolved — awaiting close',
                f"#{f.id} {f.title} — {_('Resolved by')} {current_user.display_name or current_user.username}. {_('Please verify and close')}.",
                'info',
                url_for('faults.fault_detail', fault_id=f.id)
            )
    flash(_('Fault report resolved') + '. ' + _('Awaiting close by head'), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/status', methods=['POST'])
@login_required
@role_required('technician', 'admin', 'director')
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
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    log_audit('status_change', 'fault', f.id, f'{old_status} → {new_status}')
    add_work_report(f'🔄 Поломка #{f.id} "{f.title}": статус {old_status} → {new_status}')
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
    create_notification(f.reporter_id, 'Fault closed',
        f"{_('Your fault report has been closed')}: {f.title}. {close_notes}",
        'success',
        url_for('faults.fault_detail', fault_id=f.id)
    )
    log_audit('close', 'fault', f.id, close_notes)
    add_work_report(f'🔒 Поломка #{f.id} "{f.title}" закрыта. {close_notes}')
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
    create_notification(f.reporter_id, 'Fault reopened',
        f"{_('Fault reopened')}: {f.title}. {reason}",
        'warning',
        url_for('faults.fault_detail', fault_id=f.id)
    )
    log_audit('reopen', 'fault', f.id, f'{old_status} → reopened: {reason} ({reopen_date})')
    add_work_report(f'🔓 Поломка #{f.id} "{f.title}" переоткрыта. Причина: {reason}')
    if request.is_json:
        return jsonify({'ok': True})
    flash(_('Fault reopened'), 'success')
    return redirect(url_for('faults.fault_detail', fault_id=f.id))


@bp.route('/<int:fault_id>/work-report', methods=['GET', 'POST'])
@login_required
@role_required('technician', 'admin', 'director')
def work_report_new(fault_id):
    f = FaultReport.query.get_or_404(fault_id)
    # Каждый механик ведёт свой отчёт (диагностика, ход работ, результат)
    if request.method == 'POST':
        wr = WorkReport(
            fault_id=f.id,
            technician_id=current_user.id,
            work_description=request.form['work_description'],
            parts_used=request.form.get('parts_used', '[]'),
            time_spent_hours=safe_float(request.form.get('time_spent_hours'), 0)
        )
        db.session.add(wr)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('faults.fault_detail', fault_id=f.id))

        if 'photos' in request.files:
            for photo in request.files.getlist('photos'):
                if photo.filename:
                    filename = save_uploaded_file(photo, prefix=f"work_{wr.id}_")
                    if filename:
                        wp = WorkReportPhoto(report_id=wr.id, filename=filename, description=request.form.get('photo_desc', ''))
                        db.session.add(wp)
            if not safe_commit():
                flash(_('Work report saved but photo upload failed'), 'warning')

        # Deduct parts from warehouse (non-fatal — work report already saved)
        try:
            parts = json.loads(wr.parts_used)
            for part in parts:
                item = VoorraadItem.query.get(part['part_id'])
                if item:
                    item.hoeveelheid -= part['quantity']
                    mutatie = VoorraadMutatie(
                        item_id=item.id, type='uitgaand',
                        hoeveelheid=part['quantity'],
                        opmerking=f"Work report #{wr.id} for fault #{f.id}"
                    )
                    db.session.add(mutatie)
            if not safe_commit():
                db.session.rollback()
        except (ValueError, KeyError, TypeError):
            db.session.rollback()

        # Статус — через общую машину переходов (история + проверка прав)
        ok_st, err_st = _set_fault_status(f, 'resolved', current_user,
                                          reason=f'work report #{wr.id}')
        if not ok_st:
            flash(_('Work report saved but status update failed') + f': {err_st}', 'warning')
            return redirect(url_for('faults.fault_detail', fault_id=f.id))
        if not safe_commit():
            flash(_('Work report saved but status update failed'), 'warning')
            return redirect(url_for('faults.fault_detail', fault_id=f.id))

        log_audit('create', 'work_report', wr.id, f'Отчёт по поломке #{f.id}: {f.title} ({wr.time_spent_hours}ч)')
        add_work_report(f'📝 Отчёт о работе по поломке #{f.id}: {f.title} ({wr.time_spent_hours}ч)')
        flash(_('Work report created'), 'success')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    return render_template('work_report_form.html', fault=f, warehouse_items=VoorraadItem.query.all(), report=None)


@bp.route('/<int:fault_id>/work-report/<int:report_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('technician', 'admin', 'director')
def work_report_edit(fault_id, report_id):
    f = FaultReport.query.get_or_404(fault_id)
    wr = WorkReport.query.get_or_404(report_id)
    if request.method == 'POST':
        try:
            old_parts = json.loads(wr.parts_used) if wr.parts_used else []
            for part in old_parts:
                item = VoorraadItem.query.get(part['part_id'])
                if item:
                    item.hoeveelheid += part['quantity']
                    mutatie = VoorraadMutatie(
                        item_id=item.id, type='inkomend',
                        hoeveelheid=part['quantity'],
                        opmerking=f"Reversed: work report #{wr.id} edit"
                    )
                    db.session.add(mutatie)
            if not safe_commit():
                db.session.rollback()
        except (ValueError, KeyError, TypeError):
            db.session.rollback()

        wr.work_description = request.form['work_description']
        wr.parts_used = request.form.get('parts_used', '[]')
        wr.time_spent_hours = safe_float(request.form.get('time_spent_hours'), 0)

        if 'photos' in request.files:
            for photo in request.files.getlist('photos'):
                if photo.filename:
                    filename = save_uploaded_file(photo, prefix=f"work_{wr.id}_")
                    if filename:
                        wp = WorkReportPhoto(report_id=wr.id, filename=filename, description=request.form.get('photo_desc', ''))
                        db.session.add(wp)

        try:
            new_parts = json.loads(wr.parts_used)
            for part in new_parts:
                item = VoorraadItem.query.get(part['part_id'])
                if item:
                    item.hoeveelheid -= part['quantity']
                    mutatie = VoorraadMutatie(
                        item_id=item.id, type='uitgaand',
                        hoeveelheid=part['quantity'],
                        opmerking=f"Work report #{wr.id} (edited) for fault #{f.id}"
                    )
                    db.session.add(mutatie)
            if not safe_commit():
                db.session.rollback()
        except (ValueError, KeyError, TypeError):
            db.session.rollback()

        log_audit('update', 'work_report', wr.id, f'Отчёт по поломке #{f.id}: {f.title}')
        flash(_('Work report updated'), 'success')
        return redirect(url_for('faults.fault_detail', fault_id=f.id))

    return render_template('work_report_form.html', fault=f, warehouse_items=VoorraadItem.query.all(), report=wr)


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
