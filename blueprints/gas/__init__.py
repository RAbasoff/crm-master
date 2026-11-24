"""
Gas & Gas Equipment module — interactive cylinder management
Nitrogen (N₂) and Carbon Dioxide (CO₂) cylinders with visual status
"""
import json
from collections import OrderedDict
from datetime import datetime, timedelta

from flask import Blueprint, redirect, url_for, flash, render_template
from flask_login import login_required, current_user
from flask_babel import gettext as _

from models import (db, GasCylinder, GasSystemComponent, CylinderLog,
                    CylinderOrder, MonthlyArchive)
from utils import log_audit, safe_commit, user_has_section_access

bp = Blueprint('gas', __name__, url_prefix='/gas')


@bp.after_request
def _gas_no_cache(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response


# Working set (норма): 1 in_use + 1 ready full spare per gas type
WORKING_IN_USE = 1
WORKING_READY_FULL = 1
# Минимум полных баллонов: смешанный газ — от 3, иначе заказ
MIN_FULL = {'nitrogen': 1, 'co2': 1, 'mixed': 3}
GAS_TYPES = ('nitrogen', 'co2', 'mixed')
GAS_LABELS = {'nitrogen': 'N₂', 'co2': 'CO₂', 'mixed': 'Mixed gas'}


def _mark_empty_leaves_site(c, user_id=None, note=''):
    """Пустой баллон отмечается empty (остаётся в истории/отображении)."""
    old = c.status
    c.status = 'empty'
    c.side = None
    c.installed_at = None
    db.session.add(CylinderLog(
        cylinder_id=c.id,
        action='status_%s_to_empty' % (old or 'unknown'),
        performed_by=user_id,
        notes=(note or 'Empty — waiting return with next delivery')
    ))
    return c


def _inventory_balance():
    """Per-gas inventory. Empty kept for full information (user request)."""
    bal = {}
    for gt in GAS_TYPES:
        rows = db.session.query(
            GasCylinder.status, db.func.count()
        ).filter(
            GasCylinder.gas_type == gt,
            GasCylinder.status.notin_(('scanned', 'returned')),
            GasCylinder.received_at.isnot(None),
        ).group_by(GasCylinder.status).all()
        counts = {s or 'unknown': int(c or 0) for s, c in rows}
        full = counts.get('full', 0)
        in_use = counts.get('in_use', 0)
        empty = counts.get('empty', 0)
        maint = counts.get('maintenance', 0)
        defect = counts.get('defect', 0)
        total = full + in_use + empty + maint + defect
        pending = GasCylinder.query.filter(
            GasCylinder.gas_type == gt,
            db.or_(GasCylinder.status == 'scanned', GasCylinder.received_at.is_(None))
        ).count()
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        ordered_month = db.session.query(db.func.coalesce(db.func.sum(CylinderOrder.quantity), 0)).filter(
            CylinderOrder.gas_type == gt,
            CylinderOrder.ordered_at >= month_start
        ).scalar() or 0
        # Приход = доставлено ЗА месяц (по delivered_at, а не ordered_at:
        # заказ, оформленный в одном месяце и привезённый в другом, должен
        # попасть в месяц доставки; для старых записей без delivered_at —
        # fallback на дату заказа)
        _delivered_when = db.func.coalesce(CylinderOrder.delivered_at, CylinderOrder.ordered_at)
        delivered_month = db.session.query(db.func.coalesce(db.func.sum(CylinderOrder.quantity), 0)).filter(
            CylinderOrder.gas_type == gt,
            CylinderOrder.status == 'delivered',
            _delivered_when >= month_start
        ).scalar() or 0
        bal[gt] = {
            'label': GAS_LABELS[gt],
            'full': full,
            'in_use': in_use,
            'empty': empty,
            'maintenance': maint,
            'defect': defect,
            'total': total,
            'pending_scans': int(pending),
            'identity_ok': True,
            'has_working_pair': (in_use >= WORKING_IN_USE and full >= MIN_FULL.get(gt, WORKING_READY_FULL)) if gt != 'mixed' else (full >= MIN_FULL['mixed']),
            'ready_full': full,
            'spare_full': max(0, full - WORKING_READY_FULL),
            'need_order': (full < MIN_FULL.get(gt, WORKING_READY_FULL)) if gt == 'mixed' else (in_use < WORKING_IN_USE or full < MIN_FULL.get(gt, WORKING_READY_FULL)),
            'ordered_qty': int(ordered_month),
            'delivered_qty': int(delivered_month),
            'month_label': now.strftime('%Y-%m'),
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
    if not user_has_section_access('gas'):
        flash(_('Access denied. Insufficient rights.'), 'error')
        return redirect(url_for('index'))


def _auto_archive(now):
    """Архив прошлого месяца: пустые баллоны + заказано/получено. Счётчик месяца сбрасывается."""
    # archive previous month once
    prev_month = (now.replace(day=1) - timedelta(days=1))
    month_key = prev_month.strftime('%Y-%m')
    existing = MonthlyArchive.query.filter_by(archive_month=month_key, section='gas_cylinders').first()
    if existing:
        return

    month_start = prev_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    empty = GasCylinder.query.filter_by(status='empty').all()
    orders_m = CylinderOrder.query.filter(
        CylinderOrder.ordered_at >= month_start, CylinderOrder.ordered_at < month_end
    ).all()
    received_logs_m = CylinderLog.query.filter(
        CylinderLog.action.in_(('created', 'received')),
        CylinderLog.date >= month_start, CylinderLog.date < month_end
    ).all()

    snapshot = {
        'empty': [{'number': c.cylinder_number, 'gas': c.gas_type,
                   'refill': str(c.refill_date or '')} for c in empty],
        'summary': {
            'ordered_n2': sum(o.quantity or 0 for o in orders_m if o.gas_type == 'nitrogen'),
            'ordered_co2': sum(o.quantity or 0 for o in orders_m if o.gas_type == 'co2'),
            'received_count': len(received_logs_m),
            'total_on_hand': GasCylinder.query.count(),
        },
    }
    archive = MonthlyArchive(
        archive_month=month_key,
        section='gas_cylinders',
        data_json=json.dumps(snapshot, ensure_ascii=False)
    )
    db.session.add(archive)
    if not safe_commit():
        return
    log_audit('auto_archive', 'gas_cylinders', 0, f'Gas archive {month_key}')


# ============================================================
# MAIN VIEW — Interactive Cylinder Dashboard
# ============================================================

def _orders_by_month(orders_all, now):
    """Group cylinder orders by month with ordered/used counts for the UI."""
    months = OrderedDict()
    # used (became empty) per month from logs — уникальные баллоны:
    # в старых данных на каждое опустошение падало два лога
    used_by_month = {}
    seen_used = set()
    for log in CylinderLog.query.filter(CylinderLog.action.like('%_to_empty%')).all():
        key = log.date.strftime('%Y-%m') if log.date else None
        if not key:
            continue
        mark = (key, log.cylinder_id)
        if mark in seen_used:
            continue
        seen_used.add(mark)
        used_by_month[key] = used_by_month.get(key, 0) + 1
    for o in orders_all:
        if not o.ordered_at:
            continue
        key = o.ordered_at.strftime('%Y-%m')
        if key not in months:
            months[key] = {
                'key': key,
                'label': o.ordered_at.strftime('%Y-%m'),
                'title': o.ordered_at.strftime('%B %Y'),
                'ordered': 0,
                'ordered_n2': 0,
                'ordered_co2': 0,
                'delivered': 0,
                'used': used_by_month.get(key, 0),
                'rows': [],  # NOT 'items' — collides with dict.items in Jinja
            }
        qty = int(o.quantity or 0)
        months[key]['ordered'] += qty
        if o.gas_type == 'nitrogen':
            months[key]['ordered_n2'] += qty
        else:
            months[key]['ordered_co2'] += qty
        if o.status == 'delivered':
            months[key]['delivered'] += qty
        months[key]['rows'].append(o)
    # sort newest month first
    return sorted(months.values(), key=lambda m: m['key'], reverse=True)


def _cleanup_legacy_empty():
    """Пустые остаются в системе (для полной информации) — не трогаем."""
    return None


def _side_pair(cylinders):
    """Пара [лево, право] для графики дашборда.

    Сторона берётся из поля side (баллон «съезжал» в другую позицию,
    когда порядок брался из статуса). Без назначенной стороны — свободные
    слоты заполняются по приоритету: в работе → полный на месте → полный.
    """
    left = next((c for c in cylinders if (c.side or '') == 'left'), None)
    right = next((c for c in cylinders if (c.side or '') == 'right'), None)
    rest = [c for c in cylinders if c is not left and c is not right]

    def _prio(c):
        if c.status == 'in_use':
            return 0
        if c.status == 'full' and c.installed_at:
            return 1
        if c.status == 'full':
            return 2
        return 3
    rest.sort(key=_prio)
    if left is None and rest:
        left = rest.pop(0)
    if right is None and rest:
        right = rest.pop(0)
    return [left, right]


@bp.route('/')
@login_required
def gas_dashboard():
    """Main gas module view with interactive cylinder visualization"""
    # Auto-archive previous month (empty cylinders + monthly counters)
    now = datetime.utcnow()
    _auto_archive(now)

    cylinders = GasCylinder.query.order_by(GasCylinder.gas_type, GasCylinder.cylinder_number).all()
    components = GasSystemComponent.query.order_by(
        GasSystemComponent.gas_type, GasSystemComponent.component_type).all()
    # All orders grouped by month (for collapsible Bestellingen)
    orders = CylinderOrder.query.order_by(CylinderOrder.ordered_at.desc()).limit(10).all()
    orders_all = CylinderOrder.query.order_by(CylinderOrder.ordered_at.desc()).all()
    orders_by_month = _orders_by_month(orders_all, now)

    n2_cylinders = [c for c in cylinders if c.gas_type == 'nitrogen']
    co2_cylinders = [c for c in cylinders if c.gas_type == 'co2']
    mixed_cylinders = [c for c in cylinders if c.gas_type == 'mixed']

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

    # Monthly consumption: count cylinders that became empty this month.
    # Уникальные баллоны (в старых данных на каждое опустошение падало два
    # лога — по записям расход был бы вдвое больше факта).
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    consumed_logs = CylinderLog.query.filter(
        CylinderLog.action.like('%_to_empty%'),
        CylinderLog.date >= month_start
    ).all()
    consumed_ids = set(l.cylinder_id for l in consumed_logs)
    gas_by_id = {c.id: c.gas_type for c in cylinders}
    n2_consumed = len([cid for cid in consumed_ids if gas_by_id.get(cid) == 'nitrogen'])
    co2_consumed = len([cid for cid in consumed_ids if gas_by_id.get(cid) == 'co2'])
    mixed_consumed = len([cid for cid in consumed_ids if gas_by_id.get(cid) == 'mixed'])

    stats['n2_consumed_month'] = n2_consumed
    stats['co2_consumed_month'] = co2_consumed
    stats['mixed_consumed_month'] = mixed_consumed
    stats['total_consumed_month'] = n2_consumed + co2_consumed + mixed_consumed

    # Received this month = ФАКТИЧЕСКИ доставлено за месяц (по delivered_at:
    # заказ, оформленный в одном месяце и привезённый в другом, попадает
    # в месяц доставки; для старых записей без delivered_at — дата заказа).
    _delivered_when = db.func.coalesce(CylinderOrder.delivered_at, CylinderOrder.ordered_at)
    delivered_orders = CylinderOrder.query.filter(
        CylinderOrder.status == 'delivered',
        _delivered_when >= month_start
    ).all()
    n2_received = sum(int(o.quantity or 0) for o in delivered_orders if o.gas_type == 'nitrogen')
    co2_received = sum(int(o.quantity or 0) for o in delivered_orders if o.gas_type == 'co2')
    mixed_received = sum(int(o.quantity or 0) for o in delivered_orders if o.gas_type == 'mixed')
    stats['n2_received_month'] = n2_received
    stats['co2_received_month'] = co2_received
    stats['mixed_received_month'] = mixed_received
    stats['total_received_month'] = n2_received + co2_received + mixed_received

    # Заказано с начала месяца (все заказы)
    orders_month = CylinderOrder.query.filter(CylinderOrder.ordered_at >= month_start).all()
    stats['ordered_n2_month'] = sum(o.quantity or 0 for o in orders_month if o.gas_type == 'nitrogen')
    stats['ordered_co2_month'] = sum(o.quantity or 0 for o in orders_month if o.gas_type == 'co2')
    stats['ordered_total_month'] = stats['ordered_n2_month'] + stats['ordered_co2_month']

    # Итого в наличии + неисправности + в работе (с указанием стороны)
    stats['total_on_hand'] = stats['n2_total'] + stats['co2_total'] + balance['mixed']['total']
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
                           mixed_cylinders=mixed_cylinders,
                           n2_pair=_side_pair(n2_cylinders),
                           co2_pair=_side_pair(co2_cylinders),
                           n2_spare=n2_spare,
                           co2_spare=co2_spare,
                           components=components,
                           orders=orders,
                           orders_by_month=orders_by_month,
                           stats=stats,
                           balance=balance,
                           low_stock=low_stock)


from . import cylinders, components, orders, quality, reports
