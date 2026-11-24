"""
Maintenance blueprint — calendar, plans, schedule generator
"""
from flask import Blueprint

from models import db, Equipment, EquipmentMaintenance

bp = Blueprint('maintenance', __name__)


def _target_name(obj):
    """Безопасное имя цели ТО: станок, Mule-устройство, оборудование, «—»."""
    m = getattr(obj, 'machine', None)
    if m is not None and getattr(m, 'name', None):
        return m.name
    mule_id = getattr(obj, 'mule_id', None)
    if mule_id:
        try:
            mro = db.session.get(EquipmentMaintenance, mule_id)
        except Exception:
            mro = None
        if mro:
            nm = mro.name or mro.number
            if mro.serial:
                nm = f'{nm} ({mro.serial})'
            if mro.machine_id:
                try:
                    mm = db.session.get(__import__('models').Machine, mro.machine_id)
                    if mm and mm.name:
                        nm = f'{nm} · {mm.name}'
                except Exception:
                    pass
            return nm
    eq_id = getattr(obj, 'equipment_id', None)
    if eq_id:
        eq = Equipment.query.get(eq_id)
        if eq and eq.name:
            return eq.name
    return '—'


from . import calendar, plans, schedule
