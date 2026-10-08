"""Schema reconciliation and data migrations (extracted from utils.py).

ensure_schema() is the schema source of truth — always runs, idempotent.
run_data_migrations() is version-stamped (expensive ORM work).
"""
import os
import time as _time


def safe_commit(retries=3, delay=0.5):
    """Commit with retry on SQLite lock. Returns True on success."""
    from models import db
    for attempt in range(retries):
        try:
            db.session.commit()
            return True
        except Exception as e:
            if 'database is locked' in str(e) and attempt < retries - 1:
                db.session.rollback()
                _time.sleep(delay * (attempt + 1))
            else:
                db.session.rollback()
                return False
    return False

SCHEMA_VERSION = 20261008


def _schema_log(msg):
    print(f"SCHEMA: {msg}")


def _schema_err(msg):
    print(f"SCHEMA ERROR: {msg}")


def _migrations_already_applied():
    """Fast-path for DATA migrations only. Schema always reconciles via ensure_schema()."""
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
        if not os.path.exists(db_path):
            return False
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at DATETIME)")
        row = cur.execute("SELECT version FROM schema_version ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        return bool(row and int(row[0]) >= SCHEMA_VERSION)
    except Exception as e:
        _schema_err(f"version check failed: {e}")
        return False


def _stamp_migrations_applied():
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
        if not os.path.exists(db_path):
            return
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at DATETIME)")
        cur.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, datetime('now'))", (SCHEMA_VERSION,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'schema_version stamp failed: {e}')


def _sqlite_col_type(col):
    """Map a SQLAlchemy column to a SQLite type usable in ALTER ADD COLUMN."""
    t = str(col.type).upper()
    if 'INT' in t:
        return 'INTEGER'
    if 'BOOL' in t:
        return 'BOOLEAN'
    if 'DATETIME' in t:
        return 'DATETIME'
    if 'DATE' in t:
        return 'DATE'
    if any(x in t for x in ('FLOAT', 'REAL', 'NUMERIC', 'DECIMAL')):
        return 'FLOAT'
    if 'TEXT' in t or 'CLOB' in t or 'VARCHAR' in t or 'CHAR' in t:
        return str(col.type)
    return str(col.type)


def _ensure_model_columns(cur):
    """Add any column present in SQLAlchemy models but missing in SQLite.

    Models are the source of truth. This catches legacy tables (old chat_message)
    that CREATE TABLE IF NOT EXISTS cannot upgrade. Returns (changed, errors).
    """
    changed, errors = [], []
    try:
        from models import db
    except Exception as e:
        errors.append(f"import models: {e}")
        return changed, errors

    for table in db.metadata.tables.values():
        tname = table.name
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (tname,)
        )
        if not cur.fetchone():
            continue  # created by db.create_all() / SCHEMA_TABLES
        cur.execute(f"PRAGMA table_info([{tname}])")
        existing = {r[1] for r in cur.fetchall()}
        for col in table.columns:
            if col.name in existing:
                continue
            if col.primary_key:
                errors.append(f"{tname}.{col.name}: cannot ALTER-add PRIMARY KEY")
                continue
            ddl = _sqlite_col_type(col)
            sql = f"ALTER TABLE [{tname}] ADD COLUMN [{col.name}] {ddl}"
            try:
                cur.execute(sql)
                changed.append(f"{tname}.{col.name}")
            except Exception as e:
                errors.append(f"{tname}.{col.name}: {e}")
    return changed, errors


def ensure_schema():
    """Idempotent schema reconciliation. ALWAYS safe and cheap to call.

    - creates missing tables (declared SCHEMA migrations)
    - adds missing columns (SQLAlchemy models = source of truth + declared extras)
    - runs known rebuilds and data backfills
    - logs every change and every failure (no silent except:pass)

    Returns dict with counts: tables_created, columns_added, rebuilds, backfills, errors.
    """
    import sqlite3
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
    if not os.path.exists(db_path):
        _schema_log("no db file — skip (db.create_all will build fresh)")
        return {'tables_created': 0, 'columns_added': 0, 'rebuilds': 0, 'backfills': 0, 'errors': []}

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    report = {'tables_created': 0, 'columns_added': 0, 'rebuilds': 0, 'backfills': 0, 'errors': []}

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
        ("maintenance_schedule", """CREATE TABLE IF NOT EXISTS maintenance_schedule (
            id INTEGER PRIMARY KEY,
            machine_id INTEGER REFERENCES machine(id),
            equipment_id INTEGER,
            target_type VARCHAR(20) DEFAULT 'machine',
            target_name VARCHAR(200),
            title VARCHAR(200) NOT NULL,
            description TEXT,
            maintenance_type VARCHAR(50) DEFAULT 'preventive',
            recurrence VARCHAR(20) NOT NULL,
            preferred_dow INTEGER,
            preferred_day INTEGER,
            months_ahead INTEGER DEFAULT 3,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME
        )"""),
        ("maintenance_schedule.equipment_id", "ALTER TABLE maintenance_schedule ADD COLUMN equipment_id INTEGER"),
        ("maintenance_schedule.target_type", "ALTER TABLE maintenance_schedule ADD COLUMN target_type VARCHAR(20) DEFAULT 'machine'"),
        ("maintenance_schedule.target_name", "ALTER TABLE maintenance_schedule ADD COLUMN target_name VARCHAR(200)"),
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
        ("fault_report.pause_reason", "ALTER TABLE fault_report ADD COLUMN pause_reason VARCHAR(40)"),
        ("fault_report.pause_comment", "ALTER TABLE fault_report ADD COLUMN pause_comment TEXT"),
        ("fault_report.pause_started_at", "ALTER TABLE fault_report ADD COLUMN pause_started_at DATETIME"),
        ("fault_report.pause_until", "ALTER TABLE fault_report ADD COLUMN pause_until DATE"),
        ("user.work_hours_exempt", "ALTER TABLE user ADD COLUMN work_hours_exempt BOOLEAN DEFAULT 0"),
        ("user.preferred_language", "ALTER TABLE user ADD COLUMN preferred_language VARCHAR(5)"),
        ("gas_cylinder.side", "ALTER TABLE gas_cylinder ADD COLUMN side VARCHAR(10)"),
        ("gas_cylinder.refill_date", "ALTER TABLE gas_cylinder ADD COLUMN refill_date DATE"),
        ("fault_report.lead_technician_id", "ALTER TABLE fault_report ADD COLUMN lead_technician_id INTEGER REFERENCES user(id)"),
        ("fault_report.first_response_at", "ALTER TABLE fault_report ADD COLUMN first_response_at DATETIME"),
        ("user_reminder", """CREATE TABLE IF NOT EXISTS user_reminder (
            id INTEGER PRIMARY KEY,
            created_by INTEGER NOT NULL REFERENCES user(id),
            title VARCHAR(200) NOT NULL,
            body TEXT,
            due_at DATETIME,
            is_done BOOLEAN DEFAULT 0,
            created_at DATETIME
        )"""),
        ("user_reminder_target", """CREATE TABLE IF NOT EXISTS user_reminder_target (
            reminder_id INTEGER NOT NULL REFERENCES user_reminder(id),
            user_id INTEGER NOT NULL REFERENCES user(id),
            PRIMARY KEY (reminder_id, user_id)
        )"""),
        ("machine_password", """CREATE TABLE IF NOT EXISTS machine_password (
            id INTEGER PRIMARY KEY,
            machine_id INTEGER REFERENCES machine(id),
            title VARCHAR(200) NOT NULL,
            login VARCHAR(100),
            password VARCHAR(200) NOT NULL,
            kind VARCHAR(50) DEFAULT 'other',
            notes TEXT,
            created_by INTEGER REFERENCES user(id),
            created_at DATETIME,
            updated_at DATETIME
        )"""),
        ("floor_map_line", """CREATE TABLE IF NOT EXISTS floor_map_line (
            id INTEGER PRIMARY KEY,
            name VARCHAR(120),
            color VARCHAR(20),
            points_json TEXT,
            created_by INTEGER REFERENCES user(id),
            created_at DATETIME
        )"""),
        ("technical_work_order.approved_by", "ALTER TABLE technical_work_order ADD COLUMN approved_by INTEGER REFERENCES user(id)"),
        ("technical_work_order.approved_at", "ALTER TABLE technical_work_order ADD COLUMN approved_at DATETIME"),
        ("technical_work_order.approval_comment", "ALTER TABLE technical_work_order ADD COLUMN approval_comment TEXT"),
        ("push_subscription", """CREATE TABLE IF NOT EXISTS push_subscription (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES user(id),
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            user_agent VARCHAR(300),
            created_at DATETIME,
            last_used_at DATETIME
        )"""),
        ("fault_work_session", """CREATE TABLE IF NOT EXISTS fault_work_session (
            id INTEGER PRIMARY KEY,
            fault_id INTEGER NOT NULL REFERENCES fault_report(id),
            user_id INTEGER NOT NULL REFERENCES user(id),
            started_at DATETIME NOT NULL,
            ended_at DATETIME,
            duration_minutes REAL DEFAULT 0,
            notes TEXT
        )"""),
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
        ("equipment_mro_photo", """CREATE TABLE IF NOT EXISTS equipment_mro_photo (
            id INTEGER PRIMARY KEY,
            equipment_id INTEGER NOT NULL REFERENCES equipment_maintenance(id),
            filename VARCHAR(300) NOT NULL,
            description VARCHAR(300),
            uploaded_at DATETIME
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
        ("power_outlet", """CREATE TABLE IF NOT EXISTS power_outlet (
            id INTEGER PRIMARY KEY,
            section_id INTEGER REFERENCES factory_section(id),
            voltage VARCHAR(10) NOT NULL DEFAULT '220',
            location VARCHAR(300),
            breaker_id INTEGER REFERENCES circuit_breaker(id),
            quantity INTEGER DEFAULT 1,
            status VARCHAR(20) DEFAULT 'ok',
            notes TEXT,
            created_at DATETIME,
            updated_at DATETIME
        )"""),
        ("power_outlet_photo", """CREATE TABLE IF NOT EXISTS power_outlet_photo (
            id INTEGER PRIMARY KEY,
            outlet_id INTEGER NOT NULL REFERENCES power_outlet(id),
            filename VARCHAR(300) NOT NULL,
            description VARCHAR(300),
            uploaded_at DATETIME
        )"""),
        ("air_connection_point", """CREATE TABLE IF NOT EXISTS air_connection_point (
            id INTEGER PRIMARY KEY,
            number INTEGER NOT NULL,
            name VARCHAR(200) NOT NULL,
            point_type VARCHAR(30) DEFAULT 'connection',
            location VARCHAR(300),
            section_id INTEGER REFERENCES factory_section(id),
            status VARCHAR(20) DEFAULT 'ok',
            notes TEXT,
            map_x FLOAT,
            map_y FLOAT,
            created_at DATETIME,
            updated_at DATETIME
        )"""),
        ("air_connection_photo", """CREATE TABLE IF NOT EXISTS air_connection_photo (
            id INTEGER PRIMARY KEY,
            point_id INTEGER NOT NULL REFERENCES air_connection_point(id),
            filename VARCHAR(300) NOT NULL,
            description VARCHAR(300),
            uploaded_at DATETIME
        )"""),
        ("air_line", """CREATE TABLE IF NOT EXISTS air_line (
            id INTEGER PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            section_id INTEGER REFERENCES factory_section(id),
            from_point_id INTEGER REFERENCES air_connection_point(id),
            to_point_id INTEGER REFERENCES air_connection_point(id),
            length_m FLOAT,
            diameter_mm FLOAT,
            material VARCHAR(50) DEFAULT 'steel',
            status VARCHAR(20) DEFAULT 'ok',
            color VARCHAR(20),
            notes TEXT,
            created_at DATETIME,
            updated_at DATETIME
        )"""),
        ("air_line_vertex", """CREATE TABLE IF NOT EXISTS air_line_vertex (
            id INTEGER PRIMARY KEY,
            line_id INTEGER NOT NULL REFERENCES air_line(id),
            seq INTEGER NOT NULL DEFAULT 0,
            map_x FLOAT NOT NULL,
            map_y FLOAT NOT NULL
        )"""),
        ("water_connection_point", """CREATE TABLE IF NOT EXISTS water_connection_point (
            id INTEGER PRIMARY KEY,
            number INTEGER NOT NULL,
            name VARCHAR(200) NOT NULL,
            point_type VARCHAR(30) DEFAULT 'connection',
            location VARCHAR(300),
            section_id INTEGER REFERENCES factory_section(id),
            status VARCHAR(20) DEFAULT 'ok',
            notes TEXT,
            map_x FLOAT,
            map_y FLOAT,
            created_at DATETIME,
            updated_at DATETIME
        )"""),
        ("water_connection_photo", """CREATE TABLE IF NOT EXISTS water_connection_photo (
            id INTEGER PRIMARY KEY,
            point_id INTEGER NOT NULL REFERENCES water_connection_point(id),
            filename VARCHAR(300) NOT NULL,
            description VARCHAR(300),
            uploaded_at DATETIME
        )"""),
        ("water_line", """CREATE TABLE IF NOT EXISTS water_line (
            id INTEGER PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            section_id INTEGER REFERENCES factory_section(id),
            from_point_id INTEGER REFERENCES water_connection_point(id),
            to_point_id INTEGER REFERENCES water_connection_point(id),
            length_m FLOAT,
            diameter_mm FLOAT,
            material VARCHAR(50) DEFAULT 'steel',
            status VARCHAR(20) DEFAULT 'ok',
            color VARCHAR(20),
            notes TEXT,
            created_at DATETIME,
            updated_at DATETIME
        )"""),
        ("water_line_vertex", """CREATE TABLE IF NOT EXISTS water_line_vertex (
            id INTEGER PRIMARY KEY,
            line_id INTEGER NOT NULL REFERENCES water_line(id),
            seq INTEGER NOT NULL DEFAULT 0,
            map_x FLOAT NOT NULL,
            map_y FLOAT NOT NULL
        )"""),
        ("moeskroen_zone", """CREATE TABLE IF NOT EXISTS moeskroen_zone (
            id INTEGER PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            description TEXT,
            zone_type VARCHAR(50) DEFAULT 'production',
            color VARCHAR(20) DEFAULT '#3498db',
            floor_x FLOAT DEFAULT 10,
            floor_y FLOAT DEFAULT 10,
            width FLOAT DEFAULT 20,
            height FLOAT DEFAULT 15,
            notes TEXT,
            created_at DATETIME,
            updated_at DATETIME
        )"""),
        ("moeskroen_marker", """CREATE TABLE IF NOT EXISTS moeskroen_marker (
            id INTEGER PRIMARY KEY,
            number INTEGER NOT NULL,
            name VARCHAR(200) NOT NULL,
            kind VARCHAR(50) DEFAULT 'other',
            location VARCHAR(300),
            status VARCHAR(20) DEFAULT 'ok',
            color VARCHAR(20) DEFAULT '#e67e22',
            notes TEXT,
            map_x FLOAT,
            map_y FLOAT,
            created_at DATETIME,
            updated_at DATETIME
        )"""),
        ("moeskroen_marker_photo", """CREATE TABLE IF NOT EXISTS moeskroen_marker_photo (
            id INTEGER PRIMARY KEY,
            marker_id INTEGER NOT NULL REFERENCES moeskroen_marker(id),
            filename VARCHAR(300) NOT NULL,
            description VARCHAR(300),
            uploaded_at DATETIME
        )"""),
        ("moeskroen_line", """CREATE TABLE IF NOT EXISTS moeskroen_line (
            id INTEGER PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            kind VARCHAR(50) DEFAULT 'route',
            color VARCHAR(20) DEFAULT '#8e44ad',
            width_px FLOAT DEFAULT 2.5,
            notes TEXT,
            created_at DATETIME,
            updated_at DATETIME
        )"""),
        ("moeskroen_line_vertex", """CREATE TABLE IF NOT EXISTS moeskroen_line_vertex (
            id INTEGER PRIMARY KEY,
            line_id INTEGER NOT NULL REFERENCES moeskroen_line(id),
            seq INTEGER NOT NULL DEFAULT 0,
            map_x FLOAT NOT NULL,
            map_y FLOAT NOT NULL
        )"""),
        ("machine.dim_length_mm", "ALTER TABLE machine ADD COLUMN dim_length_mm FLOAT"),
        ("machine.dim_width_mm", "ALTER TABLE machine ADD COLUMN dim_width_mm FLOAT"),
        ("machine.dim_height_mm", "ALTER TABLE machine ADD COLUMN dim_height_mm FLOAT"),
        ("machine.power_voltage", "ALTER TABLE machine ADD COLUMN power_voltage VARCHAR(20)"),
        ("machine.power_kw", "ALTER TABLE machine ADD COLUMN power_kw FLOAT"),
        ("machine.air_usage_m3h", "ALTER TABLE machine ADD COLUMN air_usage_m3h FLOAT"),
        ("machine.water_usage_lmin", "ALTER TABLE machine ADD COLUMN water_usage_lmin FLOAT"),
        ("machine.gas_usage_m3h", "ALTER TABLE machine ADD COLUMN gas_usage_m3h FLOAT"),
        ("machine.gas_natural", "ALTER TABLE machine ADD COLUMN gas_natural BOOLEAN DEFAULT 0"),
        ("machine.gas_nitrogen", "ALTER TABLE machine ADD COLUMN gas_nitrogen BOOLEAN DEFAULT 0"),
        ("machine.gas_co2", "ALTER TABLE machine ADD COLUMN gas_co2 BOOLEAN DEFAULT 0"),
        ("machine.nitrogen_usage_m3h", "ALTER TABLE machine ADD COLUMN nitrogen_usage_m3h FLOAT"),
        ("machine.co2_usage_m3h", "ALTER TABLE machine ADD COLUMN co2_usage_m3h FLOAT"),
        ("offline_mutation", """CREATE TABLE IF NOT EXISTS offline_mutation (
            id INTEGER PRIMARY KEY,
            client_id VARCHAR(64) NOT NULL UNIQUE,
            user_id INTEGER REFERENCES user(id),
            path VARCHAR(300) NOT NULL,
            method VARCHAR(10) DEFAULT 'POST',
            temp_number VARCHAR(50),
            title VARCHAR(200),
            status VARCHAR(20) DEFAULT 'done',
            result_json TEXT,
            created_at DATETIME,
            completed_at DATETIME
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
        ("worker.hire_date", "ALTER TABLE worker ADD COLUMN hire_date DATE"),
        ("worker.fire_date", "ALTER TABLE worker ADD COLUMN fire_date DATE"),
        ("gas_cylinder.barcode", "ALTER TABLE gas_cylinder ADD COLUMN barcode VARCHAR(100)"),
        ("machine_part.next_replacement", "ALTER TABLE machine_part ADD COLUMN next_replacement DATE"),
        ("machine_part.next_maintenance", "ALTER TABLE machine_part ADD COLUMN next_maintenance DATE"),
        ("machine_part.last_replaced", "ALTER TABLE machine_part ADD COLUMN last_replaced DATE"),
        ("machine_part.replacement_interval_days", "ALTER TABLE machine_part ADD COLUMN replacement_interval_days INTEGER"),
        ("machine_part.maintenance_interval_days", "ALTER TABLE machine_part ADD COLUMN maintenance_interval_days INTEGER"),
        ("warehouse_item.expiry_date", "ALTER TABLE warehouse_item ADD COLUMN expiry_date DATE"),
        ("warehouse_item.barcode", "ALTER TABLE warehouse_item ADD COLUMN barcode VARCHAR(100)"),
        ("warehouse_item.serial_number", "ALTER TABLE warehouse_item ADD COLUMN serial_number VARCHAR(100)"),
        ("air_connection_point.point_type", "ALTER TABLE air_connection_point ADD COLUMN point_type VARCHAR(30) DEFAULT 'connection'"),
        ("water_connection_point.point_type", "ALTER TABLE water_connection_point ADD COLUMN point_type VARCHAR(30) DEFAULT 'connection'"),
        ("warehouse_reservation", """CREATE TABLE IF NOT EXISTS warehouse_reservation (
            id INTEGER PRIMARY KEY,
            item_id INTEGER NOT NULL REFERENCES warehouse_item(id),
            quantity FLOAT NOT NULL,
            reserved_by INTEGER REFERENCES user(id),
            reason TEXT,
            created_at DATETIME,
            expires_at DATETIME
        )"""),
        ("supplier_price", """CREATE TABLE IF NOT EXISTS supplier_price (
            id INTEGER PRIMARY KEY,
            item_id INTEGER NOT NULL REFERENCES warehouse_item(id),
            contractor_id INTEGER REFERENCES contractor(id),
            price NUMERIC(10,2),
            delivery_days INTEGER,
            min_order FLOAT,
            last_checked DATETIME,
            notes TEXT
        )"""),
        ("chat_room", """CREATE TABLE IF NOT EXISTS chat_room (
            id INTEGER PRIMARY KEY,
            name VARCHAR(200),
            is_group BOOLEAN DEFAULT 0,
            created_by INTEGER NOT NULL REFERENCES user(id),
            created_at DATETIME,
            updated_at DATETIME
        )"""),
        ("chat_participant", """CREATE TABLE IF NOT EXISTS chat_participant (
            id INTEGER PRIMARY KEY,
            room_id INTEGER NOT NULL REFERENCES chat_room(id),
            user_id INTEGER NOT NULL REFERENCES user(id),
            joined_at DATETIME,
            last_read_at DATETIME,
            UNIQUE(room_id, user_id)
        )"""),
        ("chat_message", """CREATE TABLE IF NOT EXISTS chat_message (
            id INTEGER PRIMARY KEY,
            room_id INTEGER NOT NULL REFERENCES chat_room(id),
            sender_id INTEGER NOT NULL REFERENCES user(id),
            body TEXT NOT NULL,
            created_at DATETIME,
            status_json TEXT DEFAULT '{}'
        )"""),
        # Old chat_message schema (chat_id/message) → new (room_id/body)
        ("chat_message.room_id", "ALTER TABLE chat_message ADD COLUMN room_id INTEGER"),
        ("chat_message.body", "ALTER TABLE chat_message ADD COLUMN body TEXT"),
        ("chat_message.status_json", "ALTER TABLE chat_message ADD COLUMN status_json TEXT DEFAULT '{}'"),
        ("record_lock", """CREATE TABLE IF NOT EXISTS record_lock (
            id INTEGER PRIMARY KEY,
            record_type VARCHAR(50) NOT NULL,
            record_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL REFERENCES user(id),
            user_name VARCHAR(100),
            locked_at DATETIME,
            expires_at DATETIME NOT NULL
        )"""),
        ("equipment.floor_x", "ALTER TABLE equipment ADD COLUMN floor_x FLOAT"),
        ("equipment.floor_y", "ALTER TABLE equipment ADD COLUMN floor_y FLOAT"),
    ]

    def _col_notnull(table, column):
        """True when table exists and column is NOT NULL."""
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        )
        if not cur.fetchone():
            return False
        cur.execute(f"PRAGMA table_info({table})")
        for r in cur.fetchall():
            if r[1] == column:
                return bool(r[3])  # notnull flag
        return False

    # Fix cylinder_log.cylinder_id to be nullable (SQLite needs table rebuild)
    if _col_notnull('cylinder_log', 'cylinder_id'):
        try:
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
            report['rebuilds'] += 1
            _schema_log("rebuilt cylinder_log (cylinder_id nullable)")
        except Exception as e:
            report['errors'].append(f"cylinder_log rebuild: {e}")
            _schema_err(f"cylinder_log rebuild: {e}")

    # Fix fault_report.machine_id to be nullable (for equipment-only faults)
    if _col_notnull('fault_report', 'machine_id'):
        try:
            cur.execute("ALTER TABLE fault_report RENAME TO fault_report_old")
            cur.execute("""CREATE TABLE fault_report (
                id INTEGER PRIMARY KEY,
                title VARCHAR(200) NOT NULL,
                description TEXT NOT NULL,
                priority VARCHAR(20),
                status VARCHAR(20),
                machine_id INTEGER REFERENCES machine(id),
                reporter_id INTEGER NOT NULL REFERENCES user(id),
                technician_id INTEGER REFERENCES user(id),
                created_at DATETIME,
                accepted_at DATETIME,
                resolved_at DATETIME,
                contractor_id INTEGER REFERENCES contractor(id),
                equipment_id INTEGER REFERENCES equipment(id)
            )""")
            cur.execute("INSERT INTO fault_report SELECT * FROM fault_report_old")
            cur.execute("DROP TABLE fault_report_old")
            conn.commit()
            report['rebuilds'] += 1
            _schema_log("rebuilt fault_report (machine_id nullable)")
        except Exception as e:
            report['errors'].append(f"fault_report rebuild: {e}")
            _schema_err(f"fault_report rebuild: {e}")

    # Fix maintenance_schedule: make machine_id nullable
    if _col_notnull('maintenance_schedule', 'machine_id'):
        try:
            cur.execute("ALTER TABLE maintenance_schedule RENAME TO maintenance_schedule_old")
            cur.execute("""CREATE TABLE maintenance_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_id INTEGER,
                equipment_id INTEGER,
                target_type VARCHAR(20) DEFAULT 'machine',
                target_name VARCHAR(200),
                title VARCHAR(200) NOT NULL,
                description TEXT,
                maintenance_type VARCHAR(50) DEFAULT 'preventive',
                recurrence VARCHAR(20) NOT NULL,
                preferred_dow INTEGER,
                preferred_day INTEGER,
                months_ahead INTEGER DEFAULT 3,
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME
            )""")
            cur.execute("INSERT INTO maintenance_schedule SELECT * FROM maintenance_schedule_old")
            cur.execute("DROP TABLE maintenance_schedule_old")
            conn.commit()
            report['rebuilds'] += 1
            _schema_log("rebuilt maintenance_schedule (machine_id nullable)")
        except Exception as e:
            report['errors'].append(f"maintenance_schedule rebuild: {e}")
            _schema_err(f"maintenance_schedule rebuild: {e}")

    # Declared CREATE TABLE / ALTER COLUMN (idempotent)
    for col_name, sql in migrations:
        try:
            if 'CREATE TABLE' in sql:
                table_name = sql.split('IF NOT EXISTS ')[1].split(' ')[0]
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,)
                )
                if not cur.fetchone():
                    cur.execute(sql)
                    report['tables_created'] += 1
                    _schema_log(f"created table {table_name}")
            elif 'ALTER TABLE' in sql:
                table = sql.split('ALTER TABLE ')[1].split(' ADD COLUMN')[0]
                col = sql.split('ADD COLUMN ')[1].split(' ')[0]
                cur.execute(f"PRAGMA table_info({table})")
                cols = [c[1] for c in cur.fetchall()]
                if col not in cols:
                    cur.execute(sql)
                    report['columns_added'] += 1
                    _schema_log(f"added column {table}.{col}")
        except Exception as e:
            report['errors'].append(f"{col_name}: {e}")
            _schema_err(f"{col_name}: {e}")

    # Model-driven columns (source of truth — catches legacy tables like old chat_message)
    changed, errs = _ensure_model_columns(cur)
    report['columns_added'] += len(changed)
    report['errors'].extend(errs)
    for name in changed:
        _schema_log(f"added model column {name}")
    for err in errs:
        _schema_err(err)

    # Backfill new chat_message columns from legacy ones (chat_id/message)
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_message'")
        if cur.fetchone():
            cur.execute("PRAGMA table_info(chat_message)")
            chat_cols = [c[1] for c in cur.fetchall()]
            if 'room_id' in chat_cols and 'chat_id' in chat_cols:
                cur.execute("UPDATE chat_message SET room_id = chat_id WHERE room_id IS NULL AND chat_id IS NOT NULL")
                if cur.rowcount:
                    report['backfills'] += 1
                    _schema_log(f"backfilled chat_message.room_id x{cur.rowcount}")
            if 'body' in chat_cols and 'message' in chat_cols:
                cur.execute("UPDATE chat_message SET body = message WHERE body IS NULL AND message IS NOT NULL")
                if cur.rowcount:
                    report['backfills'] += 1
                    _schema_log(f"backfilled chat_message.body x{cur.rowcount}")
            if 'status_json' in chat_cols:
                cur.execute("UPDATE chat_message SET status_json = '{}' WHERE status_json IS NULL")
    except Exception as e:
        report['errors'].append(f"chat_message backfill: {e}")
        _schema_err(f"chat_message backfill: {e}")

    # Fix empty datetime strings that cause ValueError on read
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        datetime_fixes = {
            'maintenance_schedule': ['created_at'],
            'maintenance_plan': ['created_at'],
            'maintenance_record': ['created_at', 'date_performed', 'next_maintenance'],
        }
        for table, cols in datetime_fixes.items():
            if table in tables:
                for col in cols:
                    try:
                        cur.execute(f"UPDATE [{table}] SET [{col}] = NULL WHERE [{col}] = '' OR [{col}] = 'None'")
                        if cur.rowcount > 0:
                            report['backfills'] += 1
                            _schema_log(f"fixed {cur.rowcount} empty {col} in {table}")
                    except Exception as e:
                        report['errors'].append(f"datetime fix {table}.{col}: {e}")
                        _schema_err(f"datetime fix {table}.{col}: {e}")
    except Exception as e:
        report['errors'].append(f"datetime fixes: {e}")
        _schema_err(f"datetime fixes: {e}")

    try:
        conn.commit()
    except Exception as e:
        report['errors'].append(f"commit: {e}")
        _schema_err(f"commit: {e}")
    conn.close()

    if report['errors']:
        _schema_err(f"finished with {len(report['errors'])} error(s): {report}")
    else:
        _schema_log(f"ok tables+{report['tables_created']} cols+{report['columns_added']} "
                    f"rebuilds={report['rebuilds']} backfills={report['backfills']}")
    return report


def run_migrations():
    """Schema reconciliation — always runs (idempotent). See ensure_schema()."""
    return ensure_schema()


def run_data_migrations():
    """Data migrations via SQLAlchemy ORM — works on both SQLite and PostgreSQL."""
    if _migrations_already_applied():
        return
    try:
        from models import (db, ResponsibleGroup, GroupPermission, User, UserSectionAccess,
                            Verantwoordelijke, Machine, Equipment, WarehouseGroup,
                            fault_technicians, user_machine, FaultReport,
                            Notification, Message, AuditLog, SystemLog,
                            UserActivityLog, WorkReport, PurchaseRequest,
                            TimeEntry, Vacation, WorkSchedule, WeekendShift,
                            CylinderLog, CylinderOrder)
        from sqlalchemy import text, func

        # ── 0. Fix admin role (auto-repair from role-switcher corruption) ──
        admin_user = User.query.filter_by(username='admin').first()
        if admin_user and admin_user.role != 'admin':
            print(f"Data migration: FIXING admin role from '{admin_user.role}' to 'admin'")
            admin_user.role = 'admin'
            safe_commit()

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
        safe_commit()

        # ── 1b. Logistiek - Oktopus group ────────────────────────────
        logistiek = ResponsibleGroup.query.filter_by(name='Logistiek - Oktopus').first()
        if not logistiek:
            logistiek = ResponsibleGroup(name='Logistiek - Oktopus', access_level='technician',
                                         description='Logistics team for Oktopus spare parts')
            db.session.add(logistiek)
            db.session.flush()
            print("Data migration: created group 'Logistiek - Oktopus'")
        safe_commit()

        # Move Pablo and Paulina to Logistiek group + set login credentials
        # Passwords come from env (LOGISTIEK_PABLO_PW / LOGISTIEK_PAULINA_PW).
        # Never hardcode credentials in source.
        import os as _os
        logistiek_creds = {
            'Pablo':   ('pablo',   _os.environ.get('LOGISTIEK_PABLO_PW', '')),
            'Paulina': ('paulina', _os.environ.get('LOGISTIEK_PAULINA_PW', '')),
        }
        for pname, (uname, pw) in logistiek_creds.items():
            p = Verantwoordelijke.query.filter_by(naam=pname).first()
            if p:
                changed = False
                if p.group_id != logistiek.id:
                    p.group_id = logistiek.id
                    changed = True
                if p.access_level != 'floor':
                    p.access_level = 'floor'
                    changed = True
                if p.username != uname:
                    p.username = uname
                    changed = True
                if changed:
                    if pw:
                        p.set_password(pw)
                    # Не ставим force_change_password — смена пароля только по желанию
                    safe_commit()
                    print(f"Data migration: configured {pname} login as '{uname}' in Logistiek group"
                          + (" (password set from env)" if pw else " (password must be set by admin)"))

        # Create WarehouseGroup for Oktopus
        oktopus_wh = WarehouseGroup.query.filter_by(name='Logistiek - Oktopus').first()
        if not oktopus_wh:
            oktopus_wh = WarehouseGroup(name='Logistiek - Oktopus', description='Oktopus spare parts inventory')
            db.session.add(oktopus_wh)
            safe_commit()
            print("Data migration: created warehouse group 'Logistiek - Oktopus'")

        # Logistiek group permissions — full warehouse CRUD + notifications + purchase_requests
        logistiek_perms = {
            'warehouse': (True, True, True, False),
            'consumables': (True, True, True, False),
            'purchase_requests': (True, True, True, False),
            'notifications': (True, True, True, False),
            'messages': (True, True, True, False),
            'dashboard': (True, False, False, False),
            'machines': (True, False, False, False),
            'equipment': (True, False, False, False),
            'faults': (True, True, False, False),
            'reports': (True, False, False, False),
        }
        for section, (v, c, e, d) in logistiek_perms.items():
            p = GroupPermission.query.filter_by(group_id=logistiek.id, section_key=section).first()
            if p:
                changed = False
                if p.can_view != v: p.can_view = v; changed = True
                if p.can_create != c: p.can_create = c; changed = True
                if p.can_edit != e: p.can_edit = e; changed = True
                if p.can_delete != d: p.can_delete = d; changed = True
                if changed:
                    print(f"Data migration: updated Logistiek perm '{section}'")
            else:
                db.session.add(GroupPermission(
                    group_id=logistiek.id, section_key=section,
                    can_view=v, can_create=c, can_edit=e, can_delete=d
                ))
        safe_commit()

        # ── 1c. Create User accounts for Monteurs without one ──────────
        from models import Monteur
        monteurs = Monteur.query.filter_by(actief=True).all()
        for m in monteurs:
            if not m.user_id:
                # Create a User account for this monteur
                uname = m.naam.lower().replace(' ', '.').replace('..', '.')
                # Ensure unique username
                base_uname = uname
                counter = 1
                while User.query.filter_by(username=uname).first():
                    uname = f"{base_uname}{counter}"
                    counter += 1
                mu = User(username=uname, display_name=m.naam, role='technician',
                          is_active_user=True)
                _dpw = os.environ.get('DEFAULT_USER_PW') or __import__('secrets').token_urlsafe(12)
                mu.set_password(_dpw)
                # Без force_change_password — принудительная смена не требуется
                db.session.add(mu)
                db.session.flush()
                m.user_id = mu.id
                safe_commit()
                print(f"Data migration: created User '{uname}' for Monteur '{m.naam}'")
            elif m.user_id:
                # Ensure existing user has technician role
                linked = User.query.get(m.user_id)
                if linked and linked.role != 'technician' and linked.role != 'admin':
                    linked.role = 'technician'
                    safe_commit()
                    print(f"Data migration: fixed role for Monteur user '{linked.username}' -> technician")

        # ── 2. Base group permissions (view on all modules) ─────────────
        all_sections = [
            'dashboard', 'floor', 'machines', 'equipment', 'tool_wear',
            'assets', 'electricity', 'gas', 'air', 'water', 'maintenance', 'maintenance_plans',
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
                'air': (True, True, True, False),
                'water': (True, True, True, False),
                'moeskroen': (True, True, True, True),
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
                'air': (True, True, True, False),
                'water': (True, True, True, False),
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
                'air': (True, False, False, False),
                'water': (True, False, False, False),
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

        safe_commit()

        # ── 6b. Clean stale data in client table ────────────────────────
        try:
            db.session.execute(text("UPDATE client SET password_plain=NULL, position=NULL, notities=NULL"))
            safe_commit()
            print("Data migration: cleared password_plain and stale fields from client table")
        except Exception:
            db.session.rollback()

        # ── 6c. SECURITY: wipe plaintext passwords from user table ──────
        try:
            db.session.execute(text("UPDATE user SET password_plain=NULL"))
            safe_commit()
            print("Data migration: SECURITY — wiped password_plain from user table")
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
            'Pablo':   ('Logistiek - Oktopus', 'floor'),
            'Hashem':  ('User', 'floor'),
            'Paulina': ('Logistiek - Oktopus', 'floor'),
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

        safe_commit()

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

        safe_commit()

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
                'assets', 'electricity', 'gas', 'air', 'water', 'maintenance_plans', 'repairs',
                'schedule', 'vacations', 'time_tracking', 'clients', 'workers',
                'invoices', 'consumables', 'work_report', 'archive', 'statistics', 'sections',
            ]
            existing_usa = {s.section_key for s in UserSectionAccess.query.filter_by(user_id=director_user.id).all()}
            for section in extra_sections:
                if section not in existing_usa:
                    db.session.add(UserSectionAccess(user_id=director_user.id, section_key=section))

        # Mark migration as done
        db.session.add(UserSectionAccess(user_id=0, section_key=marker_key))
        safe_commit()
        print("Data migration: user cleanup complete.")

    except Exception as e:
        db.session.rollback()
        print(f"Data migration error: {e}")


