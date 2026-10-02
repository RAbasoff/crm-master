"""
orders blueprint
"""
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, send_file, session, g)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from werkzeug.utils import secure_filename
import os, io, json

from models import (db, Monteur, Opdracht, Verantwoordelijke, VoorraadMutatie)
from utils import genereer_nummer, log_audit, role_required, safe_commit, safe_float

bp = Blueprint('orders', __name__)

@bp.route('/orders')
@login_required
@role_required('admin', 'director', 'technician')
def orders_list():
    page = request.args.get('page', 1, type=int)
    sf = request.args.get('status', '')
    wf = request.args.get('worker', '')
    q = Opdracht.query
    if sf: q = q.filter_by(status=sf)
    if wf: q = q.filter_by(monteur_id=wf)
    pagination = q.order_by(Opdracht.aangemaakt.desc()).paginate(page=page, per_page=25, error_out=False)
    orders = pagination.items
    workers = Monteur.query.filter_by(actief=True).all()
    return render_template('orders.html', orders=orders, workers=workers, status_filter=sf, worker_filter=wf, pagination=pagination)


@bp.route('/orders/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def order_new():
    if request.method == 'POST':
        o = Opdracht(
            nummer=genereer_nummer(),
            responsible_id=request.form['klant_id'],
            monteur_id=request.form.get('monteur_id') or None,
            apparaat=request.form['apparaat'],
            model=request.form.get('model', ''),
            serienummer=request.form.get('serienummer', ''),
            probleem=request.form['probleem'],
            arbeidskosten=safe_float(request.form.get('arbeidskosten'), 0),
            status='aangenomen'
        )
        db.session.add(o)
        if not safe_commit():
            flash(_('Save failed'), 'error')
            if o.id:
                return redirect(url_for('orders.order_detail', order_id=o.id))
            return redirect(url_for('orders.orders_list'))
        flash(_('Work Order created') + f' {o.nummer}', 'success')
        return redirect(url_for('orders.order_detail', order_id=o.id))
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    monteurs = Monteur.query.filter_by(actief=True).all()
    return render_template('order_form.html', verantwoordelijken=verantwoordelijken, workers=monteurs, order=None)


@bp.route('/orders/<int:order_id>')
@login_required
@role_required('admin', 'director', 'technician')
def order_detail(order_id):
    order = Opdracht.query.get_or_404(order_id)
    return render_template('order_detail.html', order=order)


@bp.route('/orders/<int:order_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'director', 'technician')
def order_edit(order_id):
    order = Opdracht.query.get_or_404(order_id)
    if request.method == 'POST':
        order.monteur_id = request.form.get('monteur_id') or None
        order.apparaat = request.form['apparaat']
        order.model = request.form.get('model', '')
        order.serienummer = request.form.get('serienummer', '')
        order.probleem = request.form['probleem']
        order.diagnose = request.form.get('diagnose', '')
        order.uitgevoerd = request.form.get('uitgevoerd', '')
        order.arbeidskosten = safe_float(request.form.get('arbeidskosten'), 0)
        order.onderdelenkosten = safe_float(request.form.get('onderdelenkosten'), 0)
        order.totaal = order.arbeidskosten + order.onderdelenkosten
        ns = request.form.get('status', order.status)
        if ns != order.status:
            if ns == 'in behandeling' and not order.gestart: order.gestart = datetime.utcnow()
            elif ns == 'gereed' and not order.gereed: order.gereed = datetime.utcnow()
            elif ns == 'afgeleverd' and not order.afgeleverd: order.afgeleverd = datetime.utcnow()
            order.status = ns
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('orders.order_detail', order_id=order.id))
        flash(_('Work Order updated'), 'success')
        return redirect(url_for('orders.order_detail', order_id=order.id))
    verantwoordelijken = Verantwoordelijke.query.order_by(Verantwoordelijke.naam).all()
    monteurs = Monteur.query.filter_by(actief=True).all()
    return render_template('order_form.html', verantwoordelijken=verantwoordelijken, workers=monteurs, order=order)


@bp.route('/orders/<int:order_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def order_delete(order_id):
    order = Opdracht.query.get_or_404(order_id)
    nummer = order.nummer
    VoorraadMutatie.query.filter_by(opdracht_id=order.id).update({'opdracht_id': None})
    db.session.delete(order)
    if not safe_commit():
        flash(_('Save failed'), 'error')
        return redirect(url_for('orders.orders_list'))
    log_audit('delete', 'opdracht', order_id, nummer)
    flash(_('Work Order deleted') + f': {nummer}', 'success')
    return redirect(url_for('orders.orders_list'))
