"""Machine consumables (warehouse write-off) and machine passwords."""
from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user, login_required

from models import (Machine, MachineConsumable, MachinePassword,
                    VoorraadItem, VoorraadMutatie, db, now_local)
from security import director_only
from utils import (log_audit, resolve_user_id, role_required, safe_commit, adjust_stock,
                   safe_date, safe_float, safe_int)

from . import bp
from models import now_local

# ============================================================
# CONSUMABLES — link, write-off (consume), update-date, unlink
# ============================================================

@bp.route('/<int:machine_id>/consumables/link', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def machine_consumables_link(machine_id):
    m = Machine.query.get_or_404(machine_id)
    warehouse_item_id = safe_int(request.form.get('warehouse_item_id'))
    if not warehouse_item_id:
        flash(_('Select a material'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    wi = VoorraadItem.query.get_or_404(warehouse_item_id)
    # Check if already linked
    existing = MachineConsumable.query.filter_by(machine_id=machine_id, warehouse_item_id=warehouse_item_id).first()
    if existing:
        flash(_('This material is already linked'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    mc = MachineConsumable(
        machine_id=machine_id,
        warehouse_item_id=warehouse_item_id,
        quantity_per_use=safe_float(request.form.get('quantity_per_use'), 1),
        notes=request.form.get('notes', '')
    )
    db.session.add(mc)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    flash(_('Consumable linked'), 'success')
    return redirect(url_for('machines.machine_detail', machine_id=machine_id))


@bp.route('/<int:machine_id>/consumables/<int:mc_id>/consume', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def machine_consumables_consume(machine_id, mc_id):
    mc = MachineConsumable.query.get_or_404(mc_id)
    if mc.machine_id != machine_id:
        flash(_('Not found'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    qty = safe_float(request.form.get('quantity'), mc.quantity_per_use or 1)
    if qty <= 0:
        qty = mc.quantity_per_use or 1
    wi = mc.warehouse_item
    if not wi:
        flash(_('Warehouse item not found'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    if wi.hoeveelheid < qty:
        flash(_('Not enough stock. Available: %(qty)s %(unit)s', qty=wi.hoeveelheid, unit=wi.eenheid), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    # Атомарное списание остатка + движение
    adjust_stock(wi.id, -qty, mt='uitgaand',
                 user_id=resolve_user_id() if current_user.is_authenticated else None,
                 opmerking=f'Consumable used on machine #{machine_id}')
    mc.last_issued_at = now_local()
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    flash(_('%(name)s written off: %(qty)s %(unit)s', name=wi.naam, qty=qty, unit=wi.eenheid), 'success')
    return redirect(url_for('machines.machine_detail', machine_id=machine_id))


@bp.route('/<int:machine_id>/consumables/<int:mc_id>/update-date', methods=['POST'])
@login_required
@role_required('admin')
def machine_consumables_update_date(machine_id, mc_id):
    mc = MachineConsumable.query.get_or_404(mc_id)
    if mc.machine_id != machine_id:
        flash(_('Not found'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    d = safe_date(request.form.get('last_issued_at'))
    mc.last_issued_at = datetime.combine(d, datetime.min.time()) if d else None
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    flash(_('Date updated'), 'success')
    return redirect(url_for('machines.machine_detail', machine_id=machine_id))


@bp.route('/<int:machine_id>/consumables/<int:mc_id>/unlink', methods=['POST'])
@login_required
@role_required('admin', 'technician')
def machine_consumables_unlink(machine_id, mc_id):
    mc = MachineConsumable.query.get_or_404(mc_id)
    if mc.machine_id != machine_id:
        flash(_('Not found'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    db.session.delete(mc)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('machines.machine_detail', machine_id=machine_id))
    flash(_('Consumable unlinked'), 'success')
    return redirect(url_for('machines.machine_detail', machine_id=machine_id))

# ── ПАРОЛИ станков (admin + механики) ────────────────────────────

@bp.route('/passwords')
@login_required
@role_required('admin', 'technician', 'director')
def passwords_list():
    q = MachinePassword.query.order_by(MachinePassword.title)
    mid = safe_int(request.args.get('machine_id'))
    if mid:
        q = q.filter_by(machine_id=mid)
    items = q.all()
    machines = Machine.query.order_by(Machine.name).all()
    return render_template('machine_passwords.html', items=items, machines=machines, mid=mid or '')


@bp.route('/passwords/new', methods=['POST'])
@login_required
@role_required('admin', 'technician', 'director')
def password_new():
    title = (request.form.get('title') or '').strip()
    password = (request.form.get('password') or '').strip()
    if not title or not password:
        flash(_('Title and password are required'), 'error')
        return redirect(url_for('machines.passwords_list'))
    mp = MachinePassword(
        machine_id=safe_int(request.form.get('machine_id')) or None,
        title=title,
        login=(request.form.get('login') or '').strip(),
        password=password,
        kind=request.form.get('kind') or 'other',
        notes=(request.form.get('notes') or '').strip(),
        created_by=resolve_user_id(),
    )
    db.session.add(mp)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('machines.passwords_list'))
    log_audit('create', 'machine_password', mp.id, title)
    flash(_('Password saved'), 'success')
    return redirect(url_for('machines.passwords_list'))


@bp.route('/passwords/<int:pid>/update', methods=['POST'])
@login_required
@role_required('admin')
def password_update(pid):
    mp = MachinePassword.query.get_or_404(pid)
    mp.title = (request.form.get('title') or mp.title).strip()
    mp.login = (request.form.get('login') or '').strip()
    if request.form.get('password'):
        mp.password = request.form.get('password').strip()
    mp.kind = request.form.get('kind') or mp.kind
    mp.notes = (request.form.get('notes') or '').strip()
    mp.machine_id = safe_int(request.form.get('machine_id')) or None
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('update', 'machine_password', mp.id, mp.title)
        flash(_('Password saved'), 'success')
    return redirect(url_for('machines.passwords_list'))


@bp.route('/passwords/<int:pid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def password_delete(pid):
    mp = MachinePassword.query.get_or_404(pid)
    title = mp.title
    db.session.delete(mp)
    if not safe_commit():
        flash(_('Save failed'), 'error')
    else:
        log_audit('delete', 'machine_password', pid, title)
        flash(_('Deleted'), 'success')
    return redirect(url_for('machines.passwords_list'))
