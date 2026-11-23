# -*- coding: utf-8 -*-
"""Parse owner's planning Excels into planning_import.json (local step of import)."""
import openpyxl, json, sys, re
sys.stdout.reconfigure(encoding='utf-8')

def norm_date(v):
    if hasattr(v, 'strftime'):
        return v.strftime('%Y-%m-%d')
    s = str(v or '').strip()
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    return m.group(0) if m else ''

# ── EOY PLANNING: article × day quantities (December program) ──
eoy = []
wb = openpyxl.load_workbook(r'C:\Users\abaso\Desktop\EOY PLANNING LUKASZ.xlsx', data_only=True)
ws = wb['Sheet1']
headers = [c for c in next(ws.iter_rows(min_row=3, max_row=3, values_only=True))]
date_cols = {}
for i, h in enumerate(headers):
    d = norm_date(h)
    if d:
        date_cols[i] = d
for row in ws.iter_rows(min_row=4, values_only=True):
    code = str(row[0] or '').strip()
    name = str(row[1] or '').strip()
    if not code or not name:
        continue
    for i, d in date_cols.items():
        v = row[i] if i < len(row) else None
        if isinstance(v, (int, float)) and v:
            eoy.append({'code': code, 'name': name, 'qty': float(v), 'unit': 'st', 'date': d})
print('EOY rows:', len(eoy), 'cols dates:', len(date_cols))

# ── TEST PLANNING: LISA werkorders + machine mapping ──
wb2 = openpyxl.load_workbook(r'C:\Users\abaso\Desktop\TEST PLANNING 2026 - 23 november.xlsx', data_only=True)
machines = {}
ws2 = wb2['Verwerkingsstap']
for row in ws2.iter_rows(min_row=2, values_only=True):
    code = str(row[0] or '').strip()
    mach = str(row[4] or '').strip() if len(row) > 4 else ''
    if code and mach and code not in machines:
        machines[code] = mach
print('machine map:', len(machines))

wo = []
ws3 = wb2['lisa wo inplakken']
for row in ws3.iter_rows(min_row=2, values_only=True):
    st = str(row[0] or '').strip()
    if st != 'PLAN':
        continue
    worder = str(row[1] or '').strip()
    eind = norm_date(row[2])
    code = str(row[3] or '').strip()
    name = str(row[4] or '').strip()
    qty = row[5] if isinstance(row[5], (int, float)) else 0
    unit = str(row[8] or 'kg').strip() if len(row) > 8 else 'kg'
    lot = str(row[10] or '').strip() if len(row) > 10 else ''
    uvd = norm_date(row[11]) if len(row) > 11 else ''
    if not name:
        continue
    wo.append({'code': code, 'name': name, 'qty': float(qty or 0),
               'unit': unit or 'kg', 'date': eind or '2026-11-23',
               'work_order': worder, 'lot': lot, 'uvd': uvd,
               'machine': machines.get(code, '')})
print('WO rows:', len(wo))

json.dump({'eoy': eoy, 'wo': wo}, open('planning_import.json', 'w', encoding='utf-8'), ensure_ascii=False)
print('saved planning_import.json')
print('sample eoy:', eoy[:3])
print('sample wo:', wo[:3])
