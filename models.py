from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from sqlalchemy import Numeric

db = SQLAlchemy()

# ============================================================
# ASSOCIATION TABLES
# ============================================================

user_machine = db.Table('user_machine',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('machine_id', db.Integer, db.ForeignKey('machine.id'), primary_key=True)
)

section_responsible = db.Table('section_responsible',
    db.Column('section_id', db.Integer, db.ForeignKey('factory_section.id'), primary_key=True),
    db.Column('person_id', db.Integer, db.ForeignKey('client.id'), primary_key=True)
)

# ============================================================
# USER MODELS
# ============================================================

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    display_name = db.Column(db.String(200))
    role = db.Column(db.String(20), default='user', index=True)
    access_level = db.Column(db.String(20), default='full')
    person_id = db.Column(db.Integer, db.ForeignKey('client.id'))
    is_active_user = db.Column(db.Boolean, default=True, index=True)
    hire_date = db.Column(db.Date)
    fire_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    assigned_machines = db.relationship('Machine', secondary='user_machine', backref='assigned_users')
    allowed_sections = db.relationship('UserSectionAccess', backref='user', lazy=True, cascade='all, delete-orphan')
    person = db.relationship('Verantwoordelijke', foreign_keys=[person_id])
    sent_messages = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy=True, cascade='all, delete-orphan')
    received_messages = db.relationship('Message', foreign_keys='Message.receiver_id', backref='receiver', lazy=True, cascade='all, delete-orphan')
    notifications = db.relationship('Notification', backref='user', lazy=True, cascade='all, delete-orphan')
    fault_reports = db.relationship('FaultReport', foreign_keys='FaultReport.reporter_id', backref='reporter', lazy=True)
    work_reports = db.relationship('WorkReport', backref='technician', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def has_role(self, *roles):
        return self.role in roles

class UserSectionAccess(db.Model):
    __tablename__ = 'user_section_access'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    section_key = db.Column(db.String(50), nullable=False)

# ============================================================
# FACTORY MODELS
# ============================================================

class FactorySection(db.Model):
    __tablename__ = 'factory_section'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    section_type = db.Column(db.String(50), default='workshop')
    color = db.Column(db.String(20), default='#3498db')
    floor_x = db.Column(db.Float, default=10)
    floor_y = db.Column(db.Float, default=10)
    width = db.Column(db.Float, default=25)
    height = db.Column(db.Float, default=25)
    responsible_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    responsible_user = db.relationship('User', foreign_keys=[responsible_user_id], backref='administered_sections')
    responsible_persons = db.relationship('Verantwoordelijke', secondary=section_responsible, backref='resp_sections')
    machines = db.relationship('Machine', backref='section', lazy=True, cascade='all, delete-orphan')

class Machine(db.Model):
    __tablename__ = 'machine'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    serial_number = db.Column(db.String(100))
    machine_type = db.Column(db.String(100))
    manufacturer = db.Column(db.String(200))
    year_of_manufacture = db.Column(db.Integer)
    installation_location = db.Column(db.String(300))
    responsible_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    responsible_person_id = db.Column(db.Integer, db.ForeignKey('client.id'))
    contractor_id = db.Column(db.Integer, db.ForeignKey('contractor.id'))
    section_id = db.Column(db.Integer, db.ForeignKey('factory_section.id'), index=True)
    photo = db.Column(db.String(300))
    marker_size = db.Column(db.Integer, default=45)
    marker_shape = db.Column(db.String(20), default='circle')
    status = db.Column(db.String(20), default='active', index=True)
    floor_x = db.Column(db.Float, default=50)
    floor_y = db.Column(db.Float, default=50)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    responsible_user = db.relationship('User', foreign_keys=[responsible_user_id], backref='responsible_machines')
    responsible_person = db.relationship('Verantwoordelijke', foreign_keys=[responsible_person_id])
    contractor_rel = db.relationship('Contractor', foreign_keys=[contractor_id], back_populates='machines')
    fault_reports = db.relationship('FaultReport', backref='machine', lazy=True)
    spare_parts = db.relationship('MachineSparePart', backref='machine', lazy=True, cascade='all, delete-orphan')
    documents = db.relationship('MachineDocument', backref='machine', lazy=True, cascade='all, delete-orphan')
    maintenance_records = db.relationship('MaintenanceRecord', backref='machine', lazy=True, cascade='all, delete-orphan')
    parts = db.relationship('MachinePart', backref='machine', lazy=True, cascade='all, delete-orphan')

class MachinePart(db.Model):
    __tablename__ = 'machine_part'
    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    part_number = db.Column(db.String(100))
    description = db.Column(db.Text)
    category = db.Column(db.String(50), default='mechanical')
    installed_date = db.Column(db.Date)
    lifespan_days = db.Column(db.Integer)
    last_replacement = db.Column(db.Date)
    next_replacement = db.Column(db.Date)
    replacement_interval_days = db.Column(db.Integer)
    last_maintenance = db.Column(db.Date)
    next_maintenance = db.Column(db.Date)
    maintenance_interval_days = db.Column(db.Integer)
    status = db.Column(db.String(20), default='ok')
    responsible_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    responsible_user = db.relationship('User', foreign_keys=[responsible_user_id])
    logs = db.relationship('PartMaintenanceLog', backref='part', lazy=True, order_by='PartMaintenanceLog.date.desc()', cascade='all, delete-orphan')

class PartMaintenanceLog(db.Model):
    __tablename__ = 'part_maintenance_log'
    id = db.Column(db.Integer, primary_key=True)
    part_id = db.Column(db.Integer, db.ForeignKey('machine_part.id'), nullable=False)
    action = db.Column(db.String(30), nullable=False)
    description = db.Column(db.Text)
    performed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    cost = db.Column(Numeric(10, 2), default=0)
    notes = db.Column(db.Text)
    performer = db.relationship('User', foreign_keys=[performed_by])

class MachineDocument(db.Model):
    __tablename__ = 'machine_document'
    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False)
    doc_type = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    filename = db.Column(db.String(300), nullable=False)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    uploader = db.relationship('User', foreign_keys=[uploaded_by])

class MaintenanceRecord(db.Model):
    __tablename__ = 'maintenance_record'
    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False, index=True)
    maintenance_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=False)
    performed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    date_performed = db.Column(db.DateTime, nullable=False)
    next_maintenance = db.Column(db.DateTime)
    cost = db.Column(Numeric(10, 2), default=0)
    parts_used = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    performer = db.relationship('User', foreign_keys=[performed_by])
    photos = db.relationship('MaintenancePhoto', backref='maintenance', lazy=True, cascade='all, delete-orphan')

class MaintenancePhoto(db.Model):
    __tablename__ = 'maintenance_photo'
    id = db.Column(db.Integer, primary_key=True)
    maintenance_id = db.Column(db.Integer, db.ForeignKey('maintenance_record.id'), nullable=False)
    filename = db.Column(db.String(300), nullable=False)
    description = db.Column(db.String(300))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

class MaintenancePlan(db.Model):
    __tablename__ = 'maintenance_plan'
    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    maintenance_type = db.Column(db.String(50), default='preventive')
    status = db.Column(db.String(20), default='planned')
    planned_start = db.Column(db.Date, nullable=False)
    planned_end = db.Column(db.Date)
    actual_start = db.Column(db.Date)
    actual_end = db.Column(db.Date)
    is_external = db.Column(db.Boolean, default=False)
    company_name = db.Column(db.String(200))
    company_contact = db.Column(db.String(200))
    company_person = db.Column(db.String(200))
    offer_file = db.Column(db.String(300))
    worker_id = db.Column(db.Integer, db.ForeignKey('worker.id'))
    parts_used = db.Column(db.Text)
    cost = db.Column(Numeric(10, 2), default=0)
    report = db.Column(db.Text)
    work_act_file = db.Column(db.String(300))
    next_maintenance = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    machine = db.relationship('Machine', backref='maintenance_plans')
    worker = db.relationship('Monteur', backref='maintenance_plans')
    creator = db.relationship('User', foreign_keys=[created_by])

class MachineSparePart(db.Model):
    __tablename__ = 'machine_spare_part'
    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False)
    warehouse_item_id = db.Column(db.Integer, db.ForeignKey('warehouse_item.id'), nullable=False)
    quantity_needed = db.Column(db.Float, default=0)

class ToolWear(db.Model):
    __tablename__ = 'tool_wear'
    id = db.Column(db.Integer, primary_key=True)
    machine_name = db.Column(db.String(200), nullable=False)
    tool_name = db.Column(db.String(200), nullable=False)
    cycle_days = db.Column(db.Integer, default=14)
    wear_percent = db.Column(db.Float, default=0)
    critical_percent = db.Column(db.Float, default=80)
    last_replaced = db.Column(db.Date)
    notes = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.Integer, db.ForeignKey('user.id'))

# ============================================================
# CONTRACTOR MODELS
# ============================================================

class ResponsibleGroup(db.Model):
    __tablename__ = 'responsible_group'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    access_level = db.Column(db.String(20), default='user')  # admin, director, technician, user, quality
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    members = db.relationship('Verantwoordelijke', backref='resp_group', lazy=True, cascade='all, delete-orphan')
    permissions = db.relationship('GroupPermission', backref='group', lazy=True, cascade='all, delete-orphan')

class GroupPermission(db.Model):
    __tablename__ = 'group_permission'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('responsible_group.id'), nullable=False)
    section_key = db.Column(db.String(50), nullable=False)
    can_view = db.Column(db.Boolean, default=False)
    can_create = db.Column(db.Boolean, default=False)
    can_edit = db.Column(db.Boolean, default=False)
    can_delete = db.Column(db.Boolean, default=False)
    
    __table_args__ = (db.UniqueConstraint('group_id', 'section_key'),)

class Verantwoordelijke(db.Model):
    __tablename__ = 'client'
    id = db.Column(db.Integer, primary_key=True)
    naam = db.Column(db.String(200), nullable=False)
    company = db.Column(db.String(200))
    position = db.Column(db.String(100))
    telefoon = db.Column(db.String(50))
    telefoon2 = db.Column(db.String(50))
    internal_phone = db.Column(db.String(50))
    email = db.Column(db.String(100))
    website = db.Column(db.String(200))
    adres = db.Column(db.String(300))
    postcode = db.Column(db.String(20))
    stad = db.Column(db.String(100))
    land = db.Column(db.String(100), default='Nederland')
    kvk_nummer = db.Column(db.String(50))
    btw_nummer = db.Column(db.String(50))
    iban = db.Column(db.String(50))
    contract_nummer = db.Column(db.String(100))
    contract_start = db.Column(db.Date)
    contract_einde = db.Column(db.Date)
    service_type = db.Column(db.String(200))
    group_id = db.Column(db.Integer, db.ForeignKey('responsible_group.id'))
    monteur_id = db.Column(db.Integer, db.ForeignKey('worker.id'))
    notities = db.Column(db.Text)
    aangemaakt = db.Column(db.DateTime, default=datetime.utcnow)
    # Auth fields for responsible person login
    password_hash = db.Column(db.String(200))
    access_level = db.Column(db.String(20), default='floor')  # floor, full, limited
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)

    opdrachten = db.relationship('Opdracht', backref='verantwoordelijke', lazy=True, cascade='all, delete-orphan')
    monteur = db.relationship('Monteur', foreign_keys=[monteur_id], backref='linked_persons')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)


class ResponsibleAuth(UserMixin):
    """Wrapper to make Verantwoordelijke compatible with Flask-Login"""
    def __init__(self, person):
        self._person = person
        self.id = f"r_{person.id}"  # prefixed ID to distinguish from User
        self.username = f"resp_{person.id}"
        self.display_name = person.naam
        self.role = 'responsible'
        self.is_active_user = person.is_active

    @property
    def person(self):
        return self._person

    @property
    def person_id(self):
        return self._person.id

    def has_role(self, *roles):
        return 'responsible' in roles

    def check_password(self, password):
        return self._person.check_password(password)


class Monteur(db.Model):
    __tablename__ = 'worker'
    id = db.Column(db.Integer, primary_key=True)
    naam = db.Column(db.String(200), nullable=False)
    telefoon = db.Column(db.String(50))
    specialisatie = db.Column(db.String(200))
    tarief_per_uur = db.Column(Numeric(10, 2), default=0)
    actief = db.Column(db.Boolean, default=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    group_id = db.Column(db.Integer, db.ForeignKey('responsible_group.id'))
    opdrachten = db.relationship('Opdracht', backref='monteur', lazy=True, cascade='all, delete-orphan')
    user = db.relationship('User', foreign_keys=[user_id], backref='worker_profile')
    resp_group = db.relationship('ResponsibleGroup', foreign_keys=[group_id], backref='workers')

class Contractor(db.Model):
    __tablename__ = 'contractor'
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(200), nullable=False)
    contact_person = db.Column(db.String(200))
    contact_position = db.Column(db.String(100))
    phone = db.Column(db.String(50))
    phone2 = db.Column(db.String(50))
    email = db.Column(db.String(100))
    website = db.Column(db.String(200))
    address = db.Column(db.String(300))
    postcode = db.Column(db.String(20))
    city = db.Column(db.String(100))
    country = db.Column(db.String(100), default='Nederland')
    kvk_number = db.Column(db.String(50))
    btw_number = db.Column(db.String(50))
    iban = db.Column(db.String(50))
    service_type = db.Column(db.String(200))
    contract_number = db.Column(db.String(100))
    contract_start = db.Column(db.Date)
    contract_end = db.Column(db.Date)
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    employees = db.relationship('ContractorEmployee', backref='contractor', lazy=True, cascade='all, delete-orphan')
    machines = db.relationship('Machine', back_populates='contractor_rel', lazy=True)

class ContractorEmployee(db.Model):
    __tablename__ = 'contractor_employee'
    id = db.Column(db.Integer, primary_key=True)
    contractor_id = db.Column(db.Integer, db.ForeignKey('contractor.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    position = db.Column(db.String(100))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(100))
    notes = db.Column(db.Text)

# ============================================================
# WAREHOUSE MODELS
# ============================================================

class WarehouseGroup(db.Model):
    __tablename__ = 'warehouse_group'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    manufacturer = db.Column(db.String(200))
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('VoorraadItem', backref='group', lazy=True, cascade='all, delete-orphan')

class VoorraadItem(db.Model):
    __tablename__ = 'warehouse_item'
    id = db.Column(db.Integer, primary_key=True)
    naam = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text)
    categorie = db.Column(db.String(100))
    group_id = db.Column(db.Integer, db.ForeignKey('warehouse_group.id'))
    contractor_id = db.Column(db.Integer, db.ForeignKey('contractor.id'))  # supplier/contractor
    supplier_part_number = db.Column(db.String(100))
    eenheid = db.Column(db.String(20), default='st')
    hoeveelheid = db.Column(db.Float, default=0)
    minimum = db.Column(db.Float, default=0)
    prijs = db.Column(Numeric(10, 2), default=0)
    locatie = db.Column(db.String(100))
    # Consumable fields (for filters, oils, etc.)
    consumable_type = db.Column(db.String(50))  # filter, oil, grease, coolant
    consumable_subtype = db.Column(db.String(100))  # air filter, oil filter, hydraulic oil, etc.
    volume = db.Column(db.String(50))  # volume (L, kg)
    compatible_machines = db.Column(db.Text)  # compatible machine models
    replacement_interval = db.Column(db.String(50))  # replacement interval (e.g., "500 hours", "monthly")
    last_replacement = db.Column(db.Date)  # date of last replacement
    next_replacement = db.Column(db.Date)  # date of next replacement
    aangemaakt = db.Column(db.DateTime, default=datetime.utcnow)
    contractor = db.relationship('Contractor', backref='warehouse_items')
    mutaties = db.relationship('VoorraadMutatie', backref='item', lazy=True, cascade='all, delete-orphan')

class VoorraadMutatie(db.Model):
    __tablename__ = 'warehouse_movement'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('warehouse_item.id'), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    hoeveelheid = db.Column(db.Float, nullable=False)
    opdracht_id = db.Column(db.Integer, db.ForeignKey('opdracht.id'))
    opmerking = db.Column(db.Text)
    aangemaakt = db.Column(db.DateTime, default=datetime.utcnow)

class Invoice(db.Model):
    __tablename__ = 'invoice'
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(50), nullable=False)
    supplier = db.Column(db.String(200), nullable=False)
    invoice_date = db.Column(db.Date, nullable=False)
    due_date = db.Column(db.Date)
    total = db.Column(Numeric(10, 2), default=0)
    status = db.Column(db.String(20), default='draft', index=True)
    rejection_reason = db.Column(db.Text)
    signed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    signed_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    signer = db.relationship('User', foreign_keys=[signed_by])
    creator = db.relationship('User', foreign_keys=[created_by])
    items = db.relationship('InvoiceItem', backref='invoice', lazy=True, cascade='all, delete-orphan')

class InvoiceItem(db.Model):
    __tablename__ = 'invoice_item'
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    warehouse_item_id = db.Column(db.Integer, db.ForeignKey('warehouse_item.id'))
    description = db.Column(db.String(300), nullable=False)
    quantity = db.Column(db.Float, default=1)
    unit_price = db.Column(Numeric(10, 2), default=0)
    total_price = db.Column(Numeric(10, 2), default=0)
    warehouse_item = db.relationship('VoorraadItem')

# ============================================================
# OPERATIONAL MODELS
# ============================================================

class GasCylinder(db.Model):
    __tablename__ = 'gas_cylinder'
    id = db.Column(db.Integer, primary_key=True)
    gas_type = db.Column(db.String(20), nullable=False)
    cylinder_number = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='full')
    received_at = db.Column(db.DateTime)
    installed_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    logs = db.relationship('CylinderLog', backref='cylinder', lazy=True, order_by='CylinderLog.date.desc()', cascade='all, delete-orphan')

class GasSystemComponent(db.Model):
    __tablename__ = 'gas_system_component'
    id = db.Column(db.Integer, primary_key=True)
    gas_type = db.Column(db.String(20), nullable=False)  # nitrogen, co2
    component_type = db.Column(db.String(30), nullable=False)  # valve, regulator, heater, manometer, shut_off
    name = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='ok')  # ok, warning, faulty, replaced
    last_check = db.Column(db.Date)
    next_check = db.Column(db.Date)
    installed_at = db.Column(db.DateTime)  # when current unit was installed
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    repairs = db.relationship('EquipmentRepair', backref='component', lazy=True, order_by='EquipmentRepair.date_broken.desc()')


class EquipmentRepair(db.Model):
    """Track equipment repairs/replacements for gas system components"""
    __tablename__ = 'equipment_repair'
    id = db.Column(db.Integer, primary_key=True)
    component_id = db.Column(db.Integer, db.ForeignKey('gas_system_component.id'), nullable=False)
    # What broke
    fault_description = db.Column(db.Text, nullable=False)  # what exactly failed
    date_broken = db.Column(db.DateTime, nullable=False)  # when it broke
    # Repair info
    repair_company = db.Column(db.String(200))  # who fixed it
    repair_description = db.Column(db.Text)  # what was done
    repair_cost = db.Column(Numeric(10, 2), default=0)
    # Timeline
    date_sent = db.Column(db.DateTime)  # when sent for repair
    date_repaired = db.Column(db.DateTime)  # when repair was completed
    date_installed = db.Column(db.DateTime)  # when put back into service
    # Status
    status = db.Column(db.String(20), default='broken')  # broken, in_repair, repaired, installed
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    creator = db.relationship('User', foreign_keys=[created_by])

class CylinderLog(db.Model):
    __tablename__ = 'cylinder_log'
    id = db.Column(db.Integer, primary_key=True)
    cylinder_id = db.Column(db.Integer, db.ForeignKey('gas_cylinder.id'), nullable=True)
    action = db.Column(db.String(30), nullable=False)
    old_cylinder_number = db.Column(db.String(50))
    new_cylinder_number = db.Column(db.String(50))
    performed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    date = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)
    performer = db.relationship('User', foreign_keys=[performed_by])

class CylinderOrder(db.Model):
    __tablename__ = 'cylinder_order'
    id = db.Column(db.Integer, primary_key=True)
    gas_type = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    status = db.Column(db.String(20), default='pending')
    supplier = db.Column(db.String(200))
    reason = db.Column(db.Text)
    ordered_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    ordered_at = db.Column(db.DateTime, default=datetime.utcnow)
    delivered_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    orderer = db.relationship('User', foreign_keys=[ordered_by])

fault_technicians = db.Table('fault_technicians',
    db.Column('fault_id', db.Integer, db.ForeignKey('fault_report.id'), primary_key=True),
    db.Column('technician_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)

class FaultReport(db.Model):
    __tablename__ = 'fault_report'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(20), default='normal', index=True)
    status = db.Column(db.String(20), default='open', index=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False, index=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    technician_id = db.Column(db.Integer, db.ForeignKey('user.id'))  # primary technician (legacy)
    contractor_id = db.Column(db.Integer, db.ForeignKey('contractor.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    accepted_at = db.Column(db.DateTime)
    resolved_at = db.Column(db.DateTime)
    photos = db.relationship('FaultPhoto', backref='fault_report', lazy=True, cascade='all, delete-orphan')
    videos = db.relationship('FaultVideo', backref='fault_report', lazy=True, cascade='all, delete-orphan')
    work_report = db.relationship('WorkReport', backref='fault_report', lazy=True, cascade='all, delete-orphan')
    assigned_technicians = db.relationship('User', secondary=fault_technicians, backref='assigned_faults')
    contractor = db.relationship('Contractor', backref='fault_reports')
    status_history = db.relationship('FaultStatusHistory', backref='fault', lazy=True, order_by='FaultStatusHistory.changed_at', cascade='all, delete-orphan')

class FaultStatusHistory(db.Model):
    __tablename__ = 'fault_status_history'
    id = db.Column(db.Integer, primary_key=True)
    fault_id = db.Column(db.Integer, db.ForeignKey('fault_report.id'), nullable=False)
    old_status = db.Column(db.String(20))
    new_status = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.Text)
    changed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)
    changer = db.relationship('User', foreign_keys=[changed_by])

class FaultPhoto(db.Model):
    __tablename__ = 'fault_photo'
    id = db.Column(db.Integer, primary_key=True)
    fault_id = db.Column(db.Integer, db.ForeignKey('fault_report.id'), nullable=False)
    filename = db.Column(db.String(300), nullable=False)
    description = db.Column(db.String(300))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

class FaultVideo(db.Model):
    __tablename__ = 'fault_video'
    id = db.Column(db.Integer, primary_key=True)
    fault_id = db.Column(db.Integer, db.ForeignKey('fault_report.id'), nullable=False)
    filename = db.Column(db.String(300), nullable=False)
    description = db.Column(db.String(300))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

class WorkReport(db.Model):
    __tablename__ = 'work_report'
    id = db.Column(db.Integer, primary_key=True)
    fault_id = db.Column(db.Integer, db.ForeignKey('fault_report.id'), nullable=False)
    technician_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    work_description = db.Column(db.Text, nullable=False)
    parts_used = db.Column(db.Text)
    time_spent_hours = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    photos = db.relationship('WorkReportPhoto', backref='work_report', lazy=True, cascade='all, delete-orphan')

class WorkReportPhoto(db.Model):
    __tablename__ = 'work_report_photo'
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('work_report.id'), nullable=False)
    filename = db.Column(db.String(300), nullable=False)
    description = db.Column(db.String(300))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

# ============================================================
# TWO — Technical Work Order
# ============================================================

two_workers = db.Table('two_workers',
    db.Column('two_id', db.Integer, db.ForeignKey('technical_work_order.id'), primary_key=True),
    db.Column('worker_id', db.Integer, db.ForeignKey('worker.id'), primary_key=True)
)

class TechnicalWorkOrder(db.Model):
    __tablename__ = 'technical_work_order'
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(30), unique=True, nullable=False)  # TWO-20260812-0001
    status = db.Column(db.String(20), default='draft', index=True)  # draft, assigned, in_progress, completed, cancelled

    # Source fault
    fault_id = db.Column(db.Integer, db.ForeignKey('fault_report.id'), index=True)
    # Work details
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'))
    section_id = db.Column(db.Integer, db.ForeignKey('factory_section.id'))
    description = db.Column(db.Text, nullable=False)  # what needs to be done
    additional_work = db.Column(db.Text)  # extra work beyond fault

    # Dates
    planned_date = db.Column(db.Date)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    # Results
    result = db.Column(db.Text)  # what was done / result
    parts_used = db.Column(db.Text)  # JSON
    time_spent_hours = db.Column(db.Float, default=0)

    # Meta
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships
    fault = db.relationship('FaultReport', backref='work_orders')
    machine = db.relationship('Machine', backref='work_orders')
    section = db.relationship('FactorySection', backref='work_orders')
    creator = db.relationship('User', foreign_keys=[created_by])
    workers = db.relationship('Monteur', secondary=two_workers, backref='work_orders')
    photos = db.relationship('TWOPhoto', backref='two', lazy=True, cascade='all, delete-orphan')
    checklist_items = db.relationship('TWOChecklistItem', backref='two', lazy=True, order_by='TWOChecklistItem.sort_order', cascade='all, delete-orphan')
    signatures = db.relationship('TWOSignature', backref='two', lazy=True, cascade='all, delete-orphan')
    assignments = db.relationship('TWOAssignment', backref='two', lazy=True, order_by='TWOAssignment.sort_order', cascade='all, delete-orphan')

class TWOPhoto(db.Model):
    __tablename__ = 'two_photo'
    id = db.Column(db.Integer, primary_key=True)
    two_id = db.Column(db.Integer, db.ForeignKey('technical_work_order.id'), nullable=False)
    filename = db.Column(db.String(300), nullable=False)
    description = db.Column(db.String(300))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

class TWOChecklistItem(db.Model):
    __tablename__ = 'two_checklist_item'
    id = db.Column(db.Integer, primary_key=True)
    two_id = db.Column(db.Integer, db.ForeignKey('technical_work_order.id'), nullable=False)
    assignment_id = db.Column(db.Integer, db.ForeignKey('two_assignment.id'))
    text = db.Column(db.String(500), nullable=False)
    is_done = db.Column(db.Boolean, default=False)
    done_at = db.Column(db.DateTime)
    done_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    sort_order = db.Column(db.Integer, default=0)
    done_user = db.relationship('User', foreign_keys=[done_by])

class TWOAssignment(db.Model):
    __tablename__ = 'two_assignment'
    id = db.Column(db.Integer, primary_key=True)
    two_id = db.Column(db.Integer, db.ForeignKey('technical_work_order.id'), nullable=False)
    section_id = db.Column(db.Integer, db.ForeignKey('factory_section.id'))
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'))
    description = db.Column(db.Text)
    sort_order = db.Column(db.Integer, default=0)
    section = db.relationship('FactorySection', backref='two_assignments')
    machine = db.relationship('Machine', backref='two_assignments')
    checklist_items = db.relationship('TWOChecklistItem', backref='assignment',
        lazy=True, order_by='TWOChecklistItem.sort_order',
        primaryjoin='TWOChecklistItem.assignment_id == TWOAssignment.id',
        cascade='all, delete-orphan')

class TWOSignature(db.Model):
    __tablename__ = 'two_signature'
    id = db.Column(db.Integer, primary_key=True)
    two_id = db.Column(db.Integer, db.ForeignKey('technical_work_order.id'), nullable=False)
    signer_name = db.Column(db.String(200), nullable=False)
    signature_data = db.Column(db.Text, nullable=False)  # base64 PNG
    signed_at = db.Column(db.DateTime, default=datetime.utcnow)

class PurchaseRequest(db.Model):
    __tablename__ = 'purchase_request'
    id = db.Column(db.Integer, primary_key=True)
    fault_id = db.Column(db.Integer, db.ForeignKey('fault_report.id'))
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False)
    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    machine_serial = db.Column(db.String(100))
    fault_number = db.Column(db.String(50))
    fault_description = db.Column(db.Text)
    part_name = db.Column(db.String(300), nullable=False)
    part_catalog = db.Column(db.String(100))
    quantity = db.Column(db.Float, default=1)
    unit = db.Column(db.String(20), default='st')
    urgency = db.Column(db.String(20), default='normal')
    reason = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending', index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    machine = db.relationship('Machine', backref='purchase_requests')
    requester = db.relationship('User', foreign_keys=[requester_id], backref='purchase_requests')
    reviewer = db.relationship('User', foreign_keys=[reviewer_id])

class WorkSchedule(db.Model):
    __tablename__ = 'work_schedule'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    shift_start = db.Column(db.String(5), nullable=False)
    shift_end = db.Column(db.String(5), nullable=False)
    break_minutes = db.Column(db.Integer, default=60)
    work_days = db.Column(db.String(20), default='1,2,3,4,5')
    is_active = db.Column(db.Boolean, default=True)
    user = db.relationship('User', backref='schedules')

class TimeEntry(db.Model):
    __tablename__ = 'time_entry'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    clock_in = db.Column(db.DateTime)
    clock_out = db.Column(db.DateTime)
    break_minutes = db.Column(db.Integer, default=0)
    hours_worked = db.Column(db.Float, default=0)
    overtime_hours = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default='present')
    notes = db.Column(db.Text)
    approved_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', foreign_keys=[user_id], backref='time_entries')
    approver = db.relationship('User', foreign_keys=[approved_by])

class WeekendShift(db.Model):
    __tablename__ = 'weekend_shift'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    shift_type = db.Column(db.String(20), default='full')  # full, morning, afternoon
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', foreign_keys=[user_id], backref='weekend_shifts')
    creator = db.relationship('User', foreign_keys=[created_by])

class Vacation(db.Model):
    __tablename__ = 'vacation'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    vacation_type = db.Column(db.String(30), nullable=False)
    date_from = db.Column(db.Date, nullable=False)
    date_to = db.Column(db.Date, nullable=False)
    days_count = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending', index=True)
    approved_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', foreign_keys=[user_id], backref='vacations')
    approver = db.relationship('User', foreign_keys=[approved_by])

class Message(db.Model):
    __tablename__ = 'message'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    subject = db.Column(db.String(200))
    body = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    fault_id = db.Column(db.Integer, db.ForeignKey('fault_report.id'))

class Notification(db.Model):
    __tablename__ = 'notification'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text)
    type = db.Column(db.String(20), default='info')
    is_read = db.Column(db.Boolean, default=False, index=True)
    link = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Opdracht(db.Model):
    __tablename__ = 'opdracht'
    id = db.Column(db.Integer, primary_key=True)
    nummer = db.Column(db.String(20), unique=True)
    responsible_id = db.Column('klant_id', db.Integer, db.ForeignKey('client.id'), nullable=False, index=True)
    monteur_id = db.Column(db.Integer, db.ForeignKey('worker.id'))
    apparaat = db.Column(db.String(200), nullable=False)
    model = db.Column(db.String(200))
    serienummer = db.Column(db.String(100))
    probleem = db.Column(db.Text, nullable=False)
    diagnose = db.Column(db.Text)
    uitgevoerd = db.Column(db.Text)
    status = db.Column(db.String(30), default='aangenomen', index=True)
    arbeidskosten = db.Column(Numeric(10, 2), default=0)
    onderdelenkosten = db.Column(Numeric(10, 2), default=0)
    totaal = db.Column(Numeric(10, 2), default=0)
    aangemaakt = db.Column(db.DateTime, default=datetime.utcnow)
    gestart = db.Column(db.DateTime)
    gereed = db.Column(db.DateTime)
    afgeleverd = db.Column(db.DateTime)
    mutaties = db.relationship('VoorraadMutatie', backref='opdracht', lazy=True, cascade='all, delete-orphan')

class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(50), nullable=False)
    entity_type = db.Column(db.String(50), index=True)
    entity_id = db.Column(db.Integer, index=True)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    user = db.relationship('User', foreign_keys=[user_id])

class UserActivityLog(db.Model):
    __tablename__ = 'user_activity_log'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    username = db.Column(db.String(80))
    action = db.Column(db.String(50), nullable=False, index=True)  # login, logout, page_view, create, update, delete, export
    page = db.Column(db.String(200))  # URL or page name
    method = db.Column(db.String(10))  # GET, POST, PUT, DELETE
    entity_type = db.Column(db.String(50))
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    user_agent = db.Column(db.String(300))
    session_id = db.Column(db.String(100))
    duration_ms = db.Column(db.Integer)  # request duration in ms
    status_code = db.Column(db.Integer)  # HTTP status code
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    user = db.relationship('User', foreign_keys=[user_id])

class SystemLog(db.Model):
    __tablename__ = 'system_log'
    id = db.Column(db.Integer, primary_key=True)
    level = db.Column(db.String(10), nullable=False, index=True)  # INFO, WARNING, ERROR, CRITICAL
    category = db.Column(db.String(50), index=True)  # auth, database, email, payment, system, performance
    message = db.Column(db.Text, nullable=False)
    details = db.Column(db.Text)  # JSON with extra data
    source = db.Column(db.String(100))  # function name or module
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    ip_address = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    user = db.relationship('User', foreign_keys=[user_id])

class WorkReportEntry(db.Model):
    __tablename__ = 'work_report_entry'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    entry = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', foreign_keys=[user_id])

# ============================================================
# ELECTRICAL SYSTEM MODELS
# ============================================================

class ElectricalCabinet(db.Model):
    __tablename__ = 'electrical_cabinet'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    location = db.Column(db.String(300))
    cabinet_type = db.Column(db.String(50), default='distribution')
    description = db.Column(db.Text)
    photo = db.Column(db.String(300))
    manufacturer = db.Column(db.String(200))
    serial_number = db.Column(db.String(100))
    main_fuse_amps = db.Column(db.Integer)
    voltage = db.Column(db.String(20), default='400V/230V')
    schematic_x = db.Column(db.Integer, default=0)
    schematic_y = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    breakers = db.relationship('CircuitBreaker', backref='cabinet', lazy=True, cascade='all, delete-orphan',
                               order_by='CircuitBreaker.row, CircuitBreaker.position')


class CircuitBreaker(db.Model):
    __tablename__ = 'circuit_breaker'
    id = db.Column(db.Integer, primary_key=True)
    cabinet_id = db.Column(db.Integer, db.ForeignKey('electrical_cabinet.id'), nullable=False)
    label = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300))
    breaker_type = db.Column(db.String(50), default='MCB')
    amperage = db.Column(db.Integer)
    poles = db.Column(db.Integer, default=1)
    curve_type = db.Column(db.String(10), default='C')
    phase = db.Column(db.String(10))
    status = db.Column(db.String(20), default='on')
    connected_to = db.Column(db.String(300))
    notes = db.Column(db.Text)
    row = db.Column(db.Integer, default=1)
    position = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class MonthlyArchive(db.Model):
    __tablename__ = 'monthly_archive'
    id = db.Column(db.Integer, primary_key=True)
    archive_month = db.Column(db.String(7), nullable=False)  # YYYY-MM
    section = db.Column(db.String(50), nullable=False)  # faults, orders, reports, etc.
    data_json = db.Column(db.Text, nullable=False)  # JSON snapshot
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ============================================================
# CHAT MODELS
# ============================================================

chat_members = db.Table('chat_members',
    db.Column('chat_id', db.Integer, db.ForeignKey('chat_group.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)

class ChatGroup(db.Model):
    __tablename__ = 'chat_group'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    is_group = db.Column(db.Boolean, default=False)  # False = direct message, True = group
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    creator = db.relationship('User', foreign_keys=[created_by])
    members = db.relationship('User', secondary=chat_members, backref='chat_groups')
    messages = db.relationship('ChatMessage', backref='chat', lazy=True, cascade='all, delete-orphan',
        order_by='ChatMessage.created_at.desc()')

class ChatMessage(db.Model):
    __tablename__ = 'chat_message'
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chat_group.id'), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(20), default='text')  # text, image, file, system
    file_url = db.Column(db.String(500))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    sender = db.relationship('User', foreign_keys=[sender_id])

# ============================================================
# MULE MAINTENANCE MODELS
# ============================================================

class MuleMaintenance(db.Model):
    __tablename__ = 'mule_maintenance'
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(30), unique=True, nullable=False)
    mule_number = db.Column(db.String(100), nullable=False)
    mule_serial = db.Column(db.String(100))
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'))
    date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    next_date = db.Column(db.Date)
    periodicity = db.Column(db.String(50))
    status = db.Column(db.String(20), default='completed')
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    machine = db.relationship('Machine', backref='mule_maintenance')
    creator = db.relationship('User', foreign_keys=[created_by])
    parts = db.relationship('MuleMaintenancePart', backref='maintenance', lazy=True, cascade='all, delete-orphan')
    components = db.relationship('MuleComponent', backref='maintenance', lazy=True, cascade='all, delete-orphan')

class MuleComponent(db.Model):
    """Составные части муле: датчик, элемент, нож, кабель, прокладка, пружина, болт, фильтр, масло"""
    __tablename__ = 'mule_component'
    id = db.Column(db.Integer, primary_key=True)
    maintenance_id = db.Column(db.Integer, db.ForeignKey('mule_maintenance.id'), nullable=False)
    component_type = db.Column(db.String(50), nullable=False)
    model = db.Column(db.String(200))
    quantity = db.Column(db.Float, default=1)
    knife_number = db.Column(db.String(100))
    knife_size = db.Column(db.String(50))  # размер ножа в мм (длина лезвия)
    cable_type = db.Column(db.String(100))
    cable_length = db.Column(db.String(50))
    gasket_length = db.Column(db.String(50))
    spring_size = db.Column(db.String(100))
    bolt_type = db.Column(db.String(100))
    filter_type = db.Column(db.String(100))  # тип фильтра (воздушный, масляный, гидравлический)
    oil_type = db.Column(db.String(100))  # тип масла (гидравлическое, смазочное, охлаждающее)
    volume = db.Column(db.String(50))  # объём (для масла и фильтров)
    replacement_date = db.Column(db.Date)  # дата замены
    notes = db.Column(db.Text)

class MuleMaintenancePart(db.Model):
    __tablename__ = 'mule_maintenance_part'
    id = db.Column(db.Integer, primary_key=True)
    maintenance_id = db.Column(db.Integer, db.ForeignKey('mule_maintenance.id'), nullable=False)
    part_name = db.Column(db.String(200), nullable=False)
    part_number = db.Column(db.String(100))
    quantity = db.Column(db.Float, default=1)
    unit = db.Column(db.String(20), default='st')
    notes = db.Column(db.Text)

class MulePartOrder(db.Model):
    __tablename__ = 'mule_part_order'
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(30), unique=True, nullable=False)
    mule_number = db.Column(db.String(100))
    part_name = db.Column(db.String(200), nullable=False)
    part_number = db.Column(db.String(100))
    quantity = db.Column(db.Float, default=1)
    unit = db.Column(db.String(20), default='st')
    supplier = db.Column(db.String(200))
    urgency = db.Column(db.String(20), default='normal')
    status = db.Column(db.String(20), default='pending')
    notes = db.Column(db.Text)
    ordered_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    ordered_at = db.Column(db.DateTime, default=datetime.utcnow)
    delivered_at = db.Column(db.DateTime)
    orderer = db.relationship('User', foreign_keys=[ordered_by])
