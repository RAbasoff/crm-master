import os, secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_VERSION = '2.11'

def _get_secret_key():
    # Prefer env var (production: set SECRET_KEY on the server / PA)
    key = os.environ.get('SECRET_KEY')
    if key:
        return key
    # Fallback: persist a random key outside git (instance/ is gitignored)
    key_file = os.path.join(BASE_DIR, 'instance', '.secret_key')
    if os.path.exists(key_file):
        with open(key_file, 'r') as f:
            key = f.read().strip()
            if key:
                return key
    key = secrets.token_urlsafe(64)
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    with open(key_file, 'w') as f:
        f.write(key)
    return key

class Config:
    SECRET_KEY = _get_secret_key()
    
    # MySQL on Railway/PA (via env var), SQLite for local development
    _db_url = os.environ.get('DATABASE_URL', '')
    if _db_url:
        # Railway gives mysql:// — convert to mysql+pymysql://
        if _db_url.startswith('mysql://'):
            _db_url = _db_url.replace('mysql://', 'mysql+pymysql://', 1)
        SQLALCHEMY_DATABASE_URI = _db_url
    else:
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'werkplaats.db')
    
    # Fix for Render.com (postgres:// → postgresql://)
    if SQLALCHEMY_DATABASE_URI.startswith('postgres://'):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace('postgres://', 'postgresql://', 1)
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
    }
    # SQLite-specific options (only when using SQLite)
    if 'sqlite' in SQLALCHEMY_DATABASE_URI:
        SQLALCHEMY_ENGINE_OPTIONS['connect_args'] = {'timeout': 30}
    # MySQL-specific options
    elif 'mysql' in SQLALCHEMY_DATABASE_URI:
        SQLALCHEMY_ENGINE_OPTIONS['pool_recycle'] = 280
        SQLALCHEMY_ENGINE_OPTIONS['pool_size'] = 5
    BABEL_DEFAULT_LOCALE = 'nl'
    BABEL_SUPPORTED_LOCALES = ['nl', 'en', 'ru', 'pl']
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB

LANGUAGES = {'nl': 'Nederlands', 'en': 'English', 'ru': 'Русский', 'pl': 'Polski'}

SECTION_KEYS = [
    # Production
    'dashboard', 'floor', 'machines', 'equipment', 'tool_wear', 'assets',
    'electricity', 'gas', 'air', 'water', 'maintenance', 'maintenance_plans', 'repairs',
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

# Sections tree for permissions UI
SECTIONS_TREE = [
    {'group': 'Production', 'icon': '🏭', 'entries': [
        ('dashboard', 'Dashboard', '🏠'),
        ('floor', 'Floor Plan', '🏭'),
        ('machines', 'Machines', '⚙️'),
        ('equipment', 'Equipment / Mule Maintenance', '🔧'),
        ('tool_wear', 'Knife Sharpening', '🔪'),
        ('assets', 'Other Devices', '🏭'),
        ('electricity', 'Electricity', '⚡'),
        ('gas', 'Gas System', '🔴'),
        ('air', 'Compressed Air', '💨'),
        ('water', 'Water Supply', '💧'),
        ('maintenance', 'Maintenance Calendar', '📅'),
        ('maintenance_plans', 'Maintenance Plans', '📋'),
        ('repairs', 'Equipment Repairs', '🔧'),
        ('faults', 'Faults', '⚠️'),
        ('two', 'TWO', '📝'),
    ]},
    {'group': 'Communication', 'icon': '💬', 'entries': [
        ('messages', 'Messages', '💬'),
        ('notifications', 'Notifications', '🔔'),
    ]},
    {'group': 'Staff', 'icon': '👥', 'entries': [
        ('schedule', 'Schedule', '📅'),
        ('vacations', 'Vacations', '🏖'),
        ('time_tracking', 'Time Tracking', '⏱'),
    ]},
    {'group': 'Business', 'icon': '📋', 'entries': [
        ('orders', 'Work Orders', '📋'),
        ('clients', 'Responsible / Clients', '👤'),
        ('workers', 'Workers', '🔧'),
        ('invoices', 'Invoices', '📄'),
        ('contractors', 'Contractors', '🏢'),
        ('warehouse', 'Warehouse', '📦'),
        ('consumables', 'Replacement Reminders', '🔔'),
        ('purchase_requests', 'Purchase Requests', '🛒'),
    ]},
    {'group': 'Analytics', 'icon': '📊', 'entries': [
        ('reports', 'Reports', '📊'),
        ('work_report', 'Work Report', '📋'),
        ('archive', 'Archive', '📦'),
        ('statistics', 'Statistics', '📈'),
    ]},
    {'group': 'System', 'icon': '⚙️', 'entries': [
        ('settings', 'Settings', '⚙️'),
        ('users', 'Users', '👥'),
        ('audit_log', 'Audit Log', '📋'),
    ]},
]

SECTIONS_LIST = [(key, name, icon) for section in SECTIONS_TREE for key, name, icon in section['entries']]
