"""
invoices blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from models import (db, Invoice, InvoiceItem, VoorraadItem)
from utils import role_required, safe_commit, safe_date, safe_float

bp = Blueprint('invoices', __name__)

@bp.route('/invoices')
@login_required
@role_required('admin', 'director')
def invoices_list():
    if current_user.has_role('admin', 'director'):
        invoices = Invoice.query.order_by(Invoice.created_at.desc()).all()
    else:
        invoices = Invoice.query.filter_by(created_by=current_user.id).order_by(Invoice.created_at.desc()).all()
    return render_template('invoices.html', invoices=invoices)


@bp.route('/invoices/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def invoice_new():
    if request.method == 'POST':
        try:
            invoice_date = datetime.strptime(request.form['invoice_date'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid invoice date'), 'error')
            return redirect(url_for('invoices.invoice_new'))
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
            return redirect(url_for('invoices.invoice_new'))
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
            return redirect(url_for('invoices.invoice_detail', invoice_id=inv.id))
        flash(_('Invoice created'), 'success')
        return redirect(url_for('invoices.invoice_detail', invoice_id=inv.id))
    items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    return render_template('invoice_form.html', invoice=None, warehouse_items=items)


@bp.route('/invoices/<int:invoice_id>')
@login_required
@role_required('admin', 'director')
def invoice_detail(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    return render_template('invoice_detail.html', invoice=inv)


@bp.route('/invoices/<int:invoice_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director')
def invoice_edit(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    if request.method == 'POST':
        try:
            inv.invoice_date = datetime.strptime(request.form['invoice_date'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash(_('Invalid invoice date'), 'error')
            return redirect(url_for('invoices.invoice_edit', invoice_id=inv.id))
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
            return redirect(url_for('invoices.invoice_detail', invoice_id=inv.id))
        flash(_('Invoice updated'), 'success')
        return redirect(url_for('invoices.invoice_detail', invoice_id=inv.id))
    items = VoorraadItem.query.order_by(VoorraadItem.naam).all()
    return render_template('invoice_form.html', invoice=inv, warehouse_items=items)


@bp.route('/invoices/<int:invoice_id>/approve', methods=['POST'])
@login_required
@role_required('admin', 'director')
def invoice_approve(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    inv.status = 'approved'
    inv.signed_by = current_user.id
    inv.signed_at = datetime.utcnow()
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('invoices.invoice_detail', invoice_id=inv.id))
    flash(_('Invoice approved for payment'), 'success')
    return redirect(url_for('invoices.invoice_detail', invoice_id=inv.id))


@bp.route('/invoices/<int:invoice_id>/reject', methods=['POST'])
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
        return redirect(url_for('invoices.invoice_detail', invoice_id=inv.id))
    flash(_('Invoice rejected'), 'error')
    return redirect(url_for('invoices.invoice_detail', invoice_id=inv.id))


@bp.route('/invoices/<int:invoice_id>/pay', methods=['POST'])
@login_required
@role_required('admin', 'director')
def invoice_pay(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    inv.status = 'paid'
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('invoices.invoice_detail', invoice_id=inv.id))
    flash(_('Invoice marked as paid'), 'success')
    return redirect(url_for('invoices.invoice_detail', invoice_id=inv.id))


@bp.route('/invoices/<int:invoice_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def invoice_delete(invoice_id):
    inv = Invoice.query.get_or_404(invoice_id)
    db.session.delete(inv)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('invoices.invoices_list'))
    flash(_('Invoice deleted'), 'success')
    return redirect(url_for('invoices.invoices_list'))
