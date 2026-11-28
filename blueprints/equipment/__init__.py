"""
equipment blueprint
"""
from datetime import datetime
from flask import Blueprint, make_response
import re as _re

from models import (db, EquipmentMaintenance, EquipmentPartOrder, Machine,
                    MuleDevice, now_local)
from utils import next_number_suffix, safe_int

bp = Blueprint('equipment', __name__)


def mule_machines():
    """Машины модуля Mule (ТО/ремонт): CAVECO / Skin / Ypsilon / ProSeal."""
    return Machine.query.filter(db.or_(
        Machine.name.ilike('%caveco%'),
        Machine.name.ilike('%skin%'),
        Machine.name.ilike('%ypsilon%'),
        Machine.name.ilike('%pro%seal%'),
    )).order_by(Machine.name).all()


def mule_machines_json(machines):
    """Карта для автоподстановки станка по серийному номеру (в форме)."""
    out = []
    for m in machines:
        sn = (m.serial_number or '').strip()
        if sn.lower() in ('none', 'null', '-', '—'):
            sn = ''
        out.append({'id': m.id, 'name': m.name, 'serial': sn})
    return out


_SPLIT_SEP = _re.compile(r'[\s/]+')
_TOK_DIGITS = _re.compile(r'\d+$')
_TOK_CODE = _re.compile(r'[A-Za-z]{1,3}\d{2,}$')
_TOK_WORD = _re.compile(r'[A-Za-z]+$')
_TOK_COMPOUND = _re.compile(r'[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9/-]+$')


def _is_model_token(t):
    """Токен похож на обозначение модели: C2941, M1721, 1799, 55, Nutripack-V9."""
    return bool(_TOK_DIGITS.match(t) or _TOK_CODE.match(t) or _TOK_COMPOUND.match(t))


def split_mule_name(name):
    """Разделить «имя + модель» в одном поле: 'V9 II COM 1799' → ('V9 II', 'COM 1799').

    Принцип: модель — хвостовой блок кодов (буквы+цифры, 'COM 1799', 'C2902/1'),
    имя — то, что впереди ('V9 II', 'V7 ALU', 'V9 Links'). Если всё имя похоже
    на модель — первый токен остаётся именем ('M2623 C3043' → 'M2623' + 'C3043').
    Одиночные коды и словосочетания ('Kwartaal onderhoud') не трогаем.
    """
    s = (name or '').strip()
    if not s:
        return '', ''
    parts = []
    prev = 0
    for m in _SPLIT_SEP.finditer(s):
        parts.append((s[prev:m.start()], prev))
        prev = m.end()
    parts.append((s[prev:], prev))
    parts = [(t, p) for t, p in parts if t]
    n = len(parts)
    if n < 2:
        return s, ''
    # идём справа: хвостовой блок токенов-моделей
    i = n - 1
    while i >= 0:
        t = parts[i][0]
        if _is_model_token(t):
            i -= 1
            continue
        # 'COM' + '1799', 'M' + '620': буквы перед чисто цифровым токеном
        if _TOK_WORD.match(t) and i + 1 < n and _TOK_DIGITS.fullmatch(parts[i + 1][0]):
            i -= 1
            continue
        break
    k = i + 1  # первый токен модели
    if k >= n:
        return s, ''          # модели не нашли
    if k == 0:
        # всё имя — «модельные» токены: первый токен оставляем именем
        if n >= 2 and any(_re.search(r'[A-Za-z]', parts[j][0]) for j in range(1, n)):
            k = 1
        else:
            return s, ''
    new_name = s[:parts[k][1]].strip(' /')
    new_model = s[parts[k][1]:].strip()
    if not new_name or not new_model:
        return s, ''
    return new_name, new_model


def gen_eq_number(record_type='maintenance', on_date=None):
    """Номер документа ТО/ремонта.

    Правило владельца (2026-11-22, «ИСПРАВЬ и ЗАПОМНИ»):
    ТО = OH-{дата проведения обслуживания}-{порядковый}, где порядковый —
    СКВОЗНОЙ в пределах года (каждое следующее ТО = +1, «на единицу выше»),
    НЕ заново с единицы на каждую дату. С 1 января порядковый обнуляется и
    идёт с 1 до 31 декабря. Дата в номере — дата ПРОВЕДЕНИЯ (поле date
    записи), а не дата ввода. Исторические номера не перенумеровываются.
    Ремонты — REP-{дата}-{номер} как раньше.
    """
    d = on_date or now_local().date()
    day = d.strftime('%Y%m%d') if hasattr(d, 'strftime') else str(d).replace('-', '')
    if record_type == 'repair':
        num = next_number_suffix(EquipmentMaintenance, 'number', f'REP-{day}')
        return f'REP-{day}-{num:04d}'
    # порядковый за ГОД (год даты проведения): максимум по OH-{год}% + 1
    year = day[:4]
    max_n = 0
    for (val,) in db.session.query(EquipmentMaintenance.number).filter(
            EquipmentMaintenance.number.like(f'OH-{year}%')).all():
        tail = str(val).rsplit('-', 1)[-1]
        if not tail.isdigit() or len(tail) > 6:
            continue
        n = int(tail)
        if n > max_n:
            max_n = n
    return f'OH-{day}-{max_n + 1:04d}'


def _norm_serial(s):
    """Серийник без пробелов/знаков, верхний регистр — идентичность устройства."""
    return _re.sub(r'[^A-Za-z0-9]', '', (s or '')).upper()


def _find_device(serial, name=None):
    """Найти муле в реестре: по нормализованному серийнику, иначе по имени."""
    ns = _norm_serial(serial)
    if ns:
        for d in MuleDevice.query.all():
            if d.normalized_serial() == ns:
                return d
    if name:
        key = (name or '').strip().lower()
        for d in MuleDevice.query.all():
            if (d.name or '').strip().lower() == key:
                return d
    return None


def _get_or_create_device(name, model, serial, machine_id=None):
    """Муле заводится ОДИН раз: дальше только выбирается из реестра."""
    serial = (serial or '').strip()
    dev = _find_device(serial, name if not serial else None)
    if dev is None:
        if not serial:
            return None
        dev = MuleDevice(name=(name or serial).strip(), model_name=(model or '').strip(),
                         serial_number=serial, machine_id=machine_id)
        db.session.add(dev)
        db.session.flush()
    else:
        # обновляем карточку, если что-то уточнили
        if name and name.strip() and dev.name != name.strip():
            dev.name = name.strip()
        if model and model.strip() and (dev.model_name or '') != model.strip():
            dev.model_name = model.strip()
        if serial and not dev.serial_number:
            dev.serial_number = serial
    if machine_id:
        dev.machine_id = machine_id
    return dev


def gen_eq_order_number():
    day = now_local().strftime('%Y%m%d')
    num = next_number_suffix(EquipmentPartOrder, 'order_number', f'EPO-{day}')
    return f'EPO-{day}-{num:04d}'


def mule_devices_json(devices):
    """Реестр муле для автозаполнения формы ТО + закреплённые запчасти."""
    out = []
    for d in devices:
        out.append({
            'id': d.id, 'name': d.name or '', 'model': d.model_name or '',
            'serial': d.serial_number or '', 'machine_id': d.machine_id,
            'parts': [{'id': p.id, 'name': p.name, 'number': p.number or '',
                       'unit': p.unit or 'st', 'qty': p.default_qty or 1,
                       'warehouse_item_id': p.warehouse_item_id}
                      for p in d.parts_catalog],
        })
    return out


# ── Список Mule: реестр устройств (печать + PDF + история перемещений) ──

def _mule_devices():
    """Устройства Mule — реестр оборудования: ОДНА строка на устройство.

    Источник истины — MuleDevice (реестр, заводится один раз). Записи ТО
    привязаны через device_id; старые записи без привязки собираются по
    нормализованному серийнику ('SSVF148232 L2M2.0' и 'SSVF148232L2M2.0' —
    одно устройство). Без серийника — по нормализованному имени. Имя/модель —
    из самой свежей записи. Записи журнала планов (OJ-*) реестр не пополняют.
    """
    recs = (EquipmentMaintenance.query
            .filter(EquipmentMaintenance.record_type.isnot('repair'),
                    db.not_(EquipmentMaintenance.number.like('OJ-%')))
            .order_by(EquipmentMaintenance.date.asc(), EquipmentMaintenance.id.asc())
            .all())
    devices, order = {}, []
    dev_by_id = {d.id: d for d in MuleDevice.query.all()}
    dev_by_sn = {}
    for d in dev_by_id.values():
        ns = d.normalized_serial()
        if ns:
            dev_by_sn.setdefault(ns, d)

    def _key_for(r):
        if r.device_id and r.device_id in dev_by_id:
            return 'd:%s' % r.device_id
        serial_norm = _norm_serial(r.serial)
        if serial_norm and serial_norm in dev_by_sn:
            return 'd:%s' % dev_by_sn[serial_norm].id
        if serial_norm:
            return 's:' + serial_norm
        name_norm = _re.sub(r'\s+', ' ', (r.name or '').strip().lower())
        return ('n:' + name_norm) if name_norm else ''

    for r in recs:
        key = _key_for(r)
        if not key:
            continue
        if key not in devices:
            reg = dev_by_id.get(int(key[2:])) if key.startswith('d:') else None
            devices[key] = {
                'device_id': reg.id if reg else None,
                'name': (reg.name if reg else (r.name or r.number).strip()),
                'serial': (reg.serial_number if reg and reg.serial_number else (r.serial or '').strip()),
                'model': (reg.model_name if reg and reg.model_name else ''),
                'components': [], 'machine': '—', 'history': []}
            order.append(key)
        d = devices[key]
        # самая свежая запись задаёт актуальные имя/модель/серийник
        if not d.get('device_id') and r.name and r.name.strip():
            d['name'] = r.name.strip()
        if r.model_name:
            d['model'] = r.model_name
        if not d.get('device_id') and r.serial and r.serial.strip():
            d['serial'] = r.serial.strip()
        if r.machine_id:
            try:
                d['machine'] = r.machine.name if r.machine else d['machine']
            except Exception:
                pass
        comps = []
        try:
            comps = list(r.components)
        except Exception:
            pass
        if comps:
            def _qty(q):
                try:
                    f = float(q if q is not None else 1)
                    return int(f) if f == int(f) else round(f, 2)
                except Exception:
                    return 1
            d['components'] = [
                {'type': c.component_type or '', 'model': c.model or '',
                 'size': (c.size or '') + ((' ' + c.length) if c.length else ''),
                 'qty': _qty(c.quantity), 'notes': c.notes or ''}
                for c in comps
            ]
        h = (r.date, d['machine'])
        if not d['history'] or d['history'][-1] != h:
            d['history'].append(h)
    # зарегистрированные муле без записей ТО тоже показываем
    for did, reg in dev_by_id.items():
        key = 'd:%s' % did
        if key not in devices:
            devices[key] = {
                'device_id': reg.id, 'name': reg.name,
                'serial': reg.serial_number or '', 'model': reg.model_name or '',
                'components': [], 'machine': (reg.machine.name if reg.machine else '—'),
                'history': []}
            order.append(key)
    out = []
    for i, key in enumerate(order, 1):
        d = devices[key]
        d['no'] = i
        out.append(d)
    return out


def _pdf_response(pdf, filename):
    resp = make_response(pdf.output(dest='S'))
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


def _equipment_ids_query(ids_param):
    """EquipmentMaintenance query filtered by comma-separated ids; empty = all."""
    q = EquipmentMaintenance.query
    if ids_param.strip():
        id_list = [safe_int(x) for x in ids_param.split(',') if x.strip()]
        if id_list:
            q = q.filter(EquipmentMaintenance.id.in_(id_list))
        else:
            q = q.filter(EquipmentMaintenance.id == -1)
    return q


# Sub-modules attach their routes to `bp` via `from . import bp` at import time.
from . import crud, mule, exports  # noqa: E402,F401
from models import now_local
