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

from models import (db, Monteur, Opdracht, OpdrachtPhoto, OpdrachtVideo, User,
                    Verantwoordelijke, VoorraadMutatie, VoorraadItem, now_local)
from utils import (genereer_nummer, retry_unique_number, log_audit, role_required,
                   safe_commit, safe_float, save_uploaded_file)

bp = Blueprint('orders', __name__)


# События Werkorder → msgid уведомлений (перевод — на нидерландском в force_locale)
_ORDER_STATUS_MSG = {
    'aangenomen': 'Work order accepted',
    'diagnose': 'Work order in progress',
    'in behandeling': 'Work order in progress',
    'gereed': 'Work order ready',
    'afgeleverd': 'Work order closed',
    'geannuleerd': 'Work order cancelled',
}


def _notify_order_event(order, title_msgid):
    """Обязательные оповещения: механики + админ + начальник ТС + создатель наряда."""
    try:
        from blueprints.faults import _notify_i18n_row
        from logs import create_notifications
        from security import is_head_of_ts
        link = url_for('orders.order_detail', order_id=order.id)

        def build(_f):
            return f"{order.nummer}: {order.apparaat or ''} — {_f('Status')}: {order.status}"

        targets = set()
        for u in User.query.filter(User.is_active_user == True).all():
            if u.has_role('admin') or u.has_role('technician') or is_head_of_ts(u):
                targets.add(u.id)
        if order.created_by:
            targets.add(order.created_by)
        targets.discard(current_user.id)
        rows = [_notify_i18n_row(uid, title_msgid, build, link, ntype='info')
                for uid in sorted(targets)]
        create_notifications(rows)
    except Exception as _ne:
        print('order notify', _ne)


def _save_order_media(order_id):
    """Фото и видео из формы вложений (Werkorder)."""
    try:
        for photo in request.files.getlist('photos'):
            if photo and photo.filename:
                fn = save_uploaded_file(photo, prefix=f"order_{order_id}_")
                if fn:
                    db.session.add(OpdrachtPhoto(opdracht_id=order_id, filename=fn))
        for video in request.files.getlist('videos'):
            if video and video.filename:
                fn = save_uploaded_file(video, prefix=f"order_{order_id}_")
                if fn:
                    db.session.add(OpdrachtVideo(opdracht_id=order_id, filename=fn))
    except Exception as _pe:
        print('order media', _pe)

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
        def _make():
            return Opdracht(
                nummer=genereer_nummer(),
                responsible_id=request.form['klant_id'],
                monteur_id=request.form.get('monteur_id') or None,
                apparaat=request.form['apparaat'],
                model=request.form.get('model', ''),
                serienummer=request.form.get('serienummer', ''),
                probleem=request.form['probleem'],
                arbeidskosten=safe_float(request.form.get('arbeidskosten'), 0),
                status='aangenomen',
                created_by=current_user.id
            )
        try:
            o = retry_unique_number(_make)
        except RuntimeError:
            flash(_('Save failed'), 'error')
            return redirect(url_for('orders.orders_list'))
        if not safe_commit():
            flash(_('Save failed'), 'error')
            if o.id:
                return redirect(url_for('orders.order_detail', order_id=o.id))
            return redirect(url_for('orders.orders_list'))
        # фото и видео к наряду
        _save_order_media(o.id)
        safe_commit()
        _notify_order_event(o, 'New work order')
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
        # фото и видео к наряду (добавление)
        _save_order_media(order.id)
        ns = request.form.get('status', order.status)
        old_status = order.status
        if ns != old_status:
            # Закрыть (afgeleverd) может только админ / начальник ТС
            if ns == 'afgeleverd' and old_status != 'afgeleverd':
                from security import is_head_of_ts
                if not (current_user.has_role('admin') or is_head_of_ts(current_user)):
                    flash(_('Only admin or head of technical service can close a work order'), 'error')
                    return redirect(url_for('orders.order_detail', order_id=order.id))
            if ns == 'in behandeling' and not order.gestart: order.gestart = now_local()
            elif ns == 'gereed' and not order.gereed: order.gereed = now_local()
            elif ns == 'afgeleverd' and not order.afgeleverd: order.afgeleverd = now_local()
            # Отмена заказа — возврат списанных запчастей на склад
            if ns == 'geannuleerd' and old_status != 'geannuleerd':
                for m in VoorraadMutatie.query.filter_by(opdracht_id=order.id, type='uitgaand').all():
                    item = VoorraadItem.query.get(m.item_id) if m.item_id else None
                    if item:
                        item.hoeveelheid = (item.hoeveelheid or 0) + (m.hoeveelheid or 0)
                    db.session.add(VoorraadMutatie(
                        item_id=m.item_id, type='inkomend', hoeveelheid=m.hoeveelheid,
                        opmerking=f'Возврат: отмена заказа {order.nummer}',
                        user_id=current_user.id
                    ))
            order.status = ns
        if not safe_commit():
            flash(_('Save failed'), 'error')
            return redirect(url_for('orders.order_detail', order_id=order.id))
        if ns != old_status:
            _notify_order_event(order, _ORDER_STATUS_MSG.get(ns, 'Work order status changed'))
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
