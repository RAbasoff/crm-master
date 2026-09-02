"""
Contractors blueprint — suppliers/contractors management, employees
"""
from datetime import datetime
from flask import Blueprint, request, redirect, url_for, flash, render_template
from flask_login import login_required
from flask_babel import gettext as _

from models import db, Contractor, ContractorEmployee
from utils import role_required, log_audit

bp = Blueprint('contractors', __name__, url_prefix='/contractors')


@bp.route('/')
@login_required
@role_required('admin', 'director')
def contractors_list():
    contractors = Contractor.query.filter_by(is_active=True).order_by(Contractor.company_name).all()
    return render_template('contractors.html', contractors=contractors)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def contractor_new():
    if request.method == 'POST':
        c = Contractor(
            company_name=request.form['company_name'],
            contact_person=request.form.get('contact_person', ''),
            contact_position=request.form.get('contact_position', ''),
            phone=request.form.get('phone', ''),
            phone2=request.form.get('phone2', ''),
            email=request.form.get('email', ''),
            website=request.form.get('website', ''),
            address=request.form.get('address', ''),
            postcode=request.form.get('postcode', ''),
            city=request.form.get('city', ''),
            country=request.form.get('country', 'Nederland'),
            kvk_number=request.form.get('kvk_number', ''),
            btw_number=request.form.get('btw_number', ''),
            iban=request.form.get('iban', ''),
            service_type=request.form.get('service_type', ''),
            contract_number=request.form.get('contract_number', ''),
            contract_start=datetime.strptime(request.form['contract_start'], '%Y-%m-%d').date() if request.form.get('contract_start') else None,
            contract_end=datetime.strptime(request.form['contract_end'], '%Y-%m-%d').date() if request.form.get('contract_end') else None,
            notes=request.form.get('notes', '')
        )
        db.session.add(c)
        db.session.commit()
        log_audit('create', 'contractor', c.id, c.company_name)
        flash(_('Contractor added'), 'success')
        return redirect(url_for('contractors.contractor_detail', contractor_id=c.id))
    return render_template('contractor_form.html', contractor=None)


@bp.route('/<int:contractor_id>')
@login_required
@role_required('admin', 'director')
def contractor_detail(contractor_id):
    c = Contractor.query.get_or_404(contractor_id)
    today = datetime.utcnow().date()
    return render_template('contractor_detail.html', contractor=c, today=today)


@bp.route('/<int:contractor_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def contractor_edit(contractor_id):
    c = Contractor.query.get_or_404(contractor_id)
    if request.method == 'POST':
        c.company_name = request.form['company_name']
        c.contact_person = request.form.get('contact_person', '')
        c.contact_position = request.form.get('contact_position', '')
        c.phone = request.form.get('phone', '')
        c.phone2 = request.form.get('phone2', '')
        c.email = request.form.get('email', '')
        c.website = request.form.get('website', '')
        c.address = request.form.get('address', '')
        c.postcode = request.form.get('postcode', '')
        c.city = request.form.get('city', '')
        c.country = request.form.get('country', 'Nederland')
        c.kvk_number = request.form.get('kvk_number', '')
        c.btw_number = request.form.get('btw_number', '')
        c.iban = request.form.get('iban', '')
        c.service_type = request.form.get('service_type', '')
        c.contract_number = request.form.get('contract_number', '')
        c.contract_start = datetime.strptime(request.form['contract_start'], '%Y-%m-%d').date() if request.form.get('contract_start') else None
        c.contract_end = datetime.strptime(request.form['contract_end'], '%Y-%m-%d').date() if request.form.get('contract_end') else None
        c.notes = request.form.get('notes', '')
        db.session.commit()
        log_audit('update', 'contractor', c.id, c.company_name)
        flash(_('Contractor updated'), 'success')
        return redirect(url_for('contractors.contractor_detail', contractor_id=c.id))
    return render_template('contractor_form.html', contractor=c)


@bp.route('/<int:contractor_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def contractor_delete(contractor_id):
    c = Contractor.query.get_or_404(contractor_id)
    c.is_active = False
    db.session.commit()
    log_audit('delete', 'contractor', c.id, c.company_name)
    flash(_('Contractor deactivated'), 'success')
    return redirect(url_for('contractors.contractors_list'))


@bp.route('/<int:contractor_id>/add-employee', methods=['POST'])
@login_required
@role_required('admin')
def contractor_add_employee(contractor_id):
    c = Contractor.query.get_or_404(contractor_id)
    emp = ContractorEmployee(
        contractor_id=c.id,
        name=request.form['name'],
        position=request.form.get('position', ''),
        phone=request.form.get('phone', ''),
        email=request.form.get('email', ''),
        notes=request.form.get('notes', '')
    )
    db.session.add(emp)
    db.session.commit()
    flash(_('Employee added'), 'success')
    return redirect(url_for('contractors.contractor_detail', contractor_id=c.id))


@bp.route('/employee/<int:emp_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def contractor_delete_employee(emp_id):
    emp = ContractorEmployee.query.get_or_404(emp_id)
    cid = emp.contractor_id
    db.session.delete(emp)
    db.session.commit()
    flash(_('Employee removed'), 'success')
    return redirect(url_for('contractors.contractor_detail', contractor_id=cid))
