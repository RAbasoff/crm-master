"""Cylinder orders (purchase / delivery) routes."""
from datetime import datetime

from flask import request, redirect, url_for, flash, render_template
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import db, GasCylinder, CylinderLog, CylinderOrder, now_local
from utils import resolve_user_id, role_required, log_audit, safe_commit, safe_int

from . import bp, GAS_LABELS


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
        o.delivered_at = now_local()
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
                received_at=o.delivered_at or now_local(),
                notes=f'From order #{o.id} ({o.supplier or "-"})'
            )
            db.session.add(cyl)
            db.session.flush()
            db.session.add(CylinderLog(
                cylinder_id=cyl.id,
                action='created',
                new_cylinder_number=number,
                performed_by=resolve_user_id(),
                notes=f'Received via order #{o.id} x{qty}'
            ))
            created += 1
        # Обмен: при поставке ВСЕ пустые забирает поставщик (правило владельца
        # 2026-11-21: пустых в списке быть не должно вообще).
        returned = 0
        empties = GasCylinder.query.filter_by(
            gas_type=o.gas_type, status='empty'
        ).order_by(GasCylinder.id.asc()).all()
        for c in empties:
            c.status = 'returned'
            c.side = None
            db.session.add(CylinderLog(
                cylinder_id=c.id, action='return_empty',
                performed_by=resolve_user_id(),
                notes=f'Returned to supplier on delivery of order #{o.id}'
            ))
            returned += 1
        if not safe_commit():
            flash(_('Save failed. Please try again.'), 'error')
            return redirect(url_for('gas.orders_list'))
        if created:
            msg = _('Order delivered: %(n)d full %(gas)s cylinders added to stock',
                    n=created, gas=GAS_LABELS.get(o.gas_type, o.gas_type))
            if returned:
                msg += ' · ' + _('returned %(n)d empty to supplier', n=returned)
            flash(msg, 'success')
        else:
            flash(_('Order marked delivered (cylinders already in stock)'), 'info')
        log_audit('deliver', 'cylinder_order', o.id, f'{o.gas_type} x{qty}, created={created}, returned={returned}')
    else:
        flash(_('Order status updated'), 'success')
    return redirect(url_for('gas.orders_list'))
