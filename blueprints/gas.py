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
from utils import role_required, log_audit, create_notification, safe_commit

bp = Blueprint('gas', __name__, url_prefix='/gas')


@bp.before_request
def check_gas_access():
    """Check gas section access."""
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    from utils import user_has_section_access
    if not user_has_section_access('gas'):
        flash(_('ДОСТУП ЗАКРЫТ. НЕ ДОСТАТОЧНО ПРАВ.'), 'error')
        return redirect(url_for('index'))


# ============================================================
# MAIN VIEW — Interactive Cylinder Dashboard
# ============================================================

@bp.route('/')
@login_required
def gas_dashboard():
    """Main gas module view with interactive cylinder visualization"""
    cylinders = GasCylinder.query.order_by(GasCylinder.gas_type, GasCylinder.cylinder_number).all()
    components = GasSystemComponent.query.order_by(
        GasSystemComponent.gas_type, GasSystemComponent.component_type).all()
    orders = CylinderOrder.query.order_by(CylinderOrder.ordered_at.desc()).limit(10).all()

    n2_cylinders = [c for c in cylinders if c.gas_type == 'nitrogen']
    co2_cylinders = [c for c in cylinders if c.gas_type == 'co2']

    stats = {
        'n2_full': len([c for c in n2_cylinders if c.status == 'full']),
        'n2_in_use': len([c for c in n2_cylinders if c.status == 'in_use']),
        'n2_empty': len([c for c in n2_cylinders if c.status == 'empty']),
        'n2_maintenance': len([c for c in n2_cylinders if c.status == 'maintenance']),
        'co2_full': len([c for c in co2_cylinders if c.status == 'full']),
        'co2_in_use': len([c for c in co2_cylinders if c.status == 'in_use']),
        'co2_empty': len([c for c in co2_cylinders if c.status == 'empty']),
        'co2_maintenance': len([c for c in co2_cylinders if c.status == 'maintenance']),
        'total_full': len([c for c in cylinders if c.status == 'full']),
        'total_in_use': len([c for c in cylinders if c.status == 'in_use']),
        'total_empty': len([c for c in cylinders if c.status == 'empty']),
        'total_maintenance': len([c for c in cylinders if c.status == 'maintenance']),
        'total': len(cylinders),
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

    # Spare cylinders (empty, available for replacement)
    n2_spare = [c for c in n2_cylinders if c.status == 'empty']
    co2_spare = [c for c in co2_cylinders if c.status == 'empty']

    # Low stock warning: when only1 full cylinder remains per gas type
    low_stock = []
    if stats['n2_full'] <= 1 and stats['n2_full'] + stats['n2_in_use'] <= 2:
        low_stock.append('N₂')
    if stats['co2_full'] <= 1 and stats['co2_full'] + stats['co2_in_use'] <= 2:
        low_stock.append('CO₂')

    return render_template('gas/dashboard.html',
                           cylinders=cylinders,
                           n2_cylinders=n2_cylinders,
                           co2_cylinders=co2_cylinders,
                           n2_spare=n2_spare,
                           co2_spare=co2_spare,
                           components=components,
                           orders=orders,
                           stats=stats,
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
            c.received_at = datetime.strptime(request.form['received_at'], '%Y-%m-%d')
        db.session.add(c)
        safe_commit()

        log = CylinderLog(
            cylinder_id=c.id,
            action='created',
            new_cylinder_number=c.cylinder_number,
            performed_by=current_user.id,
            notes=f'New {c.gas_type} cylinder added'
        )
        db.session.add(log)
        safe_commit()

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
            c.received_at = datetime.strptime(request.form['received_at'], '%Y-%m-%d')
        if request.form.get('installed_at'):
            c.installed_at = datetime.strptime(request.form['installed_at'], '%Y-%m-%d')
        safe_commit()

        if old_status != c.status:
            log = CylinderLog(
                cylinder_id=c.id,
                action=f'status_{old_status}_to_{c.status}',
                performed_by=current_user.id,
                notes=f'Status: {old_status} → {c.status}'
            )
            db.session.add(log)
            safe_commit()

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
    safe_commit()
    log_audit('delete', 'gas_cylinder', cyl_id, name)
    flash(_('Cylinder deleted'), 'success')
    return redirect(url_for('gas.gas_dashboard'))


@bp.route('/cylinders/<int:cyl_id>/status', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def cylinder_status(cyl_id):
    """Quick status change from dashboard — with auto-switch logic"""
    c = GasCylinder.query.get_or_404(cyl_id)
    data = request.get_json() if request.is_json else request.form
    new_status = data.get('status')
    if new_status not in ('full', 'in_use', 'empty', 'maintenance'):
        return jsonify({'error': 'Invalid status'}), 400

    old_status = c.status
    c.status = new_status
    if new_status == 'in_use' and not c.installed_at:
        c.installed_at = datetime.utcnow()
    if new_status == 'empty':
        c.installed_at = None

    # Auto-switch: when setting to "full", the other cylinder of same gas type
    # that is currently "in_use" stays in_use. This cylinder becomes backup.
    # When setting to "empty", the "full" backup automatically becomes "in_use".
    if new_status == 'empty' and old_status == 'in_use':
        # Find the backup (full) cylinder of same gas type
        backup = GasCylinder.query.filter(
            GasCylinder.gas_type == c.gas_type,
            GasCylinder.status == 'full',
            GasCylinder.id != c.id
        ).first()
        if backup:
            backup.status = 'in_use'
            backup.installed_at = datetime.utcnow()
            db.session.add(CylinderLog(
                cylinder_id=backup.id,
                action='status_full_to_in_use',
                performed_by=current_user.id,
                notes='Auto-switch: backup activated after %s emptied' % c.cylinder_number
            ))

    safe_commit()

    log = CylinderLog(
        cylinder_id=c.id,
        action='status_%s_to_%s' % (old_status, new_status),
        performed_by=current_user.id,
        notes='Status: %s -> %s' % (old_status, new_status)
    )
    db.session.add(log)
    safe_commit()

    log_audit('status_change', 'gas_cylinder', c.id,
              '%s #%s: %s -> %s' % (c.gas_type, c.cylinder_number, old_status, new_status))

    if request.is_json:
        return jsonify({'ok': True, 'old': old_status, 'new': new_status})
    flash(_('Status updated'), 'success')
    return redirect(url_for('gas.gas_dashboard'))


@bp.route('/cylinders/<int:cyl_id>/swap', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def cylinder_swap(cyl_id):
    """Swap cylinder: mark current as empty, prompt for new"""
    c = GasCylinder.query.get_or_404(cyl_id)
    old_number = c.cylinder_number
    c.status = 'empty'
    c.installed_at = None
    safe_commit()

    log = CylinderLog(
        cylinder_id=c.id,
        action='removed',
        old_cylinder_number=old_number,
        performed_by=current_user.id,
        notes=f'Cylinder removed from service'
    )
    db.session.add(log)
    safe_commit()

    log_audit('swap', 'gas_cylinder', c.id, f'{c.gas_type} #{old_number} swapped')
    flash(_('Cylinder marked as empty. Add a new cylinder to replace it.'), 'info')
    return redirect(url_for('gas.cylinder_new'))


@bp.route('/archive')
@login_required
@role_required('admin')
def gas_archive():
    """Archive all empty cylinders to MonthlyArchive, then delete them."""
    now = datetime.utcnow()
    month_key = now.strftime('%Y-%m')

    empty = GasCylinder.query.filter_by(status='empty').all()
    if not empty:
        flash(_('No empty cylinders to archive'), 'info')
        return redirect(url_for('gas.gas_dashboard'))

    # Save snapshot
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
    for c in empty:
        db.session.delete(c)

    safe_commit()
    log_audit('archive', 'gas_cylinders', 0, f'{count} empty cylinders archived for {month_key}')
    flash(_('%(count)d cylinders archived', count=count), 'success')
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
            c.last_check = datetime.strptime(request.form['last_check'], '%Y-%m-%d').date()
        if request.form.get('next_check'):
            c.next_check = datetime.strptime(request.form['next_check'], '%Y-%m-%d').date()
        db.session.add(c)
        safe_commit()
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
            c.last_check = datetime.strptime(request.form['last_check'], '%Y-%m-%d').date()
        if request.form.get('next_check'):
            c.next_check = datetime.strptime(request.form['next_check'], '%Y-%m-%d').date()
        safe_commit()
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
    safe_commit()
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
            quantity=int(request.form.get('quantity', 1)),
            status='pending',
            supplier=request.form.get('supplier', ''),
            reason=request.form.get('reason', ''),
            ordered_by=current_user.id
        )
        db.session.add(o)
        safe_commit()
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
    o.status = new_status
    if new_status == 'delivered':
        o.delivered_at = datetime.utcnow()
    safe_commit()
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
    safe_commit()

    if old_status != c.status:
        log = CylinderLog(
            cylinder_id=c.id,
            action=f'status_{old_status}_to_{c.status}',
            performed_by=current_user.id,
            notes=data.get('reason', f'Status: {old_status} → {c.status}')
        )
        db.session.add(log)
        safe_commit()

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
    safe_commit()

    db.session.add(CylinderLog(
        cylinder_id=cylinder.id,
        action='created',
        new_cylinder_number=number,
        performed_by=current_user.id,
        notes='Added via dashboard scan'
    ))
    safe_commit()

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

@bp.route('/scan')
@login_required
def cylinder_scan_page():
    """Barcode scanning page for cylinder install/replace"""
    return render_template('gas/cylinder_scan.html')


@bp.route('/api/cylinders/scan', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def cylinder_scan_install():
    """Scan barcode → install cylinder on selected gas type + side.
    Logic:
    1. Find or create cylinder by barcode
    2. Set scanned cylinder to 'in_use' on selected side
    3. Set previous 'in_use' cylinder of same gas type to 'empty'
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
        safe_commit()
        db.session.add(CylinderLog(
            cylinder_id=cylinder.id,
            action='created',
            new_cylinder_number=code,
            performed_by=current_user.id,
            notes='Created via barcode scan'
        ))
        safe_commit()

    # Set current in_use cylinder of same gas type to empty
    active = GasCylinder.query.filter(
        GasCylinder.gas_type == gas_type,
        GasCylinder.status == 'in_use',
        GasCylinder.id != cylinder.id
    ).first()
    if active:
        active.status = 'empty'
        active.installed_at = None
        db.session.add(CylinderLog(
            cylinder_id=active.id,
            action='status_in_use_to_empty',
            performed_by=current_user.id,
            notes='Auto-emptied: replaced by %s (scanned)' % code
        ))

    # Install scanned cylinder
    old_status = cylinder.status
    cylinder.status = 'in_use'
    cylinder.gas_type = gas_type
    cylinder.installed_at = datetime.utcnow()
    safe_commit()

    db.session.add(CylinderLog(
        cylinder_id=cylinder.id,
        action='status_%s_to_in_use' % old_status,
        performed_by=current_user.id,
        notes='Installed via scan on %s side (%s)' % (side, gas_type)
    ))
    safe_commit()

    return jsonify({
        'ok': True,
        'cylinder_id': cylinder.id,
        'cylinder_number': cylinder.cylinder_number,
        'gas_type': gas_type,
        'side': side,
        'old_status': old_status
    })
