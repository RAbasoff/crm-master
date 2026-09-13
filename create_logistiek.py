"""Direct fix: create Logistiek group + Monteur users on PA.
Run in PA bash: cd ~/crm-master && python create_logistiek.py
"""
import sys, os
sys.path.insert(0, '/home/rabasoff/crm-master')
os.environ['FLASK_ENV'] = 'production'

from app import app
from models import db, ResponsibleGroup, GroupPermission, WarehouseGroup, Verantwoordelijke, Monteur, User

with app.app_context():
    print("=== Creating Logistiek - Oktopus ===")
    
    # 1. Create ResponsibleGroup
    rg = ResponsibleGroup.query.filter_by(name='Logistiek - Oktopus').first()
    if not rg:
        rg = ResponsibleGroup(name='Logistiek - Oktopus', access_level='technician',
                               description='Logistics team for Oktopus spare parts')
        db.session.add(rg)
        db.session.flush()
        print(f"  Created ResponsibleGroup id={rg.id}")
    else:
        print(f"  ResponsibleGroup already exists id={rg.id}")
    
    # 2. Create WarehouseGroup
    wg = WarehouseGroup.query.filter_by(name='Logistiek - Oktopus').first()
    if not wg:
        wg = WarehouseGroup(name='Logistiek - Oktopus', description='Oktopus spare parts inventory')
        db.session.add(wg)
        db.session.flush()
        print(f"  Created WarehouseGroup id={wg.id}")
    else:
        print(f"  WarehouseGroup already exists id={wg.id}")
    
    # 3. Move Pablo and Paulina to Logistiek group
    for pname, uname, pw in [('Pablo', 'pablo', 'pablo123'), ('Paulina', 'paulina', 'paulina123')]:
        p = Verantwoordelijke.query.filter_by(naam=pname).first()
        if p:
            p.group_id = rg.id
            p.access_level = 'floor'
            p.username = uname
            p.set_password(pw)
            print(f"  Configured {pname} -> group {rg.name}, login={uname}")
        else:
            print(f"  WARNING: {pname} not found in Verantwoordelijke!")
    
    # 4. Permissions
    perms = {
        'warehouse': (True, True, True, False),
        'consumables': (True, True, True, False),
        'purchase_requests': (True, True, True, False),
        'notifications': (True, True, True, False),
        'messages': (True, True, True, False),
        'dashboard': (True, False, False, False),
        'machines': (True, False, False, False),
        'equipment': (True, False, False, False),
        'faults': (True, True, False, False),
        'reports': (True, False, False, False),
    }
    for section, (v, c, e, d) in perms.items():
        existing = GroupPermission.query.filter_by(group_id=rg.id, section_key=section).first()
        if existing:
            existing.can_view = v; existing.can_create = c; existing.can_edit = e; existing.can_delete = d
        else:
            db.session.add(GroupPermission(group_id=rg.id, section_key=section,
                                           can_view=v, can_create=c, can_edit=e, can_delete=d))
    print(f"  Set {len(perms)} permissions")
    
    # 5. Create User accounts for Monteurs without one
    monteurs = Monteur.query.filter_by(actief=True).all()
    for m in monteurs:
        if not m.user_id:
            uname = m.naam.lower().replace(' ', '.').replace('..', '.')
            base = uname; n = 1
            while User.query.filter_by(username=uname).first():
                uname = f"{base}{n}"; n += 1
            mu = User(username=uname, display_name=m.naam, role='technician', is_active_user=True)
            mu.set_password(f'{uname}123')
            db.session.add(mu)
            db.session.flush()
            m.user_id = mu.id
            print(f"  Created User '{uname}' for Monteur '{m.naam}'")
        else:
            u = User.query.get(m.user_id)
            print(f"  Monteur '{m.naam}' already has user '{u.username if u else '?'}'")
    
    db.session.commit()
    print("\n=== DONE ===")
    print(f"Logistiek group id={rg.id}")
    print(f"Warehouse group id={wg.id}")
    print(f"Pablo: pablo/pablo123")
    print(f"Paulina: paulina/paulina123")
