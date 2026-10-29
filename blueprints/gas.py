"""
Gas & Gas Equipment module — interactive cylinder management
Nitrogen (N₂) and Carbon Dioxide (CO₂) cylinders with visual status
"""
import os
import json
from datetime import datetime, timedelta
from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify, current_app, send_file
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename

from models import (db, GasCylinder, GasSystemComponent, CylinderLog,
                    CylinderOrder, EquipmentRepair, User, MonthlyArchive,
                    GasQualityCheck, Monteur, now_local)
from utils import role_required, log_audit, create_notification, safe_commit, safe_int, safe_float, safe_date, save_uploaded_file
from security import director_only

bp = Blueprint('gas', __name__, url_prefix='/gas')


@bp.after_request
def _gas_no_cache(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response

# Working set (норма): 1 in_use + 1 ready full spare per gas type
WORKING_IN_USE = 1
WORKING_READY_FULL = 1
# Минимум полных баллонов: смешанный газ — от 3, иначе заказ
MIN_FULL = {'nitrogen': 1, 'co2': 1, 'mixed': 3}
GAS_TYPES = ('nitrogen', 'co2', 'mixed')
GAS_LABELS = {'nitrogen': 'N₂', 'co2': 'CO₂', 'mixed': 'Mixed gas'}


def _mark_empty_leaves_site(c, user_id=None, note=''):
    """Пустой баллон отмечается empty (остаётся в истории/отображении)."""
    old = c.status
    c.status = 'empty'
    c.side = None
    c.installed_at = None
    db.session.add(CylinderLog(
        cylinder_id=c.id,
        action='status_%s_to_empty' % (old or 'unknown'),
        performed_by=user_id,
        notes=(note or 'Empty — waiting return with next delivery')
    ))
    return c


def _inventory_balance():
    """Per-gas inventory. Empty kept for full information (user request)."""
    bal = {}
    for gt in GAS_TYPES:
        rows = db.session.query(
            GasCylinder.status, db.func.count()
        ).filter(
            GasCylinder.gas_type == gt,
            GasCylinder.status.notin_(('scanned', 'returned')),
            GasCylinder.received_at.isnot(None),
        ).group_by(GasCylinder.status).all()
        counts = {s or 'unknown': int(c or 0) for s, c in rows}
        full = counts.get('full', 0)
        in_use = counts.get('in_use', 0)
        empty = counts.get('empty', 0)
        maint = counts.get('maintenance', 0)
        defect = counts.get('defect', 0)
        total = full + in_use + empty + maint + defect
        pending = GasCylinder.query.filter(
            GasCylinder.gas_type == gt,
            db.or_(GasCylinder.status == 'scanned', GasCylinder.received_at.is_(None))
        ).count()
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        ordered_month = db.session.query(db.func.coalesce(db.func.sum(CylinderOrder.quantity), 0)).filter(
            CylinderOrder.gas_type == gt,
            CylinderOrder.ordered_at >= month_start
        ).scalar() or 0
        delivered_month = db.session.query(db.func.coalesce(db.func.sum(CylinderOrder.quantity), 0)).filter(
            CylinderOrder.gas_type == gt,
            CylinderOrder.status == 'delivered',
            CylinderOrder.ordered_at >= month_start
        ).scalar() or 0
        bal[gt] = {
            'label': GAS_LABELS[gt],
            'full': full,
            'in_use': in_use,
            'empty': empty,
            'maintenance': maint,
            'defect': defect,
            'total': total,
            'pending_scans': int(pending),
            'identity_ok': True,
            'has_working_pair': (in_use >= WORKING_IN_USE and full >= MIN_FULL.get(gt, WORKING_READY_FULL)) if gt != 'mixed' else (full >= MIN_FULL['mixed']),
            'ready_full': full,
            'spare_full': max(0, full - WORKING_READY_FULL),
            'need_order': (full < MIN_FULL.get(gt, WORKING_READY_FULL)) if gt == 'mixed' else (in_use < WORKING_IN_USE or full < MIN_FULL.get(gt, WORKING_READY_FULL)),
            'ordered_qty': int(ordered_month),
            'delivered_qty': int(delivered_month),
            'month_label': now.strftime('%Y-%m'),
        }
    return bal


def _promote_full_to_in_use(gas_type, reason='', user_id=None):
    """Take one full cylinder and put it into service (keeps 1+1 working set)."""
    full = GasCylinder.query.filter(
        GasCylinder.gas_type == gas_type,
        GasCylinder.status == 'full'
    ).order_by(GasCylinder.received_at.asc().nullslast(), GasCylinder.id.asc()).first()
    if not full:
        return None
    full.status = 'in_use'
    full.installed_at = datetime.utcnow()
    db.session.add(CylinderLog(
        cylinder_id=full.id,
        action='status_full_to_in_use',
        performed_by=user_id,
        notes=reason or f'Auto-promoted to in_use ({gas_type})'
    ))
    return full


@bp.before_request
def check_gas_access():
    """Check gas section access."""
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    from utils import user_has_section_access
    if not user_has_section_access('gas'):
        flash(_('Access denied. Insufficient rights.'), 'error')
        return redirect(url_for('index'))


def _auto_archive(now):
    """Архив прошлого месяца: пустые баллоны + заказано/получено. Счётчик месяца сбрасывается."""
    # archive previous month once
    prev_month = (now.replace(day=1) - timedelta(days=1))
    month_key = prev_month.strftime('%Y-%m')
    existing = MonthlyArchive.query.filter_by(archive_month=month_key, section='gas_cylinders').first()
    if existing:
        return

    month_start = prev_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    empty = GasCylinder.query.filter_by(status='empty').all()
    orders_m = CylinderOrder.query.filter(
        CylinderOrder.ordered_at >= month_start, CylinderOrder.ordered_at < month_end
    ).all()
    received_logs_m = CylinderLog.query.filter(
        CylinderLog.action.in_(('created', 'received')),
        CylinderLog.date >= month_start, CylinderLog.date < month_end
    ).all()

    snapshot = {
        'empty': [{'number': c.cylinder_number, 'gas': c.gas_type,
                   'refill': str(c.refill_date or '')} for c in empty],
        'summary': {
            'ordered_n2': sum(o.quantity or 0 for o in orders_m if o.gas_type == 'nitrogen'),
            'ordered_co2': sum(o.quantity or 0 for o in orders_m if o.gas_type == 'co2'),
            'received_count': len(received_logs_m),
            'total_on_hand': GasCylinder.query.count(),
        },
    }
    archive = MonthlyArchive(
        archive_month=month_key,
        section='gas_cylinders',
        data_json=json.dumps(snapshot, ensure_ascii=False)
    )
    db.session.add(archive)
    if not safe_commit():
        return
    log_audit('auto_archive', 'gas_cylinders', 0, f'Gas archive {month_key}')


# ============================================================
# MAIN VIEW — Interactive Cylinder Dashboard
# ============================================================

def _orders_by_month(orders_all, now):
    """Group cylinder orders by month with ordered/used counts for the UI."""
    from collections import OrderedDict
    months = OrderedDict()
    # used (became empty) per month from logs
    used_by_month = {}
    for log in CylinderLog.query.filter(CylinderLog.action.like('%_to_empty%')).all():
        key = (log.date or log.date).strftime('%Y-%m') if log.date else None
        if not key:
            continue
        used_by_month[key] = used_by_month.get(key, 0) + 1
    for o in orders_all:
        if not o.ordered_at:
            continue
        key = o.ordered_at.strftime('%Y-%m')
        if key not in months:
            months[key] = {
                'key': key,
                'label': o.ordered_at.strftime('%Y-%m'),
                'title': o.ordered_at.strftime('%B %Y'),
                'ordered': 0,
                'ordered_n2': 0,
                'ordered_co2': 0,
                'delivered': 0,
                'used': used_by_month.get(key, 0),
                'rows': [],  # NOT 'items' — collides with dict.items in Jinja
            }
        qty = int(o.quantity or 0)
        months[key]['ordered'] += qty
        if o.gas_type == 'nitrogen':
            months[key]['ordered_n2'] += qty
        else:
            months[key]['ordered_co2'] += qty
        if o.status == 'delivered':
            months[key]['delivered'] += qty
        months[key]['rows'].append(o)
    # sort newest month first
    return sorted(months.values(), key=lambda m: m['key'], reverse=True)


def _cleanup_legacy_empty():
    """Пустые остаются в системе (для полной информации) — не трогаем."""
    return None


@bp.route('/')
@login_required
def gas_dashboard():
    """Main gas module view with interactive cylinder visualization"""
    # Auto-archive previous month (empty cylinders + monthly counters)
    now = datetime.utcnow()
    _auto_archive(now)

    cylinders = GasCylinder.query.order_by(GasCylinder.gas_type, GasCylinder.cylinder_number).all()
    components = GasSystemComponent.query.order_by(
        GasSystemComponent.gas_type, GasSystemComponent.component_type).all()
    # All orders grouped by month (for collapsible Bestellingen)
    orders = CylinderOrder.query.order_by(CylinderOrder.ordered_at.desc()).limit(10).all()
    orders_all = CylinderOrder.query.order_by(CylinderOrder.ordered_at.desc()).all()
    orders_by_month = _orders_by_month(orders_all, now)

    n2_cylinders = [c for c in cylinders if c.gas_type == 'nitrogen']
    co2_cylinders = [c for c in cylinders if c.gas_type == 'co2']
    mixed_cylinders = [c for c in cylinders if c.gas_type == 'mixed']

    balance = _inventory_balance()
    stats = {
        'n2_full': balance['nitrogen']['full'],
        'n2_in_use': balance['nitrogen']['in_use'],
        'n2_empty': balance['nitrogen']['empty'],
        'n2_maintenance': balance['nitrogen']['maintenance'],
        'n2_total': balance['nitrogen']['total'],
        'co2_full': balance['co2']['full'],
        'co2_in_use': balance['co2']['in_use'],
        'co2_empty': balance['co2']['empty'],
        'co2_maintenance': balance['co2']['maintenance'],
        'co2_total': balance['co2']['total'],
        # NOTE: never mix N₂ and CO₂ into one "total_*" — arithmetic is per gas type
    }

    # Monthly consumption: count cylinders that became empty this month
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    consumed_logs = CylinderLog.query.filter(
        CylinderLog.action.like('%_to_empty%'),
        CylinderLog.date >= month_start
    ).all()
    consumed_ids = set(l.cylinder_id for l in consumed_logs)
    n2_consumed = len([cid for cid in consumed_ids if any(c.id == cid and c.gas_type == 'nitrogen' for c in cylinders)])
    co2_consumed = len([cid for cid in consumed_ids if any(c.id == cid and c.gas_type == 'co2' for c in cylinders)])

    stats['n2_consumed_month'] = n2_consumed
    stats['co2_consumed_month'] = co2_consumed
    stats['total_consumed_month'] = n2_consumed + co2_consumed

    # Received this month = ФАКТИЧЕСКИ доставлено (delivered orders),
    # а не «сколько строк created» (сканы/лист приёмки искусственно занижают/завышают).
    delivered_orders = CylinderOrder.query.filter(
        CylinderOrder.status == 'delivered',
        CylinderOrder.ordered_at >= month_start
    ).all()
    n2_received = sum(int(o.quantity or 0) for o in delivered_orders if o.gas_type == 'nitrogen')
    co2_received = sum(int(o.quantity or 0) for o in delivered_orders if o.gas_type == 'co2')
    stats['n2_received_month'] = n2_received
    stats['co2_received_month'] = co2_received
    stats['total_received_month'] = n2_received + co2_received

    # Заказано с начала месяца (все заказы)
    orders_month = CylinderOrder.query.filter(CylinderOrder.ordered_at >= month_start).all()
    stats['ordered_n2_month'] = sum(o.quantity or 0 for o in orders_month if o.gas_type == 'nitrogen')
    stats['ordered_co2_month'] = sum(o.quantity or 0 for o in orders_month if o.gas_type == 'co2')
    stats['ordered_total_month'] = stats['ordered_n2_month'] + stats['ordered_co2_month']

    # Итого в наличии + неисправности + в работе (с указанием стороны)
    stats['total_on_hand'] = stats['n2_total'] + stats['co2_total']
    stats['n2_defect'] = balance['nitrogen'].get('defect', 0) + balance['nitrogen'].get('maintenance', 0)
    stats['co2_defect'] = balance['co2'].get('defect', 0) + balance['co2'].get('maintenance', 0)
    stats['total_defect'] = stats['n2_defect'] + stats['co2_defect']
    stats['n2_in_use_list'] = [c for c in n2_cylinders if c.status == 'in_use']
    stats['co2_in_use_list'] = [c for c in co2_cylinders if c.status == 'in_use']
    stats['n2_empty_list'] = [c for c in n2_cylinders if c.status == 'empty']
    stats['co2_empty_list'] = [c for c in co2_cylinders if c.status == 'empty']

    # Spare cylinders (empty, available for replacement)
    n2_spare = [c for c in n2_cylinders if c.status == 'empty']
    co2_spare = [c for c in co2_cylinders if c.status == 'empty']

    # Low stock: working pair broken (need 1 in_use + 1 ready full per gas)
    low_stock = []
    for gt in ('nitrogen', 'co2'):
        b = balance[gt]
        if not b['has_working_pair']:
            low_stock.append(b['label'])

    return render_template('gas/dashboard.html',
                           cylinders=cylinders,
                           n2_cylinders=n2_cylinders,
                           co2_cylinders=co2_cylinders,
                           mixed_cylinders=mixed_cylinders,
                           n2_spare=n2_spare,
                           co2_spare=co2_spare,
                           components=components,
                           orders=orders,
                           orders_by_month=orders_by_month,
                           stats=stats,
                           balance=balance,
                           low_stock=low_stock)


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
            performed_by=current_user.id,
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
                performed_by=current_user.id,
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
        _mark_empty_leaves_site(c, user_id=current_user.id)
    else:
        c.status = new_status
        if new_status == 'in_use' and not c.installed_at:
            c.installed_at = datetime.utcnow()

    # Only ONE gas can be in use at a time (N2 or CO2 or MIX).
    # When activating a new one, others go back to full (installed, waiting) — not empty.
    if new_status == 'in_use':
        others = GasCylinder.query.filter(
            GasCylinder.status == 'in_use',
            GasCylinder.id != c.id
        ).all()
        for other in others:
            other.status = 'full'
            if not other.installed_at:
                other.installed_at = datetime.utcnow()
            try:
                from models import CylinderLog
                db.session.add(CylinderLog(
                    cylinder_id=other.id,
                    action='in_use_to_full',
                    user_id=current_user.id if isinstance(current_user.id, int) else None,
                    note=f'Standby: switched to {c.cylinder_number} ({c.gas_type})',
                ))
            except Exception:
                pass

    # When an in_use cylinder empties → promote one full spare into service
    if old_status == 'in_use' and new_status == 'empty':
        promoted = _promote_full_to_in_use(
            c.gas_type,
            reason=f'Auto-switch: backup activated after {c.cylinder_number} emptied',
            user_id=current_user.id,
        )
        if not promoted:
            flash(_('No full spare cylinder to activate! Order new %(gas)s cylinders.',
                    gas=GAS_LABELS.get(c.gas_type, c.gas_type)), 'warning')

    if not safe_commit():
        if request.is_json:
            return jsonify({'error': 'Save failed'}), 500
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('gas.gas_dashboard'))

    db.session.add(CylinderLog(
        cylinder_id=c.id,
        action='status_%s_to_%s' % (old_status, new_status),
        performed_by=current_user.id,
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


@bp.route('/cylinders/<int:cyl_id>/swap', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def cylinder_swap(cyl_id):
    """Swap cylinder: current leaves as empty (taken away), promote a full spare into service."""
    c = GasCylinder.query.get_or_404(cyl_id)
    old_number = c.cylinder_number
    _mark_empty_leaves_site(c, user_id=current_user.id, note=f'Swapped out {old_number}')
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('gas.gas_dashboard'))

    db.session.add(CylinderLog(
        cylinder_id=c.id,
        action='removed',
        old_cylinder_number=old_number,
        performed_by=current_user.id,
        notes=f'Cylinder removed from service'
    ))
    if not safe_commit():
        flash(_('Cylinder marked as empty but log entry failed'), 'warning')

    # Keep the working set: 1 in_use + 1 full spare
    promoted = _promote_full_to_in_use(
        c.gas_type,
        reason=f'Auto-promoted after swap of {old_number}',
        user_id=current_user.id,
    )
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('gas.gas_dashboard'))

    log_audit('swap', 'gas_cylinder', c.id, f'{c.gas_type} #{old_number} swapped')
    if promoted:
        flash(_('Cylinder %(old)s → empty. %(new)s is now in use.',
                old=old_number, new=promoted.cylinder_number), 'success')
    else:
        flash(_('Cylinder marked as empty. No full spare — order new cylinders!'), 'warning')
    return redirect(url_for('gas.gas_dashboard'))


@bp.route('/archive', methods=['POST'])
@login_required
@role_required('admin')
def gas_archive():
    """Snapshot empty cylinders to MonthlyArchive (keep them in stock — balance identity)."""
    now = datetime.utcnow()
    month_key = now.strftime('%Y-%m')

    empty = GasCylinder.query.filter_by(status='empty').all()
    if not empty:
        flash(_('No empty cylinders to archive'), 'info')
        return redirect(url_for('gas.gas_dashboard'))

    # Save snapshot only — DO NOT delete (full + in_use + empty = total must hold)
    snapshot = []
    for c in empty:
        snapshot.append({
            'id': c.id, 'gas_type': c.gas_type,
            'cylinder_number': c.cylinder_number, 'barcode': c.barcode,
            'status': c.status,
            'received_at': c.received_at.strftime('%Y-%m-%d') if c.received_at else None,
            'installed_at': c.installed_at.strftime('%Y-%m-%d') if c.installed_at else None,
            'notes': c.notes or ''
        })

    archive = MonthlyArchive(
        archive_month=month_key,
        section='gas_cylinders',
        data_json=json.dumps(snapshot, ensure_ascii=False)
    )
    db.session.add(archive)

    count = len(empty)
    if not safe_commit():
        flash(_('Archive failed. Please try again.'), 'error')
        return redirect(url_for('gas.gas_dashboard'))
    log_audit('archive', 'gas_cylinders', 0, f'{count} empty cylinders snapshotted for {month_key}')
    flash(_('%(count)d empty cylinders snapshotted (kept in stock)', count=count), 'success')
    return redirect(url_for('gas.gas_dashboard'))


# ============================================================
# COMPONENTS (Regulators, Valves, etc.)
# ============================================================

@bp.route('/components')
@login_required
def components_list():
    gas_type = request.args.get('gas', '')
    q = GasSystemComponent.query
    if gas_type:
        q = q.filter_by(gas_type=gas_type)
    components = q.order_by(GasSystemComponent.gas_type, GasSystemComponent.component_type).all()
    return render_template('gas/components.html', components=components, gas_filter=gas_type)


@bp.route('/components/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def component_new():
    if request.method == 'POST':
        c = GasSystemComponent(
            gas_type=request.form['gas_type'],
            component_type=request.form['component_type'],
            name=request.form['name'],
            status=request.form.get('status', 'ok'),
            notes=request.form.get('notes', '')
        )
        if request.form.get('last_check'):
            d = safe_date(request.form.get('last_check'))
            c.last_check = d.date() if d else None
        if request.form.get('next_check'):
            d = safe_date(request.form.get('next_check'))
            c.next_check = d.date() if d else None
        db.session.add(c)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('gas.component_new'))
        log_audit('create', 'gas_component', c.id, f'{c.component_type}: {c.name}')
        flash(_('Component added'), 'success')
        return redirect(url_for('gas.components_list'))

    return render_template('gas/component_form.html', component=None)


@bp.route('/components/<int:comp_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def component_edit(comp_id):
    c = GasSystemComponent.query.get_or_404(comp_id)
    if request.method == 'POST':
        c.gas_type = request.form['gas_type']
        c.component_type = request.form['component_type']
        c.name = request.form['name']
        c.status = request.form.get('status', c.status)
        c.notes = request.form.get('notes', '')
        if request.form.get('last_check'):
            d = safe_date(request.form.get('last_check'))
            c.last_check = d.date() if d else None
        if request.form.get('next_check'):
            d = safe_date(request.form.get('next_check'))
            c.next_check = d.date() if d else None
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('gas.component_edit', comp_id=comp_id))
        log_audit('update', 'gas_component', c.id, f'{c.component_type}: {c.name}')
        flash(_('Component updated'), 'success')
        return redirect(url_for('gas.components_list'))

    return render_template('gas/component_form.html', component=c)


@bp.route('/components/<int:comp_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def component_delete(comp_id):
    c = GasSystemComponent.query.get_or_404(comp_id)
    name = f'{c.component_type}: {c.name}'
    db.session.delete(c)
    if not safe_commit():
        flash(_('Delete failed. Please try again.'), 'error')
        return redirect(url_for('gas.components_list'))
    log_audit('delete', 'gas_component', comp_id, name)
    flash(_('Component deleted'), 'success')
    return redirect(url_for('gas.components_list'))


# ============================================================
# ORDERS
# ============================================================

@bp.route('/orders')
@login_required
@role_required('admin', 'technician')
def orders_list():
    orders = CylinderOrder.query.order_by(CylinderOrder.ordered_at.desc()).all()
    return render_template('gas/orders.html', orders=orders)


@bp.route('/orders/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'technician')
def order_new():
    if request.method == 'POST':
        o = CylinderOrder(
            gas_type=request.form['gas_type'],
            quantity=safe_int(request.form.get('quantity'), 1),
            status='pending',
            supplier=request.form.get('supplier', ''),
            reason=request.form.get('reason', ''),
            ordered_by=current_user.id
        )
        db.session.add(o)
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('gas.order_new'))
        log_audit('create', 'cylinder_order', o.id, f'{o.gas_type} x{o.quantity}')
        flash(_('Order created'), 'success')
        return redirect(url_for('gas.orders_list'))

    return render_template('gas/order_form.html', order=None)


@bp.route('/orders/<int:order_id>/status', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def order_status(order_id):
    o = CylinderOrder.query.get_or_404(order_id)
    new_status = request.form.get('status', 'pending')
    old_status = o.status
    o.status = new_status
    if new_status == 'delivered':
        o.delivered_at = datetime.utcnow()
    if not safe_commit():
        flash(_('Save failed. Please try again.'), 'error')
        return redirect(url_for('gas.orders_list'))

    # On delivery: add `quantity` full cylinders so inventory identity holds:
    # leftover_full + received_full + empty + in_use = total
    if new_status == 'delivered' and old_status != 'delivered':
        qty = max(1, safe_int(o.quantity) or 1)
        created = 0
        for i in range(1, qty + 1):
            number = f'ORD{o.id}-{i}'
            if GasCylinder.query.filter(
                (GasCylinder.cylinder_number == number) | (GasCylinder.barcode == number)
            ).first():
                continue
            cyl = GasCylinder(
                gas_type=o.gas_type,
                cylinder_number=number,
                barcode=number,
                status='full',
                received_at=o.delivered_at or datetime.utcnow(),
                notes=f'From order #{o.id} ({o.supplier or "-"})'
            )
            db.session.add(cyl)
            db.session.flush()
            db.session.add(CylinderLog(
                cylinder_id=cyl.id,
                action='created',
                new_cylinder_number=number,
                performed_by=current_user.id,
                notes=f'Received via order #{o.id} x{qty}'
            ))
            created += 1
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('gas.orders_list'))
        if created:
            flash(_('Order delivered: %(n)d full %(gas)s cylinders added to stock',
                    n=created, gas=GAS_LABELS.get(o.gas_type, o.gas_type)), 'success')
        else:
            flash(_('Order marked delivered (cylinders already in stock)'), 'info')
        log_audit('deliver', 'cylinder_order', o.id, f'{o.gas_type} x{qty}, created={created}')
    else:
        flash(_('Order status updated'), 'success')
    return redirect(url_for('gas.orders_list'))


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


@bp.route('/api/cylinders/<int:cyl_id>/update', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def api_cylinder_update(cyl_id):
    """Quick update from interactive dashboard"""
    c = GasCylinder.query.get_or_404(cyl_id)
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    old_status = c.status
    if 'status' in data:
        if data['status'] not in ('full', 'in_use', 'empty', 'maintenance'):
            return jsonify({'error': 'Invalid status'}), 400
        c.status = data['status']
    if 'notes' in data:
        c.notes = data['notes']
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500

    if old_status != c.status:
        log = CylinderLog(
            cylinder_id=c.id,
            action=f'status_{old_status}_to_{c.status}',
            performed_by=current_user.id,
            notes=data.get('reason', f'Status: {old_status} → {c.status}')
        )
        db.session.add(log)
        if not safe_commit():
            return jsonify({'error': 'Save failed'}), 500

    return jsonify({'ok': True, 'status': c.status})


@bp.route('/api/cylinders/quick-add', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def cylinder_quick_add():
    """Find or create cylinder by scanned number, return it for status selection."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    number = (data.get('number') or '').strip()
    gas_type = data.get('gas_type', 'nitrogen')

    if not number:
        return jsonify({'error': 'No number'}), 400
    if gas_type not in GAS_TYPES:
        return jsonify({'error': 'Invalid gas type'}), 400

    # Find by barcode or cylinder_number
    cylinder = GasCylinder.query.filter(
        (GasCylinder.barcode == number) | (GasCylinder.cylinder_number == number)
    ).first()

    if cylinder:
        return jsonify({
            'found': True,
            'id': cylinder.id,
            'number': cylinder.cylinder_number,
            'gas_type': cylinder.gas_type,
            'status': cylinder.status
        })

    # Create new
    cylinder = GasCylinder(
        gas_type=gas_type,
        cylinder_number=number,
        barcode=number,
        status='full',
        received_at=datetime.utcnow()
    )
    db.session.add(cylinder)
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500

    db.session.add(CylinderLog(
        cylinder_id=cylinder.id,
        action='created',
        new_cylinder_number=number,
        performed_by=current_user.id,
        notes='Added via dashboard scan'
    ))
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500

    log_audit('create', 'gas_cylinder', cylinder.id, f'{gas_type} #{number}')

    return jsonify({
        'found': False,
        'id': cylinder.id,
        'number': cylinder.cylinder_number,
        'gas_type': cylinder.gas_type,
        'status': cylinder.status
    })


# ============================================================
# BARCODE SCANNING — cylinder install/replace
# ============================================================

@bp.route('/api/cylinders/scan-number', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only()
def cylinder_scan_number():
    """Первое сканирование — НОМЕР баллона (главный идентификатор)."""
    data = request.get_json() or {}
    number = (data.get('number') or data.get('code') or '').strip()
    gas_type = data.get('gas_type') or 'nitrogen'
    if not number:
        return jsonify({'error': 'No number'}), 400
    if gas_type not in GAS_TYPES:
        gas_type = 'nitrogen'
    c = GasCylinder.query.filter(GasCylinder.cylinder_number == number).first()
    created = False
    if not c:
        c = GasCylinder(gas_type=gas_type, cylinder_number=number, barcode=number,
                        status='full', received_at=datetime.utcnow())
        db.session.add(c)
        db.session.flush()
        created = True
        db.session.add(CylinderLog(cylinder_id=c.id, action='created',
                                   performed_by=current_user.id,
                                   new_cylinder_number=number, notes='Scan number'))
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'created': created, 'cylinder_id': c.id,
                    'number': c.cylinder_number, 'gas_type': c.gas_type,
                    'status': c.status, 'side': c.side or '',
                    'refill_date': c.refill_date.isoformat() if c.refill_date else None})


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
                               performed_by=current_user.id,
                               notes=f'Refill {refill.isoformat()}'))
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'number': c.cylinder_number,
                    'refill_date': c.refill_date.isoformat(),
                    'gas_type': c.gas_type, 'status': c.status})


def _parse_z_refill(code):
    """Штрихкод заправки: Z + дата (Z20261005, Z26-10-05, Z26/10/05)."""
    import re as _re
    from datetime import date as _date
    s = (code or '').strip().upper()
    if not s.startswith('Z'):
        return None
    s = s[1:]
    try:
        if len(s) == 8 and s.isdigit():
            return _date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        for sep in ('-', '/'):
            if sep in s:
                parts = s.split(sep)
                if len(parts) == 3:
                    a, b, c_ = parts
                    if len(a) == 4:
                        return _date(int(a), int(b), int(c_))
                    return _date(2000 + int(a), int(b), int(c_))
    except ValueError:
        return None
    return None


@bp.route('/scan-list')
@login_required
@role_required('admin', 'director', 'technician')
def cylinder_scan_list():
    """Scan list page. Shows recent scans from DB so the list is not lost."""
    from datetime import timedelta
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
    """Добавить номер баллона в список приёмки (без требования Z)."""
    data = request.get_json() or {}
    number = (data.get('number') or '').strip()
    gas_type = data.get('gas_type') or 'nitrogen'
    if not number:
        return jsonify({'error': 'No number'}), 400
    if gas_type not in GAS_TYPES:
        gas_type = 'nitrogen'
    c = GasCylinder.query.filter_by(cylinder_number=number).first()
    created = False
    if not c:
        # scanned only — NOT in stock until «Добавить на склад»
        c = GasCylinder(gas_type=gas_type, cylinder_number=number,
                        status='scanned', received_at=None)
        db.session.add(c)
        db.session.flush()
        created = True
        db.session.add(CylinderLog(cylinder_id=c.id, action='created',
                                   performed_by=current_user.id,
                                   new_cylinder_number=number,
                                   notes='Scan list (not in stock yet)'))
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
            cylinder_id=c.id, action='received', performed_by=current_user.id,
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
    """Balloons in the intake scan list (not yet accepted to stock)."""
    return GasCylinder.query.filter(
        db.or_(GasCylinder.status == 'scanned', GasCylinder.received_at.is_(None))
    ).count()


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
                                   performed_by=current_user.id,
                                   new_cylinder_number=number,
                                   notes=f'Scan install action={action or "install"}'))
    elif action == 'add_stock' and (not c.received_at or c.status == 'scanned'):
        if not c.received_at:
            c.received_at = datetime.utcnow()
        if c.status not in ('in_use', 'empty', 'maintenance', 'defect'):
            c.status = 'full'
        db.session.add(CylinderLog(
            cylinder_id=c.id, action='received', performed_by=current_user.id,
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
            other, user_id=current_user.id,
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
                               performed_by=current_user.id,
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
            performed_by=current_user.id,
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


@bp.route('/api/cylinders/add', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def cylinder_add():
    """Add cylinder to DB only (status=full). No install/activation."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    code = (data.get('code') or '').strip()
    gas_type = data.get('gas_type', 'nitrogen')

    if not code:
        return jsonify({'error': 'No code provided'}), 400
    if gas_type not in GAS_TYPES:
        return jsonify({'error': 'Invalid gas type'}), 400

    existing = GasCylinder.query.filter(
        (GasCylinder.barcode == code) | (GasCylinder.cylinder_number == code)
    ).first()
    if existing:
        return jsonify({'ok': True, 'cylinder_id': existing.id, 'cylinder_number': existing.cylinder_number,
                        'already_exists': True, 'status': existing.status})

    cylinder = GasCylinder(
        gas_type=gas_type,
        cylinder_number=code,
        barcode=code,
        status='full',
        received_at=datetime.utcnow()
    )
    db.session.add(cylinder)
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    db.session.add(CylinderLog(
        cylinder_id=cylinder.id,
        action='created',
        new_cylinder_number=code,
        performed_by=current_user.id,
        notes='Added to DB (status=full)'
    ))
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500

    return jsonify({'ok': True, 'cylinder_id': cylinder.id, 'cylinder_number': cylinder.cylinder_number,
                    'already_exists': False, 'status': 'full'})


@bp.route('/api/cylinders/scan', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def cylinder_scan_install():
    """Scan barcode → install cylinder on selected gas type + side.
    OLD FLOW (restored):
    1. Find or create cylinder by barcode
    2. Set scanned cylinder to 'full' (newly installed, полный)
    3. Set the OTHER cylinder of same gas type to 'in_use' (в работе)
    4. Previous 'in_use' cylinder on same side → 'empty'
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    code = (data.get('code') or '').strip()
    gas_type = data.get('gas_type', 'nitrogen')
    side = data.get('side', 'left')

    if not code:
        return jsonify({'error': 'No barcode scanned'}), 400
    if gas_type not in GAS_TYPES:
        return jsonify({'error': 'Invalid gas type'}), 400
    if side not in ('left', 'right'):
        return jsonify({'error': 'Invalid side'}), 400

    # Find cylinder by barcode or cylinder_number
    cylinder = GasCylinder.query.filter(
        (GasCylinder.barcode == code) | (GasCylinder.cylinder_number == code)
    ).first()

    created = False
    if not cylinder:
        # Create new cylinder from scan
        cylinder = GasCylinder(
            gas_type=gas_type,
            cylinder_number=code,
            barcode=code,
            status='full',
            received_at=datetime.utcnow()
        )
        db.session.add(cylinder)
        if not safe_commit():
            return jsonify({'error': 'Save failed'}), 500
        db.session.add(CylinderLog(
            cylinder_id=cylinder.id,
            action='created',
            new_cylinder_number=code,
            performed_by=current_user.id,
            notes='Created via barcode scan'
        ))
        if not safe_commit():
            return jsonify({'error': 'Save failed'}), 500
        created = True

    # 1. Set scanned cylinder to 'full' (newly installed, полный)
    old_status = cylinder.status
    cylinder.status = 'full'
    cylinder.gas_type = gas_type
    cylinder.installed_at = datetime.utcnow()
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500

    db.session.add(CylinderLog(
        cylinder_id=cylinder.id,
        action='status_%s_to_full' % old_status,
        performed_by=current_user.id,
        notes='Installed via scan on %s side (%s) — set as FULL' % (side, gas_type)
    ))
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500

    # 2. The OTHER cylinder of same gas type → 'in_use' (в работе)
    other = GasCylinder.query.filter(
        GasCylinder.gas_type == gas_type,
        GasCylinder.status == 'full',
        GasCylinder.id != cylinder.id
    ).first()
    other_activated = None
    if other:
        prev = other.status
        other.status = 'in_use'
        other.installed_at = datetime.utcnow()
        db.session.add(CylinderLog(
            cylinder_id=other.id,
            action='status_%s_to_in_use' % prev,
            performed_by=current_user.id,
            notes='Auto-activated: new cylinder %s installed (full)' % cylinder.cylinder_number
        ))
        other_activated = other.cylinder_number

    # 3. Old 'in_use' cylinders that are NOT the activated one → 'empty'
    # (keep at most 1 in_use per gas type — the freshly activated one)
    old_in_use = GasCylinder.query.filter(
        GasCylinder.gas_type == gas_type,
        GasCylinder.status == 'in_use',
        GasCylinder.id != cylinder.id,
        GasCylinder.id != (other.id if other else -1)
    ).all()
    for c in old_in_use:
        _mark_empty_leaves_site(
            c, user_id=current_user.id,
            note='Auto-emptied: replaced during install of %s' % cylinder.cylinder_number
        )

    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500

    return jsonify({
        'ok': True,
        'cylinder_id': cylinder.id,
        'cylinder_number': cylinder.cylinder_number,
        'gas_type': gas_type,
        'side': side,
        'old_status': old_status,
        'created': created,
        'other_activated': other_activated
    })


# ── Контроль качества газа (O₂ / CO₂) ─────────────────────────────

@bp.route('/quality')
@login_required
@role_required('admin', 'director', 'technician', 'user', 'responsible')
def gas_quality_list():
    today = datetime.utcnow().date()
    month = request.args.get('month', today.strftime('%Y-%m'))
    try:
        y, m = map(int, month.split('-'))
        m_start = datetime(y, m, 1).date()
        m_end = datetime(y + 1, 1, 1).date() if m == 12 else datetime(y, m + 1, 1).date()
    except ValueError:
        m_start = today.replace(day=1)
        m_end = (m_start + timedelta(days=32)).replace(day=1)
    checks = GasQualityCheck.query.filter(
        GasQualityCheck.check_date >= m_start,
        GasQualityCheck.check_date < m_end
    ).order_by(GasQualityCheck.check_date.desc(), GasQualityCheck.check_time.desc()).all()
    last = GasQualityCheck.query.order_by(GasQualityCheck.check_time.desc()).first()
    today_checks = GasQualityCheck.query.filter_by(check_date=today).all()
    mechanics = User.query.filter(User.role == 'technician', User.is_active_user == True).order_by(User.display_name).all()
    prev_m = (m_start - timedelta(days=1)).strftime('%Y-%m')
    next_m = m_end.strftime('%Y-%m')
    return render_template('gas_quality.html',
        checks=checks, last=last, today_checks=today_checks,
        mechanics=mechanics, month=month,
        month_start=m_start, month_end=m_end,
        prev_month=prev_m, next_month=next_m)


@bp.route('/quality/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
@director_only()
def gas_quality_new():
    from security import is_head_of_ts
    if not (current_user.has_role('admin', 'technician') or is_head_of_ts(current_user)):
        flash(_('Access denied. Insufficient rights.'), 'error')
        return redirect(url_for('gas.gas_quality_list'))
    if request.method == 'POST':
        try:
            o2 = float(request.form.get('o2_percent') or '')
            co2 = float(request.form.get('co2_percent') or '')
        except ValueError:
            flash(_('O2 and CO2 must be numbers'), 'error')
            return redirect(url_for('gas.gas_quality_new'))
        if not (0 <= o2 <= 100) or not (0 <= co2 <= 100):
            flash(_('Percent must be 0–100'), 'error')
            return redirect(url_for('gas.gas_quality_new'))
        now = now_local()
        d = safe_date(request.form.get('check_date')) or now
        # safe_date возвращает datetime — для Date-колонки нужен date
        d = d.date() if isinstance(d, datetime) else d
        # защита от дублей: повторный POST тех же значений (двойное нажатие, медленная сеть).
        # сравниваем в Python — Numeric-колонки в SQLite хранятся как текст, float в SQL не матчит
        recent = GasQualityCheck.query.filter(
            GasQualityCheck.check_date == d,
            GasQualityCheck.created_at >= now - timedelta(seconds=120),
        ).order_by(GasQualityCheck.id.desc()).first()
        if recent is not None and \
                float(recent.o2_percent if recent.o2_percent is not None else -1) == o2 and \
                float(recent.co2_percent if recent.co2_percent is not None else -1) == co2:
            flash(_('This measurement was already saved'), 'info')
            return redirect(url_for('gas.gas_quality_list', month=d.strftime('%Y-%m')))
        chk = GasQualityCheck(
            check_date=d,
            check_time=now,
            sampled_by_id=safe_int(request.form.get('sampled_by_id'), default=None),
            o2_percent=o2,
            co2_percent=co2,
            notes=request.form.get('notes', ''),
            mixed_gas_used=bool(request.form.get('mixed_gas_used')),
            created_by=current_user.id if isinstance(current_user.id, int) else None,
        )
        if 'photo' in request.files and request.files['photo'].filename:
            fn = save_uploaded_file(request.files['photo'], prefix='gq_')
            if fn:
                chk.photo = fn
        db.session.add(chk)
        if not safe_commit():
            flash(_('Save failed'), 'error')
        else:
            flash(_('Gas quality check saved'), 'success')
        return redirect(url_for('gas.gas_quality_list', month=d.strftime('%Y-%m')))
    mechanics = User.query.filter(User.role == 'technician', User.is_active_user == True).order_by(User.display_name).all()
    now = now_local()
    return render_template('gas_quality_form.html', mechanics=mechanics, now=now)


@bp.route('/quality/<int:check_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def gas_quality_edit(check_id):
    """Администратор может править замер качества газа."""
    chk = GasQualityCheck.query.get_or_404(check_id)
    if request.method == 'POST':
        try:
            o2 = float(request.form.get('o2_percent') or '')
            co2 = float(request.form.get('co2_percent') or '')
        except ValueError:
            flash(_('O2 and CO2 must be numbers'), 'error')
            return redirect(url_for('gas.gas_quality_edit', check_id=check_id))
        chk.o2_percent = o2
        chk.co2_percent = co2
        chk.notes = request.form.get('notes', '') or ''
        chk.mixed_gas_used = bool(request.form.get('mixed_gas_used'))
        chk.sampled_by_id = safe_int(request.form.get('sampled_by_id'), default=None)
        d = safe_date(request.form.get('check_date'))
        if d:
            chk.check_date = d
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('gas.gas_quality_edit', check_id=check_id))
        flash(_('Saved'), 'success')
        return redirect(url_for('gas.gas_quality_list'))
    users = User.query.filter(User.is_active_user == True).order_by(User.display_name).all()
    return render_template('gas_quality_form.html', check=chk, users=users, mechanics=users, editing=True, now=now_local())


@bp.route('/quality/<int:check_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'director')
@director_only()
def gas_quality_delete(check_id):
    chk = GasQualityCheck.query.get_or_404(check_id)
    month = chk.check_date.strftime('%Y-%m')
    db.session.delete(chk)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        flash(_('Deleted'), 'success')
    return redirect(url_for('gas.gas_quality_list', month=month))


# ============================================================
# PDF REPORT — gas, by month, for a period
# ============================================================

@bp.route('/archive/view')
@login_required
def gas_archive_view():
    """Архив газовых баллонов по месяцам (все свёрнуты) + отчёт/статистика."""
    try:
        return _gas_archive_view_impl()
    except Exception as e:
        print('GAS ARCHIVE ERROR', e)
        flash(_('An error occurred. Please try again.'), 'error')
        return redirect(url_for('gas.gas_dashboard'))


def _gas_archive_view_impl():
    import json as _json
    from collections import OrderedDict, defaultdict
    from models import FaultReport, EquipmentRepair, MaintenanceRecord
    rows = (MonthlyArchive.query.filter_by(section='gas_cylinders')
            .order_by(MonthlyArchive.archive_month.desc()).all())
    months = OrderedDict()
    for a in rows:
        try:
            data = _json.loads(a.data_json or '{}')
        except Exception:
            data = {}
        if isinstance(data, list):
            data = {}
        if not isinstance(data, dict):
            data = {}
        summary = data.get('summary') or {}
        if not isinstance(summary, dict):
            summary = {}
        empties = data.get('empty') or []
        if not isinstance(empties, list):
            empties = []
        empties = [e for e in empties if isinstance(e, dict)][:20]
        months[a.archive_month] = {
            'key': a.archive_month,
            'summary': summary,
            'empty_count': len(empties),
            'empty_sample': empties,
            'created_at': a.created_at,
        }

    def _ensure(mk):
        if mk not in months:
            months[mk] = {'key': mk, 'summary': {}, 'empty_count': 0, 'empty_sample': [], 'created_at': None}
        return months[mk]

    # Заказы и доставки по месяцам (реальные суммы)
    for o in CylinderOrder.query.all():
        if not o.ordered_at:
            continue
        k = o.ordered_at.strftime('%Y-%m')
        row = _ensure(k)
        q = int(o.quantity or 0)
        field = {'nitrogen': 'n2', 'co2': 'co2', 'mixed': 'mixed'}.get(o.gas_type, 'n2')
        row.setdefault('ordered_n2', 0)
        row.setdefault('ordered_co2', 0)
        row.setdefault('ordered_mixed', 0)
        row.setdefault('delivered_n2', 0)
        row.setdefault('delivered_co2', 0)
        row.setdefault('delivered_mixed', 0)
        row.setdefault('orders_list', [])
        row['ordered_' + field] = row.get('ordered_' + field, 0) + q
        if o.status == 'delivered':
            row['delivered_' + field] = row.get('delivered_' + field, 0) + q
        row['orders_list'].append({
            'id': o.id, 'gas': o.gas_type, 'qty': q, 'status': o.status,
            'date': o.ordered_at.strftime('%d-%m-%Y'),
            'delivered': o.delivered_at.strftime('%d-%m-%Y') if o.delivered_at else '',
        })

    # Расход (стало пустым) и возвраты по месяцам
    for log in CylinderLog.query.all():
        if not log.date:
            continue
        k = log.date.strftime('%Y-%m')
        row = _ensure(k)
        act = (log.action or '')
        if '_to_empty' in act or act == 'return_full':
            row['used_total'] = row.get('used_total', 0) + 1
        if act == 'return_full':
            row['returned_total'] = row.get('returned_total', 0) + 1
        if act in ('created', 'received'):
            row.setdefault('received_ops', 0)
            row['received_ops'] += 1

    # Неисправности / ремонты оборудования по месяцам
    for f in FaultReport.query.all():
        ts = f.created_at
        if not ts:
            continue
        k = ts.strftime('%Y-%m')
        row = _ensure(k)
        row.setdefault('faults', 0)
        row['faults'] += 1
    for r in EquipmentRepair.query.all():
        ts = r.date_broken or r.created_at
        if not ts:
            continue
        k = ts.strftime('%Y-%m')
        row = _ensure(k)
        row.setdefault('repairs', 0)
        row['repairs'] += 1

    months = OrderedDict(sorted(months.items(), key=lambda kv: kv[0], reverse=True))
    # normalize missing keys
    for row in months.values():
        for f in ('ordered_n2', 'ordered_co2', 'ordered_mixed',
                  'delivered_n2', 'delivered_co2', 'delivered_mixed',
                  'used_total', 'returned_total', 'faults', 'repairs', 'orders_list'):
            row.setdefault(f, 0 if f != 'orders_list' else [])
    balance = _inventory_balance()
    return render_template('gas/archive.html', months=months, balance=balance,
                           gas_labels=GAS_LABELS)


@bp.route('/returns', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def gas_returns():
    """Возврат полных баллонов поставщику (дата, тип, количество, причина)."""
    if request.method == 'POST':
        try:
            ret_date = datetime.strptime(request.form.get('return_date') or '', '%Y-%m-%d').date()
        except ValueError:
            ret_date = now_local().date()
        gas_type = request.form.get('gas_type') or 'nitrogen'
        if gas_type not in GAS_TYPES:
            gas_type = 'nitrogen'
        qty = safe_int(request.form.get('quantity'), 0)
        reason = (request.form.get('reason') or '').strip()
        if qty <= 0:
            flash(_('Quantity must be positive'), 'error')
            return redirect(url_for('gas.gas_returns'))
        fulls = GasCylinder.query.filter_by(gas_type=gas_type, status='full').order_by(
            GasCylinder.id.asc()).limit(qty).all()
        n = 0
        for c in fulls:
            c.status = 'returned'
            c.side = None
            db.session.add(CylinderLog(
                cylinder_id=c.id, action='return_full', performed_by=current_user.id,
                notes=f'Returned full cylinder {ret_date}: {reason}'
            ))
            n += 1
        # не нашлось полных — отмечаем фактический возврат (пусть empty/любые)
        if n < qty:
            rest = GasCylinder.query.filter(
                GasCylinder.gas_type == gas_type,
                GasCylinder.status.in_(('empty', 'full', 'scanned'))
            ).order_by(GasCylinder.id.asc()).limit(qty - n).all()
            for c in rest:
                if c.status == 'returned':
                    continue
                old = c.status
                c.status = 'returned'
                db.session.add(CylinderLog(
                    cylinder_id=c.id, action='return_full', performed_by=current_user.id,
                    notes=f'Returned ({old}) {ret_date}: {reason}'
                ))
                n += 1
        if not safe_commit():
            flash(_('Save failed'), 'error')
        else:
            flash(_('Cylinders returned') + f': {n} ({GAS_LABELS.get(gas_type, gas_type)}), {ret_date}', 'success')
        log_audit('return_full', 'gas_cylinder', 0, f'{gas_type} x{qty} {ret_date} {reason}')
        return redirect(url_for('gas.gas_returns'))
    returns = (CylinderLog.query.filter_by(action='return_full')
               .order_by(CylinderLog.date.desc()).limit(50).all())
    return render_template('gas/returns.html', returns=returns, gas_labels=GAS_LABELS, gas_types=GAS_TYPES)


@bp.route('/report/pdf')
@login_required
@role_required('admin', 'director')
def gas_report_pdf():
    """PDF: сколько заказано / израсходовано / неисправных — по месяцам за период."""
    from datetime import date as date_cls
    date_from = request.args.get('from') or request.args.get('date_from') or ''
    date_to = request.args.get('to') or request.args.get('date_to') or ''
    try:
        d_from = datetime.strptime(date_from, '%Y-%m-%d') if date_from else datetime.utcnow().replace(day=1) - timedelta(days=180)
        d_to = datetime.strptime(date_to, '%Y-%m-%d') if date_to else datetime.utcnow() + timedelta(days=1)
    except ValueError:
        d_from = datetime.utcnow().replace(day=1) - timedelta(days=180)
        d_to = datetime.utcnow() + timedelta(days=1)

    # Собираем месяцы
    months = []
    cur = d_from.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_m = d_to.replace(day=1)
    while cur <= end_m:
        m_start = cur
        if cur.month == 12:
            m_end = cur.replace(year=cur.year + 1, month=1)
        else:
            m_end = cur.replace(month=cur.month + 1)
        key = cur.strftime('%Y-%m')
        row = {
            'month': key,
            'ordered_n2': 0, 'ordered_co2': 0,
            'used_n2': 0, 'used_co2': 0,
            'defect_n2': 0, 'defect_co2': 0,
            'received_n2': 0, 'received_co2': 0,
        }
        for o in CylinderOrder.query.filter(
            CylinderOrder.ordered_at >= m_start, CylinderOrder.ordered_at < m_end
        ).all():
            q = int(o.quantity or 0)
            if o.gas_type == 'nitrogen':
                row['ordered_n2'] += q
            else:
                row['ordered_co2'] += q
        for log in CylinderLog.query.filter(
            CylinderLog.action.like('%_to_empty%'),
            CylinderLog.date >= m_start, CylinderLog.date < m_end
        ).all():
            c = db.session.get(GasCylinder, log.cylinder_id) if log.cylinder_id else None
            if not c:
                continue
            if c.gas_type == 'nitrogen':
                row['used_n2'] += 1
            else:
                row['used_co2'] += 1
        # «Получено» = доставленные заказы за месяц
        for o in CylinderOrder.query.filter(
            CylinderOrder.status == 'delivered',
            CylinderOrder.ordered_at >= m_start, CylinderOrder.ordered_at < m_end
        ).all():
            q = int(o.quantity or 0)
            if o.gas_type == 'nitrogen':
                row['received_n2'] += q
            else:
                row['received_co2'] += q
        # неисправные: defect/maintenance на конец месяца
        for gt, field in (('nitrogen', 'defect_n2'), ('co2', 'defect_co2')):
            row[field] = GasCylinder.query.filter(
                GasCylinder.gas_type == gt,
                GasCylinder.status.in_(('defect', 'maintenance')),
            ).count()
        months.append(row)
        cur = m_end

    # PDF
    from utils import ensure_fpdf, find_pdf_font
    try:
        FPDF = ensure_fpdf().FPDF
    except Exception:
        return jsonify({'error': 'PDF library unavailable'}), 500
    font_path = find_pdf_font()
    unicode_font = bool(font_path)

    def pdf_text(s):
        s = str(s if s is not None else '')
        # ASCII-замены: N₂/CO₂ → N2/CO2, чтобы в PDF не было «?»
        s = (s.replace('₂', '2').replace('₃', '3')
               .replace('—', '-').replace('–', '-')
               .replace('№', 'No.'))
        return s if unicode_font else s.encode('latin-1', 'replace').decode('latin-1')

    title = _('Gas report')
    period = '%s — %s' % (d_from.strftime('%d.%m.%Y'), (d_to - timedelta(days=1)).strftime('%d.%m.%Y'))

    class GasPDF(FPDF):
        def header(self):
            fn = 'AppFont' if unicode_font else 'Helvetica'
            self.set_font(fn, 'B', 13)
            self.cell(0, 8, pdf_text(title), new_x='LMARGIN', new_y='NEXT')
            self.set_font(fn, '', 9)
            self.cell(0, 5, pdf_text(period), new_x='LMARGIN', new_y='NEXT')
            self.ln(3)

        def footer(self):
            fn = 'AppFont' if unicode_font else 'Helvetica'
            self.set_y(-12)
            self.set_font(fn, '', 7)
            self.set_text_color(128)
            self.cell(0, 8, pdf_text(f'ProMaster — {self.page_no()}/{{nb}}'), align='C')

    pdf = GasPDF(orientation='L', unit='mm', format='A4')
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

    headers = [
        _('Month'), 'N2 ' + _('Ordered'), 'N2 ' + _('Used'),
        'CO2 ' + _('Ordered'), 'CO2 ' + _('Used'),
        _('Defects'), _('Received')
    ]
    col_w = [30, 32, 32, 32, 32, 32, 32]
    pdf.set_font(base_font, 'B', 8)
    pdf.set_fill_color(44, 62, 80)
    pdf.set_text_color(255)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, pdf_text(h), border=1, fill=True)
    pdf.ln()
    pdf.set_text_color(51)
    pdf.set_font(base_font, '', 8)

    tot = dict(ordered_n2=0, ordered_co2=0, used_n2=0, used_co2=0)
    for m in months:
        pdf.cell(col_w[0], 6, pdf_text(m['month']), border=1)
        pdf.cell(col_w[1], 6, pdf_text(m['ordered_n2']), border=1)
        pdf.cell(col_w[2], 6, pdf_text(m['used_n2']), border=1)
        pdf.cell(col_w[3], 6, pdf_text(m['ordered_co2']), border=1)
        pdf.cell(col_w[4], 6, pdf_text(m['used_co2']), border=1)
        pdf.cell(col_w[5], 6, pdf_text(m['defect_n2'] + m['defect_co2']), border=1)
        pdf.cell(col_w[6], 6, pdf_text(m['received_n2'] + m['received_co2']), border=1)
        pdf.ln()
        tot['ordered_n2'] += m['ordered_n2']
        tot['ordered_co2'] += m['ordered_co2']
        tot['used_n2'] += m['used_n2']
        tot['used_co2'] += m['used_co2']

    pdf.set_font(base_font, 'B', 8)
    pdf.cell(col_w[0], 7, pdf_text(_('Total')), border=1)
    pdf.cell(col_w[1], 7, pdf_text(tot['ordered_n2']), border=1)
    pdf.cell(col_w[2], 7, pdf_text(tot['used_n2']), border=1)
    pdf.cell(col_w[3], 7, pdf_text(tot['ordered_co2']), border=1)
    pdf.cell(col_w[4], 7, pdf_text(tot['used_co2']), border=1)
    pdf.cell(col_w[5], 7, pdf_text(''), border=1)
    pdf.cell(col_w[6], 7, pdf_text(''), border=1)

    import io
    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    fname = f'gas_report_{d_from.strftime("%Y%m%d")}_{d_to.strftime("%Y%m%d")}.pdf'
    return send_file(buf, mimetype='application/pdf', download_name=fname, as_attachment=True)
