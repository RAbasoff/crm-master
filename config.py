import os, secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _get_secret_key():
    key = os.environ.get('SECRET_KEY')
    if key:
        return key
    # In development, persist a random key so sessions survive restarts
    key_file = os.path.join(BASE_DIR, 'instance', '.secret_key')
    if os.path.exists(key_file):
        with open(key_file, 'r') as f:
            return f.read().strip()
    # Fixed fallback for production — ensures sessions survive server reloads
    return 'werkplaats-crm-prod-2026-abasoff-stable-key'

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
