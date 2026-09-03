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
    key = secrets.token_hex(32)
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    with open(key_file, 'w') as f:
        f.write(key)
    return key

class Config:
    SECRET_KEY = _get_secret_key()
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'werkplaats.db'))
    # Fix for Render.com (postgres:// → postgresql://)
    if SQLALCHEMY_DATABASE_URI.startswith('postgres://'):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    BABEL_DEFAULT_LOCALE = 'nl'
    BABEL_SUPPORTED_LOCALES = ['nl', 'en', 'ru', 'pl']
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    MAX_CONTENT_LENGTH = 200 * 1024 * 1024  # 200MB

LANGUAGES = {'nl': 'Nederlands', 'en': 'English', 'ru': 'Русский', 'pl': 'Polski'}

SECTION_KEYS = ['machines', 'warehouse', 'orders', 'clients', 'workers', 'faults',
                'messages', 'reports', 'schedule', 'time_tracking', 'vacations',
                'cylinders', 'maintenance', 'purchase_requests', 'users', 'sections', 'floor',
                'invoices', 'contractors', 'two', 'audit_log', 'settings', 'statistics',
                'equipment', 'consumables', 'electricity', 'tool_wear']
