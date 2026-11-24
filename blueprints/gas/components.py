"""Gas system components (regulators, valves, etc.) routes."""
from flask import request, redirect, url_for, flash, render_template
from flask_login import login_required
from flask_babel import gettext as _

from models import GasSystemComponent
from utils import role_required, log_audit, safe_commit, safe_date

from . import bp


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
