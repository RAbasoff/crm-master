# -*- coding: utf-8 -*-
"""Уведомления ProMaster: email + Web Push (SMS полностью удалён).

.env (рядом с программой или в окружении):
  SMTP_HOST=smtp.yandex.ru
  SMTP_PORT=587
  SMTP_USER=robot@domain.ru
  SMTP_PASSWORD=app-password
  SMTP_FROM=robot@domain.ru
  SMTP_TLS=1
  LOG_EMAIL=robot@domain.ru          # куда слать логи (опционально)
"""
import os
import logging
from datetime import datetime

log = logging.getLogger('app.notify')

def _load_dotenv():
    """Прочитать .env рядом с программой в os.environ (без внешних зависимостей)."""
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base = os.getcwd()
    for name in ('.env', '.env.txt'):
        path = os.path.join(base, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception as e:
            log.warning(f'.env load failed: {e}')
        break


_load_dotenv()



def _env(key, default=''):
    return (os.environ.get(key) or default).strip()


def email_configured():
    return bool(_env('SMTP_USER') and _env('SMTP_PASSWORD') and _env('SMTP_HOST'))


def send_email(to_addr, subject, body_html):
    """SMTP email. Возвращает True/False."""
    if not email_configured():
        log.info('Email не настроен (SMTP_USER пуст)')
        return False
    if not to_addr:
        return False
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = _env('SMTP_FROM') or _env('SMTP_USER')
    msg['To'] = to_addr
    msg.attach(MIMEText(body_html or '', 'html', 'utf-8'))
    host = _env('SMTP_HOST')
    port = int(_env('SMTP_PORT', '587'))
    tls = _env('SMTP_TLS', '1') in ('1', 'true', 'yes')
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            if tls:
                server.starttls()
        server.login(_env('SMTP_USER'), _env('SMTP_PASSWORD'))
        server.send_message(msg)
        server.quit()
        log.info(f'Email отправлен: {to_addr} / {subject[:40]}')
        return True
    except Exception as e:
        log.error(f'Email ошибка: {e}')
        return False


def _name_of(u):
    return ((getattr(u, 'display_name', '') or '') + ' ' + (getattr(u, 'username', '') or '')).lower()


def fault_sms_recipients():
    """Кому слать уведомления о новой заявке (email):
    механики + начальник ТС + админ + Peter."""
    from models import User
    from security import is_head_of_ts
    result = {}
    for u in User.query.filter(User.is_active_user == True).all():
        n = _name_of(u)
        role = (getattr(u, 'role', '') or '').lower()
        hit_mech = role == 'technician' or any(k in n for k in ('aris', 'aristidis', 'nikita', 'iqbal', 'ruslan', 'abasoff'))
        hit_admin = role == 'admin'
        hit_peter = 'peter' in n or 'petr' in n or 'петр' in n or 'питер' in n
        hit_head = False
        try:
            hit_head = is_head_of_ts(u)
        except Exception:
            pass
        if hit_mech or hit_peter or hit_head or hit_admin:
            result[u.id] = u
    return list(result.values())


def notify_fault_created(fault, mechanics, head_of_ts_users=None):
    """Новая заявка: механики + начальник ТС — email (push шлёт faults.py)."""
    title = f"Nieuwe storing #{fault.id}: {fault.title}"
    body = (
        f"<p><b>Storing #{fault.id}</b></p>"
        f"<p>{fault.title}</p>"
        f"<p>{(fault.description or '')[:300]}</p>"
        f"<p>Prioriteit: {fault.priority}</p>"
        f"<p>Melder: {getattr(fault, 'reporter_name', '') or ''}</p>"
    )
    sent = 0
    try:
        extra = fault_sms_recipients()
    except Exception:
        extra = []
    seen = {}
    for u in list(mechanics or []) + list(head_of_ts_users or []) + extra:
        if u is not None and getattr(u, 'id', None) not in seen:
            seen[getattr(u, 'id', None)] = u
    for u in seen.values():
        email = getattr(u, 'email', None)
        if email and send_email(email, title, body):
            sent += 1
        # In-app/push уведомления НЕ создаём здесь: их шлёт faults.py
        # («New fault report») — раньше каждый получал дубль.
    return sent


def notify_fault_done(fault, reporter_user=None, head_users=None):
    """Выполнено/закрыто: email заявителю + начальнику ТС."""
    st = getattr(fault, 'status', '') or ''
    title = f"Storing #{fault.id}: {st}"
    body = f"<p>Storing <b>#{fault.id}</b> — {fault.title}</p><p>Status: <b>{st}</b></p>"
    sent = 0
    for u in list(reporter_user and [reporter_user] or []) + list(head_users or []):
        email = getattr(u, 'email', None)
        if email and send_email(email, title, body):
            sent += 1
    return sent




# ─────────────── Получатели: группы Director / механики ───────────────

def group_member_user_ids(group_names):
    """ID пользователей — участников групп ResponsibleGroup (по имени).

    Связь: ResponsibleGroup.members (Verantwoordelijke) -> User.person_id.
    Членство в группе — источник истины: все члены группы получают
    уведомления, даже если аккаунт помечен неактивным.
    """
    from models import ResponsibleGroup, User
    ids = set()
    try:
        groups = ResponsibleGroup.query.filter(
            ResponsibleGroup.name.in_(list(group_names))).all()
        person_ids = [m.id for g in groups for m in g.members]
        if person_ids:
            for u in User.query.filter(User.person_id.in_(person_ids)).all():
                ids.add(u.id)
    except Exception as e:
        log.warning('group_member_user_ids: %s', e)
    return ids


def director_recipients():
    """Директора: все члены группы Director + роль director (активные)."""
    from models import User
    ids = group_member_user_ids(('Director',))
    try:
        for u in User.query.filter(User.role == 'director',
                                   User.is_active_user == True).all():
            ids.add(u.id)
    except Exception:
        pass
    return ids


def technician_recipients():
    """Механики + админы: группа Technician + роли technician/admin (активные)."""
    from models import User
    ids = group_member_user_ids(('Technician',))
    try:
        for u in User.query.filter(User.role.in_(('technician', 'admin')),
                                   User.is_active_user == True).all():
            ids.add(u.id)
    except Exception:
        pass
    return ids


# ─────────────── Ежедневная проверка отправки уведомлений ───────────────

def daily_notify_check(force=False):
    """Самопроверка цепочки уведомлений: библиотека push, живые подписки,
    контрольный push админам. Не чаще 1 раза в 20 часов (воркеров несколько —
    координация через AppSetting). При сбое — уведомление админам.
    """
    from datetime import timedelta
    from models import AppSetting, PushSubscription, User, db
    from logs import create_notification
    now = datetime.now()
    try:
        rec = AppSetting.query.get('notify_daily_check_at')
        last = None
        if rec and rec.value:
            try:
                last = datetime.fromisoformat(rec.value)
            except Exception:
                last = None
        if not force and last and (now - last) < timedelta(hours=20):
            return None
        if rec:
            rec.value = now.isoformat(timespec='seconds')
        else:
            db.session.add(AppSetting(
                key='notify_daily_check_at',
                value=now.isoformat(timespec='seconds')))
        db.session.commit()
    except Exception as e:
        log.warning('daily_notify_check guard: %s', e)
        return None

    problems = []
    try:
        import pywebpush  # noqa: F401
    except Exception as e:
        problems.append('pywebpush: %s' % e)

    subs = []
    try:
        subs = PushSubscription.query.all()
        if not subs:
            problems.append('нет push-подписок')
    except Exception as e:
        problems.append('подписки: %s' % e)

    sent_ok = sent_err = 0
    try:
        from push import send_web_push
        admin_ids = {u.id for u in User.query.filter(User.role == 'admin').all()}
        for s in subs:
            if s.user_id not in admin_ids:
                continue
            res = send_web_push(
                {'endpoint': s.endpoint, 'keys': {'p256dh': s.p256dh, 'auth': s.auth}},
                'Dagelijkse notificatietest',
                'Automatische controle: meldingen werken.',
                url='/notifications', tag='info')
            if res == 'ok':
                sent_ok += 1
            else:
                sent_err += 1
    except Exception as e:
        problems.append('push: %s' % e)
        sent_err += 1
    if sent_err:
        problems.append('push-ошибок: %d' % sent_err)

    status = 'ok subs=%d test-ok=%d' % (len(subs), sent_ok) if not problems \
        else 'FAIL: ' + '; '.join(problems)
    try:
        rec = AppSetting.query.get('notify_daily_check')
        if rec:
            rec.value = status[:190]
        else:
            db.session.add(AppSetting(key='notify_daily_check', value=status[:190]))
        db.session.commit()
    except Exception:
        pass
    if problems:
        log.warning('NOTIFY DAILY CHECK: %s', status)
        try:
            for u in User.query.filter(User.role == 'admin',
                                       User.is_active_user == True).all():
                create_notification(u.id, 'Notification check failed',
                                    status[:300], 'warning', '/notifications')
        except Exception:
            pass
    else:
        log.info('NOTIFY DAILY CHECK: %s', status)
    return status


def start_daily_check(initial_delay=120):
    """Фоновый цикл: раз в сутки проверять отправку уведомлений."""
    import threading
    import time

    def _loop():
        time.sleep(initial_delay)
        while True:
            try:
                daily_notify_check()
            except Exception as e:
                log.warning('NOTIFY DAILY CHECK loop: %s', e)
            time.sleep(6 * 3600)

    try:
        threading.Thread(target=_loop, daemon=True).start()
    except Exception as e:
        log.warning('start_daily_check: %s', e)


def maybe_email_logs():
    """Отправка логов на почту (если задан LOG_EMAIL) — как в Рекламации."""
    to = _env('LOG_EMAIL')
    if not to:
        log.info('Отправка логов на почту отключена (не задан LOG_EMAIL/SMTP)')
        return False
    try:
        from logs_util import read_log_tail
        tail = read_log_tail('app.log', 80)
        err = read_log_tail('error.log', 40)
        html = '<pre>' + '\n'.join(tail[-40:]) + '</pre><hr><pre>' + '\n'.join(err) + '</pre>'
        return send_email(to, f'ProMaster logs {datetime.now():%Y-%m-%d %H:%M}', html)
    except Exception as e:
        log.error(f'LOG EMAIL: {e}')
        return False
