# -*- coding: utf-8 -*-
"""Уведомления ProMaster: email + SMS (как в Рекламации, через .env).

.env (рядом с программой или в окружении):
  SMTP_HOST=smtp.yandex.ru
  SMTP_PORT=587
  SMTP_USER=robot@domain.ru
  SMTP_PASSWORD=app-password
  SMTP_FROM=robot@domain.ru
  SMTP_TLS=1
  LOG_EMAIL=robot@domain.ru          # куда слать логи (опционально)

  SMS_PROVIDER=sms_ru                 # sms_ru | twilio | disabled
  SMS_API_KEY=...
  SMS_FROM=ProMaster
  SMS_ENABLED=1
"""
import os
import logging
from datetime import datetime

log = logging.getLogger('app.notify')
SMS_LAST_ERROR = ''

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


def sms_configured():
    return _env('SMS_ENABLED', '0') in ('1', 'true', 'yes') and bool(_env('SMS_API_KEY'))


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


def send_sms(phone, text):
    """SMS через провайдера из .env. Возвращает True/False."""
    import base64 as _b64
    if not sms_configured():
        log.info('SMS не настроен (SMS_ENABLED/SMS_API_KEY)')
        return False
    if not phone:
        return False
    phone = str(phone).strip().replace(' ', '')
    provider = _env('SMS_PROVIDER', 'sms_ru').lower()
    try:
        if provider in ('sms_ru', 'smsru'):
            import urllib.parse
            import urllib.request
            data = urllib.parse.urlencode({
                'api_id': _env('SMS_API_KEY'),
                'to': phone,
                'text': (text or '')[:160],
                'from': _env('SMS_FROM', 'ProMaster'),
            }).encode()
            urllib.request.urlopen('https://sms.ru/sms/send', data=data, timeout=20)
            log.info(f'SMS отправлено: {phone}')
            try:
                from sms_counter import sms_count_add
                sms_count_add()
            except Exception:
                pass
            return True
        if provider == 'twilio':
            import urllib.parse
            import urllib.request
            import base64
            # Account SID (AC...) for URL; API Key SID (SK...) or Account SID for Basic auth
            key = _env('SMS_API_KEY')
            account_sid = _env('SMS_ACCOUNT_SID') or (key if key.startswith('AC') else '')
            api_key_sid = _env('SMS_API_KEY_SID') or (key if key.startswith('SK') else '')
            secret = _env('SMS_API_SECRET') or _env('SMS_AUTH_TOKEN') or _env('SMS_CLIENT_SECRET')
            auth_user = api_key_sid or account_sid
            if not account_sid or not auth_user or not secret:
                log.error(f'Twilio config incomplete: account_sid={bool(account_sid)} auth_user={bool(auth_user)} secret={bool(secret)}')
                return False
            from_num = _env('SMS_FROM')
            url = f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json'
            auth = base64.b64encode(f'{auth_user}:{secret}'.encode()).decode()
            data = urllib.parse.urlencode({
                'To': phone, 'From': from_num, 'Body': (text or '')[:160],
            }).encode()
            req = urllib.request.Request(url, data=data, headers={'Authorization': f'Basic {auth}'})
            urllib.request.urlopen(req, timeout=20)
            log.info(f'SMS (twilio) отправлено: {phone}')
            try:
                from sms_counter import sms_count_add
                sms_count_add()
            except Exception:
                pass
            return True
        if provider in ('gatewayapi', 'gateway', 'app.gatewayapi.com'):
            import urllib.parse
            import urllib.request
            import json as _json
            token = _env('SMS_API_KEY')
            sender = _env('SMS_FROM', 'ProMaster')
            payload = _json.dumps({
                'recipients': [{'msisdn': phone}],
                'message': (text or '')[:160],
                'sender': sender,
            }).encode()
            req = urllib.request.Request(
                'https://gatewayapi.com/rest/mtsms',
                data=payload,
                headers={
                    'Authorization': 'Basic ' + _b64.b64encode((token + ':').encode()).decode(),
                    'Content-Type': 'application/json',
                },
                method='POST',
            )
            urllib.request.urlopen(req, timeout=20)
            log.info(f'SMS (gatewayapi) отправлено: {phone}')
            try:
                from sms_counter import sms_count_add
                sms_count_add()
            except Exception:
                pass
            return True
        log.warning(f'Неизвестный SMS_PROVIDER: {provider}')
        return False
    except Exception as e:
        global SMS_LAST_ERROR
        body = ''
        try:
            if hasattr(e, 'read'):
                body = e.read().decode('utf-8', 'replace')[:300]
        except Exception:
            pass
        SMS_LAST_ERROR = (body or str(e))
        log.error(f'SMS ошибка: {e} {body}')
        return False


def _name_of(u):
    return ((getattr(u, 'display_name', '') or '') + ' ' + (getattr(u, 'username', '') or '')).lower()


def fault_sms_recipients():
    """Кому слать SMS о новой заявке:
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
    """Новая заявка: механики + начальник ТС — email/SMS."""
    title = f"Nieuwe storing #{fault.id}: {fault.title}"
    body = (
        f"<p><b>Storing #{fault.id}</b></p>"
        f"<p>{fault.title}</p>"
        f"<p>{(fault.description or '')[:300]}</p>"
        f"<p>Prioriteit: {fault.priority}</p>"
        f"<p>Melder: {getattr(fault, 'reporter_name', '') or ''}</p>"
    )
    reporter = getattr(fault, 'reporter_label', None) or getattr(fault, 'reporter_name', '') or '—'
    machine = getattr(fault, 'target_name', None) or '—'
    loc = ''
    try:
        m = getattr(fault, 'machine', None)
        if m is not None and getattr(m, 'section', None) is not None:
            loc = m.section.name or ''
    except Exception:
        loc = ''
    prio = {'critical': 'Kritiek', 'high': 'Hoog', 'normal': 'Normaal', 'low': 'Laag'}.get(
        (fault.priority or '').lower(), fault.priority or '—')
    sms_text = (
        f'Gebruiker {reporter} heeft een nieuwe storing gemeld voor machine {machine}'
        f' in {loc or "—"}. '
        f'{(fault.title or "")[:100]}. '
        f'Prioriteit: {prio}'
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
        phone = getattr(u, 'phone', None) or getattr(u, 'telefoon', None)
        if email and send_email(email, title, body):
            sent += 1
        if phone and send_sms(phone, sms_text):
            sent += 1
        try:
            from app import create_notification
            create_notification(u.id, 'Nieuwe storing', sms_text, 'fault', f'/faults/{fault.id}')
        except Exception:
            pass
    return sent


def notify_fault_done(fault, reporter_user=None, head_users=None):
    """Выполнено/закрыто: SMS заявителю + начальнику ТС."""
    st = getattr(fault, 'status', '') or ''
    title = f"Заявка #{fault.id}: {st}"
    body = f"<p>Заявка <b>#{fault.id}</b> — {fault.title}</p><p>Статус: <b>{st}</b></p>"
    st_map = {
        'open': 'Open', 'accepted': 'Geaccepteerd', 'in_progress': 'In behandeling',
        'resolved': 'Opgelost', 'closed': 'Gesloten', 'reopened': 'Heropend',
    }
    st_nl = st_map.get((st or '').lower(), st or '')
    sms_text = f'Storing #{fault.id} — {st_nl}: {fault.title[:60]}'
    sent = 0
    for u in list(reporter_user and [reporter_user] or []) + list(head_users or []):
        email = getattr(u, 'email', None)
        phone = getattr(u, 'phone', None) or getattr(u, 'telefoon', None)
        if phone and send_sms(phone, sms_text):
            sent += 1
        if email and send_email(email, title, body):
            sent += 1
        try:
            from app import create_notification
            create_notification(u.id, title, sms_text, 'fault', f'/faults/{fault.id}')
        except Exception:
            pass
    return sent


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
