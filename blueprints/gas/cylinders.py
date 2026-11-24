"""Cylinder CRUD and scan/install routes for the gas module."""
from datetime import datetime, timedelta, date

from flask import request, redirect, url_for, flash, render_template, jsonify
from flask_login import login_required
from flask_babel import gettext as _

from models import db, GasCylinder, CylinderLog
from utils import (resolve_user_id, role_required, log_audit, safe_commit,
                   safe_int, safe_date)
from security import director_only

from . import bp
from . import (_mark_empty_leaves_site, _promote_full_to_in_use,
               _inventory_balance, GAS_TYPES, GAS_LABELS)


# ============================================================
# CYLINDER CRUD
# ============================================================

@bp.route('/cylinders')
@login_required
def cylinders_list():
    gas_type = request.args.get('gas', '')
    q = GasCylinder.query
    if gas_type:
        q = q.filter_by(gas_type=gas_type)
    # Пустые забирает поставщик при поставке, возвращённые — история:
    # в основном списке их быть не должно (правило владельца 2026-11-21).
    q = q.filter(GasCylinder.status.notin_(('empty', 'returned')))
    cylinders = q.order_by(GasCylinder.gas_type, GasCylinder.cylinder_number).all()
    return render_template('gas/cylinders.html', cylinders=cylinders, gas_filter=gas_type)


@bp.route('/cylinders/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def cylinder_new():
    if request.method == 'POST':
        c = GasCylinder(
            gas_type=request.form['gas_type'],
            cylinder_number=request.form['cylinder_number'],
            status=request.form.get('status', 'full'),
            notes=request.form.get('notes', '')
        )
        if request.form.get('received_at'):
            c.received_at = safe_date(request.form.get('received_at'))
        db.session.add(c)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('gas.cylinder_new'))

        log = CylinderLog(
            cylinder_id=c.id,
            action='created',
            new_cylinder_number=c.cylinder_number,
            performed_by=resolve_user_id(),
            notes=f'New {c.gas_type} cylinder added'
        )
        db.session.add(log)
        if not safe_commit():
            flash(_('Cylinder added but log entry failed'), 'warning')

        log_audit('create', 'gas_cylinder', c.id, f'{c.gas_type} #{c.cylinder_number}')
        flash(_('Cylinder added'), 'success')
        return redirect(url_for('gas.gas_dashboard'))

    return render_template('gas/cylinder_form.html', cylinder=None)


@bp.route('/cylinders/<int:cyl_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def cylinder_edit(cyl_id):
    c = GasCylinder.query.get_or_404(cyl_id)
    if request.method == 'POST':
        old_status = c.status
        c.gas_type = request.form['gas_type']
        c.cylinder_number = request.form['cylinder_number']
        c.status = request.form.get('status', c.status)
        c.notes = request.form.get('notes', '')
        if request.form.get('received_at'):
            c.received_at = safe_date(request.form.get('received_at'))
        if request.form.get('installed_at'):
            c.installed_at = safe_date(request.form.get('installed_at'))
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('gas.cylinder_edit', cyl_id=cyl_id))

        if old_status != c.status:
            log = CylinderLog(
                cylinder_id=c.id,
                action=f'status_{old_status}_to_{c.status}',
                performed_by=resolve_user_id(),
                notes=f'Status: {old_status} → {c.status}'
            )
            db.session.add(log)
            if not safe_commit():
                flash(_('Cylinder updated but status log failed'), 'warning')

        log_audit('update', 'gas_cylinder', c.id, f'{c.gas_type} #{c.cylinder_number}')
        flash(_('Cylinder updated'), 'success')
        return redirect(url_for('gas.gas_dashboard'))

    return render_template('gas/cylinder_form.html', cylinder=c)


@bp.route('/cylinders/<int:cyl_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def cylinder_delete(cyl_id):
    c = GasCylinder.query.get_or_404(cyl_id)
    name = f'{c.gas_type} #{c.cylinder_number}'
    db.session.delete(c)
    if not safe_commit():
        flash(_('Delete failed. Please try again.'), 'error')
        return redirect(url_for('gas.gas_dashboard'))
    log_audit('delete', 'gas_cylinder', cyl_id, name)
    flash(_('Cylinder deleted'), 'success')
    return redirect(url_for('gas.gas_dashboard'))


@bp.route('/cylinders/<int:cyl_id>/status', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def cylinder_status(cyl_id):
    """Quick status change from dashboard.

    Working set (норма): exactly 1 in_use + at least 1 full spare per gas type.
    Identity: full + in_use + empty + maintenance = total.
    """
    c = GasCylinder.query.get_or_404(cyl_id)
    data = request.get_json() if request.is_json else request.form
    new_status = data.get('status')
    if new_status not in ('full', 'in_use', 'empty', 'maintenance', 'defect'):
        return jsonify({'error': 'Invalid status'}), 400

    old_status = c.status
    if new_status == old_status:
        if request.is_json:
            return jsonify({'ok': True, 'old': old_status, 'new': new_status})
        flash(_('Status updated'), 'success')
        return redirect(url_for('gas.gas_dashboard'))

    if new_status == 'empty':
        _mark_empty_leaves_site(c, user_id=resolve_user_id())
    else:
        c.status = new_status
        if new_status == 'in_use' and not c.installed_at:
            c.installed_at = datetime.utcnow()

    # Only ONE in_use per gas type (N2 / CO2 / MIX — рабочая пара своя на тип).
    # When activating a new one, others go back to full (installed, waiting) — not empty.
    if new_status == 'in_use':
        others = GasCylinder.query.filter(
            GasCylinder.status == 'in_use',
            GasCylinder.gas_type == c.gas_type,
            GasCylinder.id != c.id
        ).all()
        for other in others:
            other.status = 'full'
            if not other.installed_at:
                other.installed_at = datetime.utcnow()
            db.session.add(CylinderLog(
                cylinder_id=other.id,
                action='in_use_to_full',
                performed_by=resolve_user_id(),
                notes=f'Standby: switched to {c.cylinder_number} ({c.gas_type})',
            ))

    # When an in_use cylinder empties → promote one full spare into service
    if old_status == 'in_use' and new_status == 'empty':
        promoted = _promote_full_to_in_use(
            c.gas_type,
            reason=f'Auto-switch: backup activated after {c.cylinder_number} emptied',
            user_id=resolve_user_id(),
        )
        if not promoted:
            flash(_('No full spare cylinder to activate! Order new %(gas)s cylinders.',
                    gas=GAS_LABELS.get(c.gas_type, c.gas_type)), 'warning')

    if not safe_commit():
        if request.is_json:
            return jsonify({'error': 'Save failed'}), 500
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('gas.gas_dashboard'))

    # Пустой уже залогирован в _mark_empty_leaves_site — второй лог удваивал
    # все счётчики расхода («used» считался по записям *_to_empty).
    if new_status != 'empty':
        db.session.add(CylinderLog(
            cylinder_id=c.id,
            action='status_%s_to_%s' % (old_status, new_status),
            performed_by=resolve_user_id(),
            notes='Status: %s -> %s' % (old_status, new_status)
        ))
        if not safe_commit():
            if request.is_json:
                return jsonify({'error': 'Save failed'}), 500
            flash(_('Status updated but log entry failed'), 'warning')

    log_audit('status_change', 'gas_cylinder', c.id,
              '%s #%s: %s -> %s' % (c.gas_type, c.cylinder_number, old_status, new_status))

    if request.is_json:
        bal = _inventory_balance().get(c.gas_type, {})
        return jsonify({'ok': True, 'old': old_status, 'new': new_status, 'balance': bal})
    flash(_('Status updated'), 'success')
    return redirect(url_for('gas.gas_dashboard'))


# ============================================================
# API — for interactive dashboard
# ============================================================

@bp.route('/api/cylinders')
@login_required
def api_cylinders():
    """Return cylinder data as JSON for interactive visualization"""
    cylinders = GasCylinder.query.order_by(GasCylinder.gas_type, GasCylinder.cylinder_number).all()
    result = []
    for c in cylinders:
        result.append({
            'id': c.id,
            'gas_type': c.gas_type,
            'number': c.cylinder_number,
            'status': c.status,
            'received_at': c.received_at.strftime('%Y-%m-%d') if c.received_at else None,
            'installed_at': c.installed_at.strftime('%Y-%m-%d') if c.installed_at else None,
            'notes': c.notes or '',
        })
    return jsonify(result)


@bp.route('/api/cylinders/scan-refill', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only()
def cylinder_scan_refill():
    """Второе сканирование — штрихкод с Z = дата заправки (или ввод даты вручную)."""
    data = request.get_json() or {}
    code = (data.get('code') or data.get('barcode') or '').strip()
    date_str = (data.get('date') or '').strip()
    cyl_id = safe_int(data.get('cylinder_id'))
    number = (data.get('number') or '').strip()
    c = db.session.get(GasCylinder, cyl_id) if cyl_id else None
    if not c and number:
        c = GasCylinder.query.filter_by(cylinder_number=number).first()
    if not c:
        return jsonify({'error': 'Cylinder not found — scan number first'}), 404

    refill = None
    if date_str:
        try:
            refill = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid date format YYYY-MM-DD'}), 400
    elif code:
        refill = _parse_z_refill(code)
        if not refill:
            return jsonify({'error': 'Invalid refill barcode (Z + date) or enter date manually'}), 400
    else:
        return jsonify({'error': 'No date'}), 400

    c.refill_date = refill
    if code:
        c.barcode = code
    db.session.add(CylinderLog(cylinder_id=c.id, action='refill_date',
                               performed_by=resolve_user_id(),
                               notes=f'Refill {refill.isoformat()}'))
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'number': c.cylinder_number,
                    'refill_date': c.refill_date.isoformat(),
                    'gas_type': c.gas_type, 'status': c.status})


def _parse_z_refill(code):
    """Штрихкод заправки: Z + дата (Z20261005, Z26-10-05, Z26/10/05)."""
    s = (code or '').strip().upper()
    if not s.startswith('Z'):
        return None
    s = s[1:]
    try:
        if len(s) == 8 and s.isdigit():
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        for sep in ('-', '/'):
            if sep in s:
                parts = s.split(sep)
                if len(parts) == 3:
                    a, b, c_ = parts
                    if len(a) == 4:
                        return date(int(a), int(b), int(c_))
                    return date(2000 + int(a), int(b), int(c_))
    except ValueError:
        return None
    return None


@bp.route('/scan-list')
@login_required
@role_required('admin', 'director', 'technician')
def cylinder_scan_list():
    """Scan list page. Shows recent scans from DB so the list is not lost."""
    since = datetime.utcnow() - timedelta(hours=48)
    # cylinders touched by scan-list flow (created/received recently)
    logs = CylinderLog.query.filter(
        CylinderLog.date >= since,
        CylinderLog.action.in_(('created', 'received'))
    ).order_by(CylinderLog.date.desc()).limit(200).all()
    seen = set()
    recent = []
    for log in logs:
        c = db.session.get(GasCylinder, log.cylinder_id) if log.cylinder_id else None
        if not c or c.id in seen:
            continue
        seen.add(c.id)
        recent.append({
            'id': c.id,
            'number': c.cylinder_number,
            'gas_type': c.gas_type,
            'status': c.status,
            'refill_date': c.refill_date.isoformat() if c.refill_date else '',
            'action': log.action,
            'note': (log.notes or '')[:80],
        })
    return render_template('gas/scan_list.html', recent=recent)


@bp.route('/api/cylinders/scan-list', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only()
def cylinder_scan_list_add():
    """Добавить номер баллона в список приёмки (без требования Z).

    accept=true — принять сразу на склад (для оффлайн-очереди это одна
    заявка вместо цепочки «скан → приёмка»: иначе после синхронизации
    второй шаг терял бы id первого).
    """
    data = request.get_json() or {}
    number = (data.get('number') or '').strip()
    gas_type = data.get('gas_type') or 'nitrogen'
    accept = bool(data.get('accept'))
    if not number:
        return jsonify({'error': 'No number'}), 400
    if gas_type not in GAS_TYPES:
        gas_type = 'nitrogen'
    c = GasCylinder.query.filter_by(cylinder_number=number).first()
    created = False
    if not c:
        # scanned only — NOT in stock until «Добавить на склад» (если accept — сразу)
        c = GasCylinder(gas_type=gas_type, cylinder_number=number,
                        status='full' if accept else 'scanned',
                        received_at=datetime.utcnow() if accept else None)
        db.session.add(c)
        db.session.flush()
        created = True
        db.session.add(CylinderLog(cylinder_id=c.id, action='received' if accept else 'created',
                                   performed_by=resolve_user_id(),
                                   new_cylinder_number=number,
                                   notes='Scan list (added to stock)' if accept else 'Scan list (not in stock yet)'))
    elif accept and (not c.received_at or c.status in ('scanned', None, '')):
        if not c.received_at:
            c.received_at = datetime.utcnow()
        if c.status not in ('in_use', 'empty', 'maintenance', 'defect'):
            c.status = 'full'
        db.session.add(CylinderLog(
            cylinder_id=c.id, action='received', performed_by=resolve_user_id(),
            notes='Added to stock on scan'))
    elif c.status == 'scanned' and not c.received_at:
        pass
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'created': created, 'cylinder_id': c.id,
                    'number': c.cylinder_number, 'gas_type': c.gas_type,
                    'status': c.status, 'side': c.side or '',
                    'refill_date': c.refill_date.isoformat() if c.refill_date else None})


@bp.route('/api/cylinders/scan-list/add-stock', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only()
def cylinder_scan_list_add_stock():
    """Ответ «ДА» — добавить баллоны из списка на склад (received)."""
    data = request.get_json() or {}
    ids = data.get('ids') or []
    if not ids:
        return jsonify({'error': 'No cylinders'}), 400
    n = 0
    for cid in ids:
        c = db.session.get(GasCylinder, int(cid))
        if not c:
            continue
        # Только сейчас принимаем на склад
        if not c.received_at:
            c.received_at = datetime.utcnow()
        if c.status in ('scanned', None, '') or c.status not in ('in_use', 'empty', 'maintenance', 'defect'):
            c.status = 'full'
        db.session.add(CylinderLog(
            cylinder_id=c.id, action='received', performed_by=resolve_user_id(),
            notes='Added to stock from scan list'
        ))
        n += 1
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    added_nums = []
    for cid in ids:
        c = db.session.get(GasCylinder, int(cid))
        if c:
            added_nums.append(c.cylinder_number)
    return jsonify({'ok': True, 'added': n, 'numbers': added_nums,
                    'stock_url': '/gas/cylinders'})


def _scan_list_pending():
    """Баллоны в списке приёмки (сканированы, но ещё не приняты на склад).

    Раньше ловились и старые записи без received_at — счётник висел
    навсегда и сканер спрашивал «добавить в список?» на каждом номере.
    """
    return GasCylinder.query.filter(GasCylinder.status == 'scanned').count()


@bp.route('/api/cylinders/assign-work', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only()
def cylinder_assign_work():
    """Установить баллон на место (left/right).

    Если списка сканирования нет — сразу: сканировал → поставил на место.
    Если список есть, а номера в нём нет — спросить, что делать.
    """
    data = request.get_json() or {}
    number = (data.get('number') or data.get('code') or '').strip()
    cyl_id = safe_int(data.get('cylinder_id'))
    side = (data.get('side') or '').strip()
    gas_type = data.get('gas_type') or 'nitrogen'
    force = bool(data.get('force'))
    action = (data.get('action') or '').strip()  # install | add_list | add_stock | cancel
    refill_date = (data.get('refill_date') or '').strip()
    if side not in ('left', 'right'):
        return jsonify({'error': 'Side must be left or right'}), 400

    c = db.session.get(GasCylinder, cyl_id) if cyl_id else None
    if not c and number:
        c = GasCylinder.query.filter_by(cylinder_number=number).first()

    pending = _scan_list_pending()
    # номер «в списке» = уже есть в БД (скан-лист или склад)
    in_list = bool(c)

    if pending > 0 and not in_list and not force and action not in ('add_list', 'add_stock', 'install'):
        return jsonify({
            'ok': False,
            'need_choose': True,
            'number': number,
            'list_size': pending,
            'message': 'Number is not in the scanned list',
            'options': ['add_list', 'add_stock', 'install', 'cancel'],
        }), 200

    if pending == 0 and not c:
        # Нет списка — простая схема: сканировал → поставил на место
        action = action or 'install'

    if not c:
        status0 = 'in_use'
        received0 = datetime.utcnow()
        if action == 'add_list':
            status0 = 'scanned'
            received0 = None
        elif action == 'add_stock':
            status0 = 'full'
        c = GasCylinder(gas_type=gas_type if gas_type in GAS_TYPES else 'nitrogen',
                        cylinder_number=number, barcode=number,
                        status=status0, side=None,
                        received_at=received0, installed_at=None)
        db.session.add(c)
        db.session.flush()
        db.session.add(CylinderLog(cylinder_id=c.id, action='created',
                                   performed_by=resolve_user_id(),
                                   new_cylinder_number=number,
                                   notes=f'Scan install action={action or "install"}'))
    elif action == 'add_stock' and (not c.received_at or c.status == 'scanned'):
        if not c.received_at:
            c.received_at = datetime.utcnow()
        if c.status not in ('in_use', 'empty', 'maintenance', 'defect'):
            c.status = 'full'
        db.session.add(CylinderLog(
            cylinder_id=c.id, action='received', performed_by=resolve_user_id(),
            notes='Accepted to stock on install'
        ))

    # снять предыдущий с этой стороны
    for other in GasCylinder.query.filter(
        GasCylinder.gas_type == c.gas_type,
        GasCylinder.side == side,
        GasCylinder.status == 'in_use',
        GasCylinder.id != c.id
    ).all():
        _mark_empty_leaves_site(
            other, user_id=resolve_user_id(),
            note=f'Replaced by #{c.cylinder_number} on {side}'
        )
    c.status = 'in_use'
    c.side = side
    if not c.received_at:
        c.received_at = datetime.utcnow()
    if refill_date:
        try:
            c.refill_date = datetime.strptime(refill_date, '%Y-%m-%d').date()
        except Exception:
            pass
    c.gas_type = gas_type if gas_type in GAS_TYPES else c.gas_type
    c.installed_at = datetime.utcnow()
    db.session.add(CylinderLog(cylinder_id=c.id, action='install',
                               performed_by=resolve_user_id(),
                               notes=f'Installed on {side}'))
    # Соседний баллон становится IN USE (рабочая пара переключается)
    other_activated = None
    other_side = 'right' if side == 'left' else 'left'
    neighbor = (GasCylinder.query
                .filter(GasCylinder.gas_type == c.gas_type,
                        GasCylinder.side == other_side,
                        GasCylinder.status == 'full')
                .order_by(GasCylinder.id.asc()).first())
    if neighbor:
        neighbor.status = 'in_use'
        neighbor.installed_at = datetime.utcnow()
        db.session.add(CylinderLog(
            cylinder_id=neighbor.id, action='status_full_to_in_use',
            performed_by=resolve_user_id(),
            notes=f'Working pair: switched after install of {c.cylinder_number} on {side}'
        ))
        other_activated = neighbor.cylinder_number
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    log_audit('assign_work', 'gas_cylinder', c.id, f'{c.cylinder_number} → {side}')
    return jsonify({'ok': True, 'number': c.cylinder_number, 'side': side,
                    'in_list': in_list, 'cylinder_id': c.id,
                    'other_activated': other_activated,
                    'list_pending': _scan_list_pending()})


@bp.route('/scan')
@login_required
def cylinder_scan_page():
    """Barcode scanning page for cylinder install/replace"""
    return render_template('gas/cylinder_scan.html')
