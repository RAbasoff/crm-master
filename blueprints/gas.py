"""
Gas & Gas Equipment module — interactive cylinder management
Nitrogen (N₂) and Carbon Dioxide (CO₂) cylinders with visual status
"""
import os
import json
from datetime import datetime, timedelta
from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify, current_app
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename

from models import (db, GasCylinder, GasSystemComponent, CylinderLog,
                    CylinderOrder, EquipmentRepair, User, MonthlyArchive)
from utils import role_required, log_audit, create_notification, safe_commit, safe_int, safe_float, safe_date

bp = Blueprint('gas', __name__, url_prefix='/gas')

# Working set (норма): 1 in_use + 1 ready full spare per gas type
WORKING_IN_USE = 1
WORKING_READY_FULL = 1
GAS_TYPES = ('nitrogen', 'co2')
GAS_LABELS = {'nitrogen': 'N₂', 'co2': 'CO₂'}


def _inventory_balance():
    """Per-gas inventory arithmetic.

    Identity (must always hold):
        full + in_use + empty + maintenance = total

    Norm (рабочая пара):
        in_use == 1 and full >= 1

    Incoming goods (delivered orders) must appear as `full` cylinders,
    so:  leftover_full + received_full + empty + in_use = total.
    """
    bal = {}
    for gt in GAS_TYPES:
        rows = db.session.query(
            GasCylinder.status, db.func.count()
        ).filter(GasCylinder.gas_type == gt).group_by(GasCylinder.status).all()
        counts = {s or 'unknown': int(c or 0) for s, c in rows}
        full = counts.get('full', 0)
        in_use = counts.get('in_use', 0)
        empty = counts.get('empty', 0)
        maint = counts.get('maintenance', 0)
        defect = counts.get('defect', 0)
        total = full + in_use + empty + maint + defect
        # delivered orders (informational): how many were supposed to arrive
        ordered = db.session.query(db.func.coalesce(db.func.sum(CylinderOrder.quantity), 0)).filter(
            CylinderOrder.gas_type == gt
        ).scalar() or 0
        delivered = db.session.query(db.func.coalesce(db.func.sum(CylinderOrder.quantity), 0)).filter(
            CylinderOrder.gas_type == gt, CylinderOrder.status == 'delivered'
        ).scalar() or 0
        bal[gt] = {
            'label': GAS_LABELS[gt],
            'full': full,
            'in_use': in_use,
            'empty': empty,
            'maintenance': maint,
            'defect': defect,
            'total': total,
            'identity_ok': (full + in_use + empty + maint + defect == total),
            'has_working_pair': (in_use >= WORKING_IN_USE and full >= WORKING_READY_FULL),
            'ready_full': full,  # full cylinders available to become in_use
            'spare_full': max(0, full - WORKING_READY_FULL),  # stock beyond the working pair
            'need_order': (in_use < WORKING_IN_USE or full < WORKING_READY_FULL),
            'ordered_qty': int(ordered),
            'delivered_qty': int(delivered),
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
        flash(_('ДОСТУП ЗАКРЫТ. НЕ ДОСТАТОЧНО ПРАВ.'), 'error')
        return redirect(url_for('index'))


def _auto_archive(now):
    """Archive empty cylinders at end of month. Runs once per month."""
    month_key = now.strftime('%Y-%m')
    existing = MonthlyArchive.query.filter_by(archive_month=month_key, section='gas_cylinders').first()
    if existing:
        return  # already archived this month

    empty = GasCylinder.query.filter_by(status='empty').all()
    if not empty:
        return

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
    # Keep empty cylinders in stock — identity full+in_use+empty = total
    if not safe_commit():
        return
    log_audit('auto_archive', 'gas_cylinders', 0, f'{len(empty)} empty cylinders snapshotted for {month_key}')


# ============================================================
# MAIN VIEW — Interactive Cylinder Dashboard
# ============================================================

@bp.route('/')
@login_required
def gas_dashboard():
    """Main gas module view with interactive cylinder visualization"""
    # Auto-archive empty cylinders on last day of month
    now = datetime.utcnow()
    tomorrow = now + timedelta(days=1)
    if tomorrow.month != now.month:
        _auto_archive(now)

    cylinders = GasCylinder.query.order_by(GasCylinder.gas_type, GasCylinder.cylinder_number).all()
    components = GasSystemComponent.query.order_by(
        GasSystemComponent.gas_type, GasSystemComponent.component_type).all()
    orders = CylinderOrder.query.order_by(CylinderOrder.ordered_at.desc()).limit(10).all()

    n2_cylinders = [c for c in cylinders if c.gas_type == 'nitrogen']
    co2_cylinders = [c for c in cylinders if c.gas_type == 'co2']

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

    # Received this month: cylinders created this month
    received_logs = CylinderLog.query.filter(
        CylinderLog.action == 'created',
        CylinderLog.date >= month_start
    ).all()
    received_ids = set(l.cylinder_id for l in received_logs)
    n2_received = len([cid for cid in received_ids if any(c.id == cid and c.gas_type == 'nitrogen' for c in cylinders)])
    co2_received = len([cid for cid in received_ids if any(c.id == cid and c.gas_type == 'co2' for c in cylinders)])
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
                           n2_spare=n2_spare,
                           co2_spare=co2_spare,
                           components=components,
                           orders=orders,
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

    c.status = new_status
    if new_status == 'in_use' and not c.installed_at:
        c.installed_at = datetime.utcnow()
    if new_status == 'empty':
        c.installed_at = None

    # Max 1 in_use per gas type (норма). If another is active → it becomes empty.
    if new_status == 'in_use':
        others = GasCylinder.query.filter(
            GasCylinder.gas_type == c.gas_type,
            GasCylinder.status == 'in_use',
            GasCylinder.id != c.id
        ).order_by(GasCylinder.installed_at.asc().nullslast()).all()
        for oldest in others:
            oldest.status = 'empty'
            oldest.installed_at = None
            db.session.add(CylinderLog(
                cylinder_id=oldest.id,
                action='status_in_use_to_empty',
                performed_by=current_user.id,
                notes='Auto-emptied: replaced by %s (working set = 1 in_use)' % c.cylinder_number
            ))

    # When an in_use cylinder empties → promote one full spare into service
    if new_status == 'empty' and old_status == 'in_use':
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
    """Swap cylinder: mark current as empty, promote a full spare into service."""
    c = GasCylinder.query.get_or_404(cyl_id)
    old_number = c.cylinder_number
    c.status = 'empty'
    c.installed_at = None
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
    if gas_type not in ('nitrogen', 'co2'):
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
    return render_template('gas/scan_list.html')


@bp.route('/api/cylinders/scan-list', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
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
        c = GasCylinder(gas_type=gas_type, cylinder_number=number,
                        status='full', received_at=datetime.utcnow())
        db.session.add(c)
        db.session.flush()
        created = True
        db.session.add(CylinderLog(cylinder_id=c.id, action='created',
                                   performed_by=current_user.id,
                                   new_cylinder_number=number,
                                   notes='Scan list (not in stock yet)'))
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'created': created, 'cylinder_id': c.id,
                    'number': c.cylinder_number, 'gas_type': c.gas_type,
                    'status': c.status, 'side': c.side or '',
                    'refill_date': c.refill_date.isoformat() if c.refill_date else None})


@bp.route('/api/cylinders/scan-list/add-stock', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
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
        if not c.received_at:
            c.received_at = datetime.utcnow()
        if c.status not in ('in_use', 'empty', 'maintenance', 'defect'):
            c.status = 'full'
        db.session.add(CylinderLog(
            cylinder_id=c.id, action='received', performed_by=current_user.id,
            notes='Added to stock from scan list'
        ))
        n += 1
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    return jsonify({'ok': True, 'added': n})


@bp.route('/api/cylinders/assign-work', methods=['POST'])
@login_required
@role_required('admin', 'director', 'technician')
def cylinder_assign_work():
    """Установить баллон на место (left/right). Вне списка — только после подтверждения."""
    data = request.get_json() or {}
    number = (data.get('number') or '').strip()
    cyl_id = safe_int(data.get('cylinder_id'))
    side = (data.get('side') or '').strip()
    gas_type = data.get('gas_type') or 'nitrogen'
    force = bool(data.get('force'))
    if side not in ('left', 'right'):
        return jsonify({'error': 'Side must be left or right'}), 400

    c = db.session.get(GasCylinder, cyl_id) if cyl_id else None
    if not c and number:
        c = GasCylinder.query.filter_by(cylinder_number=number).first()

    in_list = bool(c)  # уже есть в базе = был в списке/складе
    if not c:
        if not force:
            return jsonify({'ok': True, 'need_confirm': True, 'number': number,
                            'message': 'Not in scan list. Install anyway?'}), 200
        c = GasCylinder(gas_type=gas_type if gas_type in GAS_TYPES else 'nitrogen',
                        cylinder_number=number, barcode=number,
                        status='in_use', side=side,
                        received_at=datetime.utcnow(), installed_at=datetime.utcnow())
        db.session.add(c)
        db.session.flush()
        db.session.add(CylinderLog(cylinder_id=c.id, action='created',
                                   performed_by=current_user.id,
                                   new_cylinder_number=number,
                                   notes='Installed without scan list'))
    # снять предыдущий с этой стороны
    for other in GasCylinder.query.filter(
        GasCylinder.gas_type == c.gas_type,
        GasCylinder.side == side,
        GasCylinder.status == 'in_use',
        GasCylinder.id != c.id
    ).all():
        other.status = 'empty'
        other.side = None
        other.installed_at = None
        db.session.add(CylinderLog(
            cylinder_id=other.id, action='status_in_use_to_empty',
            performed_by=current_user.id,
            notes=f'Replaced by #{c.cylinder_number} on {side}'
        ))
    c.status = 'in_use'
    c.side = side
    c.gas_type = gas_type if gas_type in GAS_TYPES else c.gas_type
    c.installed_at = datetime.utcnow()
    db.session.add(CylinderLog(cylinder_id=c.id, action='install',
                               performed_by=current_user.id,
                               notes=f'Installed on {side}'))
    if not safe_commit():
        return jsonify({'error': 'Save failed'}), 500
    log_audit('assign_work', 'gas_cylinder', c.id, f'{c.cylinder_number} → {side}')
    return jsonify({'ok': True, 'number': c.cylinder_number, 'side': side,
                    'in_list': in_list, 'cylinder_id': c.id})


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
    if gas_type not in ('nitrogen', 'co2'):
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
    if gas_type not in ('nitrogen', 'co2'):
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
        c.status = 'empty'
        c.installed_at = None
        db.session.add(CylinderLog(
            cylinder_id=c.id,
            action='status_in_use_to_empty',
            performed_by=current_user.id,
            notes='Auto-emptied: replaced during install of %s' % cylinder.cylinder_number
        ))

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
