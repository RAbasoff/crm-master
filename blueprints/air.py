"""
Compressed Air (СЖАТЫЙ ВОЗДУХ) — connection points, lines, factory map
"""
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import (db, AirConnectionPoint, AirConnectionPhoto,
                    AirLine, AirLineVertex, FactorySection)
from utils import (role_required, safe_commit, safe_int, safe_float,
                   save_uploaded_file, log_audit)

bp = Blueprint('air', __name__, url_prefix='/air')


def _opt_float(value):
    """Parse optional float; empty/invalid -> None (0 stays 0)."""
    if value is None or str(value).strip() == '':
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _opt_int(value):
    if value is None or str(value).strip() == '':
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


@bp.before_request
def check_air_access():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    from utils import user_has_section_access
    if not user_has_section_access('air'):
        flash(_('ДОСТУП ЗАКРЫТ. НЕ ДОСТАТОЧНО ПРАВ.'), 'error')
        return redirect(url_for('index'))


# ───────────────────────── Dashboard ─────────────────────────

@bp.route('/')
@login_required
def air_dashboard():
    points = AirConnectionPoint.query.options().order_by(AirConnectionPoint.number).all()
    lines = AirLine.query.order_by(AirLine.name).all()
    sections = FactorySection.query.order_by(FactorySection.name).all()
    stats = {
        'points': len(points),
        'points_ok': sum(1 for p in points if p.status == 'ok'),
        'points_leak': sum(1 for p in points if p.status == 'leak'),
        'points_broken': sum(1 for p in points if p.status == 'broken'),
        'lines': len(lines),
        'lines_len': round(sum(l.length_m or 0 for l in lines), 1),
        'photos': AirConnectionPhoto.query.count(),
    }
    return render_template('air.html', points=points, lines=lines,
                           sections=sections, stats=stats)


# ───────────────────── Connection points ─────────────────────

def _next_point_number():
    mx = db.session.query(db.func.max(AirConnectionPoint.number)).scalar() or 0
    return int(mx) + 1


@bp.route('/points/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def point_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash(_('Name is required'), 'error')
            return redirect(url_for('air.point_new'))
        p = AirConnectionPoint(
            number=_opt_int(request.form.get('number')) or _next_point_number(),
            name=name,
            point_type=request.form.get('point_type', 'connection') if request.form.get('point_type') in ('connection', 'regulator', 'valve', 'filter', 'meter') else 'connection',
            location=request.form.get('location', '').strip(),
            section_id=_opt_int(request.form.get('section_id')),
            status=request.form.get('status', 'ok') if request.form.get('status') in ('ok', 'leak', 'broken') else 'ok',
            notes=request.form.get('notes', '').strip(),
            map_x=_opt_float(request.form.get('map_x')),
            map_y=_opt_float(request.form.get('map_y')),
        )
        db.session.add(p)
        db.session.flush()
        for f in request.files.getlist('photos'):
            filename = save_uploaded_file(f, prefix=f'airp_{p.id}_')
            if filename:
                db.session.add(AirConnectionPhoto(point_id=p.id, filename=filename))
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('air.point_new'))
        log_audit('create', 'air_connection_point', p.id, f'#{p.number} {p.name}')
        flash(_('Connection point added'), 'success')
        return redirect(url_for('air.air_dashboard'))
    sections = FactorySection.query.order_by(FactorySection.name).all()
    return render_template('air_point_form.html', point=None, sections=sections,
                           next_number=_next_point_number())


@bp.route('/points/<int:point_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def point_edit(point_id):
    p = AirConnectionPoint.query.get_or_404(point_id)
    if request.method == 'POST':
        p.number = _opt_int(request.form.get('number')) or p.number
        p.name = request.form.get('name', '').strip() or p.name
        pt = request.form.get('point_type', 'connection')
        p.point_type = pt if pt in ('connection', 'regulator', 'valve', 'filter', 'meter') else 'connection'
        p.location = request.form.get('location', '').strip()
        p.section_id = _opt_int(request.form.get('section_id'))
        st = request.form.get('status', 'ok')
        p.status = st if st in ('ok', 'leak', 'broken') else 'ok'
        p.notes = request.form.get('notes', '').strip()
        mx = _opt_float(request.form.get('map_x'))
        my = _opt_float(request.form.get('map_y'))
        if mx is not None:
            p.map_x = mx
        if my is not None:
            p.map_y = my
        for f in request.files.getlist('photos'):
            filename = save_uploaded_file(f, prefix=f'airp_{p.id}_')
            if filename:
                db.session.add(AirConnectionPhoto(point_id=p.id, filename=filename))
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('air.point_edit', point_id=point_id))
        log_audit('update', 'air_connection_point', p.id, f'#{p.number} {p.name}')
        flash(_('Saved'), 'success')
        return redirect(url_for('air.air_dashboard'))
    sections = FactorySection.query.order_by(FactorySection.name).all()
    return render_template('air_point_form.html', point=p, sections=sections,
                           next_number=p.number)


@bp.route('/points/<int:point_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director')
def point_delete(point_id):
    p = AirConnectionPoint.query.get_or_404(point_id)
    name = f'#{p.number} {p.name}'
    db.session.delete(p)
    if not safe_commit():
        flash(_('Delete failed'), 'error')
        return redirect(url_for('air.air_dashboard'))
    log_audit('delete', 'air_connection_point', point_id, name)
    flash(_('Deleted'), 'success')
    return redirect(url_for('air.air_dashboard'))


@bp.route('/points/<int:point_id>/toggle-status', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def point_toggle_status(point_id):
    p = AirConnectionPoint.query.get_or_404(point_id)
    order = ['ok', 'leak', 'broken']
    try:
        idx = order.index(p.status)
    except ValueError:
        idx = 0
    p.status = order[(idx + 1) % len(order)]
    safe_commit()
    log_audit('update', 'air_connection_point', p.id, f'status={p.status}')
    return redirect(request.referrer or url_for('air.air_dashboard'))


@bp.route('/points/photo/<int:photo_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def point_photo_delete(photo_id):
    ph = AirConnectionPhoto.query.get_or_404(photo_id)
    pid = ph.point_id
    db.session.delete(ph)
    safe_commit()
    return redirect(request.referrer or url_for('air.point_edit', point_id=pid))


# ───────────────────────── Lines ─────────────────────────

@bp.route('/lines/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def line_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash(_('Name is required'), 'error')
            return redirect(url_for('air.line_new'))
        ln = AirLine(
            name=name,
            section_id=_opt_int(request.form.get('section_id')),
            from_point_id=_opt_int(request.form.get('from_point_id')),
            to_point_id=_opt_int(request.form.get('to_point_id')),
            length_m=_opt_float(request.form.get('length_m')),
            diameter_mm=_opt_float(request.form.get('diameter_mm')),
            material=request.form.get('material', 'steel') if request.form.get('material') in AirLine.MATERIALS else 'steel',
            status=request.form.get('status', 'ok') if request.form.get('status') in ('ok', 'repair', 'broken') else 'ok',
            color=request.form.get('color', '').strip() or None,
            notes=request.form.get('notes', '').strip(),
        )
        db.session.add(ln)
        db.session.flush()
        # vertices from hidden JSON field
        verts = request.form.get('vertices_json', '')
        _save_vertices(ln, verts)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('air.line_new'))
        log_audit('create', 'air_line', ln.id, f'{ln.name} {ln.length_m or "?"}m Ø{ln.diameter_mm or "?"}')
        flash(_('Air line added'), 'success')
        return redirect(url_for('air.air_dashboard'))
    sections = FactorySection.query.order_by(FactorySection.name).all()
    points = AirConnectionPoint.query.order_by(AirConnectionPoint.number).all()
    return render_template('air_line_form.html', line=None, sections=sections, points=points,
                           vertices_json=json.dumps([]))


@bp.route('/lines/<int:line_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def line_edit(line_id):
    ln = AirLine.query.get_or_404(line_id)
    if request.method == 'POST':
        ln.name = request.form.get('name', '').strip() or ln.name
        ln.section_id = _opt_int(request.form.get('section_id'))
        ln.from_point_id = _opt_int(request.form.get('from_point_id'))
        ln.to_point_id = _opt_int(request.form.get('to_point_id'))
        ln.length_m = _opt_float(request.form.get('length_m'))
        ln.diameter_mm = _opt_float(request.form.get('diameter_mm'))
        mat = request.form.get('material', 'steel')
        ln.material = mat if mat in AirLine.MATERIALS else 'steel'
        st = request.form.get('status', 'ok')
        ln.status = st if st in ('ok', 'repair', 'broken') else 'ok'
        ln.color = request.form.get('color', '').strip() or None
        ln.notes = request.form.get('notes', '').strip()
        verts = request.form.get('vertices_json', '')
        if verts.strip():
            _save_vertices(ln, verts, replace=True)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('air.line_edit', line_id=line_id))
        log_audit('update', 'air_line', ln.id, ln.name)
        flash(_('Saved'), 'success')
        return redirect(url_for('air.air_dashboard'))
    sections = FactorySection.query.order_by(FactorySection.name).all()
    points = AirConnectionPoint.query.order_by(AirConnectionPoint.number).all()
    verts = [{'x': v.map_x, 'y': v.map_y} for v in ln.vertices]
    return render_template('air_line_form.html', line=ln, sections=sections, points=points,
                           vertices_json=json.dumps(verts))


@bp.route('/lines/<int:line_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director')
def line_delete(line_id):
    ln = AirLine.query.get_or_404(line_id)
    name = ln.name
    db.session.delete(ln)
    if not safe_commit():
        flash(_('Delete failed'), 'error')
        return redirect(url_for('air.air_dashboard'))
    log_audit('delete', 'air_line', line_id, name)
    flash(_('Deleted'), 'success')
    return redirect(url_for('air.air_dashboard'))


def _save_vertices(ln, verts_json, replace=False):
    """Parse [{x,y}, ...] and store as AirLineVertex."""
    if replace:
        for v in list(ln.vertices):
            db.session.delete(v)
    try:
        arr = json.loads(verts_json) if verts_json else []
    except (ValueError, TypeError):
        arr = []
    for i, pt in enumerate(arr):
        x = _opt_float(pt.get('x'))
        y = _opt_float(pt.get('y'))
        if x is None or y is None:
            continue
        db.session.add(AirLineVertex(line_id=ln.id, seq=i, map_x=x, map_y=y))


# ───────────────────────── Map (отдельная) ─────────────────────────

@bp.route('/map')
@login_required
def air_map():
    """Отдельная карта модуля: линии + точки по цехам."""
    points = AirConnectionPoint.query.order_by(AirConnectionPoint.number).all()
    lines = AirLine.query.order_by(AirLine.name).all()
    sections = FactorySection.query.order_by(FactorySection.name).all()
    # отдельный фон карты сжатого воздуха (fallback — общий план)
    return render_template('air_map.html', points=points, lines=lines, sections=sections)


@bp.route('/api/map/point-position', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def api_point_position():
    data = request.get_json(silent=True) or {}
    p = AirConnectionPoint.query.get_or_404(_opt_int(data.get('id')) or 0)
    p.map_x = _opt_float(data.get('x'))
    p.map_y = _opt_float(data.get('y'))
    if safe_commit():
        return jsonify(ok=True)
    return jsonify(ok=False), 400


@bp.route('/api/map/line-vertices', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def api_line_vertices():
    data = request.get_json(silent=True) or {}
    ln = AirLine.query.get_or_404(_opt_int(data.get('id')) or 0)
    verts = data.get('vertices') or []
    _save_vertices(ln, json.dumps(verts), replace=True)
    if safe_commit():
        return jsonify(ok=True, count=len(verts))
    return jsonify(ok=False), 400


@bp.route('/api/map/data')
@login_required
def api_map_data():
    """JSON for the map: points + lines with vertices."""
    points = [{
        'id': p.id, 'number': p.number, 'name': p.name,
        'x': p.map_x, 'y': p.map_y, 'status': p.status,
        'section': p.section.name if p.section else None,
        'section_color': p.section.color if p.section else '#95a5a6',
        'location': p.location or '',
    } for p in AirConnectionPoint.query.all()]
    lines = [{
        'id': ln.id, 'name': ln.name,
        'status': ln.status,
        'color': ln.color or (ln.section.color if ln.section else '#e67e22'),
        'section': ln.section.name if ln.section else None,
        'diameter': ln.diameter_mm,
        'length': ln.length_m,
        'material': ln.material,
        'vertices': [{'x': v.map_x, 'y': v.map_y} for v in ln.vertices],
    } for ln in AirLine.query.all()]
    return jsonify(points=points, lines=lines)
