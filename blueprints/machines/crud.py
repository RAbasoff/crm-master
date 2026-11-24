"""Machine CRUD, TO checklists, timeline."""
import os
from datetime import datetime

from flask import (current_app, flash, jsonify, make_response, redirect,
                   render_template, request, url_for)
from flask_babel import gettext as _
from flask_login import current_user, login_required

from models import (Contractor, FactorySection, FaultReport, Machine,
                    MachineConsumable, MachineDocument, MachinePart,
                    MachineSparePart, MachineTOChecklist, MaintenancePlan,
                    MaintenanceRecord, PurchaseRequest, TechnicalWorkOrder,
                    User, Verantwoordelijke, VoorraadItem, db)
from security import director_only
from utils import (acquire_lock, log_audit, release_lock, role_required,
                   safe_commit, safe_float, safe_int, sanitize_like,
                   save_uploaded_file)

from . import TO_PERIODS, _optf, bp, ensure_machine_to_checklists

@bp.route('/')
@login_required
def machines_list():
    page = request.args.get('page', 1, type=int)
    q = (request.args.get('q') or '').strip()

    def _filtered(query):
        if q:
            like = f'%{sanitize_like(q)}%'
            query = query.filter(db.or_(
                Machine.name.ilike(like),
                Machine.machine_type.ilike(like),
                Machine.serial_number.ilike(like),
                Machine.manufacturer.ilike(like),
                Machine.description.ilike(like),
            ))
        return query

    if current_user.has_role('admin', 'director', 'technician'):
        # все машины одним списком + поиск — иначе новые станки «теряются»
        # на последней странице пагинации (случай Meat slicer MBC 330)
        pagination = _filtered(Machine.query.order_by(Machine.name)).paginate(page=page, per_page=200, error_out=False)
    else:
        machines = current_user.assigned_machines
        return render_template('machines.html', machines=machines, pagination=None, q=q)
    return render_template('machines.html', machines=pagination.items, pagination=pagination, q=q)


@bp.route('/3d')
@login_required
def machines_map_3d():
    """Общая 3D-карта фабрики: только станки (без воздуховодов и воды)."""
    # без кеша: браузер не должен показывать старую версию карты
    resp = make_response(render_template('air/map_3d.html',
                           module_title=_('General map'),
                           back_url='/machines',
                           map_mode='common'))
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@bp.route('/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
@director_only('tim', 'peter')
def machine_new():
    if request.method == 'POST':
        m = Machine(
            name=request.form['name'],
            description=request.form.get('description', ''),
            serial_number=request.form.get('serial_number', ''),
            machine_type=request.form.get('machine_type', ''),
            manufacturer=request.form.get('manufacturer', ''),
            year_of_manufacture=safe_int(request.form.get('year_of_manufacture')) or None,
            installation_location=request.form.get('installation_location', ''),
            contractor_id=safe_int(request.form.get('contractor_id')) or None,
            responsible_user_id=safe_int(request.form.get('responsible_user_id')) or None,
            responsible_person_id=safe_int(request.form.get('responsible_person_id')) or None,
            section_id=safe_int(request.form.get('section_id')) or None,
            marker_size=safe_int(request.form.get('marker_size'), 45),
            marker_shape=request.form.get('marker_shape', 'circle'),
            floor_x=safe_float(request.form.get('floor_x'), 50),
            floor_y=safe_float(request.form.get('floor_y'), 50),
            dim_length_mm=_optf(request.form.get('dim_length_mm')),
            dim_width_mm=_optf(request.form.get('dim_width_mm')),
            dim_height_mm=_optf(request.form.get('dim_height_mm')),
            power_voltage=request.form.get('power_voltage', '').strip() or None,
            power_kw=_optf(request.form.get('power_kw')),
            air_usage_m3h=_optf(request.form.get('air_usage_m3h')),
            water_usage_lmin=_optf(request.form.get('water_usage_lmin')),
            gas_usage_m3h=_optf(request.form.get('gas_usage_m3h')),
            gas_natural=bool(request.form.get('gas_natural')),
            gas_nitrogen=bool(request.form.get('gas_nitrogen')),
            gas_co2=bool(request.form.get('gas_co2')),
            nitrogen_usage_m3h=_optf(request.form.get('nitrogen_usage_m3h')),
            co2_usage_m3h=_optf(request.form.get('co2_usage_m3h')),
        )
        db.session.add(m)
        db.session.flush()
        if 'photo' in request.files and request.files['photo'].filename:
            filename = save_uploaded_file(request.files['photo'], prefix=f"machine_{m.id}_")
            if filename:
                m.photo = filename
        for uid in request.form.getlist('assigned_users'):
            u = User.query.get(int(uid))
            if u:
                m.assigned_users.append(u)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('machines.machine_new'))
        flash(_('Machine created'), 'success')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    users = User.query.filter(User.is_active_user == True).all()
    sections = FactorySection.query.all()
    contractors = Contractor.query.filter_by(is_active=True).order_by(Contractor.company_name).all()
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    return render_template('machine_form.html', machine=None, users=users, sections=sections,
                           contractors=contractors, verantwoordelijken=verantwoordelijken)


@bp.route('/<int:machine_id>')
@login_required
def machine_detail(machine_id):
    m = Machine.query.get_or_404(machine_id)
    faults = FaultReport.query.filter_by(machine_id=m.id).order_by(FaultReport.created_at.desc()).all()
    maintenance = MaintenanceRecord.query.filter_by(machine_id=m.id).order_by(MaintenanceRecord.date_performed.desc()).all()
    warehouse_items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    timeline = _build_machine_timeline(m.id, limit=80)
    ensure_machine_to_checklists(m.id)
    checks = MachineTOChecklist.query.filter_by(machine_id=m.id, is_active=True).order_by(
        MachineTOChecklist.period, MachineTOChecklist.sort_order, MachineTOChecklist.id).all()
    checklist_by_period = {k: [] for k in TO_PERIODS}
    for c in checks:
        checklist_by_period.setdefault(c.period, []).append(c)
    return render_template('machine_detail.html', machine=m, faults=faults, maintenance=maintenance,
                           warehouse_items=warehouse_items, timeline=timeline,
                           to_periods=TO_PERIODS, checklist_by_period=checklist_by_period)


@bp.route('/<int:machine_id>/to-checklist/add', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only('tim', 'peter')
def machine_to_checklist_add(machine_id):
    m = Machine.query.get_or_404(machine_id)
    period = (request.form.get('period') or 'monthly').strip()
    if period not in TO_PERIODS:
        period = 'monthly'
    text = (request.form.get('text') or '').strip()
    if not text:
        flash(_('Text required'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=m.id) + '#to-checklist')
    max_o = db.session.query(db.func.coalesce(db.func.max(MachineTOChecklist.sort_order), 0)).filter_by(
        machine_id=m.id, period=period).scalar() or 0
    db.session.add(MachineTOChecklist(machine_id=m.id, period=period, text=text, sort_order=max_o + 1))
    if not safe_commit():
        flash(_('Save failed'), 'error')
    return redirect(url_for('machines.machine_detail', machine_id=m.id) + '#to-checklist')


@bp.route('/to-checklist/<int:item_id>/update', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only('tim', 'peter')
def machine_to_checklist_update(item_id):
    it = MachineTOChecklist.query.get_or_404(item_id)
    text = (request.form.get('text') or '').strip()
    if text:
        it.text = text
        if not safe_commit():
            flash(_('Save failed'), 'error')
    return redirect(url_for('machines.machine_detail', machine_id=it.machine_id) + '#to-checklist')


@bp.route('/to-checklist/<int:item_id>/toggle', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only('tim', 'peter')
def machine_to_checklist_toggle(item_id):
    it = MachineTOChecklist.query.get_or_404(item_id)
    it.is_active = not it.is_active
    if not safe_commit():
        flash(_('Save failed'), 'error')
    return redirect(url_for('machines.machine_detail', machine_id=it.machine_id) + '#to-checklist')


@bp.route('/to-checklist/<int:item_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only('tim', 'peter')
def machine_to_checklist_delete(item_id):
    it = MachineTOChecklist.query.get_or_404(item_id)
    mid = it.machine_id
    db.session.delete(it)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    return redirect(url_for('machines.machine_detail', machine_id=mid) + '#to-checklist')


def _build_machine_timeline(machine_id, limit=100):
    """Unified event feed for one machine: faults, TO, plans, TWO, parts, purchases."""
    events = []

    for f in FaultReport.query.filter_by(machine_id=machine_id).all():
        ts = f.created_at
        events.append({
            'ts': ts, 'kind': 'fault', 'icon': '⚠️', 'color': '#e74c3c',
            'title': f.title or f'#{f.id}',
            'status': f.status, 'priority': getattr(f, 'priority', None),
            'url': f'/faults/{f.id}',
            'detail': (f.description or '')[:180],
        })

    for p in MaintenancePlan.query.filter_by(machine_id=machine_id).all():
        ts = p.planned_start or p.created_at
        events.append({
            'ts': ts, 'kind': 'plan', 'icon': '📋', 'color': '#3498db',
            'title': p.title or f'Plan #{p.id}',
            'status': p.status,
            'url': f'/maintenance-plans/{p.id}',
            'detail': (p.description or '')[:180],
        })

    for r in MaintenanceRecord.query.filter_by(machine_id=machine_id).all():
        ts = r.date_performed or r.created_at
        events.append({
            'ts': ts, 'kind': 'maintenance', 'icon': '🔧', 'color': '#27ae60',
            'title': r.description or f'TO #{r.id}',
            'status': r.maintenance_type or 'done',
            'url': None,
            'detail': (r.notes or '')[:180],
        })

    for t in TechnicalWorkOrder.query.filter_by(machine_id=machine_id).all():
        ts = t.planned_date or t.created_at
        events.append({
            'ts': ts, 'kind': 'two', 'icon': '📝', 'color': '#9b59b6',
            'title': f'{getattr(t, "number", "") or "TWO"} — {(t.description or "")[:60]}',
            'status': t.status,
            'url': f'/two/{t.id}',
            'detail': (t.description or '')[:180],
        })

    for pr in PurchaseRequest.query.filter_by(machine_id=machine_id).all():
        ts = pr.created_at
        events.append({
            'ts': ts, 'kind': 'purchase', 'icon': '🛒', 'color': '#e67e22',
            'title': pr.part_name or f'PR #{pr.id}',
            'status': pr.status,
            'url': f'/purchase-requests/{pr.id}',
            'detail': (getattr(pr, 'reason', '') or getattr(pr, 'notes', '') or '')[:180],
        })

    for mp in MachinePart.query.filter_by(machine_id=machine_id).all():
        ts = mp.installed_date or mp.created_at
        if not ts:
            continue
        events.append({
            'ts': ts, 'kind': 'part', 'icon': '⚙️', 'color': '#16a085',
            'title': mp.name or f'Part #{mp.id}',
            'status': mp.status,
            'url': f'/machines/{machine_id}/parts',
            'detail': (mp.category or '')[:180],
        })

    # normalize ts to datetime for sorting
    def _ts(e):
        v = e['ts']
        if v is None:
            return datetime.min
        if hasattr(v, 'year') and not hasattr(v, 'hour'):
            return datetime(v.year, v.month, v.day)
        return v

    events.sort(key=_ts, reverse=True)

    # serialize
    out = []
    for e in events[:limit]:
        ts = e['ts']
        if ts is None:
            date_s = None
        elif hasattr(ts, 'strftime'):
            date_s = ts.strftime('%Y-%m-%d %H:%M') if hasattr(ts, 'hour') else ts.strftime('%Y-%m-%d')
        else:
            date_s = str(ts)
        out.append({**e, 'ts': date_s, 'ts_raw': str(_ts(e))})
    return out


@bp.route('/<int:machine_id>/timeline')
@login_required
def machine_timeline(machine_id):
    m = Machine.query.get_or_404(machine_id)
    limit = min(max(request.args.get('limit', 100, type=int), 1), 500)
    events = _build_machine_timeline(m.id, limit=limit)
    if request.args.get('format') == 'html':
        return render_template('machine_timeline.html', machine=m, timeline=events)
    return jsonify({'machine_id': m.id, 'machine': m.name, 'count': len(events), 'events': events})


@bp.route('/<int:machine_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
@director_only('tim', 'peter')
def machine_edit(machine_id):
    m = Machine.query.get_or_404(machine_id)
    if request.method == 'POST':
        # Verify lock on save
        ok, lock_info = acquire_lock('machine', machine_id, current_user.id, current_user.username)
        if not ok:
            flash(_('Record is being edited by %(user)s. Try again later.', user=lock_info.get('user_name', '?')), 'error')
            return redirect(url_for('machines.machine_detail', machine_id=m.id))
        m.name = request.form['name']
        m.description = request.form.get('description', '')
        m.serial_number = request.form.get('serial_number', '')
        m.machine_type = request.form.get('machine_type', '')
        m.manufacturer = request.form.get('manufacturer', '')
        m.year_of_manufacture = safe_int(request.form.get('year_of_manufacture')) or None
        m.installation_location = request.form.get('installation_location', '')
        m.contractor_id = safe_int(request.form.get('contractor_id')) or None
        m.responsible_user_id = safe_int(request.form.get('responsible_user_id')) or None
        m.responsible_person_id = safe_int(request.form.get('responsible_person_id')) or None
        m.section_id = safe_int(request.form.get('section_id')) or None
        m.marker_size = safe_int(request.form.get('marker_size'), m.marker_size or 45)
        m.marker_shape = request.form.get('marker_shape', m.marker_shape or 'circle')
        m.status = request.form.get('status', m.status)
        m.floor_x = safe_float(request.form.get('floor_x'), m.floor_x)
        m.floor_y = safe_float(request.form.get('floor_y'), m.floor_y)
        m.dim_length_mm = _optf(request.form.get('dim_length_mm'))
        m.dim_width_mm = _optf(request.form.get('dim_width_mm'))
        m.dim_height_mm = _optf(request.form.get('dim_height_mm'))
        m.power_voltage = request.form.get('power_voltage', '').strip() or None
        m.power_kw = _optf(request.form.get('power_kw'))
        m.air_usage_m3h = _optf(request.form.get('air_usage_m3h'))
        m.water_usage_lmin = _optf(request.form.get('water_usage_lmin'))
        m.gas_usage_m3h = _optf(request.form.get('gas_usage_m3h'))
        m.gas_natural = bool(request.form.get('gas_natural'))
        m.gas_nitrogen = bool(request.form.get('gas_nitrogen'))
        m.gas_co2 = bool(request.form.get('gas_co2'))
        m.nitrogen_usage_m3h = _optf(request.form.get('nitrogen_usage_m3h'))
        m.co2_usage_m3h = _optf(request.form.get('co2_usage_m3h'))
        if 'photo' in request.files and request.files['photo'].filename:
            filename = save_uploaded_file(request.files['photo'], prefix=f"machine_{m.id}_")
            if filename:
                if m.photo:
                    old_path = os.path.join(current_app.config['UPLOAD_FOLDER'], m.photo)
                    try:
                        if os.path.isfile(old_path):
                            os.remove(old_path)
                    except OSError:
                        pass
                m.photo = filename
        m.assigned_users = []
        for uid in request.form.getlist('assigned_users'):
            u = User.query.get(int(uid))
            if u:
                m.assigned_users.append(u)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('machines.machine_edit', machine_id=m.id))
        release_lock('machine', machine_id, current_user.id)
        flash(_('Machine updated'), 'success')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    # GET: acquire lock
    ok, lock_info = acquire_lock('machine', machine_id, current_user.id, current_user.username)
    if not ok:
        flash(_('⚠️ This record is being edited by %(user)s (since %(time)s). You cannot edit it now.', user=lock_info.get('user_name', '?'), time=lock_info.get('locked_at', '?')), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    users = User.query.filter(User.is_active_user == True).all()
    sections = FactorySection.query.all()
    contractors = Contractor.query.filter_by(is_active=True).order_by(Contractor.company_name).all()
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    return render_template('machine_form.html', machine=m, users=users, sections=sections,
                           contractors=contractors, verantwoordelijken=verantwoordelijken)


@bp.route('/<int:machine_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def machine_delete(machine_id):
    m = Machine.query.get_or_404(machine_id)
    name = m.name
    open_faults = FaultReport.query.filter_by(machine_id=m.id).filter(
        FaultReport.status.in_(['open', 'accepted', 'in_progress'])
    ).count()
    if open_faults > 0:
        flash(_('Cannot delete machine with {} open fault reports').format(open_faults), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    FaultReport.query.filter_by(machine_id=m.id).update({'machine_id': None})
    MaintenanceRecord.query.filter_by(machine_id=m.id).delete()
    MachinePart.query.filter_by(machine_id=m.id).delete()
    MachineConsumable.query.filter_by(machine_id=m.id).delete()
    MachineDocument.query.filter_by(machine_id=m.id).delete()
    MachineSparePart.query.filter_by(machine_id=m.id).delete()
    m.assigned_users = []
    db.session.delete(m)
    if not safe_commit():
        flash(_('Delete failed. Please try again.'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=m.id))
    log_audit('delete', 'machine', machine_id, name)
    flash(_('Machine deleted') + f': {name}', 'success')
    return redirect(url_for('machines.machines_list'))

