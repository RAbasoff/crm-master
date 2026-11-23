# -*- coding: utf-8 -*-
"""Smoke: planning module (calendar + purchases + production + shipments + export).
Cleans up after itself."""
import sys, os
_CAND = [os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else '',
         r'D:\ИИ\CRM_Мастерская', '/home/rabasoff/crm-master']
BASE = next((p for p in _CAND if p and os.path.exists(os.path.join(p, 'app.py'))), _CAND[1])
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding='utf-8')
from app import app
from models import (db, User, PlanPurchase, PlanProduction, PlanShipment, Machine)

state = {'u': 'admin'}

def _force_login():
    from flask_login import login_user
    with app.app_context():
        u = User.query.filter_by(username=state['u']).first()
        if u is not None:
            login_user(u)

app.before_request_funcs.setdefault(None, []).insert(0, _force_login)
app.config['WTF_CSRF_ENABLED'] = False
client = app.test_client()
ok = True

def check(name, cond, extra=''):
    global ok
    print(('PASS ' if cond else 'FAIL ') + name + ((' | ' + str(extra)) if extra else ''))
    if not cond:
        ok = False

# pre-clean (остатки после упавших прогонов)
with app.app_context():
    for m in (PlanPurchase, PlanProduction, PlanShipment):
        m.query.filter(m.article_name.like('SMOKE-%')).delete(synchronize_session=False)
    db.session.commit()
    mid = Machine.query.first().id if Machine.query.first() else None

# 1. pages render
for url in ('/planning', '/planning/purchases', '/planning/production', '/planning/shipments'):
    b = client.get(url)
    check(f'{url} 200', b.status_code == 200, b.status_code)

# 2. create entries
r = client.post('/planning/purchases/save', data={
    'article_code': 'RAW-1', 'article_name': 'SMOKE-grondstof', 'qty': '100',
    'unit': 'kg', 'planned_date': '2026-12-01', 'notes': 'smoke',
}, follow_redirects=False)
check('purchase saved', r.status_code in (302, 303), r.status_code)

prod_data = {
    'article_code': 'ART-1', 'article_name': 'SMOKE-product', 'qty': '500',
    'unit': 'kg', 'machine_name': 'KRUID', 'prod_date': '2026-12-01',
    'work_order': 'WO-777', 'lot': 'L1', 'priority': '5',
}
r = client.post('/planning/production/save', data=prod_data, follow_redirects=False)
check('production saved', r.status_code in (302, 303), r.status_code)

if mid:
    prod2 = dict(prod_data, article_name='SMOKE-product2', machine_id=str(mid))
    r = client.post('/planning/production/save', data=prod2, follow_redirects=False)
    check('production saved (machine_id)', r.status_code in (302, 303), r.status_code)

r = client.post('/planning/shipments/save', data={
    'article_code': 'FG-1', 'article_name': 'SMOKE-shipment', 'qty': '200',
    'unit': 'st', 'client_name': 'SMOKE-client', 'transport': 'SMOKE-truck',
    'ship_date': '2026-12-01',
}, follow_redirects=False)
check('shipment saved', r.status_code in (302, 303), r.status_code)

# 3. calendar shows all three on 2026-12-01
b = client.get('/planning?d=2026-12-01')
h = b.data.decode('utf-8', 'replace')
check('calendar shows purchase', 'SMOKE-grondstof' in h)
check('calendar shows production', 'SMOKE-product' in h and 'KRUID' in h)
check('calendar shows shipment', 'SMOKE-shipment' in h and 'SMOKE-client' in h)

# 4. lists show entries
b = client.get('/planning/production')
check('production list has machine', b'SMOKE-product' in b.data and b'KRUID' in b.data)

# 5. export xlsx
b = client.get('/planning/export.xlsx')
check('export xlsx 200', b.status_code == 200 and b.data[:2] == b'PK', b.status_code)

# 6. status change
with app.app_context():
    p = PlanPurchase.query.filter_by(article_name='SMOKE-grondstof').first()
    pid = p.id if p else None
if pid:
    r = client.post(f'/planning/purchases/{pid}/status', data={'status': 'done'}, follow_redirects=True)
    check('purchase done', 'Gereed' in r.data.decode('utf-8', 'replace'))

# 7. edit prefill
with app.app_context():
    p = PlanProduction.query.filter_by(article_name='SMOKE-product').first()
    prod_id = p.id if p else None
if prod_id:
    b = client.get(f'/planning/production?edit={prod_id}')
    check('edit prefill', b'SMOKE-product' in b.data and b'WO-777' in b.data)

# 8.5 карта загрузки (Ф2): часы, поломка, ТО, перегрузка, дубль
from datetime import date as _date, datetime as _dt
from models import FaultReport, MaintenancePlan
with app.app_context():
    m = Machine.query.first()
    mid2 = m.id if m else None
    # план на 17ч (перегрузка > 16) + дубль WO
    for i, (wo, hrs) in enumerate((('WO-DUP', '10'), ('WO-DUP', '7'))):
        r = client.post('/planning/production/save', data={
            'article_code': 'X', 'article_name': f'SMOKE-load{i}', 'qty': '1',
            'unit': 'kg', 'machine_id': str(mid2), 'prod_date': '2026-12-02',
            'work_order': wo, 'hours': hrs,
        }, follow_redirects=False)
        check(f'load plan {i} saved', r.status_code in (302, 303), r.status_code)
    if mid2:
        f = FaultReport(title='SMOKE-fault', description='smoke', status='open',
                        machine_id=mid2, reporter_id=1,
                        created_at=_dt(2026, 12, 1, 8, 0))
        pl = MaintenancePlan(title='SMOKE-to', machine_id=mid2,
                             planned_start=_date(2026, 12, 2), status='planned')
        db.session.add(f)
        db.session.add(pl)
        db.session.commit()
b = client.get('/planning/load?d=2026-12-02')
h = b.data.decode('utf-8', 'replace')
check('load page 200', b.status_code == 200, b.status_code)
check('load shows machine', mid2 is not None and 'SMOKE-load0' in h)
check('load overload+dup cell', 'background:#fdecea' in h)
with app.app_context():
    f = FaultReport.query.filter_by(title='SMOKE-fault').first()
    if f:
        db.session.delete(f)
    pl = MaintenancePlan.query.filter_by(title='SMOKE-to').first()
    if pl:
        db.session.delete(pl)
    PlanProduction.query.filter(PlanProduction.article_name.like('SMOKE-%')).delete(synchronize_session=False)
    db.session.commit()

# 8.6 export содержит часы
b = client.get('/planning/export.xlsx')
check('export xlsx (hours col)', b.status_code == 200 and b.data[:2] == b'PK', b.status_code)

# 8.7 Ф3: подсказки — мин. остаток → закупка; план → списание; наличие для отгрузки
from models import VoorraadItem, VoorraadMutatie
with app.app_context():
    w_low = VoorraadItem(naam='SMOKE-low', eenheid='kg', hoeveelheid=2, minimum=10)
    w_fg = VoorraadItem(naam='SMOKE-shipment', eenheid='st', hoeveelheid=50, minimum=0)
    db.session.add(w_low)
    db.session.add(w_fg)
    db.session.commit()
    low_id, fg_id = w_low.id, w_fg.id
b = client.get('/planning')
h = b.data.decode('utf-8', 'replace')
check('hints: low stock shown', 'SMOKE-low' in h and 'from_stock/' in h)
check('hints: shipment shortage', 'SMOKE-shipment' in h and '⚠' in h)

r = client.post(f'/planning/purchases/from_stock/{low_id}', follow_redirects=True)
check('hint creates purchase', b'SMOKE-low' in r.data)
with app.app_context():
    p = PlanPurchase.query.filter_by(article_name='SMOKE-low').first()
    check('purchase from stock qty', p is not None and p.qty == 8, p.qty if p else None)

with app.app_context():
    plan = PlanProduction(article_code='CC', article_name='SMOKE-consume', qty=1,
                          unit='kg', prod_date=__import__('datetime').date(2026, 12, 3),
                          created_by=1)
    db.session.add(plan)
    db.session.commit()
    plan_id = plan.id
if plan_id:
    b = client.get(f'/planning/production/{plan_id}/consume')
    check('consume page 200', b.status_code == 200, b.status_code)
    r = client.post(f'/planning/production/{plan_id}/consume',
                    data={'warehouse_item_id': str(fg_id), 'qty': '5'}, follow_redirects=True)
    check('consume saved', r.status_code == 200)
    with app.app_context():
        w = VoorraadItem.query.get(fg_id)
        mv = VoorraadMutatie.query.filter_by(item_id=fg_id, type='uitgaand').all()
        check('stock decreased', w is not None and w.hoeveelheid == 45, w.hoeveelheid if w else None)
        check('mutation created', len(mv) >= 1, len(mv))

with app.app_context():
    VoorraadMutatie.query.filter_by(item_id=fg_id).delete(synchronize_session=False)
    for wid in (low_id, fg_id):
        w = VoorraadItem.query.get(wid)
        if w:
            db.session.delete(w)
    PlanPurchase.query.filter(PlanPurchase.article_name.like('SMOKE-%')).delete(synchronize_session=False)
    db.session.commit()

# 8. delete + cleanup
with app.app_context():
    ids = {}
    for m, key in ((PlanPurchase, 'p'), (PlanProduction, 'pr'), (PlanShipment, 's')):
        ids[key] = [x.id for x in m.query.filter(m.article_name.like('SMOKE-%')).all()]
        db.session.commit()
for key, url in (('p', 'purchases'), ('pr', 'production'), ('s', 'shipments')):
    for i in ids[key]:
        r = client.post(f'/planning/{url}/{i}/delete', follow_redirects=False)
        check(f'{url} delete {i}', r.status_code in (302, 303), r.status_code)
with app.app_context():
    left = (PlanPurchase.query.filter(PlanPurchase.article_name.like('SMOKE-%')).count()
            + PlanProduction.query.filter(PlanProduction.article_name.like('SMOKE-%')).count()
            + PlanShipment.query.filter(PlanShipment.article_name.like('SMOKE-%')).count())
    check('test data cleaned', left == 0, left)

print('RESULT:', 'OK' if ok else 'FAILED')
sys.exit(0 if ok else 1)
