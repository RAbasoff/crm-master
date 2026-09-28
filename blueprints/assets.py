"""
assets blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from models import (db, Contractor, Equipment, EquipmentServiceLog, FactorySection, User, Verantwoordelijke)
from utils import log_audit, role_required, safe_commit, safe_date, safe_float, safe_int, save_uploaded_file

bp = Blueprint('assets', __name__)

@bp.route('/assets')
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


@bp.route('/assets/new', methods=['GET', 'POST'])
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
            return redirect(url_for('assets.assets_detail', eq_id=eq.id))
        log_audit('create', 'equipment_asset', eq.id, eq.name)
        flash(_('Equipment added'), 'success')
        return redirect(url_for('assets.assets_detail', eq_id=eq.id))
    sections = FactorySection.query.order_by(FactorySection.name).all()
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    users = User.query.filter_by(is_active_user=True).order_by(User.username).all()
    contractors = Contractor.query.order_by(Contractor.company_name).all()
    return render_template('asset_form.html', eq=None, sections=sections, verantwoordelijken=verantwoordelijken, users=users, contractors=contractors)


@bp.route('/assets/<int:eq_id>')
@login_required
def assets_detail(eq_id):
    eq = Equipment.query.get_or_404(eq_id)
    return render_template('asset_detail.html', eq=eq, now=datetime.utcnow())


@bp.route('/assets/<int:eq_id>/edit', methods=['GET', 'POST'])
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
            return redirect(url_for('assets.assets_detail', eq_id=eq.id))
        flash(_('Equipment updated'), 'success')
        return redirect(url_for('assets.assets_detail', eq_id=eq.id))
    sections = FactorySection.query.order_by(FactorySection.name).all()
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    users = User.query.filter_by(is_active_user=True).order_by(User.username).all()
    contractors = Contractor.query.order_by(Contractor.company_name).all()
    return render_template('asset_form.html', eq=eq, sections=sections, verantwoordelijken=verantwoordelijken, users=users, contractors=contractors)


@bp.route('/assets/<int:eq_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def assets_delete(eq_id):
    eq = Equipment.query.get_or_404(eq_id)
    db.session.delete(eq)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('assets.assets_list'))
    flash(_('Equipment deleted'), 'success')
    return redirect(url_for('assets.assets_list'))


@bp.route('/assets/<int:eq_id>/service', methods=['POST'])
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
        return redirect(url_for('assets.assets_detail', eq_id=eq.id))
    flash(_('Service record added'), 'success')
    return redirect(url_for('assets.assets_detail', eq_id=eq.id))
