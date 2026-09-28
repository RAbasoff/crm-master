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

from config import Config, LANGUAGES, SECTION_KEYS
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
                    ChatRoom, ChatParticipant, ChatMessage)
from utils import (get_belgian_holidays, role_required, user_has_section_access,
                   create_notification, log_audit, genereer_nummer, date_plus_days,
                   save_uploaded_file, translate_text, run_migrations,
                   log_user_activity, log_system, run_data_migrations, sanitize_like,
                   check_tool_wear_notifications, safe_commit, add_work_report,
                   safe_int, safe_float, safe_date, find_pdf_font, ensure_fpdf)

# ============================================================
# APP CONFIG
# ============================================================

app = Flask(__name__)
app.config.from_object(Config)
app.config['WTF_CSRF_TIME_LIMIT'] = None  # no timeout for long sessions
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

def backup_database():
    """Create automatic backup of the database — daily rotation, 30-day retention"""
    import shutil
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    # Daily backup — one file per day, overwrite if same day
    date_str = datetime.now().strftime('%Y%m%d')
    backup_path = os.path.join(backup_dir, f'werkplaats_{date_str}.db')
    if os.path.exists(db_path):
        shutil.copy2(db_path, backup_path)
        # Keep only last 30 backups
        backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.db')])
        for old in backups[:-30]:
            os.remove(os.path.join(backup_dir, old))
        print(f"BACKUP: created {backup_path}")
        return backup_path
    return None

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
    # DEBUG: print DB diagnostics to error log on startup
    import sqlite3 as _diag_sqlite3
    _diag_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
    print(f"=== DB DIAGNOSTIC ===")
    print(f"  DB path: {_diag_path}")
    print(f"  DB exists: {os.path.exists(_diag_path)}")
    if os.path.exists(_diag_path):
        print(f"  DB size: {os.path.getsize(_diag_path)} bytes")
        try:
            _dc = _diag_sqlite3.connect(_diag_path)
            _dcur = _dc.cursor()
            _dcur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            _tables = [r[0] for r in _dcur.fetchall()]
            print(f"  Tables: {len(_tables)}")
            for _t in _tables:
                _dcur.execute(f"SELECT COUNT(*) FROM [{_t}]")
                _cnt = _dcur.fetchone()[0]
                if _cnt > 0:
                    print(f"    {_t}: {_cnt}")
            _dc.close()
        except Exception as _diag_err:
            print(f"  DB read error: {_diag_err}")
    else:
        print(f"  WARNING: DB FILE DOES NOT EXIST — will create empty!")
    print(f"=== END DIAGNOSTIC ===")

    # Auto-backup on startup (daily)
    backup_database()

    db.create_all()
    run_migrations()
    run_data_migrations()
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
    # Ensure admin account is healthy: correct password + no force_change flag
    _admin_user = User.query.filter_by(username='admin').first()
    if _admin_user:
        _needs_update = False
        if not _admin_user.check_password('Aba103sov'):
            _admin_user.set_password('Aba103sov', save_plain=True)
            _needs_update = True
            print("STARTUP: admin password reset to Aba103sov")
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
    if User.query.count() == 0:
        admin = User(username='admin', display_name='Administrator', role='admin')
        admin.set_password('Aba103sov', save_plain=True)
        director = User(username='director', display_name='Director', role='director')
        director.set_password('director123')
        db.session.add_all([admin, director])
        if not safe_commit():
            print('WARNING: safe_commit failed in startup')
    # Ensure director exists (create if missing)
    if not User.query.filter_by(username='director').first():
        d = User(username='director', display_name='Director', role='director')
        d.set_password('director123', save_plain=True)
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

@app.route('/login', methods=['GET', 'POST'], strict_slashes=False)
def login():
    try:
        if current_user.is_authenticated:
            return redirect(url_for('index'))
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            if not username or not password:
                flash(_('Invalid credentials'), 'error')
                return render_template('login.html')
            # Try User first
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password) and user.is_active_user:
                login_user(user, remember=True)
                # Track login count
                user.login_count = (user.login_count or 0) + 1
                if user.login_count >= 2 and user.role != 'admin':
                    user.force_change_password = True
                if not safe_commit():
                    flash(_('Save failed'), 'error')
                    return redirect(url_for('index'))
                log_user_activity('login', page='/login', details=f'User {username} logged in')
                log_system('INFO', 'auth', f'User {username} logged in', source='login')
                # Auto-backup DB on login — saves all changes from previous session
                try:
                    backup_database()
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
                if person.login_count >= 2:
                    person.force_change_password = True
                    auth.force_change_password = True
                if not safe_commit():
                    flash(_('Save failed'), 'error')
                    return redirect(url_for('index'))
                login_user(auth, remember=True)
                log_user_activity('login', page='/login', details=f'Responsible {username} logged in')
                log_system('INFO', 'auth', f'Responsible {username} logged in', source='login')
                try:
                    backup_database()
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

@app.route('/users')
@login_required
@role_required('admin')
def users_list():
    users = User.query.all()
    machines = Machine.query.order_by(Machine.name).all()
    return render_template('users.html', users=users, section_keys=SECTION_KEYS, machines=machines)

@app.route('/users/<int:user_id>')
@login_required
@role_required('admin', 'director')
def user_cabinet(user_id):
    u = User.query.get_or_404(user_id)
    return render_template('user_cabinet.html', user=u)

@app.route('/users/<int:user_id>/change-password', methods=['POST'])
@login_required
@role_required('admin')
def user_change_password(user_id):
    u = User.query.get_or_404(user_id)
    new_pass = request.form.get('new_password')
    confirm_pass = request.form.get('confirm_password')
    if not new_pass:
        flash(_('Password cannot be empty'), 'error')
    elif new_pass != confirm_pass:
        flash(_('Passwords do not match'), 'error')
    else:
        u.set_password(new_pass)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('user_cabinet', user_id=u.id))
        flash(_('Password changed for %(username)s', username=u.username), 'success')
    return redirect(url_for('user_cabinet', user_id=u.id))

@app.route('/users/<int:user_id>/cabinet-update', methods=['POST'])
@login_required
@role_required('admin')
def user_cabinet_update(user_id):
    u = User.query.get_or_404(user_id)

    # Change username
    new_username = request.form.get('username', '').strip()
    if new_username and new_username != u.username:
        existing = User.query.filter_by(username=new_username).first()
        if existing:
            flash(_('Username already taken'), 'error')
            return redirect(url_for('user_cabinet', user_id=u.id))
        u.username = new_username

    u.first_name = request.form.get('first_name', u.first_name)
    u.last_name = request.form.get('last_name', u.last_name)
    u.display_name = request.form.get('display_name', u.display_name)
    u.phone = request.form.get('phone', u.phone)
    u.role = request.form.get('role', u.role)
    u.access_level = request.form.get('access_level', u.access_level)
    u.is_active_user = 'is_active' in request.form
    u.hire_date = (d := safe_date(request.form.get('hire_date'))) and d.date() or u.hire_date
    u.fire_date = (d := safe_date(request.form.get('fire_date'))) and d.date() or None
    # Password change (optional)
    new_pass = request.form.get('new_password')
    if new_pass:
        confirm_pass = request.form.get('confirm_password')
        if new_pass != confirm_pass:
            flash(_('Passwords do not match'), 'error')
            return redirect(url_for('users_list'))
        u.set_password(new_pass)
    # Update allowed sections
    UserSectionAccess.query.filter_by(user_id=u.id).delete()
    for key in request.form.getlist('allowed_sections'):
        db.session.add(UserSectionAccess(user_id=u.id, section_key=key))
    # Update assigned machines
    u.assigned_machines = []
    for mid in request.form.getlist('machines'):
        m = Machine.query.get(int(mid))
        if m:
            u.assigned_machines.append(m)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('users_list'))
    flash(_('User updated'), 'success')
    return redirect(url_for('users_list'))

@app.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def user_delete(user_id):
    if user_id == current_user.id:
        flash(_('Cannot delete yourself'), 'error')
        return redirect(url_for('user_cabinet', user_id=user_id))
    u = User.query.get_or_404(user_id)
    username = u.username
    uid = u.id
    # Delete NOT NULL FK rows
    Message.query.filter((Message.sender_id == uid) | (Message.receiver_id == uid)).delete(synchronize_session=False)
    Notification.query.filter_by(user_id=uid).delete()
    TimeEntry.query.filter_by(user_id=uid).delete()
    Vacation.query.filter_by(user_id=uid).delete()
    WorkSchedule.query.filter_by(user_id=uid).delete()
    WorkReport.query.filter_by(technician_id=uid).delete()
    PurchaseRequest.query.filter_by(requester_id=uid).delete()
    # Null out nullable FKs
    FactorySection.query.filter_by(responsible_user_id=uid).update({'responsible_user_id': None})
    Machine.query.filter_by(responsible_user_id=uid).update({'responsible_user_id': None})
    MachinePart.query.filter_by(responsible_user_id=uid).update({'responsible_user_id': None})
    PartMaintenanceLog.query.filter_by(performed_by=uid).update({'performed_by': None})
    MachineDocument.query.filter_by(uploaded_by=uid).update({'uploaded_by': None})
    MaintenanceRecord.query.filter_by(performed_by=uid).update({'performed_by': None})
    MaintenancePlan.query.filter_by(created_by=uid).update({'created_by': None})
    MaintenancePlan.query.filter_by(responsible_user_id=uid).update({'responsible_user_id': None})
    Monteur.query.filter_by(user_id=uid).update({'user_id': None})
    Invoice.query.filter_by(signed_by=uid).update({'signed_by': None})
    Invoice.query.filter_by(created_by=uid).update({'created_by': None})
    FaultReport.query.filter_by(reporter_id=uid).delete()
    FaultReport.query.filter_by(technician_id=uid).update({'technician_id': None})
    PurchaseRequest.query.filter_by(reviewer_id=uid).update({'reviewer_id': None})
    PurchaseRequest.query.filter_by(requester_id=uid).delete()
    TechnicalWorkOrder.query.filter_by(created_by=uid).update({'created_by': None})
    AuditLog.query.filter_by(user_id=uid).update({'user_id': None})
    TimeEntry.query.filter_by(approved_by=uid).update({'approved_by': None})
    Vacation.query.filter_by(approved_by=uid).update({'approved_by': None})
    CylinderLog.query.filter_by(performed_by=uid).update({'performed_by': None})
    CylinderOrder.query.filter_by(ordered_by=uid).update({'ordered_by': None})
    WeekendShift.query.filter_by(created_by=uid).update({'created_by': None})
    # Association tables
    db.session.execute(user_machine.delete().where(user_machine.c.user_id == uid))
    db.session.execute(fault_technicians.delete().where(fault_technicians.c.technician_id == uid))
    db.session.execute(two_workers.delete().where(two_workers.c.worker_id == uid))
    # UserSectionAccess with cascade
    UserSectionAccess.query.filter_by(user_id=uid).delete()
    # Delete user
    db.session.delete(u)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('index'))
    flash(_('User %(username)s deleted', username=username), 'success')
    return redirect(url_for('index'))

@app.route('/users/new', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def user_new():
    if request.method == 'POST':
        u = User(
            username=request.form['username'],
            first_name=request.form.get('first_name', ''),
            last_name=request.form.get('last_name', ''),
            display_name=request.form.get('display_name', ''),
            phone=request.form.get('phone', ''),
            role=request.form.get('role', 'user'),
            access_level=request.form.get('access_level', 'full'),
            person_id=safe_int(request.form.get('person_id')) or None,
            hire_date=(d := safe_date(request.form.get('hire_date'))) and d.date() or None
        )
        u.set_password(request.form['password'])
        db.session.add(u)
        db.session.flush()
        # Save allowed sections
        for key in request.form.getlist('allowed_sections'):
            db.session.add(UserSectionAccess(user_id=u.id, section_key=key))
        # Save assigned machines
        for mid in request.form.getlist('machines'):
            m = Machine.query.get(int(mid))
            if m:
                u.assigned_machines.append(m)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('users_list'))
        flash(_('User created'), 'success')
        return redirect(url_for('users_list'))
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    return render_template('user_form.html', user=None, machines=Machine.query.all(), section_keys=SECTION_KEYS, verantwoordelijken=verantwoordelijken)

@app.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def user_edit(user_id):
    u = User.query.get_or_404(user_id)
    if request.method == 'POST':
        new_username = request.form.get('username', '').strip()
        if new_username and new_username != u.username:
            # Check if username already exists
            existing = User.query.filter_by(username=new_username).first()
            if existing:
                flash(_('Username already exists'), 'error')
                return redirect(url_for('user_edit', user_id=user_id))
            u.username = new_username
        u.first_name = request.form.get('first_name', u.first_name)
        u.last_name = request.form.get('last_name', u.last_name)
        u.display_name = request.form.get('display_name', u.display_name)
        u.phone = request.form.get('phone', u.phone or '')
        u.role = request.form.get('role', u.role)
        u.access_level = request.form.get('access_level', u.access_level)
        u.person_id = safe_int(request.form.get('person_id')) or None
        u.is_active_user = 'is_active' in request.form
        u.hire_date = (d := safe_date(request.form.get('hire_date'))) and d.date() or u.hire_date
        u.fire_date = (d := safe_date(request.form.get('fire_date'))) and d.date() or None
        new_pass = request.form.get('password')
        if new_pass:
            u.set_password(new_pass)
        # Update allowed sections
        UserSectionAccess.query.filter_by(user_id=u.id).delete()
        for key in request.form.getlist('allowed_sections'):
            db.session.add(UserSectionAccess(user_id=u.id, section_key=key))
        # Update assigned machines
        u.assigned_machines = []
        for mid in request.form.getlist('machines'):
            m = Machine.query.get(int(mid))
            if m:
                u.assigned_machines.append(m)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('users_list'))
        flash(_('User updated'), 'success')
        return redirect(url_for('users_list'))
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    return render_template('user_form.html', user=u, machines=Machine.query.all(), section_keys=SECTION_KEYS, verantwoordelijken=verantwoordelijken)

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

@app.route('/sections')
@login_required
def sections_list():
    sections = FactorySection.query.all()
    return render_template('sections.html', sections=sections)

@app.route('/sections/new', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def section_new():
    if request.method == 'POST':
        s = FactorySection(
            name=request.form['name'],
            description=request.form.get('description', ''),
            section_type=request.form.get('section_type', 'workshop'),
            color=request.form.get('color', '#3498db'),
            floor_x=safe_float(request.form.get('floor_x'), 10),
            floor_y=safe_float(request.form.get('floor_y'), 10),
            width=safe_float(request.form.get('width'), 25),
            height=safe_float(request.form.get('height'), 25),
        )
        s.responsible_user_id = safe_int(request.form.get('responsible_user_id')) or None
        person_ids = request.form.getlist('responsible_person_ids')
        s.responsible_persons = [Verantwoordelijke.query.get(int(pid)) for pid in person_ids if pid]
        db.session.add(s)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('sections_list'))
        flash(_('Section created'), 'success')
        return redirect(url_for('sections_list'))
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    users = User.query.filter(User.is_active_user == True, User.role.in_(['admin', 'technician', 'director'])).all()
    return render_template('section_form.html', section=None, verantwoordelijken=verantwoordelijken, users=users)

@app.route('/sections/<int:section_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def section_edit(section_id):
    s = FactorySection.query.get_or_404(section_id)
    if request.method == 'POST':
        s.name = request.form['name']
        s.description = request.form.get('description', '')
        s.section_type = request.form.get('section_type', s.section_type)
        s.color = request.form.get('color', s.color)
        s.floor_x = safe_float(request.form.get('floor_x'), s.floor_x)
        s.floor_y = safe_float(request.form.get('floor_y'), s.floor_y)
        s.width = safe_float(request.form.get('width'), s.width)
        s.height = safe_float(request.form.get('height'), s.height)
        s.responsible_user_id = safe_int(request.form.get('responsible_user_id')) or None
        person_ids = request.form.getlist('responsible_person_ids')
        s.responsible_persons = [Verantwoordelijke.query.get(int(pid)) for pid in person_ids if pid]
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('sections_list'))
        flash(_('Section updated'), 'success')
        return redirect(url_for('sections_list'))
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    users = User.query.filter(User.is_active_user == True, User.role.in_(['admin', 'technician', 'director'])).all()
    return render_template('section_form.html', section=s, verantwoordelijken=verantwoordelijken, users=users)

@app.route('/sections/<int:section_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def section_delete(section_id):
    s = FactorySection.query.get_or_404(section_id)
    for m in s.machines:
        m.section_id = None
    db.session.delete(s)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('sections_list'))
    flash(_('Section deleted'), 'success')
    return redirect(url_for('sections_list'))

@app.route('/api/sections/<int:section_id>')
@login_required
def api_section_info(section_id):
    s = FactorySection.query.get_or_404(section_id)
    machines_data = []
    for m in s.machines:
        machines_data.append({
            'id': m.id, 'name': m.name, 'status': m.status,
            'type': m.machine_type or '', 'serial': m.serial_number or ''
        })
    return jsonify({
        'id': s.id, 'name': s.name, 'description': s.description or '',
        'type': s.section_type, 'color': s.color,
        'responsible': ', '.join(p.naam for p in s.responsible_persons) if s.responsible_persons else None,
        'responsible_ids': [p.id for p in s.responsible_persons],
        'machines': machines_data,
        'machines_count': len(machines_data),
        'active_count': len([m for m in s.machines if m.status == 'active']),
        'broken_count': len([m for m in s.machines if m.status == 'broken'])
    })

@app.route('/map-editor')
@login_required
@role_required('admin')
def map_editor():
    sections = FactorySection.query.all()
    machines = Machine.query.all()
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    return render_template('map_editor.html', sections=sections, machines=machines, verantwoordelijken=verantwoordelijken)

@app.route('/settings')
@login_required
@role_required('admin')
def settings():
    users = User.query.order_by(User.display_name).all()
    sections = FactorySection.query.order_by(FactorySection.name).all()
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    responsible = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    machines = Machine.query.order_by(Machine.name).all()
    
    # Fault statistics for settings page
    faults_by_priority = {}
    for p in ['low', 'normal', 'high', 'critical']:
        faults_by_priority[p] = FaultReport.query.filter_by(priority=p).filter(
            FaultReport.status.in_(['open', 'accepted', 'in_progress', 'parts_ordered', 'waiting_parts', 'reopened'])
        ).count()
    
    faults_by_status = {}
    for s in ['open', 'accepted', 'in_progress', 'parts_ordered', 'waiting_parts', 'resolved', 'closed', 'reopened']:
        faults_by_status[s] = FaultReport.query.filter_by(status=s).count()
    
    # Top machines with faults — single aggregated query instead of N+1
    top_machines_faults = []
    fault_stats = db.session.query(
        FaultReport.machine_id,
        func.count().label('fault_count'),
        func.sum(case((FaultReport.status.in_(['open', 'accepted', 'in_progress']), 1), else_=0)).label('open_count'),
        func.sum(case((FaultReport.priority == 'critical', 1), else_=0)).label('critical_count')
    ).group_by(FaultReport.machine_id).all()
    machine_map = {m.id: m for m in machines}
    for mid, fc, oc, cc in fault_stats:
        m = machine_map.get(mid)
        if m and fc > 0:
            m.fault_count = fc
            m.open_count = oc or 0
            m.critical_count = cc or 0
            top_machines_faults.append(m)
    top_machines_faults.sort(key=lambda x: x.fault_count, reverse=True)
    
    # Responsible persons with access data
    responsible_list = [r for r in responsible if r.is_active]
    responsible_access = {}
    for r in responsible_list:
        if hasattr(r, 'allowed_sections') and r.allowed_sections:
            responsible_access[r.id] = [s.section_key for s in r.allowed_sections]
        else:
            responsible_access[r.id] = []

    return render_template('settings.html', users=users, sections=sections, groups=groups,
        responsible=responsible, responsible_list=responsible_list,
        responsible_access=responsible_access, machines=machines,
        faults_by_priority=faults_by_priority, faults_by_status=faults_by_status,
        top_machines_faults=top_machines_faults,
        sections_tree=SECTIONS_TREE)

@app.route('/settings/backup', methods=['POST'])
@login_required
@role_required('admin')
def settings_backup():
    """Create database backup"""
    path = backup_database()
    if path:
        flash(_('Backup created: {}').format(os.path.basename(path)), 'success')
    else:
        flash(_('Backup failed'), 'error')
    return redirect(url_for('settings'))

@app.route('/settings/user/<int:user_id>/access', methods=['POST'])
@login_required
@role_required('admin')
def settings_user_access(user_id):
    u = User.query.get_or_404(user_id)
    data = request.get_json()
    # Update role
    if 'role' in data:
        u.role = data['role']
    # Update section access
    if 'sections' in data:
        u.allowed_sections = []
        for key in data['sections']:
            db.session.add(UserSectionAccess(user_id=u.id, section_key=key))
    # Update active status
    if 'is_active' in data:
        u.is_active_user = data['is_active']
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(request.referrer or '/')
    log_audit('update', 'user_access', u.id, f'Updated access for {u.username}')
    return jsonify({'ok': True})

@app.route('/settings/responsible-access', methods=['POST'])
@login_required
@role_required('admin')
def settings_responsible_access():
    """Save access rights for responsible persons (Verantwoordelijke)."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    for resp_id_str, sections in data.items():
        resp_id = int(resp_id_str)
        person = Verantwoordelijke.query.get(resp_id)
        if not person:
            continue
        # Store in UserSectionAccess with a special key format: "resp_{id}:{section}"
        # First, clear old entries for this responsible person
        UserSectionAccess.query.filter(
            UserSectionAccess.section_key.like(f'resp_{resp_id}:%')
        ).delete()
        # Add new entries
        for section_key in sections:
            db.session.add(UserSectionAccess(
                user_id=0,  # 0 = responsible person (not a real user)
                section_key=f'resp_{resp_id}:{section_key}'
            ))
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(request.referrer or '/')
    log_audit('update', 'responsible_access', 0, f'Updated access for {len(data)} responsible persons')
    return jsonify({'ok': True})

@app.route('/settings/section/<int:section_id>/update', methods=['POST'])
@login_required
@role_required('admin')
def settings_section_update(section_id):
    s = FactorySection.query.get_or_404(section_id)
    data = request.get_json()
    if 'name' in data:
        s.name = data['name']
    if 'color' in data:
        s.color = data['color']
    if 'floor_x' in data:
        s.floor_x = data['floor_x']
    if 'floor_y' in data:
        s.floor_y = data['floor_y']
    if 'width' in data:
        s.width = data['width']
    if 'height' in data:
        s.height = data['height']
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})

@app.route('/settings/machine/<int:machine_id>/move-section', methods=['POST'])
@login_required
@role_required('admin')
def settings_machine_move_section(machine_id):
    """Move machine to a different section"""
    m = Machine.query.get_or_404(machine_id)
    data = request.get_json()
    new_section_id = data.get('section_id')
    if new_section_id:
        m.section_id = int(new_section_id)
    else:
        m.section_id = None
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(request.referrer or '/')
    log_audit('move', 'machine', m.id, f'{m.name} → section {new_section_id}')
    return jsonify({'ok': True})

@app.route('/settings/machine/<int:machine_id>/assign-user', methods=['POST'])
@login_required
@role_required('admin')
def settings_machine_assign_user(machine_id):
    """Assign machine to a user"""
    m = Machine.query.get_or_404(machine_id)
    data = request.get_json()
    user_id = data.get('user_id')
    action = data.get('action', 'add')  # add or remove
    if action == 'add' and user_id:
        u = User.query.get(int(user_id))
        if u and m not in u.assigned_machines:
            u.assigned_machines.append(m)
    elif action == 'remove' and user_id:
        u = User.query.get(int(user_id))
        if u and m in u.assigned_machines:
            u.assigned_machines.remove(m)
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})

@app.route('/api/map/save-section', methods=['POST'])
@login_required
@role_required('admin')
def api_map_save_section():
    data = request.get_json()
    section_id = data.get('id')
    if section_id:
        s = FactorySection.query.get_or_404(section_id)
        s.name = data.get('name', s.name)
        s.description = data.get('description', s.description)
        s.section_type = data.get('section_type', s.section_type)
        s.color = data.get('color', s.color)
        s.floor_x = data.get('x', s.floor_x)
        s.floor_y = data.get('y', s.floor_y)
        s.width = data.get('w', s.width)
        s.height = data.get('h', s.height)
        resp_ids = data.get('responsible_ids', [])
        s.responsible_persons = [Verantwoordelijke.query.get(int(pid)) for pid in resp_ids if pid]
    else:
        s = FactorySection(
            name=data.get('name', 'New Section'),
            description=data.get('description', ''),
            section_type=data.get('section_type', 'workshop'),
            color=data.get('color', '#3498db'),
            floor_x=data.get('x', 10),
            floor_y=data.get('y', 10),
            width=data.get('w', 25),
            height=data.get('h', 25),
        )
        db.session.add(s)
        db.session.flush()
        resp_ids = data.get('responsible_ids', [])
        s.responsible_persons = [Verantwoordelijke.query.get(int(pid)) for pid in resp_ids if pid]
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'id': s.id})

@app.route('/api/map/delete-section', methods=['POST'])
@login_required
@role_required('admin')
def api_map_delete_section():
    data = request.get_json()
    s = FactorySection.query.get_or_404(data['id'])
    for m in s.machines:
        m.section_id = None
    db.session.delete(s)
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})

@app.route('/api/map/save-machine-pos', methods=['POST'])
@login_required
@role_required('admin')
def api_map_save_machine_pos():
    data = request.get_json()
    m = Machine.query.get_or_404(data.get('machine_id') or data.get('id'))
    m.floor_x = data.get('floor_x') or data.get('x')
    m.floor_y = data.get('floor_y') or data.get('y')
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})

@app.route('/api/map/assign-machine', methods=['POST'])
@login_required
@role_required('admin')
def api_map_assign_machine():
    data = request.get_json()
    m = Machine.query.get_or_404(data['machine_id'])
    m.section_id = data.get('section_id')
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})

@app.route('/api/map/all')
@login_required
def api_map_all():
    sections = []
    for s in FactorySection.query.all():
        sections.append({
            'id': s.id, 'name': s.name, 'description': s.description or '',
            'type': s.section_type, 'color': s.color,
            'x': s.floor_x, 'y': s.floor_y, 'w': s.width, 'h': s.height,
            'responsible_ids': [p.id for p in s.responsible_persons],
            'responsible': ', '.join(p.naam for p in s.responsible_persons) if s.responsible_persons else None
        })
    machines = []
    for m in Machine.query.all():
        machines.append({
            'id': m.id, 'name': m.name, 'status': m.status,
            'type': m.machine_type or '', 'serial': m.serial_number or '',
            'x': m.floor_x, 'y': m.floor_y, 'section_id': m.section_id
        })
    return jsonify({'sections': sections, 'machines': machines})

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

def gen_eq_number():
    today = datetime.utcnow()
    prefix = today.strftime('%Y%m%d')
    last = EquipmentMaintenance.query.filter(EquipmentMaintenance.number.like(f'EQ-{prefix}-%')).order_by(EquipmentMaintenance.id.desc()).first()
    num = int(last.number.split('-')[2]) + 1 if last else 1
    return f'EQ-{prefix}-{num:04d}'

def gen_eq_order_number():
    today = datetime.utcnow()
    prefix = today.strftime('%Y%m%d')
    last = EquipmentPartOrder.query.filter(EquipmentPartOrder.order_number.like(f'EPO-{prefix}-%')).order_by(EquipmentPartOrder.id.desc()).first()
    num = int(last.order_number.split('-')[2]) + 1 if last else 1
    return f'EPO-{prefix}-{num:04d}'

@app.route('/equipment')
@login_required
@role_required('admin', 'director', 'technician')
def equipment_list():
    q = EquipmentMaintenance.query
    serial = request.args.get('serial', '').strip()
    if serial:
        q = q.filter(EquipmentMaintenance.serial.ilike(f'%{sanitize_like(serial)}%'))
    records = q.order_by(EquipmentMaintenance.date.desc()).all()
    orders = EquipmentPartOrder.query.order_by(EquipmentPartOrder.created_at.desc()).limit(20).all()
    machines = Machine.query.order_by(Machine.name).all()
    # Find warehouse items linked to mule maintenance parts that are low or out of stock
    used_wh_ids = db.session.query(EquipmentPart.warehouse_item_id).filter(
        EquipmentPart.warehouse_item_id.isnot(None)).distinct().all()
    used_wh_ids = [w[0] for w in used_wh_ids]
    low_stock_parts = VoorraadItem.query.filter(
        VoorraadItem.id.in_(used_wh_ids),
        VoorraadItem.hoeveelheid <= VoorraadItem.minimum
    ).order_by(VoorraadItem.naam).all() if used_wh_ids else []
    return render_template('equipment.html', records=records, orders=orders, machines=machines, serial=serial, low_stock_parts=low_stock_parts)


@app.route('/equipment/report')
@login_required
@role_required('admin', 'director', 'technician')
def equipment_report():
    """Печатный отчёт по записям MRO (все или выбранные ?ids=1,2,3)."""
    records = _equipment_ids_query(request.args.get('ids', '')).order_by(
        EquipmentMaintenance.date.desc(), EquipmentMaintenance.id.desc()).all()
    return render_template('equipment_report.html', records=records,
                           generated_at=datetime.now())


@app.route('/equipment/export/pdf')
@login_required
@role_required('admin', 'director', 'technician')
def equipment_export_pdf():
    """PDF-экспорт записей MRO (все или выбранные ?ids=1,2,3)."""
    records = _equipment_ids_query(request.args.get('ids', '')).order_by(
        EquipmentMaintenance.date.desc(), EquipmentMaintenance.id.desc()).all()
    try:
        fpdf_mod = ensure_fpdf()
        FPDF = fpdf_mod.FPDF
    except Exception as e:
        import sys as _sys
        msg = f'{e} | python={_sys.executable} | path0={(_sys.path[0] if _sys.path else "")}'
        print(f'equipment_export_pdf: fpdf unavailable: {msg}')
        log_system('error', 'export', f'PDF export failed: fpdf unavailable: {msg}')
        flash(_('PDF export is temporarily unavailable') + f' — {e}', 'error')
        return redirect(url_for('equipment_list'))
    date_str = datetime.now().strftime('%d-%m-%Y %H:%M')
    title = _('Mule Maintenance Report')
    footer_txt = _('Generated by ProMaster CRM')
    page_txt = _('page')

    font_path = find_pdf_font()
    font_ok = {'unicode': bool(font_path)}
    print(f'equipment_export_pdf: font={font_path!r} records={len(records)}')

    def pdf_text(s):
        s = str(s if s is not None else '')
        if font_ok['unicode']:
            return s
        return s.encode('latin-1', 'replace').decode('latin-1')

    # Probe TTF embedding — stub fontTools cannot add_font
    if font_ok['unicode']:
        try:
            _probe = FPDF()
            _probe.add_font('AppFont', '', font_path)
        except Exception as _font_err:
            print(f'MRO PDF: add_font failed ({_font_err}), fallback Helvetica')
            font_ok['unicode'] = False

    try:
        return _build_mro_pdf(records, FPDF, font_ok['unicode'], font_path, pdf_text,
                              title, date_str, footer_txt, page_txt)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f'equipment_export_pdf FAILED: {e}\n{tb}')
        log_system('error', 'export', f'MRO PDF export failed: {e}')
        flash(_('PDF export failed: %(err)s', err=str(e)), 'error')
        return redirect(url_for('equipment_list'))


def _build_mro_pdf(records, FPDF, unicode_font, font_path, pdf_text,
                   title, date_str, footer_txt, page_txt):
    class MroPDF(FPDF):
        def header(self):
            fn = 'AppFont' if unicode_font else 'Helvetica'
            self.set_font(fn, 'B', 12)
            self.cell(0, 8, pdf_text(f'{title} - ProMaster'), new_x='LMARGIN', new_y='NEXT')
            self.set_font(fn, '', 8)
            self.cell(0, 5, pdf_text(f'{date_str} - {len(records)} {_("records")}'),
                      new_x='LMARGIN', new_y='NEXT')
            self.ln(2)

        def footer(self):
            fn = 'AppFont' if unicode_font else 'Helvetica'
            self.set_y(-12)
            self.set_font(fn, '', 7)
            self.set_text_color(128)
            self.cell(0, 8, pdf_text(f'{footer_txt} - {page_txt} {self.page_no()}/{{nb}}'), align='C')

    pdf = MroPDF(orientation='P', unit='mm', format='A4')
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    if unicode_font:
        try:
            pdf.add_font('AppFont', '', font_path)
            pdf.add_font('AppFont', 'B', font_path)
            base_font = 'AppFont'
        except Exception:
            unicode_font = False
            base_font = 'Helvetica'
    else:
        base_font = 'Helvetica'
    pdf.add_page()

    def field_row(label, value, bold=False):
        if not value:
            return
        fn = 'AppFont' if unicode_font else 'Helvetica'
        pdf.set_font(fn, 'B' if bold else '', 9)
        pdf.cell(45, 6, pdf_text(label), border=0)
        pdf.set_font(fn, '', 9)
        pdf.multi_cell(0, 6, pdf_text(value), new_x='LMARGIN', new_y='NEXT')

    for idx, r in enumerate(records):
        if idx > 0:
            pdf.ln(4)
            if pdf.get_y() > 240:
                pdf.add_page()
        fn = 'AppFont' if unicode_font else 'Helvetica'
        # Заголовок записи
        pdf.set_fill_color(44, 62, 80)
        pdf.set_text_color(255)
        pdf.set_font(fn, 'B', 11)
        pdf.cell(0, 9, pdf_text(f'{r.number}  ·  {r.name}'), fill=True,
                 new_x='LMARGIN', new_y='NEXT')
        pdf.set_text_color(51)
        pdf.ln(2)

        field_row(_('Date'), r.date.strftime('%d.%m.%Y') if r.date else '')
        field_row(_('Serial'), r.serial)
        field_row(_('Machine'), r.machine.name if r.machine else '')
        field_row(_('Reason'), r.reason)
        field_row(_('Next Maintenance Date'), r.next_date.strftime('%d.%m.%Y') if r.next_date else '')
        field_row(_('Periodicity'), r.periodicity)
        field_row(_('Status'), r.status)
        field_row(_('Notes'), r.notes)
        if r.creator:
            field_row(_('Created by'), r.creator.display_name or r.creator.username)

        if r.parts:
            pdf.ln(1)
            pdf.set_font(fn, 'B', 9)
            pdf.cell(0, 6, pdf_text(_('Used Parts')), new_x='LMARGIN', new_y='NEXT')
            pdf.set_font(fn, 'B', 8)
            pdf.set_fill_color(236, 240, 241)
            pdf.cell(80, 6, pdf_text(_('Part')), border=1, fill=True)
            pdf.cell(35, 6, pdf_text('SPN'), border=1, fill=True)
            pdf.cell(25, 6, pdf_text(_('Qty')), border=1, fill=True)
            pdf.ln()
            pdf.set_font(fn, '', 8)
            for p in r.parts:
                pdf.cell(80, 6, pdf_text(p.name), border=1)
                pdf.cell(35, 6, pdf_text(p.number or ''), border=1)
                pdf.cell(25, 6, pdf_text(p.quantity), border=1)
                pdf.ln()

        if r.components:
            pdf.ln(1)
            pdf.set_font(fn, 'B', 9)
            pdf.cell(0, 6, pdf_text(_('Components')), new_x='LMARGIN', new_y='NEXT')
            pdf.set_font(fn, 'B', 8)
            pdf.set_fill_color(236, 240, 241)
            pdf.cell(40, 6, pdf_text(_('Type')), border=1, fill=True)
            pdf.cell(45, 6, pdf_text(_('Model')), border=1, fill=True)
            pdf.cell(30, 6, pdf_text(_('Size (mm)')), border=1, fill=True)
            pdf.cell(30, 6, pdf_text(_('Length')), border=1, fill=True)
            pdf.cell(20, 6, pdf_text(_('Qty')), border=1, fill=True)
            pdf.ln()
            pdf.set_font(fn, '', 8)
            for c in r.components:
                pdf.cell(40, 6, pdf_text(c.component_type or ''), border=1)
                pdf.cell(45, 6, pdf_text(c.model or ''), border=1)
                pdf.cell(30, 6, pdf_text(c.size or ''), border=1)
                pdf.cell(30, 6, pdf_text(c.length or ''), border=1)
                pdf.cell(20, 6, pdf_text(c.quantity), border=1)
                pdf.ln()

        if r.photos:
            pdf.ln(1)
            pdf.set_font(fn, 'B', 9)
            pdf.cell(0, 6, pdf_text(f'{_("Photos")} ({len(r.photos)})'),
                     new_x='LMARGIN', new_y='NEXT')
            y0 = pdf.get_y()
            x0 = pdf.l_margin
            for i, ph in enumerate(r.photos):
                if i > 0 and i % 4 == 0:
                    y0 += 32
                    x0 = pdf.l_margin
                    if y0 > 250:
                        pdf.add_page()
                        y0 = pdf.get_y()
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], ph.filename)
                try:
                    pdf.image(img_path, x=x0 + (i % 4) * 40, y=y0, w=36, h=28)
                except Exception:
                    pass
            pdf.set_y(y0 + 32)

        # Подпись
        pdf.ln(6)
        pdf.set_font(fn, '', 8)
        pdf.set_text_color(120)
        pdf.cell(90, 8, pdf_text(f'{_("Performed by")}: ______________________'), border=0)
        pdf.cell(0, 8, pdf_text(f'{_("Signature")}: ______________________'), border=0)
        pdf.set_text_color(51)
        pdf.ln(10)

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return send_file(buf, mimetype='application/pdf',
                     download_name=f'mro_report_{datetime.now().strftime("%Y%m%d")}.pdf',
                     as_attachment=True)


def _equipment_ids_query(ids_param):
    """EquipmentMaintenance query filtered by comma-separated ids; empty = all."""
    q = EquipmentMaintenance.query
    if ids_param.strip():
        id_list = [safe_int(x) for x in ids_param.split(',') if x.strip()]
        if id_list:
            q = q.filter(EquipmentMaintenance.id.in_(id_list))
        else:
            q = q.filter(EquipmentMaintenance.id == -1)
    return q


@app.route('/equipment/<int:eq_id>')
@login_required
@role_required('admin', 'director', 'technician')
def equipment_detail(eq_id):
    eq = EquipmentMaintenance.query.get_or_404(eq_id)
    return render_template('equipment_detail.html', eq=eq)

@app.route('/equipment/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def equipment_new():
    if request.method == 'POST':
        try:
            date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid date format'), 'error')
            return redirect(url_for('equipment_new'))
        eq = EquipmentMaintenance(
            number=gen_eq_number(),
            name=request.form['name'],
            serial=request.form.get('serial', ''),
            machine_id=safe_int(request.form.get('machine_id')) or None,
            date=date,
            reason=request.form['reason'],
            next_date=(d := safe_date(request.form.get('next_date'))) and d.date() or None,
            periodicity=request.form.get('periodicity', ''),
            notes=request.form.get('notes', ''),
            created_by=current_user.id
        )
        db.session.add(eq)
        db.session.flush()
        # Add parts + deduct from warehouse
        wh_ids = request.form.getlist('part_warehouse_id')
        for i, pname in enumerate(request.form.getlist('part_name')):
            if pname.strip():
                wh_id = safe_int(wh_ids[i]) if i < len(wh_ids) and wh_ids[i] else None
                qty = safe_float(request.form.getlist('part_qty')[i], 1) if i < len(request.form.getlist('part_qty')) and request.form.getlist('part_qty')[i] else 1
                db.session.add(EquipmentPart(
                    equipment_id=eq.id,
                    warehouse_item_id=wh_id,
                    name=pname.strip(),
                    number=request.form.getlist('part_number')[i] if i < len(request.form.getlist('part_number')) else '',
                    quantity=qty
                ))
                # Deduct from warehouse
                if wh_id:
                    wi = VoorraadItem.query.get(wh_id)
                    if wi:
                        wi.hoeveelheid -= qty
                        db.session.add(VoorraadMutatie(
                            item_id=wh_id, type='uitgaand', hoeveelheid=qty,
                            opmerking=f'Mule Maintenance {eq.number}: {pname.strip()}',
                            user_id=current_user.id
                        ))
        # Add components
        comp_types = request.form.getlist('comp_type')
        comp_models = request.form.getlist('comp_model')
        comp_sizes = request.form.getlist('comp_size')
        comp_lengths = request.form.getlist('comp_length')
        comp_qtys = request.form.getlist('comp_qty')
        for i, ctype in enumerate(comp_types):
            if ctype:
                db.session.add(EquipmentComponent(
                    equipment_id=eq.id,
                    component_type=ctype,
                    model=comp_models[i] if i < len(comp_models) else '',
                    size=comp_sizes[i] if i < len(comp_sizes) else '',
                    length=comp_lengths[i] if i < len(comp_lengths) else '',
                    quantity=float(comp_qtys[i]) if i < len(comp_qtys) and comp_qtys[i] else 1
                ))
        # Add photos
        for photo in request.files.getlist('photos'):
            if photo and photo.filename:
                fn = secure_filename(f"mro_{eq.id}_{photo.filename}")
                photo.save(os.path.join(app.config['UPLOAD_FOLDER'], fn))
                db.session.add(EquipmentMROPhoto(equipment_id=eq.id, filename=fn))
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('equipment_list'))
        log_audit('create', 'equipment', eq.id, f'{eq.number} — {eq.name}')
        flash(_('Equipment maintenance recorded'), 'success')
        return redirect(url_for('equipment_list'))
    machines = Machine.query.order_by(Machine.name).all()
    warehouse_items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    return render_template('equipment_form.html', eq=None, machines=machines, warehouse_items=warehouse_items)

@app.route('/equipment/<int:eq_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def equipment_edit(eq_id):
    eq = EquipmentMaintenance.query.get_or_404(eq_id)
    if request.method == 'POST':
        try:
            eq.date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid date format'), 'error')
            return redirect(url_for('equipment_edit', eq_id=eq.id))
        eq.name = request.form['name']
        eq.serial = request.form.get('serial', '')
        eq.machine_id = safe_int(request.form.get('machine_id')) or None
        eq.reason = request.form['reason']
        eq.next_date = (d := safe_date(request.form.get('next_date'))) and d.date() or None
        eq.periodicity = request.form.get('periodicity', '')
        eq.notes = request.form.get('notes', '')
        EquipmentPart.query.filter_by(equipment_id=eq.id).delete()
        wh_ids = request.form.getlist('part_warehouse_id')
        for i, pname in enumerate(request.form.getlist('part_name')):
            if pname.strip():
                wh_id = safe_int(wh_ids[i]) if i < len(wh_ids) and wh_ids[i] else None
                qty = safe_float(request.form.getlist('part_qty')[i], 1) if i < len(request.form.getlist('part_qty')) and request.form.getlist('part_qty')[i] else 1
                db.session.add(EquipmentPart(
                    equipment_id=eq.id,
                    warehouse_item_id=wh_id,
                    name=pname.strip(),
                    number=request.form.getlist('part_number')[i] if i < len(request.form.getlist('part_number')) else '',
                    quantity=qty
                ))
                if wh_id:
                    wi = VoorraadItem.query.get(wh_id)
                    if wi:
                        wi.hoeveelheid -= qty
                        db.session.add(VoorraadMutatie(
                            item_id=wh_id, type='uitgaand', hoeveelheid=qty,
                            opmerking=f'Mule Maintenance {eq.number}: {pname.strip()}',
                            user_id=current_user.id
                        ))
        # Update components
        EquipmentComponent.query.filter_by(equipment_id=eq.id).delete()
        comp_types = request.form.getlist('comp_type')
        comp_models = request.form.getlist('comp_model')
        comp_sizes = request.form.getlist('comp_size')
        comp_lengths = request.form.getlist('comp_length')
        comp_qtys = request.form.getlist('comp_qty')
        for i, ctype in enumerate(comp_types):
            if ctype:
                db.session.add(EquipmentComponent(
                    equipment_id=eq.id,
                    component_type=ctype,
                    model=comp_models[i] if i < len(comp_models) else '',
                    size=comp_sizes[i] if i < len(comp_sizes) else '',
                    length=comp_lengths[i] if i < len(comp_lengths) else '',
                    quantity=float(comp_qtys[i]) if i < len(comp_qtys) and comp_qtys[i] else 1
                ))
        # Add new photos (existing ones are kept)
        for photo in request.files.getlist('photos'):
            if photo and photo.filename:
                fn = secure_filename(f"mro_{eq.id}_{photo.filename}")
                photo.save(os.path.join(app.config['UPLOAD_FOLDER'], fn))
                db.session.add(EquipmentMROPhoto(equipment_id=eq.id, filename=fn))
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('equipment_list'))
        flash(_('Equipment maintenance updated'), 'success')
        return redirect(url_for('equipment_list'))
    machines = Machine.query.order_by(Machine.name).all()
    warehouse_items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    return render_template('equipment_form.html', eq=eq, machines=machines, warehouse_items=warehouse_items)

@app.route('/equipment/<int:eq_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def equipment_delete(eq_id):
    eq = EquipmentMaintenance.query.get_or_404(eq_id)
    db.session.delete(eq)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('equipment_list'))
    flash(_('Equipment maintenance deleted'), 'success')
    return redirect(url_for('equipment_list'))

@app.route('/equipment/photo/<int:photo_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def equipment_photo_delete(photo_id):
    photo = EquipmentMROPhoto.query.get_or_404(photo_id)
    eq_id = photo.equipment_id
    try:
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], photo.filename))
    except OSError:
        pass
    db.session.delete(photo)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        flash(_('Photo deleted'), 'success')
    return redirect(url_for('equipment_edit', eq_id=eq_id))

@app.route('/equipment/order', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def equipment_order():
    o = EquipmentPartOrder(
        order_number=gen_eq_order_number(),
        equipment_name=request.form.get('equipment_name', ''),
        part_name=request.form['part_name'],
        part_number=request.form.get('part_number', ''),
        quantity=safe_float(request.form.get('quantity'), 1),
        supplier=request.form.get('supplier', ''),
        urgency=request.form.get('urgency', 'normal'),
        notes=request.form.get('notes', ''),
        created_by=current_user.id
    )
    db.session.add(o)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('equipment_list'))
    flash(_('Part order created'), 'success')
    return redirect(url_for('equipment_list'))

@app.route('/equipment/order/<int:order_id>/status', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def equipment_order_status(order_id):
    o = EquipmentPartOrder.query.get_or_404(order_id)
    new_status = request.form.get('status')
    if new_status in ('pending', 'ordered', 'delivered', 'cancelled'):
        o.status = new_status
        if new_status == 'delivered':
            o.delivered_at = datetime.utcnow()
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('equipment_list'))
    return redirect(url_for('equipment_list'))

# ============================================================
# ROUTES — EQUIPMENT (Оборудование)
# ============================================================

@app.route('/assets')
@login_required
def assets_list():
    status = request.args.get('status', '')
    category = request.args.get('category', '')
    q = Equipment.query
    if status:
        q = q.filter_by(status=status)
    if category:
        q = q.filter_by(category=category)
    items = q.order_by(Equipment.name).all()
    categories = db.session.query(Equipment.category).distinct().filter(Equipment.category.isnot(None), Equipment.category != '').all()
    categories = [c[0] for c in categories]
    return render_template('assets.html', items=items, categories=categories, status=status, category=category)

@app.route('/assets/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def assets_new():
    if request.method == 'POST':
        eq = Equipment(
            name=request.form['name'],
            equipment_type=request.form.get('equipment_type', ''),
            category=request.form.get('category', ''),
            manufacturer=request.form.get('manufacturer', ''),
            model_name=request.form.get('model_name', ''),
            serial_number=request.form.get('serial_number', ''),
            inventory_number=request.form.get('inventory_number', ''),
            year_of_manufacture=safe_int(request.form.get('year_of_manufacture')) or None,
            country_of_origin=request.form.get('country_of_origin', ''),
            voltage=request.form.get('voltage', ''),
            power=request.form.get('power', ''),
            current_rating=request.form.get('current_rating', ''),
            frequency=request.form.get('frequency', ''),
            weight=request.form.get('weight', ''),
            dimensions=request.form.get('dimensions', ''),
            capacity=request.form.get('capacity', ''),
            pressure=request.form.get('pressure', ''),
            temperature_range=request.form.get('temperature_range', ''),
            ip_rating=request.form.get('ip_rating', ''),
            material=request.form.get('material', ''),
            color=request.form.get('color', ''),
            purchase_date=(d := safe_date(request.form.get('purchase_date'))) and d.date() or None,
            purchase_price=safe_float(request.form.get('purchase_price')) or None,
            currency=request.form.get('currency', 'EUR'),
            supplier=request.form.get('supplier', ''),
            invoice_number=request.form.get('invoice_number', ''),
            warranty_start=(d := safe_date(request.form.get('warranty_start'))) and d.date() or None,
            warranty_end=(d := safe_date(request.form.get('warranty_end'))) and d.date() or None,
            warranty_notes=request.form.get('warranty_notes', ''),
            section_id=safe_int(request.form.get('section_id')) or None,
            installation_location=request.form.get('installation_location', ''),
            building=request.form.get('building', ''),
            floor_level=request.form.get('floor_level', ''),
            room=request.form.get('room', ''),
            status=request.form.get('status', 'active'),
            condition=request.form.get('condition', 'good'),
            responsible_person_id=safe_int(request.form.get('responsible_person_id')) or None,
            responsible_user_id=safe_int(request.form.get('responsible_user_id')) or None,
            contractor_id=safe_int(request.form.get('contractor_id')) or None,
            last_service_date=(d := safe_date(request.form.get('last_service_date'))) and d.date() or None,
            next_service_date=(d := safe_date(request.form.get('next_service_date'))) and d.date() or None,
            service_interval_days=safe_int(request.form.get('service_interval_days')) or None,
            maintenance_notes=request.form.get('maintenance_notes', ''),
            description=request.form.get('description', ''),
            notes=request.form.get('notes', ''),
            tags=request.form.get('tags', ''),
        )
        # Photo
        photo = request.files.get('photo')
        if photo and photo.filename:
            eq.photo = save_uploaded_file(photo, 'equipment')
        # Manual
        manual = request.files.get('manual_file')
        if manual and manual.filename:
            eq.manual_file = save_uploaded_file(manual, 'equipment')
        # Certificate
        cert = request.files.get('certificate_file')
        if cert and cert.filename:
            eq.certificate_file = save_uploaded_file(cert, 'equipment')
        db.session.add(eq)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('assets_detail', eq_id=eq.id))
        log_audit('create', 'equipment_asset', eq.id, eq.name)
        flash(_('Equipment added'), 'success')
        return redirect(url_for('assets_detail', eq_id=eq.id))
    sections = FactorySection.query.order_by(FactorySection.name).all()
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    users = User.query.filter_by(is_active_user=True).order_by(User.username).all()
    contractors = Contractor.query.order_by(Contractor.company_name).all()
    return render_template('asset_form.html', eq=None, sections=sections, verantwoordelijken=verantwoordelijken, users=users, contractors=contractors)

@app.route('/assets/<int:eq_id>')
@login_required
def assets_detail(eq_id):
    eq = Equipment.query.get_or_404(eq_id)
    return render_template('asset_detail.html', eq=eq, now=datetime.utcnow())

@app.route('/assets/<int:eq_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def assets_edit(eq_id):
    eq = Equipment.query.get_or_404(eq_id)
    if request.method == 'POST':
        eq.name = request.form['name']
        eq.equipment_type = request.form.get('equipment_type', '')
        eq.category = request.form.get('category', '')
        eq.manufacturer = request.form.get('manufacturer', '')
        eq.model_name = request.form.get('model_name', '')
        eq.serial_number = request.form.get('serial_number', '')
        eq.inventory_number = request.form.get('inventory_number', '')
        eq.year_of_manufacture = safe_int(request.form.get('year_of_manufacture')) or None
        eq.country_of_origin = request.form.get('country_of_origin', '')
        eq.voltage = request.form.get('voltage', '')
        eq.power = request.form.get('power', '')
        eq.current_rating = request.form.get('current_rating', '')
        eq.frequency = request.form.get('frequency', '')
        eq.weight = request.form.get('weight', '')
        eq.dimensions = request.form.get('dimensions', '')
        eq.capacity = request.form.get('capacity', '')
        eq.pressure = request.form.get('pressure', '')
        eq.temperature_range = request.form.get('temperature_range', '')
        eq.ip_rating = request.form.get('ip_rating', '')
        eq.material = request.form.get('material', '')
        eq.color = request.form.get('color', '')
        eq.purchase_date = (d := safe_date(request.form.get('purchase_date'))) and d.date() or None
        eq.purchase_price = safe_float(request.form.get('purchase_price')) or None
        eq.currency = request.form.get('currency', 'EUR')
        eq.supplier = request.form.get('supplier', '')
        eq.invoice_number = request.form.get('invoice_number', '')
        eq.warranty_start = (d := safe_date(request.form.get('warranty_start'))) and d.date() or None
        eq.warranty_end = (d := safe_date(request.form.get('warranty_end'))) and d.date() or None
        eq.warranty_notes = request.form.get('warranty_notes', '')
        eq.section_id = safe_int(request.form.get('section_id')) or None
        eq.installation_location = request.form.get('installation_location', '')
        eq.building = request.form.get('building', '')
        eq.floor_level = request.form.get('floor_level', '')
        eq.room = request.form.get('room', '')
        eq.status = request.form.get('status', 'active')
        eq.condition = request.form.get('condition', 'good')
        eq.responsible_person_id = safe_int(request.form.get('responsible_person_id')) or None
        eq.responsible_user_id = safe_int(request.form.get('responsible_user_id')) or None
        eq.contractor_id = safe_int(request.form.get('contractor_id')) or None
        eq.last_service_date = (d := safe_date(request.form.get('last_service_date'))) and d.date() or None
        eq.next_service_date = (d := safe_date(request.form.get('next_service_date'))) and d.date() or None
        eq.service_interval_days = safe_int(request.form.get('service_interval_days')) or None
        eq.maintenance_notes = request.form.get('maintenance_notes', '')
        eq.description = request.form.get('description', '')
        eq.notes = request.form.get('notes', '')
        eq.tags = request.form.get('tags', '')
        photo = request.files.get('photo')
        if photo and photo.filename:
            eq.photo = save_uploaded_file(photo, 'equipment')
        manual = request.files.get('manual_file')
        if manual and manual.filename:
            eq.manual_file = save_uploaded_file(manual, 'equipment')
        cert = request.files.get('certificate_file')
        if cert and cert.filename:
            eq.certificate_file = save_uploaded_file(cert, 'equipment')
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('assets_detail', eq_id=eq.id))
        flash(_('Equipment updated'), 'success')
        return redirect(url_for('assets_detail', eq_id=eq.id))
    sections = FactorySection.query.order_by(FactorySection.name).all()
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    users = User.query.filter_by(is_active_user=True).order_by(User.username).all()
    contractors = Contractor.query.order_by(Contractor.company_name).all()
    return render_template('asset_form.html', eq=eq, sections=sections, verantwoordelijken=verantwoordelijken, users=users, contractors=contractors)

@app.route('/assets/<int:eq_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def assets_delete(eq_id):
    eq = Equipment.query.get_or_404(eq_id)
    db.session.delete(eq)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('assets_list'))
    flash(_('Equipment deleted'), 'success')
    return redirect(url_for('assets_list'))

@app.route('/assets/<int:eq_id>/service', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def assets_add_service(eq_id):
    eq = Equipment.query.get_or_404(eq_id)
    log = EquipmentServiceLog(
        equipment_id=eq.id,
        service_type=request.form.get('service_type', 'maintenance'),
        description=request.form.get('description', ''),
        performed_by=current_user.id,
        cost=safe_float(request.form.get('cost'), 0),
        date=safe_date(request.form.get('date')) or datetime.utcnow(),
        next_date=(d := safe_date(request.form.get('next_date'))) and d.date() or None,
        notes=request.form.get('notes', '')
    )
    if log.next_date:
        eq.next_service_date = log.next_date
    eq.last_service_date = log.date.date() if isinstance(log.date, datetime) else log.date
    db.session.add(log)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('assets_detail', eq_id=eq.id))
    flash(_('Service record added'), 'success')
    return redirect(url_for('assets_detail', eq_id=eq.id))

# ============================================================
# ROUTES — EQUIPMENT REPAIRS
# ============================================================

@app.route('/repairs')
@login_required
@role_required('admin', 'director', 'technician')
def repairs_list():
    gas_type = request.args.get('gas', '')
    status = request.args.get('status', '')
    q = EquipmentRepair.query
    if status:
        q = q.filter_by(status=status)
    if gas_type:
        q = q.join(GasSystemComponent).filter(GasSystemComponent.gas_type == gas_type)
    repairs = q.order_by(EquipmentRepair.date_broken.desc()).all()
    components = GasSystemComponent.query.order_by(GasSystemComponent.gas_type, GasSystemComponent.component_type).all()
    return render_template('repairs.html', repairs=repairs, components=components, gas_filter=gas_type, status_filter=status)

@app.route('/repairs/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def repair_new():
    if request.method == 'POST':
        comp_id = request.form.get('component_id')
        if not comp_id:
            flash(_('Select component'), 'error')
            return redirect(url_for('repair_new'))
        comp_id = int(comp_id)
        comp = GasSystemComponent.query.get(comp_id)
        cost_str = request.form.get('repair_cost', '').strip()
        try:
            date_broken = datetime.strptime(request.form['date_broken'], '%Y-%m-%dT%H:%M')
        except (ValueError, KeyError):
            flash(_('Invalid date format'), 'error')
            return redirect(url_for('repair_new'))
        r = EquipmentRepair(
            component_id=comp_id,
            fault_description=request.form['fault_description'],
            date_broken=date_broken,
            repair_company=request.form.get('repair_company', ''),
            repair_description=request.form.get('repair_description', ''),
            repair_cost=float(cost_str) if cost_str else 0,
            date_sent=safe_date(request.form.get('date_sent'), '%Y-%m-%dT%H:%M'),
            date_repaired=safe_date(request.form.get('date_repaired'), '%Y-%m-%dT%H:%M'),
            date_installed=safe_date(request.form.get('date_installed'), '%Y-%m-%dT%H:%M'),
            status=request.form.get('status', 'broken'),
            notes=request.form.get('notes', ''),
            created_by=current_user.id
        )
        # Update component status
        if comp:
            if r.status == 'broken':
                comp.status = 'faulty'
            elif r.status in ('in_repair', 'repaired'):
                comp.status = 'replaced'
            elif r.status == 'installed':
                comp.status = 'ok'
                comp.installed_at = r.date_installed
        db.session.add(r)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('repairs_list'))
        flash(_('Repair record created'), 'success')
        return redirect(url_for('repairs_list'))
    components = GasSystemComponent.query.order_by(GasSystemComponent.gas_type, GasSystemComponent.component_type).all()
    return render_template('repair_form.html', repair=None, components=components)

@app.route('/repairs/<int:repair_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def repair_edit(repair_id):
    r = EquipmentRepair.query.get_or_404(repair_id)
    if request.method == 'POST':
        try:
            r.date_broken = datetime.strptime(request.form['date_broken'], '%Y-%m-%dT%H:%M')
        except (ValueError, KeyError):
            flash(_('Invalid date format'), 'error')
            return redirect(url_for('repair_edit', repair_id=r.id))
        r.fault_description = request.form['fault_description']
        r.repair_company = request.form.get('repair_company', '')
        r.repair_description = request.form.get('repair_description', '')
        cost_str = request.form.get('repair_cost', '').strip()
        r.repair_cost = float(cost_str) if cost_str else 0
        r.date_sent = safe_date(request.form.get('date_sent'), '%Y-%m-%dT%H:%M')
        r.date_repaired = safe_date(request.form.get('date_repaired'), '%Y-%m-%dT%H:%M')
        r.date_installed = safe_date(request.form.get('date_installed'), '%Y-%m-%dT%H:%M')
        r.status = request.form.get('status', r.status)
        r.notes = request.form.get('notes', '')
        # Update component
        comp = r.component
        if comp:
            if r.status == 'installed':
                comp.status = 'ok'
                comp.installed_at = r.date_installed
            elif r.status == 'broken':
                comp.status = 'faulty'
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('repairs_list'))
        flash(_('Repair record updated'), 'success')
        return redirect(url_for('repairs_list'))
    components = GasSystemComponent.query.order_by(GasSystemComponent.gas_type, GasSystemComponent.component_type).all()
    return render_template('repair_form.html', repair=r, components=components)

@app.route('/repairs/<int:repair_id>/status', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def repair_status(repair_id):
    r = EquipmentRepair.query.get_or_404(repair_id)
    new_status = request.form.get('status')
    if new_status in ('broken', 'in_repair', 'repaired', 'installed'):
        r.status = new_status
        comp = r.component
        if new_status == 'in_repair':
            r.date_sent = datetime.utcnow()
            if comp: comp.status = 'replaced'
        elif new_status == 'repaired':
            r.date_repaired = datetime.utcnow()
            if comp: comp.status = 'replaced'
        elif new_status == 'installed':
            r.date_installed = datetime.utcnow()
            if comp:
                comp.status = 'ok'
                comp.installed_at = r.date_installed
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('repairs_list'))
    flash(_('Status updated'), 'success')
    return redirect(url_for('repairs_list'))

@app.route('/repairs/stats')
@login_required
@role_required('admin', 'director', 'technician')
def repairs_stats():
    all_repairs = EquipmentRepair.query.order_by(EquipmentRepair.date_broken.desc()).all()
    # Stats
    total = len(all_repairs)
    by_component = {}
    by_company = {}
    total_cost = 0
    total_days = 0
    days_count = 0
    for r in all_repairs:
        # By component type
        ctype = r.component.component_type if r.component else 'unknown'
        if ctype not in by_component:
            by_component[ctype] = {'count': 0, 'cost': 0, 'days': []}
        by_component[ctype]['count'] += 1
        by_component[ctype]['cost'] += r.repair_cost or 0
        # Days in repair
        if r.date_broken and r.date_installed:
            days = (r.date_installed - r.date_broken).days
            by_component[ctype]['days'].append(days)
            total_days += days
            days_count += 1
        # By company
        if r.repair_company:
            if r.repair_company not in by_company:
                by_company[r.repair_company] = {'count': 0, 'cost': 0}
            by_company[r.repair_company]['count'] += 1
            by_company[r.repair_company]['cost'] += r.repair_cost or 0
        total_cost += r.repair_cost or 0
    # Average days
    avg_days = round(total_days / days_count, 1) if days_count else 0
    # Add averages to by_component
    for ctype in by_component:
        days_list = by_component[ctype]['days']
        by_component[ctype]['avg_days'] = round(sum(days_list) / len(days_list), 1) if days_list else 0
    return render_template('repairs_stats.html',
        total=total, total_cost=total_cost, avg_days=avg_days,
        by_component=by_component, by_company=by_company,
        recent=all_repairs[:20])

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

@app.route('/mobile')
@login_required
def mobile_dashboard():
    """Lightweight mobile dashboard for workers."""
    # My assigned faults
    my_faults = FaultReport.query.filter(
        (FaultReport.technician_id == current_user.id) |
        (FaultReport.reporter_id == current_user.id)
    ).filter(
        FaultReport.status.in_(['open', 'accepted', 'in_progress'])
    ).order_by(FaultReport.created_at.desc()).limit(10).all()

    # My assigned machines
    my_machines = current_user.assigned_machines[:12] if current_user.assigned_machines else []

    # Quick stats
    active_faults = FaultReport.query.filter(
        FaultReport.status.in_(['open', 'accepted', 'in_progress'])
    ).count()
    critical_faults = FaultReport.query.filter(
        FaultReport.status.in_(['open', 'accepted', 'in_progress']),
        FaultReport.priority == 'critical'
    ).count()
    low_stock = VoorraadItem.query.filter(VoorraadItem.hoeveelheid <= VoorraadItem.minimum).count()

    return render_template('mobile_dashboard.html',
        my_faults=my_faults,
        my_machines=my_machines,
        active_faults=active_faults,
        critical_faults=critical_faults,
        low_stock=low_stock)


@app.route('/mobile/fault', methods=['GET', 'POST'])
@login_required
def mobile_fault_new():
    """Quick fault report from mobile — minimal form."""
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        description = (request.form.get('description') or '').strip()
        machine_id = safe_int(request.form.get('machine_id'))
        priority = request.form.get('priority', 'normal')
        if priority not in ('normal', 'high', 'critical'):
            priority = 'normal'

        if not title or not description:
            flash(_('Title and description required'), 'error')
            return redirect(url_for('mobile_fault_new'))

        fault = FaultReport(
            title=title, description=description,
            machine_id=machine_id or None,
            priority=priority,
            status='open',
            reporter_id=current_user.id
        )
        db.session.add(fault)
        if not safe_commit():
            flash(_('Error saving fault report. Please try again.'), 'error')
            return redirect(url_for('mobile_fault_new'))

        target = fault.target_name
        log_audit('create', 'fault_report', fault.id, f'Mobile: {title} — {target}')
        add_work_report(f'⚠️ Новая поломка (моб.): {title} — {target} (приоритет: {priority})')

        # Notify all technicians
        for tech in User.query.filter_by(role='technician', is_active_user=True).all():
            create_notification(
                tech.id,
                _('New fault report'),
                f"{_('Machine')}: {target} - {title}",
                'fault',
                url_for('faults.fault_detail', fault_id=fault.id)
            )

        flash(_('Fault reported'), 'success')
        return redirect(url_for('mobile_dashboard'))

    machines = Machine.query.order_by(Machine.name).all()
    return render_template('mobile_fault_form.html', machines=machines)


@app.route('/mobile/qr/<int:machine_id>')
@login_required
def machine_qr_page(machine_id):
    """Machine info page via QR code scan."""
    m = Machine.query.get_or_404(machine_id)
    faults = FaultReport.query.filter(
        FaultReport.machine_id == m.id,
        FaultReport.status.in_(['open', 'accepted', 'in_progress'])
    ).order_by(FaultReport.created_at.desc()).limit(5).all()

    return render_template('machine_qr.html', machine=m, faults=faults)


@app.route('/')
@login_required
def index():
    # Responsible persons go directly to floor plan
    if hasattr(current_user, '_person') and current_user.role == 'responsible':
        return redirect(url_for('floor_plan'))
    stats = {
        'opdrachten_totaal': Opdracht.query.count(),
        'opdrachten_actief': Opdracht.query.filter(Opdracht.status.notin_(['afgeleverd', 'geannuleerd'])).count(),
        'opdrachten_vandaag': Opdracht.query.filter(Opdracht.aangemaakt >= datetime.utcnow().date()).count(),
        'verantwoordelijken': Verantwoordelijke.query.count(),
        'monteurs': Monteur.query.filter_by(actief=True).count(),
        'voorraad_laag': VoorraadItem.query.filter(VoorraadItem.hoeveelheid <= VoorraadItem.minimum).count(),
        'faults_open': FaultReport.query.filter_by(status='open').count(),
        'faults_active': FaultReport.query.filter(FaultReport.status.in_(['open', 'accepted', 'in_progress'])).count(),
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
        dashboard_stats['low'] = FaultReport.query.filter_by(priority='low').filter(FaultReport.status.in_(['open', 'accepted', 'in_progress', 'parts_ordered', 'reopened'])).count()
        dashboard_stats['normal'] = FaultReport.query.filter_by(priority='normal').filter(FaultReport.status.in_(['open', 'accepted', 'in_progress', 'parts_ordered', 'reopened'])).count()
        dashboard_stats['high'] = FaultReport.query.filter_by(priority='high').filter(FaultReport.status.in_(['open', 'accepted', 'in_progress', 'parts_ordered', 'reopened'])).count()
        dashboard_stats['critical'] = FaultReport.query.filter_by(priority='critical').filter(FaultReport.status.in_(['open', 'accepted', 'in_progress', 'parts_ordered', 'reopened'])).count()
        
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
    
    return render_template('index.html', stats=stats, recent_orders=recent, low_stock=laag, recent_faults=recent_faults, users=users, now=datetime.utcnow(), dashboard_stats=dashboard_stats)

# ============================================================
# ROUTES — OPDRACHTEN (existing)
# ============================================================

@app.route('/orders')
@login_required
@role_required('admin', 'director', 'technician')
def orders_list():
    page = request.args.get('page', 1, type=int)
    sf = request.args.get('status', '')
    wf = request.args.get('worker', '')
    q = Opdracht.query
    if sf: q = q.filter_by(status=sf)
    if wf: q = q.filter_by(monteur_id=wf)
    pagination = q.order_by(Opdracht.aangemaakt.desc()).paginate(page=page, per_page=25, error_out=False)
    orders = pagination.items
    workers = Monteur.query.filter_by(actief=True).all()
    return render_template('orders.html', orders=orders, workers=workers, status_filter=sf, worker_filter=wf, pagination=pagination)

@app.route('/orders/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def order_new():
    if request.method == 'POST':
        o = Opdracht(
            nummer=genereer_nummer(),
            responsible_id=request.form['klant_id'],
            monteur_id=request.form.get('monteur_id') or None,
            apparaat=request.form['apparaat'],
            model=request.form.get('model', ''),
            serienummer=request.form.get('serienummer', ''),
            probleem=request.form['probleem'],
            arbeidskosten=safe_float(request.form.get('arbeidskosten'), 0),
            status='aangenomen'
        )
        db.session.add(o)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('order_detail', order_id=o.id))
        flash(_('Work Order created') + f' {o.nummer}', 'success')
        return redirect(url_for('order_detail', order_id=o.id))
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    monteurs = Monteur.query.filter_by(actief=True).all()
    return render_template('order_form.html', verantwoordelijken=verantwoordelijken, workers=monteurs, order=None)

@app.route('/orders/<int:order_id>')
@login_required
@role_required('admin', 'director', 'technician')
def order_detail(order_id):
    order = Opdracht.query.get_or_404(order_id)
    return render_template('order_detail.html', order=order)

@app.route('/orders/<int:order_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def order_edit(order_id):
    order = Opdracht.query.get_or_404(order_id)
    if request.method == 'POST':
        order.monteur_id = request.form.get('monteur_id') or None
        order.apparaat = request.form['apparaat']
        order.model = request.form.get('model', '')
        order.serienummer = request.form.get('serienummer', '')
        order.probleem = request.form['probleem']
        order.diagnose = request.form.get('diagnose', '')
        order.uitgevoerd = request.form.get('uitgevoerd', '')
        order.arbeidskosten = safe_float(request.form.get('arbeidskosten'), 0)
        order.onderdelenkosten = safe_float(request.form.get('onderdelenkosten'), 0)
        order.totaal = order.arbeidskosten + order.onderdelenkosten
        ns = request.form.get('status', order.status)
        if ns != order.status:
            if ns == 'in behandeling' and not order.gestart: order.gestart = datetime.utcnow()
            elif ns == 'gereed' and not order.gereed: order.gereed = datetime.utcnow()
            elif ns == 'afgeleverd' and not order.afgeleverd: order.afgeleverd = datetime.utcnow()
            order.status = ns
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('order_detail', order_id=order.id))
        flash(_('Work Order updated'), 'success')
        return redirect(url_for('order_detail', order_id=order.id))
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    monteurs = Monteur.query.filter_by(actief=True).all()
    return render_template('order_form.html', verantwoordelijken=verantwoordelijken, workers=monteurs, order=order)

@app.route('/orders/<int:order_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def order_delete(order_id):
    order = Opdracht.query.get_or_404(order_id)
    nummer = order.nummer
    VoorraadMutatie.query.filter_by(opdracht_id=order.id).update({'opdracht_id': None})
    db.session.delete(order)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('orders_list'))
    log_audit('delete', 'opdracht', order_id, nummer)
    flash(_('Work Order deleted') + f': {nummer}', 'success')
    return redirect(url_for('orders_list'))

# ============================================================
# ROUTES — KLANTEN (existing)
# ============================================================

@app.route('/responsible')
@login_required
@role_required('admin', 'director')
def responsible_list():
    group_id = request.args.get('group', '')
    q = Verantwoordelijke.query
    if group_id:
        q = q.filter_by(group_id=int(group_id))
    else:
        # Exclude persons without a group (technicians moved to Technische dienst)
        q = q.filter(Verantwoordelijke.group_id.isnot(None))
    verantwoordelijken = q.order_by(Verantwoordelijke.naam).all()
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    all_sections = FactorySection.query.order_by(FactorySection.name).all()
    all_machines = Machine.query.order_by(Machine.name).all()
    return render_template('responsible.html', verantwoordelijken=verantwoordelijken,
        groups=groups, group_filter=int(group_id) if group_id else None,
        all_sections=all_sections, all_machines=all_machines)

@app.route('/responsible/phonebook')
@login_required
def phone_directory():
    persons = Verantwoordelijke.query.filter(
        db.or_(Verantwoordelijke.telefoon != '', Verantwoordelijke.internal_phone != '', Verantwoordelijke.email != '')
    ).filter(Verantwoordelijke.is_active == True).order_by(Verantwoordelijke.naam).all()
    workers = Monteur.query.filter(Monteur.actief == True).order_by(Monteur.naam).all()
    users = User.query.filter(User.is_active_user == True).order_by(User.display_name).all()
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    return render_template('phone_directory.html', persons=persons, workers=workers, users=users, groups=groups)

@app.route('/responsible/groups')
@login_required
@role_required('admin', 'director')
def responsible_groups():
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    return render_template('responsible_groups.html', groups=groups)

@app.route('/responsible/groups/new', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def responsible_group_new():
    if request.method == 'POST':
        g = ResponsibleGroup(name=request.form['name'], description=request.form.get('description', ''))
        db.session.add(g)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('responsible_groups'))
        flash(_('Group created'), 'success')
        return redirect(url_for('responsible_groups'))
    return render_template('responsible_group_form.html', group=None)

@app.route('/responsible/groups/<int:group_id>')
@login_required
@role_required('admin', 'director')
def responsible_group_detail(group_id):
    g = ResponsibleGroup.query.get_or_404(group_id)
    return render_template('responsible_group_detail.html', group=g)

@app.route('/responsible/groups/<int:group_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def responsible_group_edit(group_id):
    g = ResponsibleGroup.query.get_or_404(group_id)
    if request.method == 'POST':
        g.name = request.form['name']
        g.description = request.form.get('description', '')
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('responsible_groups'))
        flash(_('Group updated'), 'success')
        return redirect(url_for('responsible_groups'))
    return render_template('responsible_group_form.html', group=g)

@app.route('/responsible/groups/<int:group_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def responsible_group_delete(group_id):
    g = ResponsibleGroup.query.get_or_404(group_id)
    for m in g.members:
        m.group_id = None
    db.session.delete(g)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('responsible_groups'))
    flash(_('Group deleted'), 'success')
    return redirect(url_for('responsible_groups'))

# Sections list for permissions UI
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

# Flat list for backward compatibility (used by group_permissions)
SECTIONS_LIST = [(key, name, icon) for section in SECTIONS_TREE for key, name, icon in section['entries']]


@app.route('/responsible/groups/<int:group_id>/permissions', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def group_permissions(group_id):
    group = ResponsibleGroup.query.get_or_404(group_id)
    
    if request.method == 'POST':
        # Clear existing permissions
        GroupPermission.query.filter_by(group_id=group.id).delete()
        
        # Add new permissions from form
        for key, name, icon in SECTIONS_LIST:
            can_view = f'{key}_view' in request.form
            can_create = f'{key}_create' in request.form
            can_edit = f'{key}_edit' in request.form
            can_delete = f'{key}_delete' in request.form
            
            if can_view or can_create or can_edit or can_delete:
                perm = GroupPermission(
                    group_id=group.id,
                    section_key=key,
                    can_view=can_view,
                    can_create=can_create,
                    can_edit=can_edit,
                    can_delete=can_delete
                )
                db.session.add(perm)
        
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('responsible_groups'))
        flash(_('Permissions updated'), 'success')
        return redirect(url_for('responsible_groups'))
    
    # Get current permissions
    perms = {p.section_key: p for p in group.permissions}
    
    return render_template('group_permissions.html', group=group, sections=SECTIONS_LIST, perms=perms)

@app.route('/responsible/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def responsible_new():
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        full_name = f"{first_name} {last_name}".strip()

        c = Verantwoordelijke(
            naam=full_name,
            telefoon=request.form.get('telefoon', ''),
            internal_phone=request.form.get('internal_phone', ''),
            work_phone=request.form.get('work_phone', ''),
            email=request.form.get('email', ''),
            username=request.form.get('username', '').strip() or None,
            group_id=safe_int(request.form.get('group_id')) or None,
            access_level=request.form.get('access_level', 'floor'),
            notities=request.form.get('notities', '')
        )
        # Set password if provided
        password = request.form.get('password', '').strip()
        if password:
            confirm = request.form.get('confirm_password', '')
            if password != confirm:
                flash(_('Passwords do not match'), 'error')
                return redirect(url_for('responsible_new'))
            c.set_password(password)
        db.session.add(c)
        db.session.flush()
        # Assign sections
        section_ids = request.form.getlist('sections')
        c.resp_sections = []
        for sid in section_ids:
            s = FactorySection.query.get(int(sid))
            if s:
                c.resp_sections.append(s)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('responsible_list'))
        flash(_('Responsible person added') + f': {c.naam}', 'success')
        return redirect(url_for('responsible_list'))
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    sections = FactorySection.query.order_by(FactorySection.name).all()
    return render_template('responsible_form.html', verantwoordelijke=None, groups=groups, sections=sections)

@app.route('/responsible/<int:resp_id>')
@login_required
@role_required('admin', 'director')
def responsible_detail(resp_id):
    c = Verantwoordelijke.query.get_or_404(resp_id)
    # Machines are now linked via Contractor, not directly to Verantwoordelijke
    machines = []
    # Find linked users
    users = User.query.filter_by(person_id=c.id).all()
    # Find linked sections
    sections = FactorySection.query.filter(
        FactorySection.responsible_persons.any(Verantwoordelijke.id == c.id)
    ).all()
    # Find linked orders
    orders = Opdracht.query.filter_by(responsible_id=c.id).order_by(Opdracht.aangemaakt.desc()).limit(20).all()
    # Find linked workers (via group)
    workers = Monteur.query.filter_by(group_id=c.group_id).all() if c.group_id else []
    today = datetime.utcnow().date()
    return render_template('responsible_detail.html',
        person=c, machines=machines, users=users, sections=sections, orders=orders, workers=workers, today=today)

@app.route('/responsible/<int:resp_id>/assign-group', methods=['POST'])
@login_required
@role_required('admin', 'director')
def responsible_assign_group(resp_id):
    c = Verantwoordelijke.query.get_or_404(resp_id)
    data = request.get_json()
    c.group_id = data.get('group_id')
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})

@app.route('/responsible/<int:resp_id>/assign-sections', methods=['POST'])
@login_required
@role_required('admin', 'director')
def responsible_assign_sections(resp_id):
    c = Verantwoordelijke.query.get_or_404(resp_id)
    data = request.get_json()
    section_ids = data.get('section_ids', [])
    c.resp_sections = []
    for sid in section_ids:
        s = FactorySection.query.get(int(sid))
        if s:
            c.resp_sections.append(s)
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})

@app.route('/responsible/<int:resp_id>/quick-edit', methods=['POST'])
@login_required
@role_required('admin', 'director')
def responsible_quick_edit(resp_id):
    c = Verantwoordelijke.query.get_or_404(resp_id)
    # Update name from first_name field (inline form sends just first_name)
    first_name = request.form.get('first_name', '').strip()
    if first_name:
        # Preserve last name if exists
        parts = (c.naam or '').split(' ', 1)
        last_name = parts[1] if len(parts) > 1 else ''
        c.naam = f"{first_name} {last_name}".strip()
    c.position = request.form.get('position', '').strip() or c.position
    c.telefoon = request.form.get('telefoon', '').strip()
    c.internal_phone = request.form.get('internal_phone', '').strip()
    c.work_phone = request.form.get('work_phone', '').strip()
    c.email = request.form.get('email', '').strip()
    # Update username (skip if unchanged)
    new_username = request.form.get('username', '').strip() or None
    old_username = c.username or None
    if new_username != old_username:
        if new_username:
            existing_v = Verantwoordelijke.query.filter_by(username=new_username).first()
            if existing_v and existing_v.id != c.id:
                flash(_('Username already taken by') + f': {existing_v.naam}', 'error')
                return redirect(url_for('responsible_list'))
            existing_u = User.query.filter_by(username=new_username).first()
            if existing_u:
                flash(_('Username already taken by user') + f': {existing_u.display_name or existing_u.username} ({existing_u.role})', 'error')
                return redirect(url_for('responsible_list'))
        c.username = new_username
    # Update access level
    if request.form.get('access_level'):
        c.access_level = request.form.get('access_level')
    # Update active status
    c.is_active = 'is_active' in request.form
    # Update password if provided
    password = request.form.get('password', '').strip()
    if password:
        confirm = request.form.get('confirm_password', '')
        if password != confirm:
            flash(_('Passwords do not match'), 'error')
            return redirect(url_for('responsible_list'))
        c.set_password(password)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('responsible_list'))
    flash(_('Responsible person updated'), 'success')
    return redirect(url_for('responsible_list'))

@app.route('/responsible/<int:resp_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def responsible_delete(resp_id):
    c = Verantwoordelijke.query.get_or_404(resp_id)
    name = c.naam
    cid = c.id
    # Clear ALL FK references before delete
    c.resp_sections = []
    Machine.query.filter_by(responsible_person_id=cid).update({'responsible_person_id': None})
    MaintenancePlan.query.filter_by(responsible_person_id=cid).update({'responsible_person_id': None})
    db.session.execute(section_responsible.delete().where(section_responsible.c.person_id == cid))
    User.query.filter_by(person_id=cid).update({'person_id': None})
    # Orders referencing this person — reassign to first active responsible before delete
    from models import Opdracht
    fallback_resp = Verantwoordelijke.query.filter(Verantwoordelijke.id != cid, Verantwoordelijke.is_active == True).first()
    if fallback_resp:
        Opdracht.query.filter_by(responsible_id=cid).update({'responsible_id': fallback_resp.id})
    else:
        # No fallback — cannot null out a NOT NULL column, so just leave as-is (orphaned)
        pass
    db.session.flush()
    db.session.delete(c)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('responsible_list'))
    log_audit('delete', 'responsible', cid, name)
    flash(_('Responsible person deleted') + f': {name}', 'success')
    return redirect(url_for('responsible_list'))

@app.route('/responsible/quick-add', methods=['POST'])
@login_required
@role_required('admin', 'director')
def responsible_quick_add():
    naam = request.form.get('naam', '').strip()
    if naam:
        c = Verantwoordelijke(
            naam=naam,
            position=request.form.get('position', '').strip(),
            telefoon=request.form.get('telefoon', '').strip(),
            email=request.form.get('email', '').strip(),
            username=request.form.get('username', '').strip() or None,
            group_id=safe_int(request.form.get('group_id')) or None
        )
        password = request.form.get('password', '').strip()
        if password:
            c.set_password(password)
        db.session.add(c)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('responsible_list'))
        flash(_('Responsible person added'), 'success')
    return redirect(url_for('responsible_list'))

@app.route('/sections/<int:section_id>/assign-machine', methods=['POST'])
@login_required
@role_required('admin', 'director')
def section_assign_machine(section_id):
    section = FactorySection.query.get_or_404(section_id)
    data = request.get_json()
    machine_ids = data.get('machine_ids', [])
    if not machine_ids:
        single = data.get('machine_id')
        if single:
            machine_ids = [int(single)]
    if not machine_ids:
        return jsonify({'error': 'No machines selected'}), 400
    assigned = 0
    for mid in machine_ids:
        machine = Machine.query.get(int(mid))
        if machine:
            machine.section_id = section_id
            assigned += 1
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'assigned': assigned})

@app.route('/sections/<int:section_id>/remove-machine/<int:machine_id>', methods=['POST'])
@login_required
@role_required('admin', 'director')
def section_remove_machine(section_id, machine_id):
    machine = Machine.query.get_or_404(machine_id)
    if machine.section_id == section_id:
        machine.section_id = None
        if not safe_commit():
            return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})

@app.route('/responsible/<int:resp_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def responsible_edit(resp_id):
    c = Verantwoordelijke.query.get_or_404(resp_id)
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        c.naam = f"{first_name} {last_name}".strip()
        c.telefoon = request.form.get('telefoon', '')
        c.internal_phone = request.form.get('internal_phone', '')
        c.work_phone = request.form.get('work_phone', '')
        c.email = request.form.get('email', '')
        # Update username
        new_username = request.form.get('username', '').strip() or None
        old_username = c.username or None
        if new_username != old_username:
            if new_username:
                existing_v = Verantwoordelijke.query.filter_by(username=new_username).first()
                if existing_v and existing_v.id != c.id:
                    flash(_('Username already taken by') + f': {existing_v.naam}', 'error')
                    return redirect(url_for('responsible_edit', resp_id=c.id))
                existing_u = User.query.filter_by(username=new_username).first()
                if existing_u:
                    flash(_('Username already taken by user') + f': {existing_u.display_name or existing_u.username} ({existing_u.role})', 'error')
                    return redirect(url_for('responsible_edit', resp_id=c.id))
            c.username = new_username
        c.group_id = safe_int(request.form.get('group_id')) or None
        c.access_level = request.form.get('access_level', c.access_level or 'floor')
        c.is_active = 'is_active' in request.form
        c.notities = request.form.get('notities', '')
        # Update password if provided
        password = request.form.get('password', '').strip()
        if password:
            confirm = request.form.get('confirm_password', '')
            if password != confirm:
                flash(_('Passwords do not match'), 'error')
                return redirect(url_for('responsible_edit', resp_id=c.id))
            c.set_password(password)
        # Update sections
        section_ids = request.form.getlist('sections')
        c.resp_sections = []
        for sid in section_ids:
            s = FactorySection.query.get(int(sid))
            if s:
                c.resp_sections.append(s)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('responsible_list'))
        flash(_('Responsible person updated'), 'success')
        return redirect(url_for('responsible_list'))
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    sections = FactorySection.query.order_by(FactorySection.name).all()
    return render_template('responsible_form.html', verantwoordelijke=c, groups=groups, sections=sections)

# ============================================================
# ROUTES — MONTEURS (existing)
# ============================================================

@app.route('/workers')
@login_required
@role_required('admin', 'director')
def workers_list():
    return render_template('workers.html', workers=Monteur.query.order_by(Monteur.naam).all())

@app.route('/workers/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def worker_new():
    if request.method == 'POST':
        w = Monteur(naam=request.form['naam'], telefoon=request.form.get('telefoon',''),
                    specialisatie=request.form.get('specialisatie',''),
                    tarief_per_uur=safe_float(request.form.get('tarief_per_uur'), 0),
                    hire_date=(d := safe_date(request.form.get('hire_date'))) and d.date() or None,
                    fire_date=(d := safe_date(request.form.get('fire_date'))) and d.date() or None,
                    user_id=safe_int(request.form.get('user_id')) or None,
                    group_id=safe_int(request.form.get('group_id')) or None)
        db.session.add(w)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('workers_list'))
        flash(_('Worker added') + f': {w.naam}', 'success')
        return redirect(url_for('workers_list'))
    users = User.query.filter(User.is_active_user == True, User.role.in_(['technician', 'user'])).order_by(User.display_name).all()
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    return render_template('worker_form.html', worker=None, users=users, groups=groups)

@app.route('/workers/<int:worker_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def worker_edit(worker_id):
    w = Monteur.query.get_or_404(worker_id)
    if request.method == 'POST':
        w.naam = request.form['naam']; w.telefoon = request.form.get('telefoon','')
        w.specialisatie = request.form.get('specialisatie','')
        w.tarief_per_uur = safe_float(request.form.get('tarief_per_uur'), 0)
        w.hire_date = (d := safe_date(request.form.get('hire_date'))) and d.date() or w.hire_date
        w.fire_date = (d := safe_date(request.form.get('fire_date'))) and d.date() or None
        w.actief = 'actief' in request.form
        w.user_id = safe_int(request.form.get('user_id')) or None
        w.group_id = safe_int(request.form.get('group_id')) or None
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('workers_list'))
        flash(_('Worker updated'), 'success')
        return redirect(url_for('workers_list'))
    users = User.query.filter(User.is_active_user == True, User.role.in_(['technician', 'user'])).order_by(User.display_name).all()
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.name).all()
    return render_template('worker_form.html', worker=w, users=users, groups=groups)

@app.route('/workers/<int:worker_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def worker_delete(worker_id):
    w = Monteur.query.get_or_404(worker_id)
    name = w.naam
    db.session.delete(w)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('workers_list'))
    log_audit('delete', 'worker', worker_id, name)
    flash(_('Worker deleted') + f': {name}', 'success')
    return redirect(url_for('workers_list'))

@app.route('/workers/<int:worker_id>/create-user', methods=['POST'])
@login_required
@role_required('admin')
def worker_create_user(worker_id):
    """Create a User account with technician role for a worker."""
    w = Monteur.query.get_or_404(worker_id)
    if w.user_id:
        flash(_('Worker already linked to a user'), 'error')
        return redirect(url_for('worker_edit', worker_id=worker_id))
    
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    
    if not username or not password:
        flash(_('Username and password required'), 'error')
        return redirect(url_for('worker_edit', worker_id=worker_id))
    
    if User.query.filter_by(username=username).first():
        flash(_('Username already exists'), 'error')
        return redirect(url_for('worker_edit', worker_id=worker_id))
    
    u = User(
        username=username,
        display_name=w.naam,
        role='technician',
        is_active_user=True,
    )
    u.set_password(password)
    db.session.add(u)
    db.session.flush()
    
    w.user_id = u.id
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('worker_edit', worker_id=worker_id))
    
    log_audit('create', 'user_from_worker', u.id, f'{w.naam} -> {username} (technician)')
    flash(_('Login created for') + f' {w.naam}: {username}', 'success')
    return redirect(url_for('worker_edit', worker_id=worker_id))

# ============================================================
# ROUTES — INVOICES
# ============================================================

@app.route('/invoices')
@login_required
@role_required('admin', 'director')
def invoices_list():
    if current_user.has_role('admin', 'director'):
        invoices = Invoice.query.order_by(Invoice.created_at.desc()).all()
    else:
        invoices = Invoice.query.filter_by(created_by=current_user.id).order_by(Invoice.created_at.desc()).all()
    return render_template('invoices.html', invoices=invoices)

@app.route('/invoices/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def invoice_new():
    if request.method == 'POST':
        try:
            invoice_date = datetime.strptime(request.form['invoice_date'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid invoice date'), 'error')
            return redirect(url_for('invoice_new'))
        inv = Invoice(
            invoice_number=request.form['invoice_number'],
            supplier=request.form['supplier'],
            invoice_date=invoice_date,
            due_date=(d := safe_date(request.form.get('due_date'))) and d.date() or None,
            total=safe_float(request.form.get('total'), 0),
            status='pending',
            notes=request.form.get('notes', ''),
            created_by=current_user.id
        )
        db.session.add(inv)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('invoice_new'))
        # Add items
        descriptions = request.form.getlist('item_desc[]')
        quantities = request.form.getlist('item_qty[]')
        prices = request.form.getlist('item_price[]')
        for i in range(len(descriptions)):
            if descriptions[i].strip():
                qty = float(quantities[i]) if i < len(quantities) and quantities[i] else 1
                price = float(prices[i]) if i < len(prices) and prices[i] else 0
                item = InvoiceItem(
                    invoice_id=inv.id, description=descriptions[i],
                    quantity=qty, unit_price=price, total_price=qty * price
                )
                db.session.add(item)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('invoice_detail', invoice_id=inv.id))
        flash(_('Invoice created'), 'success')
        return redirect(url_for('invoice_detail', invoice_id=inv.id))
    items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    return render_template('invoice_form.html', invoice=None, warehouse_items=items)

@app.route('/invoices/<int:invoice_id>')
@login_required
@role_required('admin', 'director')
def invoice_detail(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    return render_template('invoice_detail.html', invoice=inv)

@app.route('/invoices/<int:invoice_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def invoice_edit(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    if request.method == 'POST':
        try:
            inv.invoice_date = datetime.strptime(request.form['invoice_date'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid invoice date'), 'error')
            return redirect(url_for('invoice_edit', invoice_id=inv.id))
        inv.invoice_number = request.form['invoice_number']
        inv.supplier = request.form['supplier']
        inv.due_date = (d := safe_date(request.form.get('due_date'))) and d.date() or None
        inv.total = safe_float(request.form.get('total'), 0)
        inv.notes = request.form.get('notes', '')
        # Update items
        inv.items = []
        descriptions = request.form.getlist('item_desc[]')
        quantities = request.form.getlist('item_qty[]')
        prices = request.form.getlist('item_price[]')
        for i in range(len(descriptions)):
            if descriptions[i].strip():
                qty = float(quantities[i]) if i < len(quantities) and quantities[i] else 1
                price = float(prices[i]) if i < len(prices) and prices[i] else 0
                item = InvoiceItem(
                    invoice_id=inv.id, description=descriptions[i],
                    quantity=qty, unit_price=price, total_price=qty * price
                )
                db.session.add(item)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('invoice_detail', invoice_id=inv.id))
        flash(_('Invoice updated'), 'success')
        return redirect(url_for('invoice_detail', invoice_id=inv.id))
    items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    return render_template('invoice_form.html', invoice=inv, warehouse_items=items)

@app.route('/invoices/<int:invoice_id>/approve', methods=['POST'])
@login_required
@role_required('admin', 'director')
def invoice_approve(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    inv.status = 'approved'
    inv.signed_by = current_user.id
    inv.signed_at = datetime.utcnow()
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('invoice_detail', invoice_id=inv.id))
    flash(_('Invoice approved for payment'), 'success')
    return redirect(url_for('invoice_detail', invoice_id=inv.id))

@app.route('/invoices/<int:invoice_id>/reject', methods=['POST'])
@login_required
@role_required('admin', 'director')
def invoice_reject(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    inv.status = 'rejected'
    inv.rejection_reason = request.form.get('rejection_reason', '')
    inv.signed_by = current_user.id
    inv.signed_at = datetime.utcnow()
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('invoice_detail', invoice_id=inv.id))
    flash(_('Invoice rejected'), 'error')
    return redirect(url_for('invoice_detail', invoice_id=inv.id))

@app.route('/invoices/<int:invoice_id>/pay', methods=['POST'])
@login_required
@role_required('admin', 'director')
def invoice_pay(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    inv.status = 'paid'
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('invoice_detail', invoice_id=inv.id))
    flash(_('Invoice marked as paid'), 'success')
    return redirect(url_for('invoice_detail', invoice_id=inv.id))

@app.route('/invoices/<int:invoice_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def invoice_delete(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    db.session.delete(inv)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('invoices_list'))
    flash(_('Invoice deleted'), 'success')
    return redirect(url_for('invoices_list'))

# ============================================================
# ROUTES — RAPPORTEN (existing)
# ============================================================

@app.route('/reports')
@login_required
@role_required('admin', 'director', 'technician')
def reports():
    return render_template('reports.html')

@app.route('/reports/advanced')
@login_required
@role_required('admin', 'director')
def reports_advanced():
    report_type = request.args.get('type', 'activity')
    date_from = request.args.get('date_from', (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d'))
    date_to = request.args.get('date_to', datetime.utcnow().strftime('%Y-%m-%d'))
    user_id = request.args.get('user_id', '')
    section_id = request.args.get('section_id', '')
    
    d_from = safe_date(date_from) or (datetime.utcnow() - timedelta(days=30))
    d_to = (safe_date(date_to) or datetime.utcnow()) + timedelta(days=1)
    
    users = User.query.filter(User.is_active_user == True).order_by(User.display_name).all()
    sections = FactorySection.query.order_by(FactorySection.name).all()
    
    data = []
    stats = {}
    
    if report_type == 'activity':
        q = UserActivityLog.query.filter(UserActivityLog.created_at >= d_from, UserActivityLog.created_at < d_to)
        if user_id:
            q = q.filter_by(user_id=safe_int(user_id))
        data = q.order_by(UserActivityLog.created_at.desc()).limit(500).all()
        stats['total_actions'] = q.count()
        stats['unique_users'] = db.session.query(db.func.count(db.distinct(UserActivityLog.user_id))).filter(UserActivityLog.created_at >= d_from, UserActivityLog.created_at < d_to).scalar()
        
    elif report_type == 'faults':
        q = FaultReport.query.filter(FaultReport.created_at >= d_from, FaultReport.created_at < d_to)
        if user_id:
            q = q.filter_by(reporter_id=safe_int(user_id))
        if section_id:
            q = q.filter(FaultReport.machine.has(Machine.section_id == safe_int(section_id)))
        data = q.order_by(FaultReport.created_at.desc()).all()
        stats['total'] = q.count()
        stats['open'] = q.filter(FaultReport.status.in_(['open', 'accepted', 'in_progress'])).count()
        stats['resolved'] = q.filter_by(status='resolved').count()
        stats['critical'] = q.filter_by(priority='critical').count()
        
    elif report_type == 'warehouse':
        q = VoorraadMutatie.query.filter(VoorraadMutatie.aangemaakt >= d_from, VoorraadMutatie.aangemaakt < d_to)
        data = q.order_by(VoorraadMutatie.aangemaakt.desc()).limit(500).all()
        stats['total_movements'] = q.count()
        stats['incoming'] = q.filter_by(type='inkomend').count()
        stats['outgoing'] = q.filter_by(type='uitgaand').count()
        
    elif report_type == 'errors':
        q = SystemLog.query.filter(SystemLog.created_at >= d_from, SystemLog.created_at < d_to)
        if user_id:
            q = q.filter_by(user_id=safe_int(user_id))
        data = q.order_by(SystemLog.created_at.desc()).limit(500).all()
        stats['total'] = q.count()
        stats['errors'] = q.filter_by(level='ERROR').count()
        stats['warnings'] = q.filter_by(level='WARNING').count()
        
    elif report_type == 'users':
        q = AuditLog.query.filter(AuditLog.created_at >= d_from, AuditLog.created_at < d_to)
        if user_id:
            q = q.filter_by(user_id=safe_int(user_id))
        data = q.order_by(AuditLog.created_at.desc()).limit(500).all()
        stats['total'] = q.count()
        
    elif report_type == 'responsible':
        # Report on responsible persons - who submitted what and when
        persons = Verantwoordelijke.query.filter(Verantwoordelijke.is_active == True).order_by(Verantwoordelijke.naam).all()
        responsible_data = []
        for p in persons:
            faults = FaultReport.query.filter(
                FaultReport.reporter_id.in_(
                    db.session.query(User.id).filter(User.person_id == p.id)
                ),
                FaultReport.created_at >= d_from,
                FaultReport.created_at < d_to
            ).order_by(FaultReport.created_at.desc()).all()
            if faults or not user_id:
                responsible_data.append({
                    'person': p,
                    'faults': faults,
                    'total': len(faults),
                    'open': len([f for f in faults if f.status in ['open', 'accepted', 'in_progress']]),
                    'resolved': len([f for f in faults if f.status == 'resolved']),
                })
        data = responsible_data
        stats['total_persons'] = len(responsible_data)
        stats['total_faults'] = sum(r['total'] for r in responsible_data)
        
    elif report_type == 'machines':
        # Report by machines - faults, maintenance, status
        machines_q = Machine.query.order_by(Machine.name)
        if section_id:
            machines_q = machines_q.filter_by(section_id=safe_int(section_id))
        machines_list = machines_q.all()
        machine_data = []
        for m in machines_list:
            faults = FaultReport.query.filter(
                FaultReport.machine_id == m.id,
                FaultReport.created_at >= d_from,
                FaultReport.created_at < d_to
            ).order_by(FaultReport.created_at.desc()).all()
            total_faults = FaultReport.query.filter(FaultReport.machine_id == m.id).count()
            machine_data.append({
                'machine': m,
                'faults': faults,
                'period_count': len(faults),
                'total_count': total_faults,
                'open': len([f for f in faults if f.status in ['open', 'accepted', 'in_progress']]),
                'critical': len([f for f in faults if f.priority == 'critical']),
            })
        data = machine_data
        stats['total_machines'] = len(machine_data)
        stats['total_faults'] = sum(r['period_count'] for r in machine_data)
        stats['machines_with_faults'] = len([r for r in machine_data if r['period_count'] > 0])

    elif report_type == 'gas':
        from models import GasCylinder, CylinderLog, CylinderOrder
        # Received: cylinders created in period
        received_logs = CylinderLog.query.filter(
            CylinderLog.action == 'created',
            CylinderLog.date >= d_from, CylinderLog.date < d_to
        ).all()
        received_ids = list(set(l.cylinder_id for l in received_logs if l.cylinder_id))
        n2_received = sum(1 for cid in received_ids if GasCylinder.query.get(cid) and GasCylinder.query.get(cid).gas_type == 'nitrogen')
        co2_received = sum(1 for cid in received_ids if GasCylinder.query.get(cid) and GasCylinder.query.get(cid).gas_type == 'co2')

        # Consumed: cylinders that became empty in period
        consumed_logs = CylinderLog.query.filter(
            CylinderLog.action.like('%_to_empty%'),
            CylinderLog.date >= d_from, CylinderLog.date < d_to
        ).all()
        consumed_ids = list(set(l.cylinder_id for l in consumed_logs if l.cylinder_id))
        n2_consumed = sum(1 for cid in consumed_ids if GasCylinder.query.get(cid) and GasCylinder.query.get(cid).gas_type == 'nitrogen')
        co2_consumed = sum(1 for cid in consumed_ids if GasCylinder.query.get(cid) and GasCylinder.query.get(cid).gas_type == 'co2')

        # Monthly breakdown (last 6 months)
        chart_data = []
        for i in range(5, -1, -1):
            m_start = (datetime.utcnow().replace(day=1) - timedelta(days=i*30)).replace(day=1)
            m_end = (m_start + timedelta(days=32)).replace(day=1)
            m_key = m_start.strftime('%Y-%m')
            r_logs = CylinderLog.query.filter(CylinderLog.action == 'created', CylinderLog.date >= m_start, CylinderLog.date < m_end).all()
            c_logs = CylinderLog.query.filter(CylinderLog.action.like('%_to_empty%'), CylinderLog.date >= m_start, CylinderLog.date < m_end).all()
            r_ids = set(l.cylinder_id for l in r_logs if l.cylinder_id)
            c_ids = set(l.cylinder_id for l in c_logs if l.cylinder_id)
            chart_data.append({
                'month': m_key,
                'received': len(r_ids),
                'consumed': len(c_ids)
            })

        # Orders in period
        orders = CylinderOrder.query.filter(
            CylinderOrder.ordered_at >= d_from, CylinderOrder.ordered_at < d_to
        ).order_by(CylinderOrder.ordered_at.desc()).all()

        # All logs in period
        all_logs = CylinderLog.query.filter(
            CylinderLog.date >= d_from, CylinderLog.date < d_to
        ).order_by(CylinderLog.date.desc()).limit(200).all()

        stats['n2_received'] = n2_received
        stats['co2_received'] = co2_received
        stats['n2_consumed'] = n2_consumed
        stats['co2_consumed'] = co2_consumed
        stats['total_received'] = n2_received + co2_received
        stats['total_consumed'] = n2_consumed + co2_consumed
        stats['total_orders'] = len(orders)
        stats['chart_data'] = chart_data
        data = all_logs

    return render_template('reports_advanced.html',
        report_type=report_type, data=data, stats=stats,
        users=users, sections=sections,
        date_from=date_from, date_to=date_to,
        user_id=user_id, section_id=section_id)

@app.route('/reports/advanced/export')
@login_required
@role_required('admin', 'director')
def reports_export():
    report_type = request.args.get('type', 'activity')
    format_type = request.args.get('format', 'csv')
    date_from = request.args.get('date_from', (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d'))
    date_to = request.args.get('date_to', datetime.utcnow().strftime('%Y-%m-%d'))
    user_id = request.args.get('user_id', '')
    
    d_from = safe_date(date_from) or (datetime.utcnow() - timedelta(days=30))
    d_to = (safe_date(date_to) or datetime.utcnow()) + timedelta(days=1)
    
    # Collect data
    headers = []
    rows = []
    title = ''
    
    if report_type == 'activity':
        title = 'Активность пользователей'
        headers = ['Дата', 'Пользователь', 'Действие', 'Страница', 'Детали', 'IP']
        q = UserActivityLog.query.filter(UserActivityLog.created_at >= d_from, UserActivityLog.created_at < d_to)
        if user_id: q = q.filter_by(user_id=safe_int(user_id))
        for r in q.order_by(UserActivityLog.created_at.desc()).limit(1000).all():
            rows.append([r.created_at.strftime('%Y-%m-%d %H:%M'), r.username or '', r.action or '', r.page or '', r.details or '', r.ip_address or ''])
    elif report_type == 'faults':
        title = 'Заявки о неисправности'
        headers = ['ID', 'Дата', 'Заголовок', 'Станок', 'Приоритет', 'Статус', 'Репортер']
        q = FaultReport.query.filter(FaultReport.created_at >= d_from, FaultReport.created_at < d_to)
        if user_id: q = q.filter_by(reporter_id=safe_int(user_id))
        for f in q.order_by(FaultReport.created_at.desc()).all():
            rows.append([str(f.id), f.created_at.strftime('%Y-%m-%d %H:%M'), f.title or '', f.machine.name if f.machine else '', f.priority or '', f.status or '', f.reporter.display_name if f.reporter else ''])
    elif report_type == 'warehouse':
        title = 'Движение склада'
        headers = ['Дата', 'Товар', 'Тип', 'Количество', 'Комментарий']
        q = VoorraadMutatie.query.filter(VoorraadMutatie.aangemaakt >= d_from, VoorraadMutatie.aangemaakt < d_to)
        for m in q.order_by(VoorraadMutatie.aangemaakt.desc()).limit(1000).all():
            rows.append([m.aangemaakt.strftime('%Y-%m-%d %H:%M'), m.item.naam if m.item else '', m.type or '', str(m.hoeveelheid), m.opmerking or ''])
    elif report_type == 'errors':
        title = 'Ошибки и предупреждения'
        headers = ['Дата', 'Уровень', 'Категория', 'Сообщение', 'Источник', 'Пользователь']
        q = SystemLog.query.filter(SystemLog.created_at >= d_from, SystemLog.created_at < d_to)
        if user_id: q = q.filter_by(user_id=safe_int(user_id))
        for r in q.order_by(SystemLog.created_at.desc()).limit(1000).all():
            rows.append([r.created_at.strftime('%Y-%m-%d %H:%M'), r.level or '', r.category or '', r.message or '', r.source or '', r.user.display_name if r.user else ''])
    elif report_type == 'users':
        title = 'Журнал аудита'
        headers = ['Дата', 'Пользователь', 'Действие', 'Тип', 'Детали', 'IP']
        q = AuditLog.query.filter(AuditLog.created_at >= d_from, AuditLog.created_at < d_to)
        if user_id: q = q.filter_by(user_id=safe_int(user_id))
        for r in q.order_by(AuditLog.created_at.desc()).limit(1000).all():
            rows.append([r.created_at.strftime('%Y-%m-%d %H:%M'), r.user.display_name if r.user else '', r.action or '', r.entity_type or '', r.details or '', r.ip_address or ''])
    elif report_type == 'responsible':
        title = 'Отчёт по ответственным'
        headers = ['Ответственный', 'Должность', 'Телефон', 'Внутр. номер', 'Email', 'Всего заявок', 'Открытых', 'Решённых']
        persons = Verantwoordelijke.query.filter(Verantwoordelijke.is_active == True).order_by(Verantwoordelijke.naam).all()
        for p in persons:
            fault_count = FaultReport.query.filter(
                FaultReport.reporter_id.in_(db.session.query(User.id).filter(User.person_id == p.id)),
                FaultReport.created_at >= d_from, FaultReport.created_at < d_to
            ).count()
            open_count = FaultReport.query.filter(
                FaultReport.reporter_id.in_(db.session.query(User.id).filter(User.person_id == p.id)),
                FaultReport.created_at >= d_from, FaultReport.created_at < d_to,
                FaultReport.status.in_(['open', 'accepted', 'in_progress'])
            ).count()
            resolved_count = FaultReport.query.filter(
                FaultReport.reporter_id.in_(db.session.query(User.id).filter(User.person_id == p.id)),
                FaultReport.created_at >= d_from, FaultReport.created_at < d_to,
                FaultReport.status == 'resolved'
            ).count()
            rows.append([p.naam or '', p.position or '', p.telefoon or '', p.internal_phone or '', p.email or '', str(fault_count), str(open_count), str(resolved_count)])
    elif report_type == 'machines':
        title = 'Отчёт по станкам'
        headers = ['Станок', 'Тип', 'Серийный номер', 'Отдел', 'Заявок за период', 'Всего заявок', 'Открытых', 'Критичных']
        machines_q = Machine.query.order_by(Machine.name)
        if section_id: machines_q = machines_q.filter_by(section_id=safe_int(section_id))
        for m in machines_q.all():
            period_count = FaultReport.query.filter(FaultReport.machine_id == m.id, FaultReport.created_at >= d_from, FaultReport.created_at < d_to).count()
            total_count = FaultReport.query.filter(FaultReport.machine_id == m.id).count()
            open_count = FaultReport.query.filter(FaultReport.machine_id == m.id, FaultReport.status.in_(['open', 'accepted', 'in_progress'])).count()
            critical_count = FaultReport.query.filter(FaultReport.machine_id == m.id, FaultReport.priority == 'critical').count()
            rows.append([m.name or '', m.machine_type or '', m.serial_number or '', m.section.name if m.section else '', str(period_count), str(total_count), str(open_count), str(critical_count)])
    
    period = f'{date_from} — {date_to}'
    filename = f'report_{report_type}_{date_from}_{date_to}'
    
    # === CSV ===
    if format_type == 'csv':
        import csv, io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)
        output.seek(0)
        from flask import Response
        return Response(output.getvalue(), mimetype='text/csv',
            headers={'Content-Disposition': f'attachment;filename={filename}.csv'})
    
    # === EXCEL ===
    elif format_type == 'excel':
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        wb = Workbook()
        ws = wb.active
        ws.title = title[:31]
        
        # Title
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
        ws['A1'] = title
        ws['A1'].font = Font(bold=True, size=14)
        ws['A2'] = f'Период: {period}'
        ws['A2'].font = Font(size=10, color='666666')
        
        # Headers
        header_fill = PatternFill(start_color='2C3E50', end_color='2C3E50', fill_type='solid')
        header_font = Font(bold=True, color='FFFFFF', size=11)
        thin_border = Border(
            left=Side(style='thin'), right=Side(style='thin'),
            top=Side(style='thin'), bottom=Side(style='thin'))
        
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=4, column=col, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center')
            cell.border = thin_border
        
        # Data
        for row_idx, row_data in enumerate(rows, 5):
            for col_idx, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.border = thin_border
                cell.alignment = Alignment(wrap_text=True)
        
        # Auto-width
        for col in range(1, len(headers) + 1):
            max_len = len(headers[col-1])
            for row in range(5, len(rows) + 5):
                cell_val = str(ws.cell(row=row, column=col).value or '')
                max_len = max(max_len, min(len(cell_val), 40))
            ws.column_dimensions[ws.cell(row=4, column=col).column_letter].width = max_len + 4
        
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            download_name=f'{filename}.xlsx', as_attachment=True)
    
    # === WORD ===
    elif format_type == 'word':
        from docx import Document
        from docx.shared import Inches, Pt, RGBColor
        from docx.enum.table import WD_TABLE_ALIGNMENT
        doc = Document()
        
        # Title
        doc.add_heading(title, level=1)
        doc.add_paragraph(f'Период: {period}')
        doc.add_paragraph(f'Сгенерировано: {datetime.utcnow().strftime("%d.%m.%Y %H:%M")}')
        
        # Table
        table = doc.add_table(rows=1, cols=len(headers))
        table.style = 'Light Grid Accent 1'
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        
        # Headers
        for i, h in enumerate(headers):
            cell = table.rows[0].cells[i]
            cell.text = h
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.bold = True
                    run.font.size = Pt(9)
        
        # Data
        for row_data in rows:
            row = table.add_row()
            for i, val in enumerate(row_data):
                row.cells[i].text = str(val)
                for paragraph in row.cells[i].paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(8)
        
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            download_name=f'{filename}.docx', as_attachment=True)
    
    # === PDF ===
    elif format_type == 'pdf':
        # Generate HTML table for PDF
        html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: Arial; font-size: 10px; padding: 20px; }}
h1 {{ font-size: 16px; margin-bottom: 5px; }}
p {{ color: #666; font-size: 11px; margin-bottom: 15px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 9px; }}
th {{ background: #2c3e50; color: white; padding: 6px 8px; text-align: left; font-weight: bold; }}
td {{ padding: 5px 8px; border-bottom: 1px solid #eee; }}
tr:nth-child(even) {{ background: #f9f9f9; }}
.footer {{ margin-top: 20px; font-size: 8px; color: #999; text-align: center; }}
</style></head><body>
<h1>{title}</h1>
<p>Период: {period} | Сгенерировано: {datetime.utcnow().strftime("%d.%m.%Y %H:%M")}</p>
<table><tr>'''
        for h in headers:
            html += f'<th>{h}</th>'
        html += '</tr>'
        for row in rows:
            html += '<tr>'
            for val in row:
                html += f'<td>{val}</td>'
            html += '</tr>'
        html += f'</table><div class="footer">CRM Мастерская — {title} — {period}</div></body></html>'
        
        try:
            import pdfkit
            pdf = pdfkit.from_string(html, False)
            from flask import Response
            return Response(pdf, mimetype='application/pdf',
                headers={'Content-Disposition': f'attachment;filename={filename}.pdf'})
        except Exception:
            # Fallback: return HTML that can be printed as PDF
            from flask import Response
            return Response(html, mimetype='text/html',
                headers={'Content-Disposition': f'attachment;filename={filename}.html'})
    
    return jsonify({'error': 'Unknown format'}), 400

@app.route('/reports/period', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def report_period():
    if request.method == 'POST':
        try:
            d_from = datetime.strptime(request.form['date_from'], '%Y-%m-%d')
            d_to = datetime.strptime(request.form['date_to'], '%Y-%m-%d') + timedelta(days=1)
        except (ValueError, KeyError):
            flash(_('Invalid date format'), 'error')
            return redirect(url_for('report_period'))
    else:
        d_to = datetime.utcnow()
        d_from = d_to - timedelta(days=30)
    orders = Opdracht.query.filter(Opdracht.aangemaakt >= d_from, Opdracht.aangemaakt < d_to).order_by(Opdracht.aangemaakt.desc()).all()
    omzet = sum(o.totaal for o in orders if o.status == 'afgeleverd')
    voltooid = len([o for o in orders if o.status == 'afgeleverd'])
    geannuleerd = len([o for o in orders if o.status == 'geannuleerd'])
    # Pre-fetch all monteurs in one query
    monteur_ids = list(set(o.monteur_id for o in orders if o.monteur_id))
    monteurs_map = {m.id: m.naam for m in Monteur.query.filter(Monteur.id.in_(monteur_ids)).all()} if monteur_ids else {}
    ws = {}
    for o in orders:
        if o.monteur_id:
            if o.monteur_id not in ws:
                ws[o.monteur_id] = {'naam': monteurs_map.get(o.monteur_id, 'Onbekend'), 'orders': 0, 'omzet': 0, 'voltooid': 0}
            ws[o.monteur_id]['orders'] += 1
            if o.status == 'afgeleverd':
                ws[o.monteur_id]['omzet'] += o.totaal
                ws[o.monteur_id]['voltooid'] += 1
    return render_template('report_period.html', orders=orders,
                         date_from=d_from.strftime('%Y-%m-%d'),
                         date_to=(d_to - timedelta(days=1)).strftime('%Y-%m-%d'),
                         total_revenue=omzet, total_orders=len(orders),
                         completed=voltooid, cancelled=geannuleerd, worker_stats=ws)

@app.route('/reports/worker/<int:worker_id>')
@login_required
@role_required('admin', 'director', 'technician')
def report_worker(worker_id):
    w = Monteur.query.get_or_404(worker_id)
    df = request.args.get('date_from', (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d'))
    dt = request.args.get('date_to', datetime.utcnow().strftime('%Y-%m-%d'))
    orders = Opdracht.query.filter(Opdracht.monteur_id == worker_id,
        Opdracht.aangemaakt >= datetime.strptime(df, '%Y-%m-%d'),
        Opdracht.aangemaakt < datetime.strptime(dt, '%Y-%m-%d') + timedelta(days=1)
    ).order_by(Opdracht.aangemaakt.desc()).all()
    omzet = sum(o.totaal for o in orders if o.status == 'afgeleverd')
    voltooid = len([o for o in orders if o.status == 'afgeleverd'])
    avg = 0
    gereed = [o for o in orders if o.gereed and o.gestart]
    if gereed: avg = sum((o.gereed - o.gestart).total_seconds()/3600 for o in gereed) / len(gereed)
    return render_template('report_worker.html', worker=w, orders=orders, date_from=df, date_to=dt,
                         total_revenue=omzet, completed=voltooid, avg_time=round(avg,1))


# ============================================================
# ROUTES — QR CODE (existing)
# ============================================================

@app.route('/qr/scan')
@login_required
def qr_scan():
    return render_template('qr_scan.html')

@app.route('/scanner')
@login_required
def universal_scanner():
    """Scanner hub — links to warehouse, machine, and cylinder scanners"""
    return render_template('universal_scanner.html')

@app.route('/warehouse/scan')
@login_required
def warehouse_scan_page():
    """Dedicated warehouse barcode scanner"""
    return render_template('warehouse_scan.html')

@app.route('/qr/generate/<int:order_id>')
@login_required
def qr_generate(order_id):
    order = Opdracht.query.get_or_404(order_id)
    data = {
        'type': 'opdracht',
        'id': order.id,
        'nummer': order.nummer,
        'apparaat': order.apparaat,
        'model': order.model or '',
        'serienummer': order.serienummer or '',
        'klant': order.verantwoordelijke.naam,
        'status': order.status
    }
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(json.dumps(data, ensure_ascii=False))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'QR_{order.nummer}.png')


@app.route('/machines/<int:machine_id>/qr')
@login_required
def machine_qr_generate(machine_id):
    """Generate QR code for a machine — links to /mobile/qr/<id>."""
    m = Machine.query.get_or_404(machine_id)
    url = request.host_url.rstrip('/') + url_for('machine_qr_page', machine_id=m.id)
    qr = qrcode.QRCode(version=None, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'QR_{m.name.replace(" ", "_")}.png')


@app.route('/machines/qr/batch')
@login_required
@role_required('admin', 'director')
def machines_qr_batch():
    """Generate printable page with QR codes for all active machines."""
    machines = Machine.query.filter_by(status='active').order_by(Machine.name).all()
    return render_template('machines_qr_batch.html', machines=machines)


@app.route('/api/machines/search')
@login_required
def api_machines_search():
    """Search machines by name, serial number, or barcode"""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    machines = Machine.query.filter(
        db.or_(
            Machine.name.ilike(f'%{sanitize_like(q)}%'),
            Machine.serial_number.ilike(f'%{sanitize_like(q)}%'),
            Machine.machine_type.ilike(f'%{sanitize_like(q)}%')
        )
    ).limit(10).all()
    return jsonify([{
        'id': m.id,
        'name': m.name,
        'type': m.machine_type or '',
        'serial': m.serial_number or '',
        'status': m.status
    } for m in machines])

@app.route('/api/machine/<int:machine_id>/faults')
@login_required
def api_machine_faults(machine_id):
    """Return faults and stats for a machine (used by floor plan)"""
    m = Machine.query.get_or_404(machine_id)
    faults = FaultReport.query.filter_by(machine_id=m.id).order_by(FaultReport.created_at.desc()).limit(10).all()

    # Single aggregated query for stats
    stats = db.session.query(
        func.count().label('total'),
        func.sum(case((FaultReport.status.in_(['open', 'accepted', 'in_progress']), 1), else_=0)).label('open'),
        func.sum(case((FaultReport.status == 'resolved', 1), else_=0)).label('resolved'),
        func.sum(case((FaultReport.priority == 'critical', 1), else_=0)).label('critical'),
    ).filter(FaultReport.machine_id == m.id).first()

    faults_data = [{
        'id': f.id,
        'title': f.title or '',
        'status': f.status or '',
        'priority': f.priority or '',
        'date': f.created_at.strftime('%d-%m-%Y'),
    } for f in faults]

    return jsonify({
        'total': stats.total if stats else 0,
        'open': int(stats.open or 0) if stats else 0,
        'resolved': int(stats.resolved or 0) if stats else 0,
        'critical': int(stats.critical or 0) if stats else 0,
        'faults': faults_data
    })

@app.route('/api/machines/faults-batch')
@login_required
def api_machines_faults_batch():
    """Return fault stats for all machines in one query (replaces N+1 calls on floor plan)."""
    stats = db.session.query(
        FaultReport.machine_id,
        func.count().label('total'),
        func.sum(case((FaultReport.status.in_(['open', 'accepted', 'in_progress']), 1), else_=0)).label('open'),
        func.sum(case((FaultReport.status == 'resolved', 1), else_=0)).label('resolved'),
        func.sum(case((FaultReport.priority == 'critical', 1), else_=0)).label('critical'),
    ).group_by(FaultReport.machine_id).all()
    result = {}
    for row in stats:
        result[str(row.machine_id)] = {
            'total': row.total,
            'open': int(row.open or 0),
            'resolved': int(row.resolved or 0),
            'critical': int(row.critical or 0),
        }
    return jsonify(result)

@app.route('/api/machines/<int:machine_id>/qr')
@login_required
def machine_qr(machine_id):
    m = Machine.query.get_or_404(machine_id)
    data = {
        'type': 'machine',
        'id': m.id,
        'name': m.name,
        'serial': m.serial_number or '',
        'manufacturer': m.manufacturer or '',
        'location': m.installation_location or '',
        'section': m.section.name if m.section else ''
    }
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(json.dumps(data, ensure_ascii=False))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'QR_{m.name}.png')

@app.route('/api/machines/<int:machine_id>/barcode')
@login_required
def machine_barcode(machine_id):
    from PIL import Image, ImageDraw, ImageFont
    m = Machine.query.get_or_404(machine_id)
    code_str = f"M{m.id:05d}"
    
    try:
        import barcode
        from barcode.writer import ImageWriter
        code128 = barcode.get('code128', code_str, writer=ImageWriter())
        buf = io.BytesIO()
        code128.write(buf, options={'module_width': 0.3, 'module_height': 8, 'font_size': 8, 'text_distance': 2, 'quiet_zone': 2})
        buf.seek(0)
        return send_file(buf, mimetype='image/png', download_name=f'BAR_{m.name}.png')
    except ImportError:
        pass
    
    # Fallback: generate with Pillow
    def code128_encode(text):
        chars = ' !"#$%&\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~'
        codes = [104]
        checksum = 104
        for i, c in enumerate(text):
            if c in chars:
                val = chars.index(c) + 32
                codes.append(val)
                checksum += val * (i + 1)
        codes.append(checksum % 103)
        codes.append(106)
        patterns = [
            '11011001100','11001101100','11001100110','10010011000','10010001100',
            '10001001100','10011001000','10011000100','10001100100','11001001000',
            '11001000100','11000100100','10110011100','10011011100','10011001110',
            '10111001100','10011101100','10011100110','11001110010','11001011100',
            '11001001110','11011100100','11001110100','11101101110','11101001100',
            '11100101100','11100100110','11101100100','11100110100','11100110010',
            '11011011000','11011000110','11000110110','10100011000','10001011000',
            '10001000110','10110001000','10001101000','10001100010','11010001000',
            '11000101000','11000100010','10110111000','10110001110','10001101110',
            '10111011000','10111000110','10001110110','11101110110','11010001110',
            '11000101110','11011101000','11011100010','11011101110','11101011000',
            '11101000110','11100010110','11101101000','11101100010','11100011010',
            '11101111010','11001000010','11110001010','10100110000','10100001100',
            '10010110000','10010000110','10000101100','10000100110','10110010000',
            '10110000100','10011010000','10011000010','10000110100','10000110010',
            '11000010010','11001010000','11110111010','11000010100','10001111010',
            '10100111100','10010111100','10010011110','10111100100','10011110100',
            '10011110010','11110100100','11110010100','11110010010','11011011110',
            '11011110110','11110110110','10101111000','10100011110','10001011110',
            '10111101000','10111100010','11110101000','11110100010','10111011110',
            '10111101110','11101011110','11110101110','11010000100','11010010000',
            '11010011100','1100011101011'
        ]
        bars = []
        for c in codes:
            if c < len(patterns):
                bars.append(patterns[c])
        return bars
    
    bar_width = 2
    height = 60
    text_height = 16
    bars = code128_encode(code_str)
    total_width = sum(len(b) for b in bars) * bar_width + 20
    img = Image.new('RGB', (total_width, height + text_height + 4), 'white')
    draw = ImageDraw.Draw(img)
    x = 10
    for bar_pattern in bars:
        for bit in bar_pattern:
            if bit == '1':
                draw.rectangle([x, 0, x + bar_width - 1, height - 1], fill='black')
            x += bar_width
    try:
        font = ImageFont.truetype("arial.ttf", 12)
    except (OSError, IOError):
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), code_str, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((total_width - tw) // 2, height + 2), code_str, fill='black', font=font)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'BAR_{m.name}.png')

@app.route('/machines/<int:machine_id>/qr-label')
@login_required
def machine_qr_label(machine_id):
    m = Machine.query.get_or_404(machine_id)
    return render_template('machine_qr_label.html', machine=m)

@app.route('/qr/product', methods=['POST'])
@login_required
def qr_product():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Geen data'}), 400
    klant = None
    if data.get('klant'):
        klant = Verantwoordelijke.query.filter(Verantwoordelijke.naam.ilike(f"%{sanitize_like(data['klant'])}%")).first()
    if not klant and data.get('telefoon'):
        klant = Verantwoordelijke.query.filter(Verantwoordelijke.telefoon.like(f"%{sanitize_like(data['telefoon'])}%")).first()
    result = {
        'apparaat': data.get('apparaat', ''),
        'model': data.get('model', ''),
        'serienummer': data.get('serienummer', ''),
        'probleem': data.get('probleem', ''),
        'klant_id': klant.id if klant else None,
        'klant_naam': klant.naam if klant else data.get('klant', '')
    }
    return jsonify(result)

@app.route('/api/qr/lookup', methods=['POST'])
@login_required
def qr_lookup():
    data = request.get_json()
    code = data.get('code', '')
    try:
        parsed = json.loads(code)
        if parsed.get('type') == 'opdracht':
            order = Opdracht.query.get(parsed.get('id'))
            if order:
                return jsonify({
                    'found': True, 'type': 'opdracht',
                    'id': order.id, 'nummer': order.nummer,
                    'apparaat': order.apparaat, 'model': order.model,
                    'klant': order.verantwoordelijke.naam, 'status': order.status,
                    'totaal': order.totaal
                })
    except (json.JSONDecodeError, AttributeError):
        pass
    order = Opdracht.query.filter_by(serienummer=code).order_by(Opdracht.aangemaakt.desc()).first()
    if order:
        return jsonify({
            'found': True, 'type': 'serienummer',
            'id': order.id, 'nummer': order.nummer,
            'apparaat': order.apparaat, 'model': order.model,
            'klant': order.verantwoordelijke.naam, 'status': order.status
        })
    klant = Verantwoordelijke.query.filter(Verantwoordelijke.telefoon.like(f"%{sanitize_like(code)}%")).first()
    if klant:
        orders = Opdracht.query.filter_by(responsible_id=klant.id).order_by(Opdracht.aangemaakt.desc()).limit(5).all()
        return jsonify({
            'found': True, 'type': 'klant',
            'klant': klant.naam, 'telefoon': klant.telefoon,
            'orders': [{'id': o.id, 'nummer': o.nummer, 'apparaat': o.apparaat, 'status': o.status} for o in orders]
        })
    return jsonify({'found': False, 'code': code})

# ============================================================
# ROUTES — WAREHOUSE QR & PARTS SEARCH
# ============================================================

@app.route('/api/warehouse/search')
@login_required
def warehouse_search():
    q = request.args.get('q', '')
    if not q:
        items = VoorraadItem.query.order_by(VoorraadItem.naam).limit(50).all()
    else:
        items = VoorraadItem.query.filter(
            (VoorraadItem.naam.ilike(f'%{sanitize_like(q)}%')) | 
            (VoorraadItem.categorie.ilike(f'%{sanitize_like(q)}%'))
        ).order_by(VoorraadItem.naam).all()
    return jsonify([{
        'id': i.id, 'name': i.naam, 'category': i.categorie or '',
        'quantity': i.hoeveelheid, 'unit': i.eenheid, 'price': i.prijs,
        'available': i.hoeveelheid > 0
    } for i in items])

@app.route('/api/warehouse/qr/<int:item_id>')
@login_required
def warehouse_qr(item_id):
    item = VoorraadItem.query.get_or_404(item_id)
    data = {
        'type': 'warehouse_item',
        'id': item.id,
        'name': item.naam,
        'category': item.categorie or '',
        'location': item.locatie or '',
        'quantity': item.hoeveelheid,
        'unit': item.eenheid
    }
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(json.dumps(data, ensure_ascii=False))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'QR_{item.naam}.png')

@app.route('/api/warehouse/barcode/<int:item_id>')
@login_required
def warehouse_barcode(item_id):
    from PIL import Image, ImageDraw, ImageFont
    item = VoorraadItem.query.get_or_404(item_id)
    code_str = item.supplier_part_number if item.supplier_part_number else f"W{item.id:05d}"
    
    try:
        import barcode
        from barcode.writer import ImageWriter
        code128 = barcode.get('code128', code_str, writer=ImageWriter())
        buf = io.BytesIO()
        code128.write(buf, options={'module_width': 0.3, 'module_height': 8, 'font_size': 8, 'text_distance': 2, 'quiet_zone': 2})
        buf.seek(0)
        return send_file(buf, mimetype='image/png', download_name=f'BAR_{item.naam}.png')
    except ImportError:
        # Fallback: generate barcode with Pillow using Code128B encoding
        pass
    
    # Code128 encoding table (subset for alphanumeric)
    def code128_encode(text):
        # Code128B character set
        chars = ' !"#$%&\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~'
        # Start code B = 104, Stop = 106
        codes = [104]  # Start B
        checksum = 104
        for i, c in enumerate(text):
            if c in chars:
                val = chars.index(c) + 32
                codes.append(val)
                checksum += val * (i + 1)
            else:
                codes.append(0)  # fallback
        codes.append(checksum % 103)
        codes.append(106)  # Stop
        
        # Code128 bar patterns (widths of bars and spaces)
        patterns = [
            '11011001100','11001101100','11001100110','10010011000','10010001100',
            '10001001100','10011001000','10011000100','10001100100','11001001000',
            '11001000100','11000100100','10110011100','10011011100','10011001110',
            '10111001100','10011101100','10011100110','11001110010','11001011100',
            '11001001110','11011100100','11001110100','11101101110','11101001100',
            '11100101100','11100100110','11101100100','11100110100','11100110010',
            '11011011000','11011000110','11000110110','10100011000','10001011000',
            '10001000110','10110001000','10001101000','10001100010','11010001000',
            '11000101000','11000100010','10110111000','10110001110','10001101110',
            '10111011000','10111000110','10001110110','11101110110','11010001110',
            '11000101110','11011101000','11011100010','11011101110','11101011000',
            '11101000110','11100010110','11101101000','11101100010','11100011010',
            '11101111010','11001000010','11110001010','10100110000','10100001100',
            '10010110000','10010000110','10000101100','10000100110','10110010000',
            '10110000100','10011010000','10011000010','10000110100','10000110010',
            '11000010010','11001010000','11110111010','11000010100','10001111010',
            '10100111100','10010111100','10010011110','10111100100','10011110100',
            '10011110010','11110100100','11110010100','11110010010','11011011110',
            '11011110110','11110110110','10101111000','10100011110','10001011110',
            '10111101000','10111100010','11110101000','11110100010','10111011110',
            '10111101110','11101011110','11110101110','11010000100','11010010000',
            '11010011100','1100011101011'
        ]
        bars = []
        for c in codes:
            if c < len(patterns):
                bars.append(patterns[c])
        return bars
    
    # Generate image
    bar_width = 2
    height = 60
    text_height = 16
    total_height = height + text_height + 4
    
    bars = code128_encode(code_str)
    total_width = sum(len(b) for b in bars) * bar_width + 20  # margins
    
    img = Image.new('RGB', (total_width, total_height), 'white')
    draw = ImageDraw.Draw(img)
    
    x = 10
    for bar_pattern in bars:
        for i, bit in enumerate(bar_pattern):
            if bit == '1':
                draw.rectangle([x, 0, x + bar_width - 1, height - 1], fill='black')
            x += bar_width
    
    # Draw text
    try:
        font = ImageFont.truetype("arial.ttf", 12)
    except (OSError, IOError):
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), code_str, font=font)
    text_width = bbox[2] - bbox[0]
    text_x = (total_width - text_width) // 2
    draw.text((text_x, height + 2), code_str, fill='black', font=font)
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'BAR_{item.naam}.png')

@app.route('/api/warehouse/scan', methods=['POST'])
@login_required
def warehouse_scan():
    data = request.get_json()
    code = data.get('code', '')
    try:
        parsed = json.loads(code)
        if parsed.get('type') == 'warehouse_item':
            item = VoorraadItem.query.get(parsed.get('id'))
            if item:
                return jsonify({
                    'found': True, 'id': item.id, 'name': item.naam,
                    'category': item.categorie, 'quantity': item.hoeveelheid,
                    'unit': item.eenheid, 'price': item.prijs,
                    'available': item.hoeveelheid > 0
                })
    except (json.JSONDecodeError, AttributeError):
        pass
    # Search by supplier part number (barcode), then by name
    item = VoorraadItem.query.filter(
        db.or_(
            VoorraadItem.supplier_part_number == code,
            VoorraadItem.naam.ilike(f'%{sanitize_like(code)}%')
        )
    ).first()
    if item:
        return jsonify({
            'found': True, 'id': item.id, 'name': item.naam,
            'category': item.categorie, 'quantity': item.hoeveelheid,
            'unit': item.eenheid, 'price': item.prijs,
            'available': item.hoeveelheid > 0
        })
    return jsonify({'found': False, 'code': code})

# ============================================================
# ROUTES — PURCHASE REQUESTS
# ============================================================

@app.route('/purchase-requests')
@login_required
@role_required('admin', 'director', 'technician')
def purchase_requests_list():
    if current_user.has_role('admin', 'director'):
        requests = PurchaseRequest.query.order_by(PurchaseRequest.created_at.desc()).all()
    else:
        requests = PurchaseRequest.query.filter_by(requester_id=current_user.id).order_by(PurchaseRequest.created_at.desc()).all()
    return render_template('purchase_requests.html', requests=requests)

@app.route('/purchase-requests/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def purchase_request_new():
    if request.method == 'POST':
        try:
            machine_id = int(request.form['machine_id'])
        except (ValueError, KeyError):
            flash(_('Invalid machine'), 'error')
            return redirect(url_for('purchase_request_new'))
        pr = PurchaseRequest(
            fault_id=request.form.get('fault_id') or None,
            machine_id=machine_id,
            requester_id=current_user.id,
            machine_serial=request.form.get('machine_serial', ''),
            fault_number=request.form.get('fault_number', ''),
            fault_description=request.form.get('fault_description', ''),
            part_name=request.form['part_name'],
            part_catalog=request.form.get('part_catalog', ''),
            quantity=safe_float(request.form.get('quantity'), 1),
            unit=request.form.get('unit', 'st'),
            urgency=request.form.get('urgency', 'normal'),
            reason=request.form.get('reason', '')
        )
        db.session.add(pr)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('purchase_request_new'))
        
        log_audit('create', 'purchase_request', pr.id, f'{pr.part_name} x{pr.quantity} — {pr.machine.name} (срочность: {pr.urgency})')
        add_work_report(f'🛒 Новая заявка: {pr.part_name} x{pr.quantity} — {pr.machine.name} (срочность: {pr.urgency})')
        
        admins = User.query.filter(User.role.in_(['admin', 'director']), User.is_active_user == True).all()
        for admin in admins:
            create_notification(
                admin.id,
                _('New purchase request'),
                f"{pr.part_name} x{pr.quantity} — {pr.machine.name}",
                'fault',
                url_for('purchase_request_detail', request_id=pr.id)
            )
        
        flash(_('Purchase request created'), 'success')
        return redirect(url_for('purchase_requests_list'))
    
    if current_user.has_role('admin', 'director', 'technician'):
        machines = Machine.query.all()
    else:
        machines = current_user.assigned_machines
    faults = FaultReport.query.filter(
        (FaultReport.reporter_id == current_user.id) | (FaultReport.technician_id == current_user.id)
    ).order_by(FaultReport.created_at.desc()).limit(20).all()
    return render_template('purchase_request_form.html', machines=machines, faults=faults)

@app.route('/purchase-requests/<int:request_id>')
@login_required
@role_required('admin', 'director', 'technician')
def purchase_request_detail(request_id):
    pr = PurchaseRequest.query.get_or_404(request_id)
    return render_template('purchase_request_detail.html', pr=pr)

@app.route('/purchase-requests/<int:request_id>/approve', methods=['POST'])
@login_required
@role_required('admin', 'director')
def purchase_request_approve(request_id):
    pr = PurchaseRequest.query.get_or_404(request_id)
    pr.status = 'approved'
    pr.reviewed_at = datetime.utcnow()
    pr.reviewer_id = current_user.id
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(request.referrer or '/')
    
    log_audit('approve', 'purchase_request', pr.id, f'{pr.part_name} x{pr.quantity} — {pr.machine.name}')
    add_work_report(f'✅ Заявка одобрена: {pr.part_name} x{pr.quantity} — {pr.machine.name}')
    
    create_notification(
        pr.requester_id,
        _('Purchase request approved'),
        f"{pr.part_name} x{pr.quantity} — {pr.machine.name}",
        'info',
        url_for('purchase_request_detail', request_id=pr.id)
    )
    
    flash(_('Purchase request approved'), 'success')
    return redirect(url_for('purchase_request_detail', request_id=pr.id))

@app.route('/purchase-requests/<int:request_id>/reject', methods=['POST'])
@login_required
@role_required('admin', 'director')
def purchase_request_reject(request_id):
    pr = PurchaseRequest.query.get_or_404(request_id)
    pr.status = 'rejected'
    pr.reviewed_at = datetime.utcnow()
    pr.reviewer_id = current_user.id
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(request.referrer or '/')
    
    log_audit('reject', 'purchase_request', pr.id, f'{pr.part_name} x{pr.quantity} — {pr.machine.name}')
    add_work_report(f'❌ Заявка отклонена: {pr.part_name} x{pr.quantity} — {pr.machine.name}')
    
    create_notification(
        pr.requester_id,
        _('Purchase request rejected'),
        f"{pr.part_name} x{pr.quantity} — {pr.machine.name}",
        'warning',
        url_for('purchase_request_detail', request_id=pr.id)
    )
    
    flash(_('Purchase request rejected'), 'error')
    return redirect(url_for('purchase_request_detail', request_id=pr.id))

# ============================================================
# ROUTES — WORK SCHEDULE & TIME TRACKING
# ============================================================

@app.route('/schedule')
@login_required
@role_required('admin', 'director', 'technician')
def schedule_list():
    monteurs = Monteur.query.filter_by(actief=True).order_by(Monteur.naam).all()
    # Only monteurs with linked user accounts can have schedules
    monteur_user_ids = [m.user_id for m in monteurs if m.user_id]
    schedules = WorkSchedule.query.filter(WorkSchedule.user_id.in_(monteur_user_ids)).all() if monteur_user_ids else []
    return render_template('schedule.html', monteurs=monteurs, schedules=schedules)

@app.route('/schedule/<int:user_id>', methods=['GET', 'POST'])
@login_required
def schedule_user(user_id):
    if not current_user.has_role('admin', 'director') and current_user.id != user_id:
        flash(_('Access denied'), 'error')
        return redirect(url_for('schedule_list'))
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        work_days = ','.join(request.form.getlist('work_days'))
        s = WorkSchedule(
            user_id=user.id,
            name=request.form['name'],
            shift_start=request.form['shift_start'],
            shift_end=request.form['shift_end'],
            break_minutes=safe_int(request.form.get('break_minutes'), 60),
            work_days=work_days or '1,2,3,4,5'
        )
        db.session.add(s)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('schedule_list'))
        flash(_('Schedule created'), 'success')
    schedules = WorkSchedule.query.filter_by(user_id=user.id).all()
    return render_template('schedule_user.html', user=user, schedules=schedules)

@app.route('/schedule/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director')
def schedule_delete(user_id):
    user = User.query.get_or_404(user_id)
    deleted = WorkSchedule.query.filter_by(user_id=user.id).delete()
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('schedule_list'))
    flash(_('Schedule deleted for') + ' ' + (user.display_name or user.username) + f' ({deleted})', 'success')
    return redirect(url_for('schedule_list'))

# Belgian public holidays (fixed + Easter-based)
# Belgian holidays helpers live in utils.get_belgian_holidays

@app.route('/schedule/monthly')
@login_required
@role_required('admin', 'director')
def schedule_monthly():
    year = safe_int(request.args.get('year'), datetime.utcnow().year)
    month = safe_int(request.args.get('month'), datetime.utcnow().month)
    filter_user = request.args.get('user', '')
    if month < 1: month = 12; year -= 1
    if month > 12: month = 1; year += 1

    first_day = datetime(year, month, 1).date()
    if month == 12:
        last_day = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        last_day = datetime(year, month + 1, 1).date() - timedelta(days=1)

    all_monteurs = Monteur.query.filter_by(actief=True).order_by(Monteur.naam).all()
    # Only monteurs with linked user accounts can appear in schedule
    # Exclude workers fired before the start of the displayed month
    all_users = [m.user for m in all_monteurs if m.user and m.user.is_active_user
                 and not (m.fire_date and m.fire_date < first_day)]
    if filter_user:
        users = [u for u in all_users if str(u.id) == filter_user]
    else:
        users = all_users

    # Get all weekend shifts for this month
    shifts = WeekendShift.query.filter(
        WeekendShift.date >= first_day,
        WeekendShift.date <= last_day
    ).all()
    shift_map = {}
    for s in shifts:
        key = (s.user_id, s.date)
        shift_map[key] = s

    # Get work schedules for each user
    user_schedules = {}
    all_schedules = WorkSchedule.query.filter(WorkSchedule.user_id.in_([u.id for u in users]), WorkSchedule.is_active == True).all()
    for s in all_schedules:
        if s.user_id not in user_schedules:
            user_schedules[s.user_id] = s

    # Get Belgian holidays
    holidays = get_belgian_holidays(year)

    # Build days list
    days = []
    current = first_day
    while current <= last_day:
        days.append({
            'date': current,
            'day': current.day,
            'weekday': current.weekday(),  # 0=Mon, 6=Sun
            'is_weekend': current.weekday() >= 5,
            'is_holiday': current in holidays,
            'holiday_name': holidays.get(current, ''),
        })
        current += timedelta(days=1)

    return render_template('schedule_monthly.html',
        users=users, days=days, year=year, month=month,
        shift_map=shift_map, holidays=holidays, user_schedules=user_schedules,
        all_users=all_users, filter_user=filter_user)

@app.route('/schedule/monthly/shift', methods=['POST'])
@login_required
@role_required('admin', 'director')
def schedule_monthly_shift():
    data = request.get_json()
    user_id = data.get('user_id')
    date_str = data.get('date')
    action = data.get('action')  # 'add' or 'remove'
    shift_type = data.get('shift_type', 'full')

    date = datetime.strptime(date_str, '%Y-%m-%d').date()
    existing = WeekendShift.query.filter_by(user_id=user_id, date=date).first()

    if action == 'add':
        if existing:
            existing.shift_type = shift_type
        else:
            s = WeekendShift(user_id=user_id, date=date, shift_type=shift_type, created_by=current_user.id)
            db.session.add(s)
    elif action == 'remove' and existing:
        db.session.delete(existing)

    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True})

@app.route('/schedule/monthly/delete', methods=['POST'])
@login_required
@role_required('admin', 'director')
def schedule_monthly_delete():
    data = request.get_json()
    user_id = data.get('user_id')
    year = data.get('year')
    month = data.get('month')
    first_day = datetime(year, month, 1).date()
    if month == 12:
        last_day = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        last_day = datetime(year, month + 1, 1).date() - timedelta(days=1)
    deleted = WeekendShift.query.filter(
        WeekendShift.user_id == user_id,
        WeekendShift.date >= first_day,
        WeekendShift.date <= last_day
    ).delete()
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'deleted': deleted})

@app.route('/time-tracking')
@login_required
def time_tracking():
    if current_user.has_role('admin', 'director'):
        users = User.query.filter(User.is_active_user == True, User.role.in_(['technician', 'user'])).all()
    else:
        users = [current_user]
    
    today = datetime.utcnow().date()
    month_start = today.replace(day=1)
    
    entries = TimeEntry.query.filter(
        TimeEntry.user_id.in_([u.id for u in users]),
        TimeEntry.date >= month_start,
        TimeEntry.date <= today
    ).order_by(TimeEntry.date.desc()).all()
    
    return render_template('time_tracking.html', users=users, entries=entries, today=today, month_start=month_start)

@app.route('/time-tracking/clock-in', methods=['POST'])
@login_required
def clock_in():
    today = datetime.utcnow().date()
    existing = TimeEntry.query.filter_by(user_id=current_user.id, date=today).first()
    if existing and existing.clock_in:
        flash(_('Already clocked in today'), 'error')
        return redirect(url_for('time_tracking'))
    
    if existing:
        existing.clock_in = datetime.utcnow()
        existing.status = 'present'
    else:
        entry = TimeEntry(
            user_id=current_user.id,
            date=today,
            clock_in=datetime.utcnow(),
            status='present'
        )
        db.session.add(entry)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('time_tracking'))
    flash(_('Clocked in at') + ' ' + datetime.utcnow().strftime('%H:%M'), 'success')
    return redirect(url_for('time_tracking'))

@app.route('/time-tracking/clock-out', methods=['POST'])
@login_required
def clock_out():
    today = datetime.utcnow().date()
    entry = TimeEntry.query.filter_by(user_id=current_user.id, date=today).first()
    if not entry or not entry.clock_in:
        flash(_('Not clocked in today'), 'error')
        return redirect(url_for('time_tracking'))
    if entry.clock_out:
        flash(_('Already clocked out today'), 'error')
        return redirect(url_for('time_tracking'))
    
    entry.clock_out = datetime.utcnow()
    delta = entry.clock_out - entry.clock_in
    hours = delta.total_seconds() / 3600
    entry.hours_worked = round(hours - (entry.break_minutes / 60), 2)
    
    # Calculate overtime (standard 8h)
    if entry.hours_worked > 8:
        entry.overtime_hours = round(entry.hours_worked - 8, 2)
    
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('time_tracking'))
    flash(_('Clocked out at') + ' ' + entry.clock_out.strftime('%H:%M') + '. ' + _('Hours worked') + ': ' + str(entry.hours_worked), 'success')
    return redirect(url_for('time_tracking'))

@app.route('/time-tracking/manual', methods=['POST'])
@login_required
@role_required('admin', 'director')
def time_tracking_manual():
    try:
        user_id = int(request.form['user_id'])
        date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
    except (ValueError, KeyError):
        flash(_('Invalid user or date'), 'error')
        return redirect(url_for('time_tracking'))
    status = request.form.get('status', 'present')
    
    entry = TimeEntry.query.filter_by(user_id=user_id, date=date).first()
    if not entry:
        entry = TimeEntry(user_id=user_id, date=date, status=status)
        db.session.add(entry)
    
    entry.status = status
    entry.notes = request.form.get('notes', '')
    
    if status == 'present':
        try:
            entry.clock_in = datetime.combine(date, datetime.strptime(request.form['clock_in'], '%H:%M').time())
            entry.clock_out = datetime.combine(date, datetime.strptime(request.form['clock_out'], '%H:%M').time())
        except (ValueError, KeyError):
            flash(_('Invalid time format (use HH:MM)'), 'error')
            return redirect(url_for('time_tracking'))
        delta = entry.clock_out - entry.clock_in
        hours = delta.total_seconds() / 3600
        entry.break_minutes = safe_int(request.form.get('break_minutes'), 60)
        entry.hours_worked = round(hours - (entry.break_minutes / 60), 2)
        if entry.hours_worked > 8:
            entry.overtime_hours = round(entry.hours_worked - 8, 2)
    
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('time_tracking'))
    flash(_('Time entry saved'), 'success')
    return redirect(url_for('time_tracking'))

@app.route('/vacations')
@login_required
@role_required('admin', 'director', 'technician')
def vacations_list():
    if current_user.has_role('admin', 'director'):
        vacations = Vacation.query.order_by(Vacation.created_at.desc()).all()
    else:
        vacations = Vacation.query.filter_by(user_id=current_user.id).order_by(Vacation.created_at.desc()).all()
    return render_template('vacations.html', vacations=vacations)

@app.route('/vacations/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def vacation_new():
    if request.method == 'POST':
        try:
            d_from = datetime.strptime(request.form['date_from'], '%Y-%m-%d').date()
            d_to = datetime.strptime(request.form['date_to'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid date format'), 'error')
            return redirect(url_for('vacation_new'))
        days = (d_to - d_from).days + 1
        v = Vacation(
            user_id=current_user.id,
            vacation_type=request.form['vacation_type'],
            date_from=d_from,
            date_to=d_to,
            days_count=days,
            reason=request.form.get('reason', '')
        )
        db.session.add(v)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('vacation_new'))
        
        admins = User.query.filter(User.role.in_(['admin', 'director']), User.is_active_user == True).all()
        for admin in admins:
            create_notification(
                admin.id,
                _('New vacation request'),
                f"{current_user.display_name}: {v.vacation_type} {v.date_from} - {v.date_to}",
                'info',
                url_for('vacations_list')
            )
        
        flash(_('Vacation request submitted'), 'success')
        return redirect(url_for('vacations_list'))
    return render_template('vacation_form.html')

@app.route('/vacations/<int:vacation_id>/approve', methods=['POST'])
@login_required
@role_required('admin', 'director')
def vacation_approve(vacation_id):
    v = Vacation.query.get_or_404(vacation_id)
    v.status = 'approved'
    v.approved_by = current_user.id
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('vacations_list'))
    create_notification(v.user_id, _('Vacation approved'), f"{v.vacation_type} {v.date_from} - {v.date_to}", 'info')
    flash(_('Vacation approved'), 'success')
    return redirect(url_for('vacations_list'))

@app.route('/vacations/<int:vacation_id>/reject', methods=['POST'])
@login_required
@role_required('admin', 'director')
def vacation_reject(vacation_id):
    v = Vacation.query.get_or_404(vacation_id)
    v.status = 'rejected'
    v.approved_by = current_user.id
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('vacations_list'))
    create_notification(v.user_id, _('Vacation rejected'), f"{v.vacation_type} {v.date_from} - {v.date_to}", 'warning')
    flash(_('Vacation rejected'), 'error')
    return redirect(url_for('vacations_list'))

@app.route('/time-report/<int:user_id>')
@login_required
def time_report(user_id):
    if not current_user.has_role('admin', 'director') and current_user.id != user_id:
        flash(_('Access denied'), 'error')
        return redirect(url_for('time_tracking'))
    
    user = User.query.get_or_404(user_id)
    month = request.args.get('month', datetime.utcnow().strftime('%Y-%m'))
    year, mon = map(int, month.split('-'))
    start = datetime(year, mon, 1).date()
    if mon == 12:
        end = datetime(year + 1, 1, 1).date()
    else:
        end = datetime(year, mon + 1, 1).date()
    
    entries = TimeEntry.query.filter(
        TimeEntry.user_id == user_id,
        TimeEntry.date >= start,
        TimeEntry.date < end
    ).order_by(TimeEntry.date).all()
    
    total_hours = sum(e.hours_worked for e in entries)
    total_overtime = sum(e.overtime_hours for e in entries)
    days_present = len([e for e in entries if e.status == 'present'])
    days_absent = len([e for e in entries if e.status in ['absent', 'sick']])
    
    vacations = Vacation.query.filter(
        Vacation.user_id == user_id,
        Vacation.status == 'approved',
        Vacation.date_from < end,
        Vacation.date_to >= start
    ).all()
    
    return render_template('time_report.html', user=user, entries=entries, month=month,
                         total_hours=total_hours, total_overtime=total_overtime,
                         days_present=days_present, days_absent=days_absent, vacations=vacations)

# ============================================================
# ROUTES — TRANSLATION API
# ============================================================

@app.route('/api/translate', methods=['POST'])
@login_required
def api_translate():
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400
    text = data['text']
    target = data.get('target', g.lang)
    translated = translate_text(text, target)
    return jsonify({'translated': translated, 'original': text})

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
                create_notification(p.responsible_user_id, title, msg, 'warning', url_for('machine_parts', machine_id=p.machine_id))
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
                create_notification(admin.id, title, msg, 'warning', url_for('warehouse_list'))
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
                create_notification(admin.id, title, msg, 'warning', url_for('contractor_detail', contractor_id=c.id))
                results['contracts'] += 1

    log_audit('automation_check', details=json.dumps(results))
    return jsonify({'ok': True, 'results': results})

# ============================================================
# EXPORT — CSV/EXCEL REPORTS
# ============================================================

@app.route('/export/machines')
@login_required
@role_required('admin', 'director')
def export_machines():
    import csv
    import io
    machines = Machine.query.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Name', 'Type', 'Manufacturer', 'Serial', 'Status', 'Section', 'Contractor'])
    for m in machines:
        writer.writerow([m.id, m.name, m.machine_type, m.manufacturer, m.serial_number,
                         m.status, m.section.name if m.section else '',
                         m.contractor_rel.company_name if m.contractor_rel else ''])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                     mimetype='text/csv', as_attachment=True,
                     download_name=f'machines_{datetime.utcnow().strftime("%Y%m%d")}.csv')

@app.route('/export/warehouse')
@login_required
@role_required('admin', 'director')
def export_warehouse():
    import csv
    import io
    items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Name', 'Category', 'Group', 'Quantity', 'Unit', 'Min', 'Price', 'Location'])
    for i in items:
        writer.writerow([i.id, i.naam, i.categorie, i.group.name if i.group else '',
                         i.hoeveelheid, i.eenheid, i.minimum, i.prijs, i.locatie])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                     mimetype='text/csv', as_attachment=True,
                     download_name=f'warehouse_{datetime.utcnow().strftime("%Y%m%d")}.csv')

@app.route('/export/movements')
@login_required
@role_required('admin', 'director')
def export_movements():
    import csv
    import io
    item_id = request.args.get('item', '')
    move_type = request.args.get('type', '')
    q = VoorraadMutatie.query
    if item_id:
        q = q.filter_by(item_id=int(item_id))
    if move_type:
        q = q.filter_by(type=move_type)
    movements = q.order_by(VoorraadMutatie.aangemaakt.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Type', 'Item', 'Quantity', 'Unit', 'Order', 'Comment'])
    for m in movements:
        writer.writerow([
            m.aangemaakt.strftime('%Y-%m-%d %H:%M'),
            m.type,
            m.item.naam if m.item else '',
            m.hoeveelheid,
            m.item.eenheid if m.item else '',
            m.opdracht.nummer if m.opdracht else '',
            m.opmerking or ''
        ])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                     mimetype='text/csv', as_attachment=True,
                     download_name=f'movements_{datetime.utcnow().strftime("%Y%m%d")}.csv')

@app.route('/export/maintenance')
@login_required
@role_required('admin', 'director')
def export_maintenance():
    import csv
    import io
    records = MaintenanceRecord.query.order_by(MaintenanceRecord.date_performed.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Machine', 'Type', 'Description', 'Date', 'Cost', 'Next', 'Performed By'])
    for r in records:
        writer.writerow([r.id, r.machine.name, r.maintenance_type, r.description,
                         r.date_performed.strftime('%Y-%m-%d'), r.cost,
                         r.next_maintenance.strftime('%Y-%m-%d') if r.next_maintenance else '',
                         r.performer.display_name if r.performer else ''])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                     mimetype='text/csv', as_attachment=True,
                     download_name=f'maintenance_{datetime.utcnow().strftime("%Y%m%d")}.csv')

@app.route('/export/faults')
@login_required
@role_required('admin', 'director')
def export_faults():
    import csv
    import io
    faults = FaultReport.query.order_by(FaultReport.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Title', 'Machine', 'Priority', 'Status', 'Reporter', 'Technician', 'Created', 'Resolved'])
    for f in faults:
        tech = User.query.get(f.technician_id) if f.technician_id else None
        writer.writerow([f.id, f.title, f.target_name, f.priority, f.status,
                         f.reporter.display_name if f.reporter else '',
                         tech.display_name if tech else '',
                         f.created_at.strftime('%Y-%m-%d'),
                         f.resolved_at.strftime('%Y-%m-%d') if f.resolved_at else ''])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),
                     mimetype='text/csv', as_attachment=True,
                     download_name=f'faults_{datetime.utcnow().strftime("%Y%m%d")}.csv')

# ============================================================
# WORK REPORT — STORING
# ============================================================

@app.route('/work-report')
@login_required
@role_required('admin', 'director', 'technician')
def work_report_page():
    entries = WorkReportEntry.query.order_by(WorkReportEntry.created_at.desc()).limit(500).all()
    return render_template('work_report_page.html', entries=entries)

@app.route('/work-report/add', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def work_report_add():
    entry_text = request.form.get('entry', '').strip()
    if not entry_text:
        flash(_('Entry cannot be empty'), 'error')
        return redirect(url_for('work_report_page'))
    entry = WorkReportEntry(
        user_id=current_user.id,
        entry=entry_text
    )
    db.session.add(entry)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('work_report_page'))
    flash(_('Entry added'), 'success')
    return redirect(url_for('work_report_page'))

@app.route('/work-report/delete/<int:entry_id>', methods=['POST'])
@login_required
@role_required('admin')
def work_report_delete(entry_id):
    entry = WorkReportEntry.query.get_or_404(entry_id)
    db.session.delete(entry)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('work_report_page'))
    flash(_('Entry deleted'), 'success')
    return redirect(url_for('work_report_page'))


# ============================================================
# TOOL WEAR TRACKING
# ============================================================








# ============================================================
# MONTHLY ARCHIVE
# ============================================================




# ============================================================
# GLOBAL SEARCH
# ============================================================

@app.route('/api/search')
@login_required
def api_search():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'results': []})
    
    results = []
    limit = 20
    
    # Search machines
    machines = Machine.query.filter(
        (Machine.name.ilike(f'%{sanitize_like(q)}%')) | 
        (Machine.serial_number.ilike(f'%{sanitize_like(q)}%')) |
        (Machine.description.ilike(f'%{sanitize_like(q)}%'))
    ).limit(5).all()
    for m in machines:
        results.append({
            'type': 'machine',
            'icon': '⚙️',
            'title': m.name,
            'subtitle': f'{m.machine_type or ""} {m.serial_number or ""}'.strip(),
            'url': f'/machines/{m.id}',
            'status': m.status
        })
    
    # Search faults
    faults = FaultReport.query.filter(
        (FaultReport.title.ilike(f'%{sanitize_like(q)}%')) | 
        (FaultReport.description.ilike(f'%{sanitize_like(q)}%'))
    ).limit(5).all()
    for f in faults:
        results.append({
            'type': 'fault',
            'icon': '⚠️',
            'title': f.title,
            'subtitle': f'{getattr(f, "target_name", None) or (f.machine.name if f.machine else "")} - {f.priority}',
            'url': f'/faults/{f.id}',
            'status': f.status
        })
    
    # Search work orders
    orders = Opdracht.query.filter(
        (Opdracht.nummer.ilike(f'%{sanitize_like(q)}%')) | 
        (Opdracht.apparaat.ilike(f'%{sanitize_like(q)}%')) |
        (Opdracht.model.ilike(f'%{sanitize_like(q)}%')) |
        (Opdracht.serienummer.ilike(f'%{sanitize_like(q)}%'))
    ).limit(5).all()
    for o in orders:
        results.append({
            'type': 'order',
            'icon': '📋',
            'title': f'{o.nummer} - {o.apparaat}',
            'subtitle': f'{o.model or ""} | {o.verantwoordelijke.naam if o.verantwoordelijke else ""}',
            'url': f'/orders/{o.id}',
            'status': o.status
        })
    
    # Search warehouse
    items = VoorraadItem.query.filter(
        (VoorraadItem.naam.ilike(f'%{sanitize_like(q)}%')) | 
        (VoorraadItem.categorie.ilike(f'%{sanitize_like(q)}%')) |
        (VoorraadItem.locatie.ilike(f'%{sanitize_like(q)}%'))
    ).limit(5).all()
    for i in items:
        results.append({
            'type': 'warehouse',
            'icon': '📦',
            'title': i.naam,
            'subtitle': f'{i.categorie or ""} | {i.hoeveelheid} {i.eenheid}',
            'url': f'/warehouse/{i.id}/edit',
            'status': 'low' if i.hoeveelheid <= i.minimum else 'ok'
        })
    
    # Search clients/responsible
    clients = Verantwoordelijke.query.filter(
        (Verantwoordelijke.naam.ilike(f'%{sanitize_like(q)}%')) | 
        (Verantwoordelijke.company.ilike(f'%{sanitize_like(q)}%')) |
        (Verantwoordelijke.telefoon.ilike(f'%{sanitize_like(q)}%')) |
        (Verantwoordelijke.email.ilike(f'%{sanitize_like(q)}%'))
    ).limit(5).all()
    for c in clients:
        results.append({
            'type': 'client',
            'icon': '👤',
            'title': c.naam,
            'subtitle': f'{c.company or ""} {c.telefoon or ""}'.strip(),
            'url': f'/responsible/{c.id}',
            'status': 'active'
        })
    
    # Search workers
    workers = Monteur.query.filter(
        (Monteur.naam.ilike(f'%{sanitize_like(q)}%')) | 
        (Monteur.specialisatie.ilike(f'%{sanitize_like(q)}%'))
    ).limit(3).all()
    for w in workers:
        results.append({
            'type': 'worker',
            'icon': '🔧',
            'title': w.naam,
            'subtitle': w.specialisatie or '',
            'url': f'/workers/{w.id}/edit',
            'status': 'active' if w.actief else 'inactive'
        })
    
    # Search contractors
    contractors = Contractor.query.filter(
        (Contractor.company_name.ilike(f'%{sanitize_like(q)}%')) | 
        (Contractor.service_type.ilike(f'%{sanitize_like(q)}%'))
    ).limit(3).all()
    for c in contractors:
        results.append({
            'type': 'contractor',
            'icon': '🏢',
            'title': c.company_name,
            'subtitle': c.service_type or '',
            'url': f'/contractors/{c.id}',
            'status': 'active' if c.is_active else 'inactive'
        })
    
    # Search TWO
    twos = TechnicalWorkOrder.query.filter(
        (TechnicalWorkOrder.number.ilike(f'%{sanitize_like(q)}%')) | 
        (TechnicalWorkOrder.description.ilike(f'%{sanitize_like(q)}%'))
    ).limit(3).all()
    for t in twos:
        results.append({
            'type': 'two',
            'icon': '🔧',
            'title': t.number,
            'subtitle': t.description[:50] if t.description else '',
            'url': f'/two/{t.id}',
            'status': t.status
        })
    
    # Search users (admin only)
    if current_user.has_role('admin'):
        users = User.query.filter(
            (User.username.ilike(f'%{sanitize_like(q)}%')) | 
            (User.display_name.ilike(f'%{sanitize_like(q)}%')) |
            (User.first_name.ilike(f'%{sanitize_like(q)}%')) |
            (User.last_name.ilike(f'%{sanitize_like(q)}%'))
        ).limit(3).all()
        for u in users:
            results.append({
                'type': 'user',
                'icon': '👥',
                'title': u.display_name or u.username,
                'subtitle': f'{u.role} | {u.username}',
                'url': f'/users/{u.id}',
                'status': 'active' if u.is_active_user else 'inactive'
            })
    
    return jsonify({'results': results[:limit], 'total': len(results)})

@app.route('/search')
@login_required
def search_page():
    q = request.args.get('q', '').strip()
    return render_template('search.html', query=q)

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
