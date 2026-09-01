"""
CRM Мастерская - Module improvements
This file contains all the improvements to be integrated into the main app.
"""

# ============================================================
# 1. DATABASE BACKUP AUTOMATION
# ============================================================

import os
import shutil
from datetime import datetime

def backup_database():
    """Create automatic backup of the database"""
    db_path = os.path.join(os.path.dirname(__file__), 'instance', 'werkplaats.db')
    backup_dir = os.path.join(os.path.dirname(__file__), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(backup_dir, f'werkplaats_{timestamp}.db')
    
    if os.path.exists(db_path):
        shutil.copy2(db_path, backup_path)
        # Keep only last 10 backups
        backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.db')])
        for old in backups[:-10]:
            os.remove(os.path.join(backup_dir, old))
        return backup_path
    return None

# ============================================================
# 2. EMAIL NOTIFICATIONS
# ============================================================

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_email_notification(to_email, subject, body, html=False):
    """Send email notification (configure SMTP settings)"""
    # Configure these in your environment
    smtp_host = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_pass = os.environ.get('SMTP_PASS', '')
    
    if not smtp_user or not smtp_pass:
        return False
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to_email
    
    if html:
        msg.attach(MIMEText(body, 'html'))
    else:
        msg.attach(MIMEText(body, 'plain'))
    
    try:
        server = smtplib.SMTP(smtp_host, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

# ============================================================
# 3. WHATSAPP NOTIFICATIONS (via Twilio)
# ============================================================

def send_whatsapp(to_number, message):
    """Send WhatsApp message via Twilio (configure credentials)"""
    account_sid = os.environ.get('TWILIO_SID', '')
    auth_token = os.environ.get('TWILIO_TOKEN', '')
    from_number = os.environ.get('TWILIO_WHATSAPP_FROM', 'whatsapp:+14155238886')
    
    if not account_sid or not auth_token:
        return False
    
    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        client.messages.create(
            body=message,
            from_=from_number,
            to=f'whatsapp:{to_number}'
        )
        return True
    except Exception as e:
        print(f"WhatsApp error: {e}")
        return False

# ============================================================
# 4. COST TRACKING
# ============================================================

def calculate_fault_cost(fault):
    """Calculate estimated cost for a fault"""
    cost = 0
    # Labor cost (estimated €50/hour)
    for wr in fault.work_report:
        cost += (wr.time_spent_hours or 0) * 50
    # Parts cost (if tracked)
    # TODO: add parts cost tracking
    return cost

# ============================================================
# 5. MAINTENANCE REMINDER CHECK
# ============================================================

def check_maintenance_reminders():
    """Check for upcoming maintenance and send notifications"""
    from datetime import date, timedelta
    today = date.today()
    soon = today + timedelta(days=7)
    
    # Check consumable replacements
    from models import VoorraadItem
    consumables = VoorraadItem.query.filter(
        VoorraadItem.next_replacement.isnot(None),
        VoorraadItem.next_replacement <= soon
    ).all()
    
    reminders = []
    for c in consumables:
        days_left = (c.next_replacement - today).days
        reminders.append({
            'type': 'consumable',
            'name': c.naam,
            'date': c.next_replacement.isoformat(),
            'days_left': days_left,
            'overdue': days_left < 0
        })
    
    return reminders

# ============================================================
# 6. QR CODE GENERATOR FOR MACHINES
# ============================================================

def generate_machine_qr(machine):
    """Generate QR code data for a machine"""
    return {
        'type': 'machine',
        'id': machine.id,
        'name': machine.name,
        'serial': machine.serial_number or '',
        'section': machine.section.name if machine.section else ''
    }

# ============================================================
# 7. REPORT GENERATOR
# ============================================================

def generate_report_data(report_type, date_from, date_to, user_id=None, section_id=None):
    """Generate report data for export"""
    from datetime import datetime
    from models import (FaultReport, VoorraadMutatie, UserActivityLog, 
                       SystemLog, AuditLog, Machine, User)
    
    d_from = datetime.strptime(date_from, '%Y-%m-%d')
    d_to = datetime.strptime(date_to, '%Y-%m-%d')
    
    data = {'headers': [], 'rows': [], 'title': ''}
    
    if report_type == 'faults':
        data['title'] = 'Отчёт по заявкам'
        data['headers'] = ['ID', 'Дата', 'Заголовок', 'Станок', 'Приоритет', 'Статус', 'Репортер']
        q = FaultReport.query.filter(FaultReport.created_at >= d_from, FaultReport.created_at <= d_to)
        if user_id: q = q.filter_by(reporter_id=int(user_id))
        for f in q.order_by(FaultReport.created_at.desc()).all():
            data['rows'].append([
                str(f.id), f.created_at.strftime('%Y-%m-%d %H:%M'), f.title or '',
                f.machine.name if f.machine else '', f.priority or '', f.status or '',
                f.reporter.display_name if f.reporter else ''
            ])
    
    elif report_type == 'warehouse':
        data['title'] = 'Отчёт по складу'
        data['headers'] = ['Дата', 'Товар', 'Тип', 'Количество', 'Комментарий']
        q = VoorraadMutatie.query.filter(VoorraadMutatie.aangemaakt >= d_from, VoorraadMutatie.aangemaakt <= d_to)
        for m in q.order_by(VoorraadMutatie.aangemaakt.desc()).limit(500).all():
            data['rows'].append([
                m.aangemaakt.strftime('%Y-%m-%d %H:%M'), m.item.naam if m.item else '',
                m.type or '', str(m.hoeveelheid), m.opmerking or ''
            ])
    
    elif report_type == 'activity':
        data['title'] = 'Активность пользователей'
        data['headers'] = ['Дата', 'Пользователь', 'Действие', 'Страница', 'Детали']
        q = UserActivityLog.query.filter(UserActivityLog.created_at >= d_from, UserActivityLog.created_at <= d_to)
        if user_id: q = q.filter_by(user_id=int(user_id))
        for r in q.order_by(UserActivityLog.created_at.desc()).limit(500).all():
            data['rows'].append([
                r.created_at.strftime('%Y-%m-%d %H:%M'), r.username or '', r.action or '',
                r.page or '', r.details or ''
            ])
    
    return data

print("CRM improvements module loaded successfully")
