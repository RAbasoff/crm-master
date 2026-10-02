"""
CRM-система для мастерской с производственным модулем
Авторизация · Роли · Карта цеха · Заявки · Уведомления · Отчёты
"""
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session, g
from flask_babel import Babel, gettext as _
from flask_login import login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.utils import secure_filename
from urllib.parse import urlparse
from datetime import datetime, timedelta
import os, io, json
import qrcode
from sqlalchemy import func, case

from config import Config, LANGUAGES, SECTION_KEYS, SECTIONS_TREE, SECTIONS_LIST
from models import (db, User, UserSectionAccess, FactorySection, Machine, MachinePart,
                    PartMaintenanceLog, MachineDocument, MaintenanceRecord, MaintenancePhoto,
                    MaintenancePlan, MaintenanceSchedule, MachineSparePart, MachineConsumable, ResponsibleGroup, Verantwoordelijke,
                    Monteur, Contractor, ContractorEmployee, WarehouseGroup, VoorraadItem,
                    VoorraadMutatie, Invoice, InvoiceItem, FaultReport, FaultPhoto, FaultVideo, WorkReport, WorkReportPhoto,
                    PurchaseRequest, WorkSchedule, TimeEntry, Vacation, Message, Notification,
                    Opdracht, AuditLog, TechnicalWorkOrder, TWOPhoto, two_workers,
                    GasSystemComponent, EquipmentRepair, fault_technicians, user_machine, section_responsible,
                    GroupPermission, ResponsibleAuth, ElectricalCabinet, CircuitBreaker, WeekendShift,
                    FaultStatusHistory, WorkReportEntry, ToolWear, MonthlyArchive,
                    TWOChecklistItem, TWOSignature, TWOAssignment,
                    UserActivityLog, SystemLog,
                    EquipmentMaintenance, EquipmentPart, EquipmentComponent, EquipmentPartOrder, EquipmentMROPhoto,
                    Equipment, EquipmentDocument, EquipmentServiceLog,
                    WarehouseReservation, SupplierPrice,
                    GasCylinder, CylinderLog, CylinderOrder,
                    ChatRoom, ChatParticipant, ChatMessage, OfflineMutation)
from utils import (get_belgian_holidays, role_required, user_has_section_access,
                   create_notification, log_audit, genereer_nummer, date_plus_days,
                   save_uploaded_file, translate_text, run_migrations,
                   log_user_activity, log_system, run_data_migrations, sanitize_like,
                   check_tool_wear_notifications, safe_commit, add_work_report,
                   safe_int, safe_float, safe_date, find_pdf_font, ensure_fpdf,
                   is_user_at_work, user_schedule_restricted)

# ============================================================
# APP CONFIG
# ============================================================

app = Flask(__name__)
app.config.from_object(Config)
app.config['WTF_CSRF_TIME_LIMIT'] = 60 * 60 * 24 * 7  # 7 days (was: unlimited)
app.config['WTF_CSRF_SSL_STRICT'] = False  # allow CSRF across HTTP/HTTPS (PythonAnywhere proxy)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance'), exist_ok=True)

# Register blueprints
from blueprints.electricity import bp as electricity_bp
app.register_blueprint(electricity_bp)
from blueprints.monteurs import bp as monteurs_bp
app.register_blueprint(monteurs_bp)
from blueprints.contractors import bp as contractors_bp
app.register_blueprint(contractors_bp)
from blueprints.warehouse import bp as warehouse_bp
app.register_blueprint(warehouse_bp)
from blueprints.faults import bp as faults_bp
app.register_blueprint(faults_bp)
from blueprints.gas import bp as gas_bp
app.register_blueprint(gas_bp)
from blueprints.air import bp as air_bp
app.register_blueprint(air_bp)
from blueprints.water import bp as water_bp
app.register_blueprint(water_bp)
from blueprints.moeskroen import bp as moeskroen_bp
app.register_blueprint(moeskroen_bp)
from blueprints.chat import bp as chat_bp
app.register_blueprint(chat_bp)
from blueprints.machines import bp as machines_bp
app.register_blueprint(machines_bp)
from blueprints.tool_wear import bp as tool_wear_bp
app.register_blueprint(tool_wear_bp)
from blueprints.archive import bp as archive_bp
app.register_blueprint(archive_bp)
from blueprints.two import bp as two_bp
app.register_blueprint(two_bp)
from blueprints.messages import bp as messages_bp
app.register_blueprint(messages_bp)
from blueprints.notifications import bp as notifications_bp
app.register_blueprint(notifications_bp)
from blueprints.maintenance import bp as maintenance_bp
app.register_blueprint(maintenance_bp)
from blueprints.stats import bp as stats_bp
app.register_blueprint(stats_bp)
from blueprints.api import bp as api_bp
app.register_blueprint(api_bp)
from blueprints.qr import bp as qr_bp
app.register_blueprint(qr_bp)
from blueprints.settings import bp as settings_bp
app.register_blueprint(settings_bp)
from blueprints.responsible import bp as responsible_bp
app.register_blueprint(responsible_bp)
from blueprints.reports import bp as reports_bp
app.register_blueprint(reports_bp)
from blueprints.export import bp as export_bp
app.register_blueprint(export_bp)
from blueprints.timekeeping import bp as timekeeping_bp
app.register_blueprint(timekeeping_bp)
from blueprints.schedule import bp as schedule_bp
app.register_blueprint(schedule_bp)
from blueprints.sections import bp as sections_bp
app.register_blueprint(sections_bp)
from blueprints.assets import bp as assets_bp
app.register_blueprint(assets_bp)
from blueprints.orders import bp as orders_bp
app.register_blueprint(orders_bp)
from blueprints.mobile import bp as mobile_bp
app.register_blueprint(mobile_bp)
from blueprints.repairs import bp as repairs_bp
app.register_blueprint(repairs_bp)
from blueprints.work_report import bp as work_report_bp
app.register_blueprint(work_report_bp)
from blueprints.purchase import bp as purchase_bp
app.register_blueprint(purchase_bp)
from blueprints.invoices import bp as invoices_bp
app.register_blueprint(invoices_bp)
from blueprints.workers import bp as workers_bp
app.register_blueprint(workers_bp)
from blueprints.equipment import bp as equipment_bp
app.register_blueprint(equipment_bp)
from blueprints.users import bp as users_bp
app.register_blueprint(users_bp)
from blueprints.offline import bp as offline_bp, remember_mutation, find_mutation
app.register_blueprint(offline_bp)

csrf = CSRFProtect(app)
db.init_app(app)

# SQLite concurrency: enable WAL mode + busy_timeout on every new connection
from sqlalchemy import event, text
from sqlalchemy.pool import Pool

@event.listens_for(Pool, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    import sqlite3
    if isinstance(dbapi_conn, sqlite3.Connection):
        try:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=DELETE")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
            cursor.close()
        except Exception:
            pass

from flask_login import LoginManager

login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Flask-Compress — gzip responses (optional)
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

# Flask-Caching — cache heavy queries (optional)
try:
    from flask_caching import Cache
    cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 300})
except ImportError:
    cache = None

# Flask-Migrate — safe DB migrations (optional)
try:
    from flask_migrate import Migrate
    migrate = Migrate(app, db)
except ImportError:
    migrate = None

# ============================================================
# IMPROVEMENTS: Backup, Email, Cost Tracking
# ============================================================

def backup_database(force=False):
    """Daily DB backup with 30-day rotation.

    Skips the copy when today's backup already exists unless force=True
    (startup is cheap; login/settings can force a fresh snapshot).
    """
    import shutil
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    date_str = datetime.now().strftime('%Y%m%d')
    backup_path = os.path.join(backup_dir, f'werkplaats_{date_str}.db')
    if not os.path.exists(db_path):
        return None
    if not force and os.path.exists(backup_path):
        return backup_path
    shutil.copy2(db_path, backup_path)
    backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.db')])
    for old in backups[:-30]:
        os.remove(os.path.join(backup_dir, old))
    print(f"BACKUP: created {backup_path}")
    return backup_path

def send_email(to_email, subject, body):
    """Send email notification"""
    import smtplib
    from email.mime.text import MIMEText
    smtp_host = os.environ.get('SMTP_HOST', '')
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_pass = os.environ.get('SMTP_PASS', '')
    if not smtp_host or not smtp_user:
        return False
    msg = MIMEText(body, 'html')
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to_email
    server = None
    try:
        server = smtplib.SMTP(smtp_host, 587, timeout=10)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        return True
    except Exception:
        return False
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass

# Auto-create database tables and seed data on import (for WSGI deployment)
with app.app_context():
    # Clean up WAL/SHM files from previous sessions (causes crashes on PA)
    _db_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
    for _ext in ('-wal', '-shm'):
        _wal = _db_file + _ext
        if os.path.exists(_wal):
            try:
                os.remove(_wal)
                print(f"STARTUP: removed {_ext} file")
            except Exception:
                pass
    # Compact startup diagnostic (no per-table row counts — those ran on every import)
    import sqlite3 as _diag_sqlite3
    _diag_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
    if os.path.exists(_diag_path):
        try:
            _dc = _diag_sqlite3.connect(_diag_path)
            _dcur = _dc.cursor()
            _dcur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
            _ntables = _dcur.fetchone()[0]
            _dc.close()
            print(f"STARTUP: db ok size={os.path.getsize(_diag_path)} tables={_ntables}")
        except Exception as _diag_err:
            print(f"STARTUP: db read error: {_diag_err}")
    else:
        print("STARTUP: db missing — will create empty")

    # Daily backup only when today's snapshot is absent (skip if already taken)
    backup_database()

    db.create_all()
    run_migrations()
    run_data_migrations()
    try:
        from utils import _migrations_already_applied, _stamp_migrations_applied
        if not _migrations_already_applied():
            _stamp_migrations_applied()
    except Exception as _st_err:
        print(f'schema stamp skip: {_st_err}')
    # PDF export needs fpdf2 (often missing on PA) — install once at startup
    try:
        ensure_fpdf()
        print(f"APP STARTUP: fpdf2 OK, font={find_pdf_font()!r}")
    except Exception as _fpdf_err:
        print(f"APP STARTUP: fpdf2 unavailable: {_fpdf_err}")
    # Auto-fix admin role (role-switcher corruption) — triple safety
    _admin = User.query.filter_by(username='admin').first()
    if _admin:
        _old_role = _admin.role
        if _old_role != 'admin':
            _admin.role = 'admin'
            if not safe_commit():
                print('WARNING: safe_commit failed in startup')
            print(f"APP STARTUP: Fixed admin role from '{_old_role}' to 'admin'")
        # Also force via raw attribute for this request
        _admin.role = 'admin'
    # Also fix via raw SQL as fallback
    try:
        import sqlite3 as _sqlite3
        _db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
        if os.path.exists(_db_path):
            _conn = _sqlite3.connect(_db_path)
            _cur = _conn.cursor()
            _cur.execute("SELECT id, role FROM user WHERE username='admin'")
            _row = _cur.fetchone()
            if _row:
                print(f"STARTUP DEBUG: admin id={_row[0]} role={_row[1]}")
            if _row and _row[1] != 'admin':
                _cur.execute("UPDATE user SET role='admin' WHERE username='admin'")
                _conn.commit()
                print(f"SQL FIX: admin role fixed from '{_row[1]}' to 'admin'")
            _conn.close()
    except Exception as _e:
        print(f"SQL fix skipped: {_e}")
    # Ensure admin account is healthy: role/active flags only — do NOT reset password here
    _admin_user = User.query.filter_by(username='admin').first()
    if _admin_user:
        _needs_update = False
        if not _admin_user.password_hash:
            _pw, _force = _bootstrap_password('BOOTSTRAP_ADMIN_PW')
            _admin_user.set_password(_pw, save_plain=False)
            if _force:
                _admin_user.force_change_password = True
            _needs_update = True
            print("STARTUP: admin password set (was empty)")
        if not _admin_user.is_active_user:
            _admin_user.is_active_user = True
            _needs_update = True
            print("STARTUP: admin is_active_user fixed to True")
        if _admin_user.role != 'admin':
            _admin_user.role = 'admin'
            _needs_update = True
            print("STARTUP: admin role fixed to 'admin'")
        if _admin_user.force_change_password:
            _admin_user.force_change_password = False
            _needs_update = True
        if _admin_user.login_count and _admin_user.login_count > 0:
            _admin_user.login_count = 0
            _needs_update = True
        if _needs_update:
            if not safe_commit():
                print('WARNING: safe_commit failed in startup')
    # Снимаем флаг принудительной смены пароля у всех —
    # смена только по желанию пользователя (или при выдаче временного пароля).
    try:
        _cleared = User.query.filter(User.force_change_password == True).update(
            {'force_change_password': False}, synchronize_session=False)
        _cleared += Verantwoordelijke.query.filter(
            Verantwoordelijke.force_change_password == True).update(
            {'force_change_password': False}, synchronize_session=False)
        if _cleared:
            if not safe_commit():
                print('WARNING: safe_commit failed clearing force_change_password')
            print(f"STARTUP: cleared force_change_password for {_cleared} account(s)")
    except Exception as _e:
        db.session.rollback()
        print(f"STARTUP: force_change clear skipped: {_e}")
    if User.query.count() == 0:
        admin = User(username='admin', display_name='Administrator', role='admin')
        _apw, _aforce = _bootstrap_password('BOOTSTRAP_ADMIN_PW')
        admin.set_password(_apw, save_plain=False)
        if _aforce:
            admin.force_change_password = True
        director = User(username='director', display_name='Director', role='director')
        _dpw, _dforce = _bootstrap_password('BOOTSTRAP_DIRECTOR_PW')
        director.set_password(_dpw, save_plain=False)
        if _dforce:
            director.force_change_password = True
        db.session.add_all([admin, director])
        if not safe_commit():
            print('WARNING: safe_commit failed in startup')
    # Ensure director exists (create if missing)
    if not User.query.filter_by(username='director').first():
        d = User(username='director', display_name='Director', role='director')
        _dpw, _dforce = _bootstrap_password('BOOTSTRAP_DIRECTOR_PW')
        d.set_password(_dpw, save_plain=False)
        if _dforce:
            d.force_change_password = True
        d.is_active_user = True
        db.session.add(d)
        if not safe_commit():
            print('WARNING: safe_commit failed in startup')
        print("STARTUP: created missing user 'director'")
    # Remove fake test users (Sergei Petrov, Jan de Vries)
    for _fake in ('tech', 'user'):
        _fu = User.query.filter_by(username=_fake).first()
        if _fu:
            db.session.delete(_fu)
            if not safe_commit():
                print('WARNING: safe_commit failed in startup')
            print(f"STARTUP: removed fake test user '{_fake}'")

def _bootstrap_password(env_key):
    """Password for freshly seeded accounts. Never a fixed literal — env or random + force change."""
    import secrets
    pw = os.environ.get(env_key)
    if pw:
        return pw, False
    pw = secrets.token_urlsafe(16)
    print(f"WARNING: {env_key} not set — generated one-time password; set {env_key} to control it")
    return pw, True


def get_current_locale():
    return session.get('lang', 'ru')

babel = Babel(app, locale_selector=get_current_locale)

@app.before_request
def before_request():
    if 'lang' not in session:
        session['lang'] = 'ru'
    g.lang = session.get('lang', 'ru')
    g.LANGUAGES = LANGUAGES

    # Auto-fix admin role (role-switcher corruption)
    if current_user.is_authenticated and current_user.username == 'admin' and current_user.role != 'admin':
        current_user.role = 'admin'
        try:
            User.query.filter_by(username='admin').update({'role': 'admin'})
            if not safe_commit():
                print('WARNING: safe_commit failed in startup')
        except Exception:
            db.session.rollback()
    
    # Force password change after 2 logins
    if current_user.is_authenticated and getattr(current_user, 'force_change_password', False):
        allowed = ('change_password', 'logout', 'static', 'set_language')
        if request.endpoint and request.endpoint not in allowed:
            return redirect(url_for('change_password'))

    # Доступ механиков только в рабочие часы по графику
    if current_user.is_authenticated and user_schedule_restricted(current_user):
        allowed = ('login', 'logout', 'static', 'set_language', 'change_password', 'work_hours_block')
        endpoint = request.endpoint or ''
        if endpoint not in allowed and not endpoint.startswith('static'):
            if not is_user_at_work(current_user):
                return redirect(url_for('work_hours_block'))

    # Авто-выход после 10 минут бездействия (механики)
    if current_user.is_authenticated and user_schedule_restricted(current_user):
        allowed = ('login', 'logout', 'static', 'set_language', 'change_password', 'work_hours_block')
        endpoint = request.endpoint or ''
        if endpoint not in allowed and not endpoint.startswith('static'):
            import time as _time
            now_ts = _time.time()
            last = session.get('last_activity')
            if last is not None and (now_ts - last) > 600:
                session.clear()
                flash(_('Сессия завершена из-за бездействия. Войдите снова.'), 'error')
                return redirect(url_for('login'))
            session['last_activity'] = now_ts

    # Offline queue idempotency: a replayed mutation with a known client_id
    # must not create a second document.
    if request.method == 'POST':
        client_id = request.headers.get('X-Offline-Client-Id') or (request.form.get('_offline_client_id') if request.form else None)
        if client_id and len(client_id) <= 64:
            existing = find_mutation(client_id)
            if existing and existing.result_json:
                try:
                    payload = json.loads(existing.result_json)
                    payload['duplicate'] = True
                    return jsonify(payload), 200
                except Exception:
                    pass

@app.context_processor
def inject_section_access():
    """Make user_has_section_access available in all templates"""
    def has_access(section_key, action='view'):
        if not current_user.is_authenticated:
            return False
        return user_has_section_access(section_key, action)

    # Reminder count for sidebar badge
    reminder_count = 0
    chat_unread = 0
    if current_user.is_authenticated and request.endpoint not in ('static',):
        try:
            today = datetime.utcnow().date()
            soon = today + timedelta(days=7)
            reminder_count = MachinePart.query.filter(
                db.or_(
                    MachinePart.next_replacement <= soon,
                    MachinePart.next_maintenance <= soon
                )
            ).count()
            # Add overdue consumables
            reminder_count += VoorraadItem.query.filter(
                VoorraadItem.consumable_type.isnot(None),
                VoorraadItem.next_replacement.isnot(None),
                VoorraadItem.next_replacement <= soon
            ).count()
        except Exception:
            pass
        try:
            from blueprints.chat import get_unread_count
            chat_unread = get_unread_count(current_user.id)
        except Exception:
            pass

    return dict(has_access=has_access, reminder_count=reminder_count, chat_unread=chat_unread)


@app.after_request
def offline_queue_response(response):
    """When a mutation is replayed from the offline queue, wrap classic
    form-POST responses (redirect / HTML) as JSON so the client can finish
    the sync without parsing HTML. Document numbers assigned by the server
    are extracted from flash messages or the redirect Location."""
    if request.headers.get('X-Offline-Queue') != '1':
        return response
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return response
    ct = (response.headers.get('Content-Type') or '')
    if 'application/json' in ct:
        # Already JSON (API endpoints) — record for idempotency and pass through.
        client_id = request.headers.get('X-Offline-Client-Id')
        if client_id and response.status_code < 400:
            try:
                body = response.get_data(as_text=True)
                remember_mutation(
                    client_id, request.path, request.method,
                    request.headers.get('X-Offline-Temp-Number'),
                    request.headers.get('X-Offline-Title'),
                    'done', body,
                )
            except Exception:
                pass
        return response

    flashes = []
    try:
        flashes = session.pop('_flashes', [])
        session.modified = True
    except Exception:
        pass
    flash_list = [{'category': c, 'message': m} for c, m in flashes]
    has_error_flash = any((f.get('category') or '') in ('error', 'danger') for f in flash_list)

    location = response.headers.get('Location')
    number = None
    import re as _re
    num_re = _re.compile(r'\b((?:TWO|EQ|EPO|WO|INV|ORD|OFFLINE)-[A-Z0-9]{4,}-\d{2,6})\b')
    for f in flash_list:
        found = num_re.search(f.get('message') or '')
        if found:
            number = found.group(1)
            break
    if not number and location:
        found = num_re.search(location)
        if found:
            number = found.group(1)

    # Redirect back to /login means auth/CSRF failure — not a successful mutation.
    login_redirect = bool(location and location.startswith('/login'))

    if 300 <= response.status_code < 400:
        ok = (not has_error_flash) and (not login_redirect)
        error = None
        if login_redirect:
            error = 'auth_or_csrf'
        elif has_error_flash:
            error = 'flash_error'
        payload = {'ok': ok, 'error': error, 'redirect': location, 'flashes': flash_list, 'number': number}
        client_id = request.headers.get('X-Offline-Client-Id')
        if client_id and ok:
            remember_mutation(
                client_id, request.path, request.method,
                request.headers.get('X-Offline-Temp-Number'),
                request.headers.get('X-Offline-Title'),
                'done', json.dumps(payload, ensure_ascii=False),
            )
        resp = jsonify(payload)
        resp.status_code = 200
        return resp

    if response.status_code == 200:
        # Successful non-redirect (rare) or validation re-render with 200.
        # Treat as validation failure when flashes contain an error.
        has_error = any((f.get('category') or '') == 'error' for f in flash_list)
        payload = {
            'ok': not has_error,
            'error': 'validation' if has_error else None,
            'redirect': location,
            'flashes': flash_list,
            'number': number,
        }
        client_id = request.headers.get('X-Offline-Client-Id')
        if client_id and payload['ok']:
            remember_mutation(
                client_id, request.path, request.method,
                request.headers.get('X-Offline-Temp-Number'),
                request.headers.get('X-Offline-Title'),
                'done', json.dumps(payload, ensure_ascii=False),
            )
        return jsonify(payload)

    payload = {'ok': False, 'error': f'http_{response.status_code}', 'status': response.status_code, 'flashes': flash_list}
    return jsonify(payload), response.status_code

@app.route('/set_language/<lang>')
def set_language(lang):
    if lang in LANGUAGES:
        session['lang'] = lang
    return redirect(request.referrer or url_for('index'))

@app.route('/reset-role')
@login_required
def reset_role():
    """NUCLEAR reset — clears ALL session data."""
    session.clear()
    session.modified = True
    return redirect(url_for('login'))

# ============================================================
# USER LOADER
# ============================================================

@login_manager.user_loader
def load_user(user_id):
    # Responsible person IDs are prefixed with "r_"
    if str(user_id).startswith('r_'):
        try:
            person_id = int(str(user_id)[2:])
            person = Verantwoordelijke.query.get(person_id)
            if person and person.password_hash and person.is_active:
                return ResponsibleAuth(person)
        except (ValueError, TypeError):
            pass
        return None
    return User.query.get(int(user_id))

# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(400)
def bad_request(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify(error=str(e)), 400
    flash(_('Bad request'), 'error')
    return redirect(request.referrer or url_for('index'))

@app.errorhandler(403)
def forbidden(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify(error='Forbidden'), 403
    flash(_('Access denied'), 'error')
    return redirect(url_for('index'))

@app.errorhandler(404)
def not_found(e):
    # Static / XHR / API / asset misses must NOT flash or redirect —
    # otherwise every missing image/css pollutes the session with "Page not found".
    path = request.path or ''
    is_asset = (
        path.startswith('/static/')
        or path.startswith('/favicon')
        or path.startswith('/api/')
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or (request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html)
        or request.headers.get('Sec-Fetch-Dest', '') in ('image', 'script', 'style', 'font', 'empty')
    )
    if is_asset:
        return ('', 404)
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify(error='Not found'), 404
    flash(_('Page not found'), 'error')
    return redirect(url_for('index'))

@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify(error='Internal server error'), 500
    flash(_('An error occurred. Please try again.'), 'error')
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return redirect(url_for('login'))

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify(error='CSRF token missing or expired'), 400
    flash(_('Session expired. Please try again.'), 'error')
    return redirect(url_for('login'))

@app.route('/favicon.ico')
def favicon():
    response = send_file(os.path.join(app.static_folder, 'icon-192.png'), mimetype='image/png')
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response

# ============================================================
# ROUTES — AUTH
# ============================================================

# ── Login rate limiting (in-memory, per IP) ─────────────────
_LOGIN_ATTEMPTS = {}  # ip -> (count, window_start)
_LOGIN_MAX = 10       # attempts per window
_LOGIN_WINDOW = 300   # seconds (5 min)


def _login_rate_ok(ip):
    import time as _time
    now = _time.time()
    count, start = _LOGIN_ATTEMPTS.get(ip, (0, 0.0))
    if now - start > _LOGIN_WINDOW:
        _LOGIN_ATTEMPTS[ip] = (1, now)
        return True
    if count >= _LOGIN_MAX:
        return False
    _LOGIN_ATTEMPTS[ip] = (count + 1, start)
    return True


def _login_rate_reset(ip):
    _LOGIN_ATTEMPTS.pop(ip, None)


@app.route('/login', methods=['GET', 'POST'], strict_slashes=False)
def login():
    try:
        if current_user.is_authenticated:
            return redirect(url_for('index'))
        if request.method == 'POST':
            ip = request.remote_addr or 'unknown'
            if not _login_rate_ok(ip):
                log_system('WARN', 'auth', f'Login rate-limit hit for {ip}', source='login')
                flash(_('Too many login attempts. Try again later.'), 'error')
                return render_template('login.html'), 429
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            if not username or not password:
                flash(_('Invalid credentials'), 'error')
                return render_template('login.html')
            # Try User first
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password) and user.is_active_user:
                _login_rate_reset(ip)
                login_user(user, remember=True)
                # Track login count
                user.login_count = (user.login_count or 0) + 1
                # Смена пароля — только по флагу force_change_password
                # (выставляется при создании учётки с временным паролем).
                # Автосброс по счётчику входов убран — блокировал техников.
                if not safe_commit():
                    flash(_('Save failed'), 'error')
                    return redirect(url_for('index'))
                log_user_activity('login', page='/login', details=f'User {username} logged in')
                log_system('INFO', 'auth', f'User {username} logged in', source='login')
                # Auto-backup DB on login — saves all changes from previous session
                try:
                    backup_database(force=True)
                except Exception as be:
                    print(f'LOGIN BACKUP WARNING: {be}')
                # Reminders on login (tool wear etc.) for admin/director/masters/responsible
                try:
                    check_tool_wear_notifications()
                except Exception as te:
                    print(f'LOGIN TOOL REMINDER WARNING: {te}')
                # Force password change after 2 logins
                if user.force_change_password:
                    flash(_('You must change your password'), 'warning')
                    return redirect(url_for('change_password'))
                # Блокировка вне рабочих часов (механики)
                if user_schedule_restricted(user) and not is_user_at_work(user):
                    flash(_('ПРОГРАММА ДОСТУПНА ТОЛЬКО В РАБОЧИЕ ЧАСЫ СОГЛАСНО ВАШЕГО ГРАФИКА'), 'error')
                    return redirect(url_for('work_hours_block'))
                import time as _time
                session['last_activity'] = _time.time()
                # Механик с назначенными ордерами — уведомление с кнопкой ОК
                if user_schedule_restricted(user):
                    open_order = _get_open_two_for_user(user)
                    if open_order:
                        session['me_order_id'] = open_order.id
                        return redirect(url_for('me_order_notice'))
                next_url = request.args.get('next')
                if next_url:
                    parsed = urlparse(next_url)
                    if parsed.netloc and parsed.netloc != request.host:
                        next_url = None
                return redirect(next_url or url_for('index'))
            # Try Verantwoordelijke by username, email or naam
            person = Verantwoordelijke.query.filter(
                (Verantwoordelijke.username == username) | (Verantwoordelijke.email == username) | (Verantwoordelijke.naam == username)
            ).first()
            if person and person.check_password(password) and person.is_active:
                auth = ResponsibleAuth(person)
                person.last_login = datetime.utcnow()
                person.login_count = (person.login_count or 0) + 1
                # Без автосброса по счётчику входов — только по флагу bootstrap
                if not safe_commit():
                    flash(_('Save failed'), 'error')
                    return redirect(url_for('index'))
                login_user(auth, remember=True)
                log_user_activity('login', page='/login', details=f'Responsible {username} logged in')
                log_system('INFO', 'auth', f'Responsible {username} logged in', source='login')
                try:
                    backup_database(force=True)
                except Exception as be:
                    print(f'LOGIN BACKUP WARNING: {be}')
                try:
                    check_tool_wear_notifications()
                except Exception as te:
                    print(f'LOGIN TOOL REMINDER WARNING: {te}')
                if auth.force_change_password:
                    flash(_('You must change your password'), 'warning')
                    return redirect(url_for('change_password'))
                next_url = request.args.get('next')
                if next_url:
                    parsed = urlparse(next_url)
                    if parsed.netloc and parsed.netloc != request.host:
                        next_url = None
                return redirect(next_url or url_for('floor_plan'))
            log_system('WARNING', 'auth', f'Failed login attempt for {username}', source='login')
            flash(_('Invalid credentials'), 'error')
        return render_template('login.html')
    except Exception as _login_err:
        import traceback as _tb
        _trace = _tb.format_exc()
        # Write to a file so we can read it from the server
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'login_error.log'), 'a') as _f:
                _f.write(f"\n{'='*60}\n{datetime.utcnow().isoformat()}\n{_trace}\n")
        except Exception:
            pass
        flash(f'Login error: {_login_err}', 'error')
        db.session.rollback()
        return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    username = current_user.username if current_user.is_authenticated else 'unknown'
    log_user_activity('logout', page='/logout', details=f'User {username} logged out')
    log_system('INFO', 'auth', f'User {username} logged out', source='logout')
    logout_user()
    return redirect(url_for('login'))

def _get_open_two_for_user(user):
    """Открытый наряд (TWO), назначенный пользователю-механику. None если нет."""
    if not user or not getattr(user, 'id', None):
        return None
    from models import Monteur, TechnicalWorkOrder, two_workers
    monteur = Monteur.query.filter_by(user_id=user.id).first()
    if not monteur:
        return None
    return (TechnicalWorkOrder.query
            .join(two_workers, two_workers.c.two_id == TechnicalWorkOrder.id)
            .filter(two_workers.c.worker_id == monteur.id)
            .filter(TechnicalWorkOrder.status.notin_(['completed', 'cancelled']))
            .order_by(TechnicalWorkOrder.planned_date.asc().nulls_last(),
                      TechnicalWorkOrder.created_at.asc())
            .first())


@app.route('/work-hours-block')
@login_required
def work_hours_block():
    """Блокировка доступа вне рабочих часов по графику."""
    from models import WorkSchedule
    sched = WorkSchedule.query.filter_by(user_id=current_user.id, is_active=True).first()
    return render_template('work_hours_block.html', schedule=sched)


@app.route('/me-order-notice')
@login_required
def me_order_notice():
    """Уведомление механику о назначенном наряде. Кнопка ОК → переход к наряду."""
    from models import TechnicalWorkOrder
    order_id = session.pop('me_order_id', None)
    order = db.session.get(TechnicalWorkOrder, order_id) if order_id else None
    if not order:
        return redirect(url_for('index'))
    return render_template('me_order_notice.html', order=order)


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        # Change username
        new_username = request.form.get('username', '').strip()
        if new_username and new_username != current_user.username:
            existing = User.query.filter_by(username=new_username).first()
            if existing:
                flash(_('Username already taken'), 'error')
                return redirect(url_for('profile'))
            current_user.username = new_username

        # Change display name
        current_user.display_name = request.form.get('display_name', current_user.display_name)
        current_user.phone = request.form.get('phone', current_user.phone)

        # Change password
        new_pass = request.form.get('new_password')
        if new_pass:
            confirm_pass = request.form.get('confirm_password')
            if new_pass != confirm_pass:
                flash(_('Passwords do not match'), 'error')
                return redirect(url_for('profile'))
            if len(new_pass) < 8:
                flash(_('Password must be at least 8 characters'), 'error')
                return redirect(url_for('profile'))
            current_user.set_password(new_pass)
            current_user.force_change_password = False

        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('profile'))
        flash(_('Profile updated'), 'success')
    return render_template('profile.html')


@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        new_pass = request.form.get('new_password', '').strip()
        confirm_pass = request.form.get('confirm_password', '').strip()
        if not new_pass or len(new_pass) < 8:
            flash(_('Password must be at least 8 characters'), 'error')
        elif new_pass != confirm_pass:
            flash(_('Passwords do not match'), 'error')
        else:
            current_user.set_password(new_pass)
            current_user.force_change_password = False
            if not safe_commit():
                flash(_('Save failed'), 'error')
                return redirect(url_for('index'))
            flash(_('Password changed'), 'success')
            return redirect(url_for('index'))
    return render_template('change_password.html')

# ============================================================
# ROUTES — USER MANAGEMENT (Admin only)
# ============================================================

# ============================================================
@app.route('/floor')
@login_required
def floor_plan():
    if current_user.has_role('admin', 'director'):
        machines = Machine.query.all()
        sections = FactorySection.query.all()
    elif hasattr(current_user, '_person') and current_user.role == 'responsible':
        # Responsible person — show only their sections and machines
        person = current_user._person
        sections = list(person.resp_sections)
        section_ids = [s.id for s in sections]
        if person.access_level == 'full':
            machines = Machine.query.all()
        else:
            machines = Machine.query.filter(
                (Machine.responsible_person_id == person.id) |
                (Machine.section_id.in_(section_ids) if section_ids else False)
            ).all()
    elif current_user.has_role('technician'):
        machines = Machine.query.all()
        sections = FactorySection.query.all()
    else:
        machines = current_user.assigned_machines
        section_ids = list(set(m.section_id for m in machines if m.section_id))
        sections = FactorySection.query.filter(FactorySection.id.in_(section_ids)).all() if section_ids else []
    is_filtered = hasattr(current_user, '_person') and current_user.role == 'responsible'
    # Also show equipment on floor plan
    from models import Equipment
    if current_user.has_role('admin', 'director', 'technician'):
        equipment = Equipment.query.filter(
            Equipment.floor_x.isnot(None), Equipment.floor_y.isnot(None)
        ).all()
    else:
        equipment = []
    return render_template('floor_plan.html', machines=machines, sections=sections, is_filtered=is_filtered, equipment=equipment)

# ============================================================
# ROUTES — FACTORY SECTIONS
# ============================================================

@app.route('/map-editor')
@login_required
@role_required('admin')
def map_editor():
    sections = FactorySection.query.all()
    machines = Machine.query.all()
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    return render_template('map_editor.html', sections=sections, machines=machines, verantwoordelijken=verantwoordelijken)

# ============================================================
# ROUTES — MAINTENANCE CALENDAR
# ============================================================












# ============================================================
# ROUTES — MAINTENANCE PLANS
# ============================================================




def _weekday_only(d):
    """Return d if Mon–Fri, otherwise next Monday."""
    return skip_weekend(d)








# ============================================================
# ROUTES — MAINTENANCE SCHEDULE CONFIGURATION
# ============================================================









# ============================================================
# ROUTES — EQUIPMENT MAINTENANCE
# ============================================================






# ============================================================
# ROUTES — EQUIPMENT (Оборудование)
# ============================================================

# ============================================================
# ROUTES — EQUIPMENT REPAIRS
# ============================================================

# ============================================================
# ROUTES — TWO (Technical Work Orders)
# ============================================================


















# ============================================================
# ROUTES — MESSAGES
# ============================================================





# ============================================================
# ROUTES — NOTIFICATIONS
# ============================================================








# ============================================================
# ROUTES — DIRECTOR STATISTICS
# ============================================================





# ============================================================
# ROUTES — DASHBOARD
# ============================================================




@app.route('/')
@login_required
def index():
    # Responsible persons go directly to floor plan
    if hasattr(current_user, '_person') and current_user.role == 'responsible':
        return redirect(url_for('floor_plan'))

    # Aggregate stats in one query (C2)
    today_start = datetime.utcnow().date()
    open_states = ['open', 'accepted', 'in_progress']
    row = db.session.query(
        func.count(Opdracht.id),
        func.coalesce(func.sum(case((Opdracht.status.notin_(['afgeleverd', 'geannuleerd']), 1), else_=0)), 0),
        func.coalesce(func.sum(case((Opdracht.aangemaakt >= today_start, 1), else_=0)), 0),
    ).one()
    ver_count = Verantwoordelijke.query.count()
    mont_count = Monteur.query.filter_by(actief=True).count()
    low_count = VoorraadItem.query.filter(VoorraadItem.hoeveelheid <= VoorraadItem.minimum).count()
    fault_row = db.session.query(
        func.coalesce(func.sum(case((FaultReport.status == 'open', 1), else_=0)), 0),
        func.coalesce(func.sum(case((FaultReport.status.in_(open_states), 1), else_=0)), 0),
    ).one()
    stats = {
        'opdrachten_totaal': int(row[0] or 0),
        'opdrachten_actief': int(row[1] or 0),
        'opdrachten_vandaag': int(row[2] or 0),
        'verantwoordelijken': ver_count,
        'monteurs': mont_count,
        'voorraad_laag': low_count,
        'faults_open': int(fault_row[0] or 0),
        'faults_active': int(fault_row[1] or 0),
    }
    recent = Opdracht.query.order_by(Opdracht.aangemaakt.desc()).limit(10).all()
    laag = VoorraadItem.query.filter(VoorraadItem.hoeveelheid <= VoorraadItem.minimum).all()
    
    recent_faults = []
    if current_user.has_role('admin', 'director', 'technician'):
        recent_faults = FaultReport.query.order_by(FaultReport.created_at.desc()).limit(5).all()
    else:
        recent_faults = FaultReport.query.filter_by(reporter_id=current_user.id).order_by(FaultReport.created_at.desc()).limit(5).all()
    
    users = User.query.order_by(User.id).all() if current_user.has_role('admin') else []
    
    # Dashboard statistics for admin/director
    dashboard_stats = {}
    if current_user.has_role('admin', 'director'):
        active_fault_states = ['open', 'accepted', 'in_progress', 'parts_ordered', 'reopened']
        prio_rows = db.session.query(
            FaultReport.priority, func.count()
        ).filter(FaultReport.status.in_(active_fault_states)).group_by(FaultReport.priority).all()
        prio_map = {p or 'normal': int(c or 0) for p, c in prio_rows}
        dashboard_stats['low'] = prio_map.get('low', 0)
        dashboard_stats['normal'] = prio_map.get('normal', 0)
        dashboard_stats['high'] = prio_map.get('high', 0)
        dashboard_stats['critical'] = prio_map.get('critical', 0)
        
        # Top machines with faults (single aggregated query)
        machine_stats = db.session.query(
            FaultReport.machine_id,
            func.count().label('fault_count'),
            func.sum(case((FaultReport.status.in_(['open', 'accepted', 'in_progress']), 1), else_=0)).label('open_count'),
            func.sum(case((FaultReport.priority == 'critical', 1), else_=0)).label('critical_count'),
        ).group_by(FaultReport.machine_id).all()
        machine_map = {m.id: m for m in Machine.query.filter(Machine.id.in_([s.machine_id for s in machine_stats])).all()}
        top_machines = []
        for s in machine_stats:
            m = machine_map.get(s.machine_id)
            if m:
                m.fault_count = s.fault_count
                m.open_count = int(s.open_count or 0)
                m.critical_count = int(s.critical_count or 0)
                top_machines.append(m)
        top_machines.sort(key=lambda x: x.fault_count, reverse=True)
        dashboard_stats['top_machines'] = top_machines
        
        # TWO warnings - active TWOs that need attention
        today = datetime.utcnow().date()
        active_twos = TechnicalWorkOrder.query.filter(
            TechnicalWorkOrder.status.in_(['draft', 'assigned', 'in_progress'])
        ).all()
        overdue_twos = [t for t in active_twos if t.planned_date and t.planned_date < today]
        dashboard_stats['active_twos'] = len(active_twos)
        dashboard_stats['overdue_twos'] = len(overdue_twos)
        dashboard_stats['overdue_two_list'] = overdue_twos[:5]

        # ── Charts data (last 6 months) ─────────────────────
        today = datetime.utcnow().date()
        months = []
        for i in range(5, -1, -1):
            m0 = today.replace(day=1)
            y, mo = m0.year, m0.month - i
            while mo <= 0:
                mo += 12
                y -= 1
            months.append((y, mo))
        labels = [f'{mo:02d}.{y % 100:02d}' for y, mo in months]
        faults_by_month = []
        to_by_month = []
        for y, mo in months:
            start = datetime(y, mo, 1).date()
            if mo == 12:
                end = datetime(y + 1, 1, 1).date()
            else:
                end = datetime(y, mo + 1, 1).date()
            faults_by_month.append(
                FaultReport.query.filter(
                    FaultReport.created_at >= start,
                    FaultReport.created_at < end
                ).count()
            )
            to_by_month.append(
                MaintenanceRecord.query.filter(
                    MaintenanceRecord.date_performed >= start,
                    MaintenanceRecord.date_performed < end
                ).count()
            )
        dashboard_stats['chart_labels'] = labels
        dashboard_stats['chart_faults'] = faults_by_month
        dashboard_stats['chart_to'] = to_by_month

        # faults by status (all-time, for donut/bars)
        status_rows = db.session.query(
            FaultReport.status, func.count()
        ).group_by(FaultReport.status).all()
        dashboard_stats['chart_status'] = [
            {'status': s or 'unknown', 'count': int(c or 0)} for s, c in status_rows
        ]

    return render_template('index.html', stats=stats, recent_orders=recent, low_stock=laag, recent_faults=recent_faults, users=users, now=datetime.utcnow(), dashboard_stats=dashboard_stats)

# ============================================================
# ROUTES — OPDRACHTEN (existing)
# ============================================================

# ============================================================
# ROUTES — KLANTEN (existing)
# ============================================================

# ROUTES — MONTEURS (existing)
# ============================================================

# ============================================================
# ROUTES — INVOICES
# ============================================================

# ============================================================
# ROUTES — RAPPORTEN (existing)
# ============================================================


# ============================================================
# ROUTES — QR CODE (existing)
# ============================================================

@app.route('/scanner')
@login_required
def universal_scanner():
    """Scanner hub — links to warehouse, machine, and cylinder scanners"""
    return render_template('universal_scanner.html')




# ============================================================
# ROUTES — WAREHOUSE QR & PARTS SEARCH
# ============================================================

# ============================================================
# ROUTES — PURCHASE REQUESTS
# ============================================================

# ============================================================
# ROUTES — WORK SCHEDULE & TIME TRACKING
# ============================================================

# Belgian public holidays (fixed + Easter-based)
# Belgian holidays helpers live in utils.get_belgian_holidays

# ============================================================
# ROUTES — TRANSLATION API
# ============================================================

# ============================================================
# AUTOMATION — AUTO-NOTIFICATIONS & REMINDERS
# ============================================================

@app.route('/api/automation/check', methods=['POST'])
@login_required
@role_required('admin')
def automation_check():
    """Run all automated checks and send notifications"""
    today = datetime.utcnow().date()
    soon = today + timedelta(days=14)
    results = {'maintenance': 0, 'low_stock': 0, 'contracts': 0, 'cylinders': 0}

    # Check maintenance due
    parts = MachinePart.query.filter(MachinePart.next_replacement <= soon).all()
    for p in parts:
        if p.responsible_user_id:
            days = (p.next_replacement - today).days
            title = f"{'OVERDUE' if days < 0 else 'Upcoming'} replacement: {p.name}"
            msg = f"{p.machine.name}: {p.name} - {'overdue by ' + str(-days) + ' days' if days < 0 else 'due in ' + str(days) + ' days'}"
            existing = Notification.query.filter_by(user_id=p.responsible_user_id, is_read=False, title=title).first()
            if not existing:
                create_notification(p.responsible_user_id, title, msg, 'warning', url_for('machines.machine_parts', machine_id=p.machine_id))
                results['maintenance'] += 1

    # Check low stock
    low_items = VoorraadItem.query.filter(VoorraadItem.hoeveelheid <= VoorraadItem.minimum).all()
    if low_items:
        admins = User.query.filter(User.role.in_(['admin', 'director']), User.is_active_user == True).all()
        for admin in admins:
            title = f"Low stock: {len(low_items)} items"
            msg = ', '.join([f"{i.naam} ({i.hoeveelheid}/{i.minimum})" for i in low_items[:5]])
            existing = Notification.query.filter_by(user_id=admin.id, is_read=False, title=title).first()
            if not existing:
                create_notification(admin.id, title, msg, 'warning', url_for('warehouse.warehouse_list'))
                results['low_stock'] += 1

    # Check expiring contracts
    contractors = Contractor.query.filter(Contractor.contract_end <= soon, Contractor.contract_end >= today, Contractor.is_active == True).all()
    for c in contractors:
        days = (c.contract_end - today).days
        admins = User.query.filter(User.role.in_(['admin', 'director']), User.is_active_user == True).all()
        for admin in admins:
            title = f"Contract expiring: {c.company_name}"
            msg = f"Contract with {c.company_name} expires in {days} days"
            existing = Notification.query.filter_by(user_id=admin.id, is_read=False, title=title).first()
            if not existing:
                create_notification(admin.id, title, msg, 'warning', url_for('contractors.contractor_detail', contractor_id=c.id))
                results['contracts'] += 1

    log_audit('automation_check', details=json.dumps(results))
    return jsonify({'ok': True, 'results': results})

# ============================================================
# EXPORT — CSV/EXCEL REPORTS
# ============================================================

# ============================================================
# WORK REPORT — STORING
# ============================================================


# ============================================================
# TOOL WEAR TRACKING
# ============================================================








# ============================================================
# MONTHLY ARCHIVE
# ============================================================




# ============================================================
# GLOBAL SEARCH
# ============================================================

@app.route('/search')
@login_required
def search_page():
    q = request.args.get('q', '').strip()
    type_f = request.args.get('type', '').strip()
    results = []
    if q and len(q) >= 2:
        like = f'%{sanitize_like(q)}%'
        if type_f in ('', 'machine'):
            for m in Machine.query.filter(
                Machine.name.ilike(like) | Machine.serial_number.ilike(like) | Machine.description.ilike(like)
            ).limit(8).all():
                results.append({'type': 'machine', 'icon': '⚙️', 'title': m.name,
                                'subtitle': f'{m.machine_type or ""} {m.serial_number or ""}'.strip(),
                                'url': f'/machines/{m.id}', 'status': m.status})
        if type_f in ('', 'fault'):
            for f in FaultReport.query.filter(
                FaultReport.title.ilike(like) | FaultReport.description.ilike(like)
            ).limit(8).all():
                results.append({'type': 'fault', 'icon': '⚠️', 'title': f.title or f'#{f.id}',
                                'subtitle': (f.machine.name if f.machine else '') + ' · ' + (f.priority or ''),
                                'url': f'/faults/{f.id}', 'status': f.status})
        if type_f in ('', 'order'):
            for o in Opdracht.query.filter(
                Opdracht.nummer.ilike(like) | Opdracht.apparaat.ilike(like) | Opdracht.model.ilike(like)
            ).limit(8).all():
                results.append({'type': 'order', 'icon': '📋', 'title': f'{o.nummer} — {o.apparaat}',
                                'subtitle': o.model or '', 'url': f'/orders/{o.id}', 'status': o.status})
        if type_f in ('', 'warehouse'):
            for i in VoorraadItem.query.filter(
                VoorraadItem.naam.ilike(like) | VoorraadItem.categorie.ilike(like) | VoorraadItem.locatie.ilike(like)
            ).limit(8).all():
                results.append({'type': 'warehouse', 'icon': '📦', 'title': i.naam,
                                'subtitle': f'{i.categorie or ""} · {i.hoeveelheid} {i.eenheid or ""}',
                                'url': f'/warehouse/{i.id}/edit',
                                'status': 'low' if (i.minimum or 0) and i.hoeveelheid <= i.minimum else 'ok'})
        if type_f in ('', 'client'):
            for c in Verantwoordelijke.query.filter(
                Verantwoordelijke.naam.ilike(like) | Verantwoordelijke.company.ilike(like) |
                Verantwoordelijke.telefoon.ilike(like) | Verantwoordelijke.email.ilike(like)
            ).limit(8).all():
                results.append({'type': 'client', 'icon': '👤', 'title': c.naam,
                                'subtitle': f'{c.company or ""} {c.telefoon or ""}'.strip(),
                                'url': f'/responsible/{c.id}', 'status': 'active'})
        if type_f in ('', 'two'):
            for t in TechnicalWorkOrder.query.filter(
                TechnicalWorkOrder.number.ilike(like) | TechnicalWorkOrder.description.ilike(like)
            ).limit(8).all():
                results.append({'type': 'two', 'icon': '🔧', 'title': t.number,
                                'subtitle': (t.description or '')[:60], 'url': f'/two/{t.id}', 'status': t.status})
        if type_f in ('', 'worker'):
            for w in Monteur.query.filter(
                Monteur.naam.ilike(like) | Monteur.specialisatie.ilike(like)
            ).limit(5).all():
                results.append({'type': 'worker', 'icon': '👷', 'title': w.naam,
                                'subtitle': w.specialisatie or '', 'url': f'/monteurs', 'status': 'active' if w.actief else 'inactive'})
    type_counts = {}
    for r in results:
        type_counts[r['type']] = type_counts.get(r['type'], 0) + 1
    return render_template('search.html', query=q, type_f=type_f, results=results, type_counts=type_counts)

# ============================================================
# START
# ============================================================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        # Migrations
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            
            # Create section_responsible table if missing
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='section_responsible'")
            if not cur.fetchone():
                cur.execute("CREATE TABLE section_responsible (section_id INTEGER REFERENCES factory_section(id), person_id INTEGER REFERENCES client(id), PRIMARY KEY (section_id, person_id))")
                # Migrate data from responsible_person_id column if it exists
                cur.execute("PRAGMA table_info(factory_section)")
                cols = [c[1] for c in cur.fetchall()]
                if 'responsible_person_id' in cols:
                    cur.execute("INSERT INTO section_responsible (section_id, person_id) SELECT id, responsible_person_id FROM factory_section WHERE responsible_person_id IS NOT NULL")
                conn.commit()
                print("Migration: created section_responsible table")
            
            # Add received_at to gas_cylinder if missing
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gas_cylinder'")
            if cur.fetchone():
                cur.execute("PRAGMA table_info(gas_cylinder)")
                cols = [c[1] for c in cur.fetchall()]
                if 'received_at' not in cols:
                    cur.execute("ALTER TABLE gas_cylinder ADD COLUMN received_at DATETIME")
                    conn.commit()
                    print("Migration: added received_at to gas_cylinder")
            
            # Add responsible_user_id to machine_part if missing
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='machine_part'")
            if cur.fetchone():
                cur.execute("PRAGMA table_info(machine_part)")
                cols = [c[1] for c in cur.fetchall()]
                if 'responsible_user_id' not in cols:
                    cur.execute("ALTER TABLE machine_part ADD COLUMN responsible_user_id INTEGER REFERENCES user(id)")
                    conn.commit()
                    print("Migration: added responsible_user_id to machine_part")

            # Create fault_video table if missing
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fault_video'")
            if not cur.fetchone():
                cur.execute("CREATE TABLE fault_video (id INTEGER PRIMARY KEY, fault_id INTEGER NOT NULL REFERENCES fault_report(id), filename TEXT NOT NULL, description TEXT, uploaded_at DATETIME)")
                conn.commit()
                print("Migration: created fault_video table")

            conn.close()

        
        # Create admin user if no users exist
        if User.query.count() == 0:
            import secrets as _secrets
            creds = []
            admin_pw = _secrets.token_urlsafe(12)
            admin = User(username='admin', display_name='Administrator', role='admin')
            admin.set_password(admin_pw)
            db.session.add(admin)
            creds.append(('admin', admin_pw))

            tech_pw = _secrets.token_urlsafe(12)
            tech = User(username='tech', display_name='Sergei Petrov', role='technician')
            tech.set_password(tech_pw)
            db.session.add(tech)
            creds.append(('tech', tech_pw))

            user_pw = _secrets.token_urlsafe(12)
            user = User(username='user', display_name='Jan de Vries', role='user')
            user.set_password(user_pw)
            db.session.add(user)
            creds.append(('user', user_pw))

            director_pw = _secrets.token_urlsafe(12)
            director = User(username='director', display_name='Director', role='director')
            director.set_password(director_pw)
            db.session.add(director)
            creds.append(('director', director_pw))
            
            # Create demo sections
            sections = [
                FactorySection(name='CNC Machining', description='CNC milling and turning operations', section_type='workshop', color='#3498db', floor_x=10, floor_y=10, width=40, height=35, responsible_user_id=2),
                FactorySection(name='Welding & Assembly', description='Welding stations and assembly area', section_type='workshop', color='#e67e22', floor_x=55, floor_y=10, width=35, height=35, responsible_user_id=2),
                FactorySection(name='Finishing', description='Grinding, polishing and surface treatment', section_type='workshop', color='#27ae60', floor_x=10, floor_y=50, width=30, height=30, responsible_user_id=2),
                FactorySection(name='Quality Control', description='Inspection and quality assurance', section_type='office', color='#9b59b6', floor_x=45, floor_y=50, width=25, height=30, responsible_user_id=4),
                FactorySection(name='Storage', description='Raw materials and finished goods storage', section_type='storage', color='#95a5a6', floor_x=75, floor_y=50, width=20, height=30, responsible_user_id=None),
            ]
            db.session.add_all(sections)
            if not safe_commit():
                print('WARNING: safe_commit failed in startup')
            
            # Create demo machines
            machines = [
                Machine(name='CNC Mill X500', description='5-axis CNC milling machine', serial_number='CNC-2024-001', machine_type='Milling', floor_x=20, floor_y=25, status='active', section_id=1),
                Machine(name='Lathe LT200', description='CNC lathe for precision turning', serial_number='LT-2024-002', machine_type='Turning', floor_x=35, floor_y=25, status='active', section_id=1),
                Machine(name='Press HP100', description='Hydraulic press 100 ton', serial_number='HP-2024-003', machine_type='Pressing', floor_x=70, floor_y=25, status='active', section_id=2),
                Machine(name='Welder WS300', description='MIG/MAG welding station', serial_number='WS-2024-004', machine_type='Welding', floor_x=80, floor_y=25, status='active', section_id=2),
                Machine(name='Grinder GR50', description='Surface grinder', serial_number='GR-2024-005', machine_type='Grinding', floor_x=20, floor_y=65, status='maintenance', section_id=3),
                Machine(name='Drill DP20', description='Radial drill press', serial_number='DP-2024-006', machine_type='Drilling', floor_x=55, floor_y=65, status='active', section_id=4),
            ]
            db.session.add_all(machines)
            if not safe_commit():
                print('WARNING: safe_commit failed in startup')
            
            # Assign machines to user
            user.assigned_machines = [machines[0], machines[1], machines[2]]
            if not safe_commit():
                print('WARNING: safe_commit failed in startup')
            
            # Demo data
            demo = [
                Verantwoordelijke(naam='Jan de Vries', telefoon='+31 6 12345678', email='jan@mail.nl'),
                Verantwoordelijke(naam='Maria Bakker', telefoon='+31 6 23456789'),
                Verantwoordelijke(naam='Pieter Jansen', telefoon='+31 6 34567890', adres='Hoofdstraat 10, Amsterdam'),
            ]
            demo_w = [
                Monteur(naam='Sergei Petrov', specialisatie='Elektronica', tarief_per_uur=45),
                Monteur(naam='Alex de Boer', specialisatie='Mechanica', tarief_per_uur=40),
                Monteur(naam='Dmitri Smit', specialisatie='Algemeen onderhoud', tarief_per_uur=42),
            ]
            demo_i = [
                VoorraadItem(naam='Soldeerpasta', categorie='Verbruiksartikelen', eenheid='st', hoeveelheid=10, minimum=3, prijs=12),
                VoorraadItem(naam='Koperdraad 0.5mm', categorie='Materialen', eenheid='m', hoeveelheid=50, minimum=10, prijs=2),
                VoorraadItem(naam='Lager 608ZZ', categorie='Onderdelen', eenheid='st', hoeveelheid=20, minimum=5, prijs=6),
                VoorraadItem(naam='Lijm SuperMoment', categorie='Verbruiksartikelen', eenheid='st', hoeveelheid=5, minimum=2, prijs=8),
                VoorraadItem(naam='V-snaar', categorie='Onderdelen', eenheid='st', hoeveelheid=3, minimum=2, prijs=25),
            ]
            db.session.add_all(demo + demo_w + demo_i)
            if not safe_commit():
                print('WARNING: safe_commit failed in startup')
            
            demo_o = [
                Opdracht(nummer='WO-20260809-0001', responsible_id=1, monteur_id=1, apparaat='Smartphone', model='iPhone 12',
                         probleem='Gebroken scherm, touchscreen werkt niet', status='afgeleverd',
                         arbeidskosten=250, onderdelenkosten=150, totaal=400,
                         gestart=datetime.utcnow()-timedelta(days=5), gereed=datetime.utcnow()-timedelta(days=3),
                         afgeleverd=datetime.utcnow()-timedelta(days=2)),
                Opdracht(nummer='WO-20260809-0002', responsible_id=2, monteur_id=2, apparaat='Laptop', model='ASUS X515',
                         probleem='Start niet op, laadt niet', status='in behandeling',
                         arbeidskosten=150, onderdelenkosten=0, totaal=150,
                         gestart=datetime.utcnow()-timedelta(days=1)),
                Opdracht(nummer='WO-20260809-0003', responsible_id=3, monteur_id=1, apparaat='Tablet', model='Samsung Tab A',
                         probleem='WiFi werkt niet, traag', status='aangenomen', arbeidskosten=0, onderdelenkosten=0, totaal=0),
            ]
            db.session.add_all(demo_o)
            if not safe_commit():
                print('WARNING: safe_commit failed in startup')
            
            # Demo fault report
            fault = FaultReport(
                title='CNC Mill spindle vibration',
                description='Excessive vibration during high-speed operation. Possible bearing wear.',
                priority='high',
                machine_id=1,
                reporter_id=3  # user
            )
            db.session.add(fault)
            if not safe_commit():
                print('WARNING: safe_commit failed in startup')
            
            print("\n" + "=" * 50)
            print("Demo data loaded! Generated credentials:")
            print("=" * 50)
            for username, pw in creds:
                print(f"  {username} / {pw}")
            print("=" * 50)
            print("Save these passwords! They are shown only once.\n")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
