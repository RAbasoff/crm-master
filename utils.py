from functools import wraps
from flask import flash, redirect, url_for, request
from flask_login import current_user
from flask_babel import gettext as _
from datetime import datetime, timedelta
from models import db, Notification, AuditLog, GroupPermission, Verantwoordelijke, UserActivityLog, SystemLog, WorkReportEntry
import os
from werkzeug.utils import secure_filename

SECTION_KEYS = [
    'machines', 'warehouse', 'orders', 'clients', 'workers', 'faults',
    'messages', 'reports', 'schedule', 'time_tracking', 'vacations',
    'cylinders', 'maintenance', 'purchase_requests', 'users', 'sections', 'floor',
    'invoices', 'contractors', 'two', 'audit_log', 'quality'
]

# Role hierarchy: admin > moderator > director > technician > user
# admin: full access, can modify program settings
# moderator: full access, cannot modify program settings (users, sections)
# director/technician/user: access controlled by allowed_sections and group permissions

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
            if current_user.role not in roles:
                flash(_('Access denied'), 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_user_group_permissions(user):
    """Get permissions from user's group (via person_id link)"""
    if not user.person_id:
        return {}
    person = Verantwoordelijke.query.get(user.person_id)
    if not person or not person.group_id:
        return {}
    perms = GroupPermission.query.filter_by(group_id=person.group_id).all()
    return {p.section_key: p for p in perms}

def user_has_section_access(section_key, action='view'):
    # Admin always has full access
    if current_user.role == 'admin':
        return True
    # Moderator has full access except system settings
    if current_user.role == 'moderator':
        if section_key in ('users', 'audit_log'):
            return False
        return True
    # Check group permissions first
    group_perms = get_user_group_permissions(current_user)
    if section_key in group_perms:
        perm = group_perms[section_key]
        if action == 'view': return perm.can_view
        if action == 'create': return perm.can_create
        if action == 'edit': return perm.can_edit
        if action == 'delete': return perm.can_delete
        return perm.can_view
    # Fallback to access_level
    if current_user.access_level == 'full':
        return True
    if current_user.access_level == 'limited':
        return False
    return any(s.section_key == section_key for s in current_user.allowed_sections)

def section_access_required(section_key, action='view'):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
            if not user_has_section_access(section_key, action):
                flash(_('Access denied'), 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def create_notification(user_id, title, message, ntype='info', link=None):
    n = Notification(user_id=user_id, title=title, message=message, type=ntype, link=link)
    db.session.add(n)
    db.session.commit()

def log_audit(action, entity_type=None, entity_id=None, details=None):
    try:
        log = AuditLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            action=action, entity_type=entity_type, entity_id=entity_id,
            details=details, ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()

def log_user_activity(action, page=None, method=None, entity_type=None, entity_id=None, details=None, duration_ms=None, status_code=None):
    """Log user activity (page views, actions, etc.)"""
    try:
        from models import UserActivityLog
        log = UserActivityLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            username=current_user.username if current_user.is_authenticated else None,
            action=action,
            page=page or (request.path if request else None),
            method=method or (request.method if request else None),
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            ip_address=request.remote_addr if request else None,
            user_agent=str(request.user_agent)[:300] if request else None,
            session_id=session.get('_id', '') if session else None,
            duration_ms=duration_ms,
            status_code=status_code
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()

def log_system(level, category, message, details=None, source=None):
    """Log system events (errors, warnings, info)"""
    try:
        from models import SystemLog
        log = SystemLog(
            level=level,
            category=category,
            message=message,
            details=details,
            source=source,
            user_id=current_user.id if current_user.is_authenticated else None,
            ip_address=request.remote_addr if request else None
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()

def genereer_nummer():
    from models import Opdracht
    vandaag = datetime.utcnow()
    prefix = vandaag.strftime('%Y%m%d')
    laatste = Opdracht.query.filter(Opdracht.nummer.like(f'WO-{prefix}-%')).order_by(Opdracht.id.desc()).first()
    if laatste:
        num = int(laatste.nummer.split('-')[2]) + 1
    else:
        num = 1
    return f'WO-{prefix}-{num:04d}'

def date_plus_days(d, days):
    if d and days:
        return d + timedelta(days=days)
    return None

def save_uploaded_file(file, prefix=''):
    if file and file.filename:
        filename = secure_filename(f"{prefix}{file.filename}")
        from flask import current_app
        file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
        return filename
    return None

def translate_text(text, target_lang):
    if not text or target_lang == 'auto':
        return text
    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source='auto', target=target_lang)
        return translator.translate(text)
    except Exception:
        return text

def run_migrations():
    import sqlite3
    from flask import current_app
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    migrations = [
        ("section_responsible", "CREATE TABLE IF NOT EXISTS section_responsible (section_id INTEGER REFERENCES factory_section(id), person_id INTEGER REFERENCES client(id), PRIMARY KEY (section_id, person_id))"),
        ("gas_cylinder.received_at", "ALTER TABLE gas_cylinder ADD COLUMN received_at DATETIME"),
        ("machine_part.responsible_user_id", "ALTER TABLE machine_part ADD COLUMN responsible_user_id INTEGER REFERENCES user(id)"),
        ("user.access_level", "ALTER TABLE user ADD COLUMN access_level VARCHAR(20) DEFAULT 'full'"),
        ("user.person_id", "ALTER TABLE user ADD COLUMN person_id INTEGER REFERENCES client(id)"),
        ("user_section_access", "CREATE TABLE IF NOT EXISTS user_section_access (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES user(id) NOT NULL, section_key VARCHAR(50) NOT NULL)"),
        ("warehouse_group", "CREATE TABLE IF NOT EXISTS warehouse_group (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, manufacturer VARCHAR(200), description TEXT, created_at DATETIME)"),
        ("warehouse_item.group_id", "ALTER TABLE warehouse_item ADD COLUMN group_id INTEGER REFERENCES warehouse_group(id)"),
        ("responsible_group", "CREATE TABLE IF NOT EXISTS responsible_group (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, description TEXT, created_at DATETIME)"),
        ("client.group_id", "ALTER TABLE client ADD COLUMN group_id INTEGER REFERENCES responsible_group(id)"),
        ("client.monteur_id", "ALTER TABLE client ADD COLUMN monteur_id INTEGER REFERENCES worker(id)"),
        ("worker.user_id", "ALTER TABLE worker ADD COLUMN user_id INTEGER REFERENCES user(id)"),
        ("worker.group_id", "ALTER TABLE worker ADD COLUMN group_id INTEGER REFERENCES responsible_group(id)"),
        ("machine.marker_size", "ALTER TABLE machine ADD COLUMN marker_size INTEGER DEFAULT 45"),
        ("machine.marker_shape", "ALTER TABLE machine ADD COLUMN marker_shape VARCHAR(20) DEFAULT 'circle'"),
        ("machine.contractor_id", "ALTER TABLE machine ADD COLUMN contractor_id INTEGER REFERENCES contractor(id)"),
        ("contractor", """CREATE TABLE IF NOT EXISTS contractor (
            id INTEGER PRIMARY KEY, company_name VARCHAR(200) NOT NULL, contact_person VARCHAR(200),
            contact_position VARCHAR(100), phone VARCHAR(50), phone2 VARCHAR(50), email VARCHAR(100),
            website VARCHAR(200), address VARCHAR(300), postcode VARCHAR(20), city VARCHAR(100),
            country VARCHAR(100) DEFAULT 'Nederland', kvk_number VARCHAR(50), btw_number VARCHAR(50),
            iban VARCHAR(50), service_type VARCHAR(200), contract_number VARCHAR(100),
            contract_start DATE, contract_end DATE, notes TEXT, is_active BOOLEAN DEFAULT 1, created_at DATETIME
        )"""),
        ("contractor_employee", "CREATE TABLE IF NOT EXISTS contractor_employee (id INTEGER PRIMARY KEY, contractor_id INTEGER REFERENCES contractor(id) NOT NULL, name VARCHAR(200) NOT NULL, position VARCHAR(100), phone VARCHAR(50), email VARCHAR(100), notes TEXT)"),
        ("audit_log", "CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES user(id), action VARCHAR(50) NOT NULL, entity_type VARCHAR(50), entity_id INTEGER, details TEXT, ip_address VARCHAR(50), created_at DATETIME)"),
        ("responsible_group.access_level", "ALTER TABLE responsible_group ADD COLUMN access_level VARCHAR(20) DEFAULT 'user'"),
        ("group_permission", """CREATE TABLE IF NOT EXISTS group_permission (
            id INTEGER PRIMARY KEY, 
            group_id INTEGER NOT NULL REFERENCES responsible_group(id), 
            section_key VARCHAR(50) NOT NULL,
            can_view BOOLEAN DEFAULT 0,
            can_create BOOLEAN DEFAULT 0,
            can_edit BOOLEAN DEFAULT 0,
            can_delete BOOLEAN DEFAULT 0,
            UNIQUE(group_id, section_key)
        )"""),
        ("warehouse_item.description", "ALTER TABLE warehouse_item ADD COLUMN description TEXT"),
        ("client.password_hash", "ALTER TABLE client ADD COLUMN password_hash VARCHAR(200)"),
        ("client.access_level", "ALTER TABLE client ADD COLUMN access_level VARCHAR(20) DEFAULT 'floor'"),
        ("client.is_active", "ALTER TABLE client ADD COLUMN is_active BOOLEAN DEFAULT 1"),
        ("client.last_login", "ALTER TABLE client ADD COLUMN last_login DATETIME"),
        ("client.username", "ALTER TABLE client ADD COLUMN username VARCHAR(80)"),
        ("gas_system_component.installed_at", "ALTER TABLE gas_system_component ADD COLUMN installed_at DATETIME"),
        ("equipment_repair", """CREATE TABLE IF NOT EXISTS equipment_repair (
            id INTEGER PRIMARY KEY,
            component_id INTEGER NOT NULL REFERENCES gas_system_component(id),
            fault_description TEXT NOT NULL,
            date_broken DATETIME NOT NULL,
            repair_company VARCHAR(200),
            repair_description TEXT,
            repair_cost FLOAT DEFAULT 0,
            date_sent DATETIME,
            date_repaired DATETIME,
            date_installed DATETIME,
            status VARCHAR(20) DEFAULT 'broken',
            notes TEXT,
            created_by INTEGER REFERENCES user(id),
            created_at DATETIME
        )"""),
        ("warehouse_item.supplier_part_number", "ALTER TABLE warehouse_item ADD COLUMN supplier_part_number VARCHAR(100)"),
        ("warehouse_item.contractor_id", "ALTER TABLE warehouse_item ADD COLUMN contractor_id INTEGER REFERENCES contractor(id)"),
        ("warehouse_item.consumable_type", "ALTER TABLE warehouse_item ADD COLUMN consumable_type VARCHAR(50)"),
        ("warehouse_item.consumable_subtype", "ALTER TABLE warehouse_item ADD COLUMN consumable_subtype VARCHAR(100)"),
        ("warehouse_item.volume", "ALTER TABLE warehouse_item ADD COLUMN volume VARCHAR(50)"),
        ("warehouse_item.compatible_machines", "ALTER TABLE warehouse_item ADD COLUMN compatible_machines TEXT"),
        ("warehouse_item.replacement_interval", "ALTER TABLE warehouse_item ADD COLUMN replacement_interval VARCHAR(50)"),
        ("warehouse_item.last_replacement", "ALTER TABLE warehouse_item ADD COLUMN last_replacement DATE"),
        ("warehouse_item.next_replacement", "ALTER TABLE warehouse_item ADD COLUMN next_replacement DATE"),
        ("fault_report.contractor_id", "ALTER TABLE fault_report ADD COLUMN contractor_id INTEGER REFERENCES contractor(id)"),
        ("weekend_shift", "CREATE TABLE IF NOT EXISTS weekend_shift (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES user(id) NOT NULL, date DATE NOT NULL, shift_type VARCHAR(20) DEFAULT 'full', notes TEXT, created_by INTEGER REFERENCES user(id), created_at DATETIME)"),
        ("fault_status_history", "CREATE TABLE IF NOT EXISTS fault_status_history (id INTEGER PRIMARY KEY, fault_id INTEGER REFERENCES fault_report(id) NOT NULL, old_status VARCHAR(20), new_status VARCHAR(20) NOT NULL, reason TEXT, changed_by INTEGER REFERENCES user(id), changed_at DATETIME)"),
        ("tool_wear.cycle_days", "ALTER TABLE tool_wear ADD COLUMN cycle_days INTEGER DEFAULT 14"),
        ("monthly_archive", """CREATE TABLE IF NOT EXISTS monthly_archive (
            id INTEGER PRIMARY KEY,
            archive_month VARCHAR(7) NOT NULL,
            section VARCHAR(50) NOT NULL,
            data_json TEXT NOT NULL,
            created_at DATETIME,
            created_by INTEGER REFERENCES user(id)
        )"""),
        ("two_checklist_item", """CREATE TABLE IF NOT EXISTS two_checklist_item (
            id INTEGER PRIMARY KEY,
            two_id INTEGER NOT NULL REFERENCES technical_work_order(id),
            text VARCHAR(500) NOT NULL,
            is_done BOOLEAN DEFAULT 0,
            done_at DATETIME,
            done_by INTEGER REFERENCES user(id),
            sort_order INTEGER DEFAULT 0
        )"""),
        ("two_signature", """CREATE TABLE IF NOT EXISTS two_signature (
            id INTEGER PRIMARY KEY,
            two_id INTEGER NOT NULL REFERENCES technical_work_order(id),
            signer_name VARCHAR(200) NOT NULL,
            signature_data TEXT NOT NULL,
            signed_at DATETIME
        )"""),
        ("client.internal_phone", "ALTER TABLE client ADD COLUMN internal_phone VARCHAR(50)"),
        ("client.work_phone", "ALTER TABLE client ADD COLUMN work_phone VARCHAR(50)"),
        ("user.phone", "ALTER TABLE user ADD COLUMN phone VARCHAR(50)"),
        ("two_assignment", """CREATE TABLE IF NOT EXISTS two_assignment (
            id INTEGER PRIMARY KEY,
            two_id INTEGER NOT NULL REFERENCES technical_work_order(id),
            section_id INTEGER REFERENCES factory_section(id),
            machine_id INTEGER REFERENCES machine(id),
            description TEXT,
            sort_order INTEGER DEFAULT 0
        )"""),
        ("two_checklist_item.assignment_id", "ALTER TABLE two_checklist_item ADD COLUMN assignment_id INTEGER REFERENCES two_assignment(id)"),
        ("user_activity_log", """CREATE TABLE IF NOT EXISTS user_activity_log (
            id INTEGER PRIMARY KEY,
            user_id INTEGER REFERENCES user(id),
            username VARCHAR(80),
            action VARCHAR(50) NOT NULL,
            page VARCHAR(200),
            method VARCHAR(10),
            entity_type VARCHAR(50),
            entity_id INTEGER,
            details TEXT,
            ip_address VARCHAR(50),
            user_agent VARCHAR(300),
            session_id VARCHAR(100),
            duration_ms INTEGER,
            status_code INTEGER,
            created_at DATETIME
        )"""),
        ("system_log", """CREATE TABLE IF NOT EXISTS system_log (
            id INTEGER PRIMARY KEY,
            level VARCHAR(10) NOT NULL,
            category VARCHAR(50),
            message TEXT NOT NULL,
            details TEXT,
            source VARCHAR(100),
            user_id INTEGER REFERENCES user(id),
            ip_address VARCHAR(50),
            created_at DATETIME
        )"""),
        ("mule_maintenance", """CREATE TABLE IF NOT EXISTS mule_maintenance (
            id INTEGER PRIMARY KEY,
            number VARCHAR(30) UNIQUE NOT NULL,
            mule_number VARCHAR(100) NOT NULL,
            mule_serial VARCHAR(100),
            machine_id INTEGER REFERENCES machine(id),
            date DATE NOT NULL,
            reason TEXT NOT NULL,
            next_date DATE,
            periodicity VARCHAR(50),
            status VARCHAR(20) DEFAULT 'completed',
            notes TEXT,
            created_by INTEGER REFERENCES user(id),
            created_at DATETIME
        )"""),
        ("mule_maintenance_part", """CREATE TABLE IF NOT EXISTS mule_maintenance_part (
            id INTEGER PRIMARY KEY,
            maintenance_id INTEGER NOT NULL REFERENCES mule_maintenance(id),
            part_name VARCHAR(200) NOT NULL,
            part_number VARCHAR(100),
            quantity FLOAT DEFAULT 1,
            unit VARCHAR(20) DEFAULT 'st',
            notes TEXT
        )"""),
        ("mule_part_order", """CREATE TABLE IF NOT EXISTS mule_part_order (
            id INTEGER PRIMARY KEY,
            order_number VARCHAR(30) UNIQUE NOT NULL,
            mule_number VARCHAR(100),
            part_name VARCHAR(200) NOT NULL,
            part_number VARCHAR(100),
            quantity FLOAT DEFAULT 1,
            unit VARCHAR(20) DEFAULT 'st',
            supplier VARCHAR(200),
            urgency VARCHAR(20) DEFAULT 'normal',
            status VARCHAR(20) DEFAULT 'pending',
            notes TEXT,
            ordered_by INTEGER REFERENCES user(id),
            ordered_at DATETIME,
            delivered_at DATETIME
        )"""),
        ("mule_component", """CREATE TABLE IF NOT EXISTS mule_component (
            id INTEGER PRIMARY KEY,
            maintenance_id INTEGER NOT NULL REFERENCES mule_maintenance(id),
            component_type VARCHAR(50) NOT NULL,
            model VARCHAR(200),
            quantity FLOAT DEFAULT 1,
            knife_number VARCHAR(100),
            knife_size VARCHAR(50),
            cable_type VARCHAR(100),
            cable_length VARCHAR(50),
            gasket_length VARCHAR(50),
            spring_size VARCHAR(100),
            bolt_type VARCHAR(100),
            filter_type VARCHAR(100),
            oil_type VARCHAR(100),
            volume VARCHAR(50),
            replacement_date DATE,
            notes TEXT
        )"""),
        ("equipment_maintenance", """CREATE TABLE IF NOT EXISTS equipment_maintenance (
            id INTEGER PRIMARY KEY,
            number VARCHAR(30) UNIQUE NOT NULL,
            name VARCHAR(200) NOT NULL,
            serial VARCHAR(100),
            machine_id INTEGER REFERENCES machine(id),
            date DATE NOT NULL,
            reason TEXT NOT NULL,
            next_date DATE,
            periodicity VARCHAR(50),
            status VARCHAR(20) DEFAULT 'completed',
            notes TEXT,
            created_by INTEGER REFERENCES user(id),
            created_at DATETIME
        )"""),
        ("equipment_part", """CREATE TABLE IF NOT EXISTS equipment_part (
            id INTEGER PRIMARY KEY,
            equipment_id INTEGER NOT NULL REFERENCES equipment_maintenance(id),
            name VARCHAR(200) NOT NULL,
            number VARCHAR(100),
            quantity FLOAT DEFAULT 1,
            notes TEXT
        )"""),
        ("equipment_component", """CREATE TABLE IF NOT EXISTS equipment_component (
            id INTEGER PRIMARY KEY,
            equipment_id INTEGER NOT NULL REFERENCES equipment_maintenance(id),
            component_type VARCHAR(50) NOT NULL,
            model VARCHAR(200),
            size VARCHAR(100),
            length VARCHAR(50),
            quantity FLOAT DEFAULT 1,
            notes TEXT
        )"""),
        ("equipment_part_order", """CREATE TABLE IF NOT EXISTS equipment_part_order (
            id INTEGER PRIMARY KEY,
            order_number VARCHAR(30) UNIQUE NOT NULL,
            equipment_name VARCHAR(200),
            part_name VARCHAR(200) NOT NULL,
            part_number VARCHAR(100),
            quantity FLOAT DEFAULT 1,
            supplier VARCHAR(200),
            urgency VARCHAR(20) DEFAULT 'normal',
            status VARCHAR(20) DEFAULT 'pending',
            notes TEXT,
            created_by INTEGER REFERENCES user(id),
            created_at DATETIME,
            delivered_at DATETIME
        )"""),
        ("warehouse_item.serial_number", "ALTER TABLE warehouse_item ADD COLUMN serial_number VARCHAR(100)"),
        ("warehouse_item.expiry_date", "ALTER TABLE warehouse_item ADD COLUMN expiry_date DATE"),
        ("warehouse_item.barcode", "ALTER TABLE warehouse_item ADD COLUMN barcode VARCHAR(100)"),
        ("warehouse_movement.user_id", "ALTER TABLE warehouse_movement ADD COLUMN user_id INTEGER REFERENCES user(id)"),
        ("warehouse_reservation", """CREATE TABLE IF NOT EXISTS warehouse_reservation (
            id INTEGER PRIMARY KEY,
            item_id INTEGER NOT NULL REFERENCES warehouse_item(id),
            quantity FLOAT NOT NULL,
            reserved_for VARCHAR(200),
            reserved_by INTEGER REFERENCES user(id),
            reserved_at DATETIME,
            expires_at DATETIME,
            notes TEXT
        )"""),
        ("supplier_price", """CREATE TABLE IF NOT EXISTS supplier_price (
            id INTEGER PRIMARY KEY,
            item_id INTEGER NOT NULL REFERENCES warehouse_item(id),
            supplier_name VARCHAR(200) NOT NULL,
            price DECIMAL(10,2) NOT NULL,
            delivery_days INTEGER,
            min_order FLOAT,
            notes TEXT,
            updated_at DATETIME
        )"""),
    ]

    # Fix cylinder_log.cylinder_id to be nullable (SQLite needs table rebuild)
    try:
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='cylinder_log'")
        row = cur.fetchone()
        if row and 'NOT NULL' in (row[0] or '') and 'cylinder_id' in (row[0] or ''):
            cur.execute("ALTER TABLE cylinder_log RENAME TO cylinder_log_old")
            cur.execute("""CREATE TABLE cylinder_log (
                id INTEGER PRIMARY KEY,
                cylinder_id INTEGER REFERENCES gas_cylinder(id),
                action VARCHAR(30) NOT NULL,
                old_cylinder_number VARCHAR(50),
                new_cylinder_number VARCHAR(50),
                performed_by INTEGER REFERENCES user(id),
                date DATETIME,
                notes TEXT
            )""")
            cur.execute("INSERT INTO cylinder_log SELECT * FROM cylinder_log_old")
            cur.execute("DROP TABLE cylinder_log_old")
            conn.commit()
            print("Migration: fixed cylinder_log.cylinder_id to nullable")
    except Exception as e:
        pass

    for col_name, sql in migrations:
        try:
            if 'CREATE TABLE' in sql:
                table_name = sql.split('IF NOT EXISTS ')[1].split(' ')[0]
                cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
                if not cur.fetchone():
                    cur.execute(sql)
                    print(f"Migration: created {table_name}")
            elif 'ALTER TABLE' in sql:
                table = sql.split('ALTER TABLE ')[1].split(' ADD COLUMN')[0]
                col = sql.split('ADD COLUMN ')[1].split(' ')[0]
                cur.execute(f"PRAGMA table_info({table})")
                cols = [c[1] for c in cur.fetchall()]
                if col not in cols:
                    cur.execute(sql)
                    print(f"Migration: added {col} to {table}")
        except Exception as e:
            pass

    conn.commit()
    conn.close()


def add_work_report(entry_text):
    """Add entry to Work Report log from anywhere in the app"""
    from flask_login import current_user
    try:
        entry = WorkReportEntry(
            user_id=current_user.id if current_user.is_authenticated else None,
            entry=entry_text
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()
