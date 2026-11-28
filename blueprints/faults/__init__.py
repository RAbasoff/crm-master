"""
Faults blueprint — fault reports, work reports, status management
"""
import os, json
from datetime import datetime, date, timedelta
from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify, current_app
from flask_login import login_required, current_user
from flask_babel import gettext as _, force_locale
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload, subqueryload

from models import (db, FaultReport, FaultPhoto, FaultVideo, FaultStatusHistory,
                    WorkReport, WorkReportPhoto, User, Machine, Equipment, Contractor,
                    VoorraadItem, VoorraadMutatie, FaultWorkSession, now_local)
from utils import resolve_user_id
from utils import role_required, log_audit, create_notification, add_work_report, safe_commit, safe_int, safe_float, safe_date, save_uploaded_file, sanitize_like
from logs import create_notifications, _user_language, notify_row
from security import director_only, is_head_of_ts
from notify import director_recipients

import logging as _lg_faults

bp = Blueprint('faults', __name__, url_prefix='/faults')


# ── Статусы заявки (CMMS-модель ProMaster) ──────────────────────────
# open=open/NEW, diagnosis, in_progress, paused, waiting_parts,
# parts_ordered, testing, resolved, rejected, closed.
# reopened — только как переход из resolved/closed/rejected.
# Свободный порядок статусов (без диагностики). Диагностика — только для старых заявок.
FAULT_STATUSES = (
    'open', 'accepted', 'in_progress', 'paused',
    'waiting_parts', 'parts_ordered', 'testing', 'resolved', 'rejected',
    'closed', 'reopened',
)
# «Диагностика» остаётся в словарях для отображения старых заявок
LEGACY_STATUSES = ('diagnosis',)
ALL_WORK_STATUSES = FAULT_STATUSES

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
# Статусы можно менять в любом порядке (кроме diagnosis — старые только для показа)
ALLOWED_TRANSITIONS = {
    s: tuple(x for x in FAULT_STATUSES if x != s)
    for s in FAULT_STATUSES
}
# старые статусы → любые новые
ALLOWED_TRANSITIONS['diagnosis'] = FAULT_STATUSES

# SLA: реакция (минуты); время допустимой паузы не входит в рабочее время
SLA_MINUTES = {'critical': 15, 'high': 60, 'normal': 240, 'low': 1440}
SLA_MSGIDS = {
    'critical': 'Critical < 15 min',
    'high': 'High < 1 hour',
    'normal': 'Normal < 4 hours',
    'low': 'Low < 24 hours',
}
SLA_LABELS = SLA_MSGIDS


def sla_info(f):
    limit = SLA_MINUTES.get(f.priority or 'normal', 240)
    created = f.created_at
    reacted = f.first_response_at or f.accepted_at
    react_min = None
    if created and reacted:
        react_min = round((reacted - created).total_seconds() / 60.0, 1)
    key = f.priority or 'normal'
    return {
        'limit_min': limit,
        'label': _(SLA_MSGIDS.get(key, str(limit) + ' min')),
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

    RESOLVED — механик / директор (отметить выполнение).
    CLOSED / REOPENED — только админ или начальник ТС (Ruslan Abasoff).
    """
    if new_status in ('closed', 'reopened'):
        return is_head_of_ts(user)
    # остальные рабочие переходы — механик / админ / директор
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
        # Только начальник ТС / админ может закрыть или переоткрыть из любого статуса
        if new_status in ('closed', 'reopened') and is_head_of_ts(user):
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
                reason = f"{_('Pause reason')}: {translate_pause_reason(f.pause_reason)}"
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
    _prev_tech = f.technician_id
    if new_status == 'accepted' and not f.accepted_at:
        f.accepted_at = now_local()
        if not f.technician_id:
            f.technician_id = user.id
    _new_tech = f.technician_id if f.technician_id != _prev_tech else None

    f.status = new_status
    history = FaultStatusHistory(
        fault_id=f.id, old_status=old_status, new_status=new_status,
        reason=reason, changed_by=user.id,
    )
    db.session.add(history)

    # Уведомляем создателя заявки: создана / принята / решена / на паузе / закрыта —
    # на языке заявителя, с временем и результатом
    try:
        _detail = None
        if new_status == 'paused':
            # Причина паузы — человекочитаемая (не код), комментарий в скобках
            with force_locale('nl'):
                _reason = (translate_pause_reason(pause_reason)
                           if pause_reason in PAUSE_REASONS else (reason or ''))
            if pause_comment:
                _reason = f'{_reason} ({pause_comment})' if _reason else pause_comment
            _detail = _reason or None
        elif new_status == 'closed' and reason and reason != 'closed by head':
            _detail = reason
        if new_status != old_status and new_status in ('accepted', 'resolved', 'closed', 'paused'):
            _notify_reporter_status(f, new_status, detail=_detail)
        # Заявителю: НАЗНАЧЕН МЕХАНИК (когда механик только что назначен)
        if _new_tech:
            _tu = db.session.get(User, _new_tech)
            if _tu is not None:
                _an = _reporter_row(f, 'assigned',
                                    detail=_tu.display_name or _tu.username)
                if _an:
                    create_notifications([_an])
        # Директор: только создание / пауза (с причиной) / решение / закрытие
        if new_status != old_status and new_status in ('paused', 'resolved', 'closed'):
            _notify_director_status(f, new_status, detail=_detail)
    except Exception:
        pass

    return True, ''


_LIFECYCLE_WORDS = {
    'created':  ('Fault created',  'CREATED'),
    'accepted': ('Fault accepted', 'ACCEPTED'),
    'assigned': ('Mechanic assigned', 'ASSIGNED'),
    'paused':   ('Fault paused',   'PAUSED'),
    'resolved': ('Fault resolved', 'RESOLVED'),
    'closed':   ('Fault closed',   'CLOSED'),
}


def _lifecycle_msg(f, kind, detail=None, lang='ru'):
    """Локализованный текст: СЛОВО: <заголовок> — <дата время>[. причина]"""
    title_msgid, word_msgid = _LIFECYCLE_WORDS[kind]
    when = now_local().strftime('%d.%m.%Y %H:%M')
    with force_locale(lang):
        title = _(title_msgid)
        word = _(word_msgid)
        msg = f'{word}: {f.title} — {when}'
        if detail:
            if kind == 'paused':
                msg += f'. {_("Reason")}: {detail}'
            else:
                msg += f'. {detail}'
    return title, msg


def _lang_for(f):
    try:
        return _user_language(f.reporter_id) if getattr(f, 'reporter_id', None) else 'ru'
    except Exception:
        return 'ru'


def _notify_reporter_status(f, kind, detail=None):
    """Заявитель: создана / прочитана / решена / закрыта — на его языке."""
    r = _reporter_row(f, kind, detail)
    if r:
        create_notifications([r])


def _reporter_row(f, kind, detail=None):
    """Строка уведомления заявителю (или None)."""
    if not getattr(f, 'reporter_id', None) or kind not in ('created', 'accepted', 'assigned', 'paused', 'resolved', 'closed'):
        return None
    title, msg = _lifecycle_msg(f, kind, detail=detail, lang=_lang_for(f))
    return {'user_id': f.reporter_id, 'title': title, 'message': msg,
            'type': 'fault', 'link': f'/faults/{f.id}'}


def _notify_i18n(user_id, title_msgid, build_msg, link, ntype='fault'):
    """Одиночное уведомление в локали ПОЛУЧАТЕЛЯ."""
    create_notifications([_notify_i18n_row(user_id, title_msgid, build_msg, link, ntype)])


def fault_work_hours(f):
    """Чистое время ремонта (часы): подтверждённое (work_hours) или авто-расчёт
    «от принятия до решения минус паузы» (правило владельца 2026-11-21)."""
    if f is None:
        return None
    if f.work_hours:
        try:
            return round(float(f.work_hours), 2)
        except (TypeError, ValueError):
            pass
    start = f.accepted_at or f.created_at
    end = f.resolved_at or now_local()
    if not start or not end:
        return None
    total = (end - start).total_seconds() / 3600.0
    pause_st = ('paused', 'waiting_parts', 'parts_ordered')
    try:
        pauses = 0.0
        t_pause = None
        hist = (FaultStatusHistory.query.filter_by(fault_id=f.id)
                .order_by(FaultStatusHistory.changed_at).all())
        for h in hist:
            st = (h.new_status or '')
            if st in pause_st and t_pause is None:
                t_pause = h.changed_at
            elif t_pause is not None and st not in pause_st:
                if h.changed_at:
                    pauses += (h.changed_at - t_pause).total_seconds() / 3600.0
                t_pause = None
        if t_pause is not None:
            pauses += (now_local() - t_pause).total_seconds() / 3600.0
        total -= pauses
    except Exception:
        pass
    return round(max(total, 0.0), 2)


def _notify_i18n_row(user_id, title_msgid, build_msg, link, ntype='fault'):
    """Строка уведомления в локали получателя — единый слой logs.notify_row."""
    return notify_row(user_id, title_msgid, build_msg, link, ntype=ntype)


def _notify_director_status(f, kind, detail=None):
    """Директор: только создание / пауза (с причиной) / решение / закрытие.

    Назначения, склад, отпуска, личные — директору НЕ шлются.
    """
    create_notifications(_director_rows(f, kind, detail))


def _director_rows(f, kind, detail=None):
    """Строки уведомлений директорам (список, пачкой)."""
    if kind not in ('created', 'paused', 'resolved', 'closed'):
        return []
    rows = []
    _ids = set()
    try:
        _ids = director_recipients()
    except Exception:
        pass
    _directors = (User.query.filter(User.id.in_(_ids)).all() if _ids
                  else User.query.filter(User.role == 'director',
                                         User.is_active_user == True).all())
    for d in _directors:
        if getattr(f, 'reporter_id', None) and d.id == f.reporter_id:
            continue
        try:
            lang = _user_language(d.id)
        except Exception:
            lang = 'nl'
        title, msg = _lifecycle_msg(f, kind, detail=detail, lang=lang)
        rows.append({'user_id': d.id, 'title': title, 'message': msg,
                     'type': 'fault', 'link': f'/faults/{f.id}'})
    return rows


# Register route sub-modules
from . import crud, workflow, media
from models import now_local
