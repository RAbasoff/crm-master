# -*- coding: utf-8 -*-
"""Smoke: mule registry + card + checklist + attached parts. Cleans up after.

Runs locally (D:\ИИ\CRM_Мастерская) and on PA (/home/rabasoff/crm-master)."""
import sys, re, os
_CAND = [os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else '',
         r'D:\ИИ\CRM_Мастерская', '/home/rabasoff/crm-master']
BASE = next((p for p in _CAND if p and os.path.exists(os.path.join(p, 'app.py'))), _CAND[1])
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding='utf-8')
from app import app
from models import (db, User, EquipmentMaintenance, EquipmentPart,
                    MuleDevice, MuleDevicePart)

DB_PATH = os.path.join(BASE, 'instance', 'werkplaats.db')

state = {'u': 'admin'}

def _force_login():
    from flask_login import login_user
    with app.app_context():
        u = User.query.filter_by(username=state['u']).first()
        if u is not None:
            login_user(u)

app.before_request_funcs.setdefault(None, []).insert(0, _force_login)
app.config['WTF_CSRF_ENABLED'] = False  # тестовый клиент без CSRF
client = app.test_client()
ok = True

def check(name, cond, extra=''):
    global ok
    print(('PASS ' if cond else 'FAIL ') + name + ((' | ' + str(extra)) if extra else ''))
    if not cond:
        ok = False

# 1. pages render
b = client.get('/equipment/mule-list')
check('mule-list 200', b.status_code == 200, b.status_code)
b = client.get('/equipment/mule-checklist')
check('checklist 200', b.status_code == 200, b.status_code)
b = client.get('/equipment/new')
check('new form 200 + mule select', b.status_code == 200 and b'muleSelect' in b.data)

SN = 'TEST-SN-998877'
with app.app_context():
    dev0 = MuleDevice.query.filter_by(serial_number=SN).first()
    if dev0:
        for p in MuleDevicePart.query.filter_by(device_id=dev0.id).all():
            db.session.delete(p)
        for r in EquipmentMaintenance.query.filter_by(device_id=dev0.id).all():
            db.session.delete(r)
        db.session.delete(dev0)
        db.session.commit()

# 2. create TO record via form -> device auto-created, number OH-YYYYMMDD-NNNN
r = client.post('/equipment/new', data={
    'record_type': 'maintenance', 'name': 'TestMule X1', 'serial': SN,
    'model_name': 'TM-1', 'date': '2026-11-22', 'reason': 'smoke test TO',
    'next_date': '2026-12-22', 'periodicity': 'monthly', 'notes': '',
}, follow_redirects=False)
check('create TO redirects', r.status_code in (302, 303), r.status_code)

with app.app_context():
    rec = EquipmentMaintenance.query.filter_by(serial=SN).first()
    check('record created', rec is not None)
    if rec:
        check('number OH- shape', bool(re.match(r'^OH-\d{8}-\d{4}$', rec.number or '')), rec.number)
        check('device linked', rec.device_id is not None, rec.device_id)
        dev_id = rec.device_id
        dev = MuleDevice.query.get(dev_id)
        check('device serial', dev and dev.serial_number == SN, dev.serial_number if dev else None)
    else:
        dev_id = None

if dev_id:
    # 3. card page
    b = client.get(f'/equipment/mule/{dev_id}')
    check('card 200', b.status_code == 200, b.status_code)
    check('card shows serial', SN.encode() in b.data)
    check('card shows history', b'smoke test TO' in b.data)

    # 4. attach part
    r = client.post(f'/equipment/mule/{dev_id}/parts', data={
        'name': 'TEST-knife', 'number': 'K-1', 'default_qty': '2', 'unit': 'st',
    }, follow_redirects=True)
    check('part attach', r.status_code == 200 and b'TEST-knife' in r.data)

    # 5. quick parts json in form
    b = client.get('/equipment/new')
    check('form has parts json', b'TEST-knife' in b.data)

    # 6. checklist has the device done in 2026-11
    b = client.get('/equipment/mule-checklist?month=2026-11')
    check('checklist shows mule', b'TestMule X1' in b.data, b.status_code)
    check('checklist shows done', '✅'.encode() in b.data)

    # 7. second TO in same month via ?device_id prefill
    b = client.get(f'/equipment/new?device_id={dev_id}')
    check('prefill device', b'TestMule X1' in b.data and b'TEST-SN-998877' in b.data)

# 8.4 numbering scheme (правило 2026-11-22):
# OH-{дата проведения}-{сквозной годовой +1}, с 1 января обнуляется
for _i, _dt in (('1', '2026-12-05'), ('2', '2026-12-20'), ('3', '2027-01-10')):
    r = client.post('/equipment/new', data={
        'record_type': 'maintenance', 'name': 'TestMule X1', 'serial': SN,
        'model_name': 'TM-1', 'date': _dt, 'reason': f'num check {_i}',
    }, follow_redirects=False)
    check(f'num rec{_i} created', r.status_code in (302, 303), r.status_code)
with app.app_context():
    _m = {rr.reason: rr.number for rr in EquipmentMaintenance.query.filter(
        EquipmentMaintenance.reason.in_(('num check 1', 'num check 2', 'num check 3'))).all()}
    n1, n2, n3 = _m.get('num check 1', ''), _m.get('num check 2', ''), _m.get('num check 3', '')

    def _ord(s):
        t = (s or '').rsplit('-', 1)[-1]
        return int(t) if t.isdigit() else -1

    check('num1 date part = service date', n1.startswith('OH-20261205-'), n1)
    check('num2 date part = service date', n2.startswith('OH-20261220-'), n2)
    check('num3 date part = service date', n3.startswith('OH-20270110-'), n3)
    check('num2 = num1 + 1 (сквозной)', _ord(n2) == _ord(n1) + 1, f'{n1} -> {n2}')
    check('num3 yearly reset to 1', _ord(n3) == 1, n3)

# 8.5 regressions from independent review
# (a) repair must NOT create a registry device
r = client.post('/equipment/new', data={
    'record_type': 'repair', 'name': 'TestRepair Z9', 'serial': 'TEST-SN-REPAIR',
    'model_name': '', 'date': '2026-11-22', 'reason': 'smoke repair',
}, follow_redirects=False)
check('repair created', r.status_code in (302, 303), r.status_code)
with app.app_context():
    check('repair no registry device',
          MuleDevice.query.filter_by(serial_number='TEST-SN-REPAIR').count() == 0)
    for rec in EquipmentMaintenance.query.filter_by(serial='TEST-SN-REPAIR').all():
        db.session.delete(rec)
    db.session.commit()

# (b) legacy record (device_id NULL, serial matches registry) must count done
with app.app_context():
    from datetime import date as _d
    leg = EquipmentMaintenance(number='OH-20261012-9999', record_type='maintenance',
                               name='TestMule X1', serial=SN,
                               date=_d(2026, 10, 15), reason='legacy smoke',
                               created_by=1)
    db.session.add(leg)
    db.session.commit()
b = client.get('/equipment/mule-checklist?month=2026-10')
check('legacy record counts done',
      b'TestMule X1' in b.data and '✅'.encode() in b.data, b.status_code)

# 8. part delete + cleanup
with app.app_context():
    if dev_id:
        part_ids = [p.id for p in MuleDevicePart.query.filter_by(device_id=dev_id).all()]
        db.session.commit()  # закрыть read-транзакцию, чтобы не блокировать DELETE
    else:
        part_ids = []
print('  part_ids to delete:', part_ids)
for pid in part_ids:
    r = client.post(f'/equipment/mule/part/{pid}/delete', follow_redirects=True)
    body = r.data.decode('utf-8', 'replace')
    ok_del = ('losgekoppeld' in body) or ('Part detached' in body)
    check('part delete POST', r.status_code == 200 and ok_del,
          ('flash ok' if ok_del else 'SAVE FAILED?') + ' status=' + str(r.status_code))
import sqlite3 as _sq
_c = _sq.connect(DB_PATH)
print('  RAW rows in mule_device_part:', _c.execute('SELECT id, name, device_id FROM mule_device_part').fetchall())
_c.close()
with app.app_context():
    if dev_id:
        left = MuleDevicePart.query.filter_by(device_id=dev_id).all()
        for q in left:
            print('  LEFTOVER part:', q.id, q.name, 'dev', q.device_id)
        check('parts deleted', len(left) == 0, [(q.id, q.name) for q in left])
    for rec in EquipmentMaintenance.query.filter_by(serial=SN).all():
        db.session.delete(rec)
    dev = MuleDevice.query.filter_by(serial_number=SN).first()
    if dev:
        db.session.delete(dev)
    db.session.commit()
    left = EquipmentMaintenance.query.filter_by(serial=SN).count()
    check('test data cleaned', left == 0)

print('RESULT:', 'OK' if ok else 'FAILED')
sys.exit(0 if ok else 1)
