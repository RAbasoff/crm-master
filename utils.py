from functools import wraps
from flask import flash, redirect, url_for, request
from flask_login import current_user
from flask_babel import gettext as _
from datetime import datetime, timedelta
from models import db, Notification, AuditLog, GroupPermission, ResponsibleGroup, Verantwoordelijke, UserActivityLog, SystemLog, WorkReportEntry
import os
from werkzeug.utils import secure_filename

SECTION_KEYS = [
    # Production
    'dashboard', 'floor', 'machines', 'equipment', 'tool_wear', 'assets',
    'electricity', 'gas', 'maintenance', 'maintenance_plans', 'repairs',
    'faults', 'two',
    # Communication
    'messages', 'notifications',
    # Staff
    'schedule', 'vacations', 'time_tracking',
    # Business
    'orders', 'clients', 'workers', 'invoices', 'contractors',
    'warehouse', 'consumables', 'purchase_requests',
    # Analytics
    'reports', 'work_report', 'archive', 'statistics',
    # System
    'settings', 'users', 'audit_log', 'sections',
]

# Role hierarchy: admin > director > technician > user
# admin: full access, can modify program settings
# director/technician/user: access controlled by allowed_sections and group permissions

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
            if current_user.role not in roles:
                flash(_('ДОСТУП ЗАКРЫТ. НЕ ДОСТАТОЧНО ПРАВ.'), 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_user_group_permissions(user):
    """Get permissions from user's group (via person_id link or role fallback)"""
    if user.person_id:
        person = Verantwoordelijke.query.get(user.person_id)
        if person and person.group_id:
            perms = GroupPermission.query.filter_by(group_id=person.group_id).all()
            return {p.section_key: p for p in perms}
    # Fallback: look up group by role name (e.g. role='technician' -> group 'Technician')
    if user.role and user.role != 'admin':
        group = ResponsibleGroup.query.filter_by(access_level=user.role).first()
        if group:
            perms = GroupPermission.query.filter_by(group_id=group.id).all()
            return {p.section_key: p for p in perms}
    return {}

def user_has_section_access(section_key, action='view'):
    # Admin always has full access
    if current_user.role == 'admin':
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
    # Section not in group permissions — check individual allowed_sections
    # (allowed_sections grant full view+create+edit on top of group permissions)
    if any(s.section_key == section_key for s in current_user.allowed_sections):
        if action in ('view', 'create', 'edit'):
            return True
        return False
    # No group perms and no individual override
    if group_perms:
        return False  # has a group but section not in group or allowed_sections
    # Fallback to access_level (only when no group permissions exist)
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
                flash(_('ДОСТУП ЗАКРЫТ. НЕ ДОСТАТОЧНО ПРАВ.'), 'error')
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
        ("equipment_part.warehouse_item_id", "ALTER TABLE equipment_part ADD COLUMN warehouse_item_id INTEGER REFERENCES warehouse_item(id)"),
        ("maintenance_plan.responsible_user_id", "ALTER TABLE maintenance_plan ADD COLUMN responsible_user_id INTEGER REFERENCES user(id)"),
        ("maintenance_plan.recurrence", "ALTER TABLE maintenance_plan ADD COLUMN recurrence VARCHAR(20)"),
        ("machine_consumable", """CREATE TABLE IF NOT EXISTS machine_consumable (
            id INTEGER PRIMARY KEY,
            machine_id INTEGER NOT NULL REFERENCES machine(id),
            warehouse_item_id INTEGER NOT NULL REFERENCES warehouse_item(id),
            quantity_per_use FLOAT DEFAULT 1,
            notes TEXT,
            added_at DATETIME,
            UNIQUE(machine_id, warehouse_item_id)
        )"""),
        ("electrical_switch_log", """CREATE TABLE IF NOT EXISTS electrical_switch_log (
            id INTEGER PRIMARY KEY,
            cabinet_id INTEGER NOT NULL REFERENCES electrical_cabinet(id),
            from_breaker_id INTEGER REFERENCES circuit_breaker(id),
            to_breaker_id INTEGER REFERENCES circuit_breaker(id),
            reason TEXT NOT NULL,
            notes TEXT,
            performed_by INTEGER REFERENCES user(id),
            created_at DATETIME
        )"""),
        ("electrical_document", """CREATE TABLE IF NOT EXISTS electrical_document (
            id INTEGER PRIMARY KEY,
            cabinet_id INTEGER NOT NULL REFERENCES electrical_cabinet(id),
            doc_type VARCHAR(50) NOT NULL DEFAULT 'schematic',
            title VARCHAR(200) NOT NULL,
            filename VARCHAR(300) NOT NULL,
            uploaded_by INTEGER REFERENCES user(id),
            uploaded_at DATETIME
        )"""),
        ("machine_consumable.last_issued_at", "ALTER TABLE machine_consumable ADD COLUMN last_issued_at DATETIME"),
        ("fault_report.equipment_id", "ALTER TABLE fault_report ADD COLUMN equipment_id INTEGER REFERENCES equipment(id)"),
        ("user.password_plain", "ALTER TABLE user ADD COLUMN password_plain VARCHAR(200)"),
        ("user.login_count", "ALTER TABLE user ADD COLUMN login_count INTEGER DEFAULT 0"),
        ("user.force_change_password", "ALTER TABLE user ADD COLUMN force_change_password BOOLEAN DEFAULT 0"),
        ("circuit_breaker.schematic_label", "ALTER TABLE circuit_breaker ADD COLUMN schematic_label VARCHAR(20)"),
        ("circuit_breaker.schematic_x", "ALTER TABLE circuit_breaker ADD COLUMN schematic_x FLOAT"),
        ("circuit_breaker.schematic_y", "ALTER TABLE circuit_breaker ADD COLUMN schematic_y FLOAT"),
        ("client.login_count", "ALTER TABLE client ADD COLUMN login_count INTEGER DEFAULT 0"),
        ("client.force_change_password", "ALTER TABLE client ADD COLUMN force_change_password BOOLEAN DEFAULT 0"),
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


def run_data_migrations():
    """Data migrations via SQLAlchemy ORM — works on both SQLite and PostgreSQL."""
    try:
        from models import (ResponsibleGroup, GroupPermission, User, UserSectionAccess,
                            Verantwoordelijke, Machine, Equipment,
                            fault_technicians, user_machine, FaultReport,
                            Notification, Message, AuditLog, SystemLog,
                            UserActivityLog, WorkReport, PurchaseRequest,
                            TimeEntry, Vacation, WorkSchedule, WeekendShift,
                            CylinderLog, CylinderOrder)
        from sqlalchemy import text, func

        # ── 1. Ensure 4 standard groups exist ───────────────────────────
        groups_spec = [
            ('Administrator', 'admin'),
            ('Director',      'director'),
            ('Technician',    'technician'),
            ('User',          'user'),
        ]
        for name, level in groups_spec:
            g = ResponsibleGroup.query.filter_by(name=name).first()
            if not g:
                g = ResponsibleGroup(name=name, access_level=level)
                db.session.add(g)
                db.session.flush()
                print(f"Data migration: created group '{name}'")
        db.session.commit()

        # ── 2. Base group permissions (view on all modules) ─────────────
        all_sections = [
            'dashboard', 'floor', 'machines', 'equipment', 'tool_wear',
            'assets', 'electricity', 'gas', 'maintenance', 'maintenance_plans',
            'repairs', 'faults', 'two', 'messages', 'notifications',
            'schedule', 'vacations', 'time_tracking', 'orders', 'clients',
            'workers', 'invoices', 'contractors', 'warehouse', 'consumables',
            'purchase_requests', 'reports', 'work_report', 'archive',
            'statistics', 'sections',
        ]

        groups = ResponsibleGroup.query.all()
        for g in groups:
            existing = {p.section_key for p in GroupPermission.query.filter_by(group_id=g.id).all()}
            added = 0
            for section in all_sections:
                if section not in existing:
                    db.session.add(GroupPermission(
                        group_id=g.id, section_key=section,
                        can_view=True, can_create=False, can_edit=False, can_delete=False
                    ))
                    added += 1
            if added:
                print(f"Data migration: added {added} base permissions to '{g.name}'")

        # ── 3. Extra CRUD for Director group ────────────────────────────
        director = ResponsibleGroup.query.filter_by(name='Director').first()
        if director:
            director_crud = {
                'faults': (True, True, True, False),
                'maintenance': (True, True, True, False),
                'two': (True, True, True, False),
                'orders': (True, True, True, False),
                'messages': (True, True, True, False),
                'notifications': (True, True, True, False),
                'contractors': (True, True, True, False),
                'warehouse': (True, True, True, False),
                'reports': (True, True, True, False),
                'purchase_requests': (True, True, True, False),
                'invoices': (True, True, False, False),
                'machines': (True, True, True, False),
                'maintenance_plans': (True, True, True, False),
                'equipment': (True, False, False, False),
                'tool_wear': (True, False, False, False),
                'floor': (True, True, True, False),
                'staff': (True, True, True, True),
                'workers': (True, True, True, True),
                'clients': (True, True, True, True),
                'statistics': (True, True, True, True),
                'schedule': (True, True, True, True),
                'vacations': (True, True, True, True),
                'time_tracking': (True, True, True, True),
                'repairs': (True, False, False, False),
                'dashboard': (True, False, False, False),
            }
            for section, (v, c, e, d) in director_crud.items():
                p = GroupPermission.query.filter_by(group_id=director.id, section_key=section).first()
                if p:
                    changed = False
                    if p.can_view != v: p.can_view = v; changed = True
                    if p.can_create != c: p.can_create = c; changed = True
                    if p.can_edit != e: p.can_edit = e; changed = True
                    if p.can_delete != d: p.can_delete = d; changed = True
                    if changed:
                        print(f"Data migration: updated Director perm '{section}'")
                else:
                    db.session.add(GroupPermission(
                        group_id=director.id, section_key=section,
                        can_view=v, can_create=c, can_edit=e, can_delete=d
                    ))

        # ── 4. Extra CRUD for Technician group ──────────────────────────
        tech = ResponsibleGroup.query.filter_by(name='Technician').first()
        if tech:
            tech_crud = {
                'faults': (True, True, True, False),
                'machines': (True, False, True, False),
                'floor': (True, True, True, False),
                'schedule': (True, True, True, False),
                'two': (True, True, True, False),
                'messages': (True, True, True, False),
                'notifications': (True, True, True, False),
                'orders': (True, True, True, False),
                'reports': (True, True, True, False),
                'warehouse': (True, True, True, False),
            }
            for section, (v, c, e, d) in tech_crud.items():
                p = GroupPermission.query.filter_by(group_id=tech.id, section_key=section).first()
                if p:
                    changed = False
                    if p.can_view != v: p.can_view = v; changed = True
                    if p.can_create != c: p.can_create = c; changed = True
                    if p.can_edit != e: p.can_edit = e; changed = True
                    if p.can_delete != d: p.can_delete = d; changed = True
                    if changed:
                        print(f"Data migration: updated Technician perm '{section}'")

        # ── 5. Extra CRUD for User group ────────────────────────────────
        user_group = ResponsibleGroup.query.filter_by(name='User').first()
        if user_group:
            user_crud = {
                'faults': (True, True, True, False),
                'messages': (True, True, True, False),
                'notifications': (True, True, True, False),
                'orders': (True, True, True, False),
                'maintenance_plans': (True, False, False, False),
                'maintenance': (True, False, False, False),
                'machines': (True, False, False, False),
                'equipment': (True, False, False, False),
                'tool_wear': (True, False, False, False),
                'floor': (True, False, False, False),
            }
            for section, (v, c, e, d) in user_crud.items():
                p = GroupPermission.query.filter_by(group_id=user_group.id, section_key=section).first()
                if p:
                    changed = False
                    if p.can_view != v: p.can_view = v; changed = True
                    if p.can_create != c: p.can_create = c; changed = True
                    if p.can_edit != e: p.can_edit = e; changed = True
                    if p.can_delete != d: p.can_delete = d; changed = True
                    if changed:
                        print(f"Data migration: updated User perm '{section}'")

        # ── 6. Clean up legacy section keys and access levels ──────────
        legacy = GroupPermission.query.filter(
            GroupPermission.section_key.in_(['cylinders', 'quality'])
        ).delete(synchronize_session=False)
        if legacy:
            print(f"Data migration: removed {legacy} legacy permissions (cylinders/quality)")

        UserSectionAccess.query.filter(
            UserSectionAccess.section_key.in_(['cylinders', 'quality'])
        ).delete(synchronize_session=False)

        # Remove quality access level from groups
        quality_groups = ResponsibleGroup.query.filter_by(access_level='quality').all()
        for g in quality_groups:
            db.session.delete(g)
            print(f"Data migration: deleted group '{g.name}' with access_level=quality")

        # Remove quality access level from persons
        try:
            db.session.execute(text("UPDATE client SET access_level='floor' WHERE access_level='quality'"))
        except Exception:
            pass

        db.session.commit()

        # ── 6b. Clean stale data in client table ────────────────────────
        try:
            db.session.execute(text("UPDATE client SET password_plain=NULL, position=NULL, notities=NULL"))
            db.session.commit()
            print("Data migration: cleared password_plain and stale fields from client table")
        except Exception:
            db.session.rollback()

        # ── 7. One-time user cleanup (runs once via marker) ─────────────
        marker_key = 'user_cleanup_v13'
        marker = UserSectionAccess.query.filter_by(user_id=0, section_key=marker_key).first()
        if marker:
            print("Data migration: user cleanup already done, skipping.")
            return

        print("Data migration: running one-time user cleanup...")

        admin_user = User.query.filter_by(role='admin').first()
        if not admin_user:
            print("Data migration: no admin user found, skipping cleanup")
            return
        admin_id = admin_user.id

        # Desired persons: name -> (group_name, access_level)
        desired_persons = {
            'Directeur':  ('Director', 'full'),
            'Tim':      ('Director', 'full'),
            'Thijs':    ('Director', 'full'),
            'Peter':    ('Director', 'full'),
            'Javier':   ('Director', 'full'),
            'Technicus':  ('Technician', 'floor'),
            'Maico':   ('Technician', 'floor'),
            'Aris':    ('Technician', 'floor'),
            'Filip':   ('Technician', 'floor'),
            'Bartek':  ('User', 'floor'),
            'Pablo':   ('User', 'floor'),
            'Hashem':  ('User', 'floor'),
            'Paulina': ('User', 'floor'),
        }

        # Names to remove (if they exist and are NOT in desired list)
        names_to_remove = ['Hashim', 'Dina', 'Lukas', 'Lukash', 'Rusln', '\u0414\u0438\u0440\u0435\u043a\u0442\u043e\u0440', '\u0422\u0435\u0445\u043d\u0438\u043a']

        # 7a. Delete old system users FIRST (before removing persons, to clear User.person_id FK)
        for old_username in ['tim', 'thijs', 'user', 'tech', 'Tim', 'Thijs']:
            u = User.query.filter(func.lower(User.username) == old_username.lower()).first()
            if u and u.role != 'admin':
                # Rewrite FKs from this user to admin before deletion
                for model, fk_field in [
                    (FaultReport, 'reporter_id'), (FaultReport, 'technician_id'),
                    (Notification, 'user_id'), (Message, 'sender_id'), (Message, 'receiver_id'),
                    (AuditLog, 'user_id'), (SystemLog, 'user_id'), (UserActivityLog, 'user_id'),
                    (WorkReport, 'technician_id'), (PurchaseRequest, 'requester_id'),
                    (PurchaseRequest, 'reviewer_id'), (TimeEntry, 'user_id'),
                    (Vacation, 'user_id'), (WorkSchedule, 'user_id'),
                    (WeekendShift, 'user_id'), (WeekendShift, 'created_by'),
                ]:
                    try:
                        model.query.filter(getattr(model, fk_field) == u.id).update(
                            {fk_field: admin_id}, synchronize_session=False
                        )
                    except Exception:
                        pass
                try:
                    db.session.execute(text("DELETE FROM fault_technicians WHERE technician_id=:uid"), {'uid': u.id})
                    db.session.execute(text("DELETE FROM user_machine WHERE user_id=:uid"), {'uid': u.id})
                except Exception:
                    pass
                db.session.delete(u)
                print(f"Data migration: deleted system user '{u.username}' (ID={u.id}), FKs -> admin")
        db.session.flush()

        # 7b. Remove old persons by name (User.person_id already cleared in 7a)
        for name in names_to_remove:
            persons = Verantwoordelijke.query.filter_by(naam=name).all()
            for p in persons:
                # Clear remaining FK references
                Machine.query.filter_by(responsible_person_id=p.id).update({'responsible_person_id': None})
                Equipment.query.filter_by(responsible_person_id=p.id).update({'responsible_person_id': None})
                User.query.filter_by(person_id=p.id).update({'person_id': None})
                try:
                    db.session.execute(text("DELETE FROM section_responsible WHERE person_id=:pid"), {'pid': p.id})
                except Exception:
                    pass
                db.session.delete(p)
                print(f"Data migration: removed person '{name}' (ID={p.id})")

        db.session.flush()

        # 7c. Rewrite FK from remaining non-admin system users to admin
        old_system_users = User.query.filter(User.id != admin_id, User.role != 'admin').all()
        for u in old_system_users:
            # Skip if this user is linked to a desired person
            if u.person_id:
                person = Verantwoordelijke.query.get(u.person_id)
                if person and person.naam in desired_persons:
                    continue
            # Rewrite FK
            for model, fk_field in [
                (FaultReport, 'reporter_id'), (FaultReport, 'technician_id'),
                (Notification, 'user_id'), (Message, 'sender_id'), (Message, 'receiver_id'),
                (AuditLog, 'user_id'), (SystemLog, 'user_id'), (UserActivityLog, 'user_id'),
                (WorkReport, 'technician_id'), (PurchaseRequest, 'requester_id'),
                (PurchaseRequest, 'reviewer_id'), (TimeEntry, 'user_id'),
                (Vacation, 'user_id'), (WorkSchedule, 'user_id'),
                (WeekendShift, 'user_id'), (WeekendShift, 'created_by'),
            ]:
                try:
                    model.query.filter(getattr(model, fk_field) == u.id).update(
                        {fk_field: admin_id}, synchronize_session=False
                    )
                except Exception:
                    pass
            try:
                db.session.execute(text("DELETE FROM fault_technicians WHERE technician_id=:uid"), {'uid': u.id})
                db.session.execute(text("DELETE FROM user_machine WHERE user_id=:uid"), {'uid': u.id})
            except Exception:
                pass
            username = u.username
            db.session.delete(u)
            print(f"Data migration: removed system user '{username}' (ID={u.id}), FKs -> admin")

        db.session.commit()

        # 7c. Ensure desired persons exist with correct groups
        for name, (group_name, access_level) in desired_persons.items():
            group = ResponsibleGroup.query.filter_by(name=group_name).first()
            person = Verantwoordelijke.query.filter_by(naam=name).first()
            if not person:
                person = Verantwoordelijke(
                    naam=name, group_id=group.id if group else None,
                    access_level=access_level, is_active=True
                )
                db.session.add(person)
                db.session.flush()
                print(f"Data migration: created person '{name}' -> {group_name}")
            else:
                if group and person.group_id != group.id:
                    person.group_id = group.id
                    print(f"Data migration: moved '{name}' -> {group_name}")
                if person.access_level != access_level:
                    person.access_level = access_level

        db.session.commit()

        # 7d. Delete technician system users (maico, aris, filip) — they should be persons only
        for tech_username in ['maico', 'aris', 'filip']:
            u = User.query.filter(func.lower(User.username) == tech_username).first()
            if u and u.role != 'admin':
                for model, fk_field in [
                    (FaultReport, 'reporter_id'), (FaultReport, 'technician_id'),
                    (Notification, 'user_id'), (Message, 'sender_id'), (Message, 'receiver_id'),
                    (AuditLog, 'user_id'), (SystemLog, 'user_id'), (UserActivityLog, 'user_id'),
                    (WorkReport, 'technician_id'), (PurchaseRequest, 'requester_id'),
                    (PurchaseRequest, 'reviewer_id'), (TimeEntry, 'user_id'),
                    (Vacation, 'user_id'), (WorkSchedule, 'user_id'),
                    (WeekendShift, 'user_id'), (WeekendShift, 'created_by'),
                ]:
                    try:
                        model.query.filter(getattr(model, fk_field) == u.id).update(
                            {fk_field: admin_id}, synchronize_session=False
                        )
                    except Exception:
                        pass
                try:
                    db.session.execute(text("DELETE FROM fault_technicians WHERE technician_id=:uid"), {'uid': u.id})
                    db.session.execute(text("DELETE FROM user_machine WHERE user_id=:uid"), {'uid': u.id})
                except Exception:
                    pass
                # Unlink from worker
                try:
                    from models import Monteur
                    Monteur.query.filter_by(user_id=u.id).update({'user_id': None})
                except Exception:
                    pass
                db.session.delete(u)
                print(f"Data migration: deleted tech system user '{u.username}' (ID={u.id}), FKs -> admin")
        db.session.flush()

        # 7e. Ensure Director + generic Technician system user accounts exist
        for username, display, role, access_level, person_name in [
            ('director', 'Directeur', 'director', 'full', 'Directeur'),
            ('technician', 'Technicus', 'technician', 'full', 'Technicus'),
        ]:
            person = Verantwoordelijke.query.filter_by(naam=person_name).first()
            if not person:
                continue
            user = User.query.filter_by(person_id=person.id).first()
            if not user:
                user = User(
                    username=username, display_name=display,
                    role=role, access_level=access_level,
                    person_id=person.id, is_active_user=True
                )
                user.password_hash = ''
                db.session.add(user)
                db.session.flush()
                print(f"Data migration: created system user '{username}' -> {display}")

        db.session.flush()

        # 7e. Add allowed_sections for Director user (extra on top of group)
        director_user = User.query.filter_by(username='director').first()
        if director_user:
            extra_sections = [
                'dashboard', 'floor', 'machines', 'equipment', 'tool_wear',
                'assets', 'electricity', 'gas', 'maintenance_plans', 'repairs',
                'schedule', 'vacations', 'time_tracking', 'clients', 'workers',
                'invoices', 'consumables', 'work_report', 'archive', 'statistics', 'sections',
            ]
            existing_usa = {s.section_key for s in UserSectionAccess.query.filter_by(user_id=director_user.id).all()}
            for section in extra_sections:
                if section not in existing_usa:
                    db.session.add(UserSectionAccess(user_id=director_user.id, section_key=section))

        # Mark migration as done
        db.session.add(UserSectionAccess(user_id=0, section_key=marker_key))
        db.session.commit()
        print("Data migration: user cleanup complete.")

    except Exception as e:
        db.session.rollback()
        print(f"Data migration error: {e}")


def sanitize_like(query_str):
    """Escape LIKE wildcards in user input to prevent ILIKE injection."""
    if not query_str:
        return ''
    return query_str.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

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


def check_tool_wear_notifications():
    """Send notifications to users when their assigned machines have tools with wear >= 80%."""
    from models import ToolWear, User, Machine
    today = datetime.utcnow().date()
    tools = ToolWear.query.all()
    for t in tools:
        cycle = t.cycle_days or 14
        if t.last_replaced:
            days = (today - t.last_replaced).days
            wear = min(100.0, round((days / cycle) * 100, 1))
        else:
            wear = 100.0
        if wear < 80:
            continue
        # Find machine by name
        machine = Machine.query.filter_by(name=t.machine_name).first()
        if not machine:
            continue
        # Notify assigned users
        for user in machine.assigned_users:
            if not user.is_active_user:
                continue
            link = f'/tool-wear'
            existing = Notification.query.filter_by(
                user_id=user.id, type='tool_wear', link=link
            ).first()
            if existing:
                continue
            create_notification(
                user.id,
                _('Knife replacement needed'),
                f"{t.machine_name}: {t.tool_name} — {wear}% {_('wear')}",
                'tool_wear',
                link
            )
