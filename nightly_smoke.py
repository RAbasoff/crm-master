# -*- coding: utf-8 -*-
"""NIGHTLY SMOKE — проверка ключевых страниц под тремя ролями (ночная проверка 02:30).

Запускается отдельным процессом из nightly_self_check (изолированно от
живого веб-воркера). Код выхода 0 = всё ок; 1 = есть страницы с ошибкой.
"""
import sys, re
sys.path.insert(0, '/home/rabasoff/crm-master')
sys.stdout.reconfigure(encoding='utf-8')
from app import app
from models import User

PAGES = [
    '/', '/faults', '/faults/new', '/warehouse', '/machines', '/two', '/orders',
    '/gas', '/gas/cylinders', '/schedule', '/stats/faults', '/equipment',
    '/equipment/mule-list', '/notifications', '/settings', '/time-tracking',
    '/vacations', '/mobile', '/scanner', '/air', '/water', '/moeskroen',
    '/responsible', '/workers', '/repairs', '/maintenance-calendar',
]

state = {'u': 'admin'}

def _force_login():
    from flask_login import login_user
    with app.app_context():
        if state['u'] == 'ar':
            from models import ResponsibleAuth, Verantwoordelijke
            person = Verantwoordelijke.query.filter(Verantwoordelijke.username.ilike('%aris%')).first()
            if person is not None:
                login_user(ResponsibleAuth(person))
        else:
            u = User.query.filter_by(username=state['u']).first()
            if u is not None:
                login_user(u)

app.before_request_funcs.setdefault(None, []).insert(0, _force_login)
client = app.test_client()

problems = []
ok = 0
for user in ('admin', 'nikita', 'ar'):
    state['u'] = user
    for p in PAGES:
        try:
            r = client.get(p, follow_redirects=False)
            if r.status_code >= 500:
                body = r.data.decode('utf-8', 'replace')
                m = re.search(r'(Error|Exception)[^<]{0,120}', body)
                problems.append(f'{user} {p} -> {r.status_code} {(m.group(0)[:120] if m else "")}')
            elif r.status_code < 400 or r.status_code in (403, 404):
                ok += 1
            else:
                problems.append(f'{user} {p} -> {r.status_code}')
        except Exception as e:
            problems.append(f'{user} {p} -> EXC {str(e)[:120]}')

print('SMOKE ok=%d problems=%d' % (ok, len(problems)))
for x in problems:
    print('SMOKE PROBLEM:', x)
sys.exit(1 if problems else 0)
