"""
Moeskroen — new factory (новая фабрика).
Access: admin + director only. Subsections: plan / factory map.
"""
import json
import os
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, jsonify, current_app)
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import (db, MoeskroenZone, MoeskroenMarker, MoeskroenMarkerPhoto,
                    MoeskroenLine, MoeskroenLineVertex)
from utils import (role_required, safe_commit, safe_int, safe_float,
                   save_uploaded_file, log_audit)

bp = Blueprint('moeskroen', __name__, url_prefix='/moeskroen')

MAP_BG = 'moeskroen_map_bg.jpeg'


def _opt_float(value):
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
def check_moeskroen_access():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    if not current_user.has_role('admin', 'director'):
        flash(_('ДОСТУП ЗАКРЫТ. НЕ ДОСТАТОЧНО ПРАВ.'), 'error')
        return redirect(url_for('index'))


# ───────────────────────── Dashboard ─────────────────────────

@bp.route('/')
@login_required
@role_required('admin', 'director')
def dashboard():
    zones = MoeskroenZone.query.order_by(MoeskroenZone.name).all()
    markers = MoeskroenMarker.query.order_by(MoeskroenMarker.number).all()
    lines = MoeskroenLine.query.order_by(MoeskroenLine.name).all()
    stats = {
        'zones': len(zones),
        'markers': len(markers),
        'markers_ok': sum(1 for m in markers if m.status == 'ok'),
        'markers_issue': sum(1 for m in markers if m.status == 'issue'),
        'markers_planned': sum(1 for m in markers if m.status == 'planned'),
        'lines': len(lines),
        'photos': MoeskroenMarkerPhoto.query.count(),
    }
    return render_template('moeskroen.html', zones=zones, markers=markers,
                           lines=lines, stats=stats)


# ───────────────────────── Plan (zones) ─────────────────────────

@bp.route('/plan')
@login_required
@role_required('admin', 'director')
def plan():
    zones = MoeskroenZone.query.order_by(MoeskroenZone.name).all()
    return render_template('moeskroen_plan.html', zones=zones, map_bg=MAP_BG)


@bp.route('/zones/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def zone_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash(_('Name is required'), 'error')
            return redirect(url_for('moeskroen.zone_new'))
        z = MoeskroenZone(
            name=name,
            description=request.form.get('description', '').strip(),
            zone_type=request.form.get('zone_type', 'production')
            if request.form.get('zone_type') in MoeskroenZone.ZONE_TYPES else 'production',
            color=request.form.get('color', '').strip() or '#3498db',
            floor_x=_opt_float(request.form.get('floor_x')) or 10,
            floor_y=_opt_float(request.form.get('floor_y')) or 10,
            width=_opt_float(request.form.get('width')) or 20,
            height=_opt_float(request.form.get('height')) or 15,
            notes=request.form.get('notes', '').strip(),
        )
        db.session.add(z)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('moeskroen.zone_new'))
        log_audit('create', 'moeskroen_zone', z.id, z.name)
        flash(_('Saved'), 'success')
        return redirect(url_for('moeskroen.plan'))
    return render_template('moeskroen_zone_form.html', zone=None)


@bp.route('/zones/<int:zone_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def zone_edit(zone_id):
    z = MoeskroenZone.query.get_or_404(zone_id)
    if request.method == 'POST':
        z.name = request.form.get('name', '').strip() or z.name
        z.description = request.form.get('description', '').strip()
        zt = request.form.get('zone_type', '')
        if zt in MoeskroenZone.ZONE_TYPES:
            z.zone_type = zt
        z.color = request.form.get('color', '').strip() or z.color
        for fld, attr in (('floor_x', 'floor_x'), ('floor_y', 'floor_y'),
                          ('width', 'width'), ('height', 'height')):
            val = _opt_float(request.form.get(fld))
            if val is not None:
                setattr(z, attr, val)
        z.notes = request.form.get('notes', '').strip()
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('moeskroen.zone_edit', zone_id=zone_id))
        log_audit('update', 'moeskroen_zone', z.id, z.name)
        flash(_('Saved'), 'success')
        return redirect(url_for('moeskroen.plan'))
    return render_template('moeskroen_zone_form.html', zone=z)


@bp.route('/zones/<int:zone_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director')
def zone_delete(zone_id):
    z = MoeskroenZone.query.get_or_404(zone_id)
    name = z.name
    db.session.delete(z)
    if not safe_commit():
        flash(_('Delete failed'), 'error')
        return redirect(url_for('moeskroen.plan'))
    log_audit('delete', 'moeskroen_zone', zone_id, name)
    flash(_('Deleted'), 'success')
    return redirect(url_for('moeskroen.plan'))


@bp.route('/api/zone-position', methods=['POST'])
@login_required
@role_required('admin', 'director')
def api_zone_position():
    data = request.get_json(silent=True) or {}
    z = MoeskroenZone.query.get_or_404(_opt_int(data.get('id')) or 0)
    z.floor_x = _opt_float(data.get('x'))
    z.floor_y = _opt_float(data.get('y'))
    if safe_commit():
        return jsonify(ok=True)
    return jsonify(ok=False), 400


@bp.route('/plan/bg', methods=['POST'])
@login_required
@role_required('admin', 'director')
def plan_bg_upload():
    f = request.files.get('bg')
    if not f or not f.filename:
        flash(_('No file selected'), 'error')
        return redirect(url_for('moeskroen.plan'))
    filename = save_uploaded_file(f, prefix='moeskroen_bg_')
    if not filename:
        flash(_('Upload failed'), 'error')
        return redirect(url_for('moeskroen.plan'))
    # Stable name used by templates
    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads')
    src = os.path.join(upload_dir, filename)
    dst = os.path.join(upload_dir, MAP_BG)
    try:
        if os.path.exists(dst):
            os.remove(dst)
        os.replace(src, dst)
    except OSError:
        pass
    log_audit('update', 'moeskroen_bg', 0, MAP_BG)
    flash(_('Saved'), 'success')
    return redirect(url_for('moeskroen.plan'))


# ───────────────────────── Map (markers + lines) ─────────────────────────

@bp.route('/map')
@login_required
@role_required('admin', 'director')
def factory_map():
    markers = MoeskroenMarker.query.order_by(MoeskroenMarker.number).all()
    lines = MoeskroenLine.query.order_by(MoeskroenLine.name).all()
    return render_template('moeskroen_map.html', markers=markers, lines=lines,
                           map_bg=MAP_BG)


def _next_marker_number():
    mx = db.session.query(db.func.max(MoeskroenMarker.number)).scalar() or 0
    return int(mx) + 1


@bp.route('/markers/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def marker_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash(_('Name is required'), 'error')
            return redirect(url_for('moeskroen.marker_new'))
        kind = request.form.get('kind', 'other')
        if kind not in MoeskroenMarker.KINDS:
            kind = 'other'
        st = request.form.get('status', 'ok')
        if st not in ('ok', 'issue', 'planned'):
            st = 'ok'
        m = MoeskroenMarker(
            number=_opt_int(request.form.get('number')) or _next_marker_number(),
            name=name,
            kind=kind,
            location=request.form.get('location', '').strip(),
            status=st,
            color=request.form.get('color', '').strip() or '#e67e22',
            notes=request.form.get('notes', '').strip(),
            map_x=_opt_float(request.form.get('map_x')),
            map_y=_opt_float(request.form.get('map_y')),
        )
        db.session.add(m)
        db.session.flush()
        for f in request.files.getlist('photos'):
            filename = save_uploaded_file(f, prefix=f'mk_{m.id}_')
            if filename:
                db.session.add(MoeskroenMarkerPhoto(marker_id=m.id, filename=filename))
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('moeskroen.marker_new'))
        log_audit('create', 'moeskroen_marker', m.id, f'#{m.number} {m.name}')
        flash(_('Saved'), 'success')
        return redirect(url_for('moeskroen.factory_map'))
    return render_template('moeskroen_marker_form.html', marker=None,
                           next_number=_next_marker_number())


@bp.route('/markers/<int:marker_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def marker_edit(marker_id):
    m = MoeskroenMarker.query.get_or_404(marker_id)
    if request.method == 'POST':
        m.number = _opt_int(request.form.get('number')) or m.number
        m.name = request.form.get('name', '').strip() or m.name
        kind = request.form.get('kind', '')
        if kind in MoeskroenMarker.KINDS:
            m.kind = kind
        m.location = request.form.get('location', '').strip()
        st = request.form.get('status', '')
        if st in ('ok', 'issue', 'planned'):
            m.status = st
        m.color = request.form.get('color', '').strip() or m.color
        m.notes = request.form.get('notes', '').strip()
        for fld in ('map_x', 'map_y'):
            if request.form.get(fld) not in (None, ''):
                setattr(m, fld, _opt_float(request.form.get(fld)))
        for f in request.files.getlist('photos'):
            filename = save_uploaded_file(f, prefix=f'mk_{m.id}_')
            if filename:
                db.session.add(MoeskroenMarkerPhoto(marker_id=m.id, filename=filename))
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('moeskroen.marker_edit', marker_id=marker_id))
        log_audit('update', 'moeskroen_marker', m.id, m.name)
        flash(_('Saved'), 'success')
        return redirect(url_for('moeskroen.factory_map'))
    return render_template('moeskroen_marker_form.html', marker=m,
                           next_number=m.number)


@bp.route('/markers/<int:marker_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director')
def marker_delete(marker_id):
    m = MoeskroenMarker.query.get_or_404(marker_id)
    name = m.name
    db.session.delete(m)
    if not safe_commit():
        flash(_('Delete failed'), 'error')
        return redirect(url_for('moeskroen.factory_map'))
    log_audit('delete', 'moeskroen_marker', marker_id, name)
    flash(_('Deleted'), 'success')
    return redirect(url_for('moeskroen.factory_map'))


@bp.route('/lines/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def line_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash(_('Name is required'), 'error')
            return redirect(url_for('moeskroen.line_new'))
        kind = request.form.get('kind', 'route')
        if kind not in MoeskroenLine.KINDS:
            kind = 'route'
        ln = MoeskroenLine(
            name=name,
            kind=kind,
            color=request.form.get('color', '').strip() or '#8e44ad',
            width_px=_opt_float(request.form.get('width_px')) or 2.5,
            notes=request.form.get('notes', '').strip(),
        )
        db.session.add(ln)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('moeskroen.line_new'))
        log_audit('create', 'moeskroen_line', ln.id, ln.name)
        flash(_('Saved'), 'success')
        return redirect(url_for('moeskroen.factory_map'))
    return render_template('moeskroen_line_form.html', line=None)


@bp.route('/lines/<int:line_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def line_edit(line_id):
    ln = MoeskroenLine.query.get_or_404(line_id)
    if request.method == 'POST':
        ln.name = request.form.get('name', '').strip() or ln.name
        kind = request.form.get('kind', '')
        if kind in MoeskroenLine.KINDS:
            ln.kind = kind
        ln.color = request.form.get('color', '').strip() or ln.color
        ln.width_px = _opt_float(request.form.get('width_px')) or ln.width_px
        ln.notes = request.form.get('notes', '').strip()
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('moeskroen.line_edit', line_id=line_id))
        log_audit('update', 'moeskroen_line', ln.id, ln.name)
        flash(_('Saved'), 'success')
        return redirect(url_for('moeskroen.factory_map'))
    return render_template('moeskroen_line_form.html', line=ln)


@bp.route('/lines/<int:line_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director')
def line_delete(line_id):
    ln = MoeskroenLine.query.get_or_404(line_id)
    name = ln.name
    db.session.delete(ln)
    if not safe_commit():
        flash(_('Delete failed'), 'error')
        return redirect(url_for('moeskroen.factory_map'))
    log_audit('delete', 'moeskroen_line', line_id, name)
    flash(_('Deleted'), 'success')
    return redirect(url_for('moeskroen.factory_map'))


def _save_vertices(ln, verts, replace=False):
    if replace:
        for v in list(ln.vertices):
            db.session.delete(v)
    for i, pt in enumerate(verts or []):
        x = _opt_float(pt.get('x') if isinstance(pt, dict) else None)
        y = _opt_float(pt.get('y') if isinstance(pt, dict) else None)
        if x is None or y is None:
            continue
        db.session.add(MoeskroenLineVertex(line_id=ln.id, seq=i, map_x=x, map_y=y))


@bp.route('/api/map/data')
@login_required
@role_required('admin', 'director')
def api_map_data():
    markers = [{
        'id': m.id, 'number': m.number, 'name': m.name,
        'kind': m.kind, 'x': m.map_x, 'y': m.map_y,
        'status': m.status, 'color': m.color,
        'location': m.location or '',
    } for m in MoeskroenMarker.query.all()]
    lines = [{
        'id': ln.id, 'name': ln.name, 'kind': ln.kind,
        'color': ln.color or '#8e44ad', 'width': ln.width_px or 2.5,
        'vertices': [{'x': v.map_x, 'y': v.map_y} for v in ln.vertices],
    } for ln in MoeskroenLine.query.all()]
    return jsonify(markers=markers, lines=lines)


@bp.route('/api/map/marker-position', methods=['POST'])
@login_required
@role_required('admin', 'director')
def api_marker_position():
    data = request.get_json(silent=True) or {}
    m = MoeskroenMarker.query.get_or_404(_opt_int(data.get('id')) or 0)
    m.map_x = _opt_float(data.get('x'))
    m.map_y = _opt_float(data.get('y'))
    if safe_commit():
        return jsonify(ok=True)
    return jsonify(ok=False), 400


@bp.route('/api/map/line-vertices', methods=['POST'])
@login_required
@role_required('admin', 'director')
def api_line_vertices():
    data = request.get_json(silent=True) or {}
    ln = MoeskroenLine.query.get_or_404(_opt_int(data.get('id')) or 0)
    verts = data.get('vertices') or []
    _save_vertices(ln, verts, replace=True)
    if safe_commit():
        return jsonify(ok=True, count=len(verts))
    return jsonify(ok=False), 400
