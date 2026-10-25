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
            return True
        if provider == 'twilio':
            import urllib.parse
            import urllib.request
            import base64
            sid = _env('SMS_API_KEY')
            token = _env('SMS_AUTH_TOKEN') or _env('SMS_API_SECRET')
            from_num = _env('SMS_FROM')
            url = f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json'
            auth = base64.b64encode(f'{sid}:{token}'.encode()).decode()
            data = urllib.parse.urlencode({
                'To': phone, 'From': from_num, 'Body': (text or '')[:160],
            }).encode()
            req = urllib.request.Request(url, data=data, headers={'Authorization': f'Basic {auth}'})
            urllib.request.urlopen(req, timeout=20)
            log.info(f'SMS (twilio) отправлено: {phone}')
            return True
        log.warning(f'Неизвестный SMS_PROVIDER: {provider}')
        return False
    except Exception as e:
        log.error(f'SMS ошибка: {e}')
        return False


def notify_fault_created(fault, mechanics, head_of_ts_users=None):
    """Новая заявка: механики + начальник ТС — email/SMS."""
    title = f"Новая заявка #{fault.id}: {fault.title}"
    body = (
        f"<p><b>Заявка #{fault.id}</b></p>"
        f"<p>{fault.title}</p>"
        f"<p>{(fault.description or '')[:300]}</p>"
        f"<p>Приоритет: {fault.priority}</p>"
        f"<p>Заявитель: {getattr(fault, 'reporter_name', '') or ''}</p>"
    )
    sms_text = f'Заявка #{fault.id}: {fault.title[:80]}'
    sent = 0
    for u in list(mechanics or []) + list(head_of_ts_users or []):
        email = getattr(u, 'email', None)
        phone = getattr(u, 'phone', None) or getattr(u, 'telefoon', None)
        if email and send_email(email, title, body):
            sent += 1
        if phone and send_sms(phone, sms_text):
            sent += 1
        try:
            from app import create_notification
            create_notification(u.id, 'New fault report', sms_text, 'fault', f'/faults/{fault.id}')
        except Exception:
            pass
    return sent


def notify_fault_done(fault, reporter_user=None, head_users=None):
    """Выполнено/закрыто: SMS заявителю + начальнику ТС."""
    st = getattr(fault, 'status', '') or ''
    title = f"Заявка #{fault.id}: {st}"
    body = f"<p>Заявка <b>#{fault.id}</b> — {fault.title}</p><p>Статус: <b>{st}</b></p>"
    sms_text = f'Заявка #{fault.id} — {st}: {fault.title[:60]}'
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
