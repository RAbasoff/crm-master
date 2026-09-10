#!/usr/bin/env python3
"""Diagnostic: check users, persons, technicians after migration v11."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('FLASK_APP', 'app')

from app import app
from models import db, User, Verantwoordelijke, ResponsibleGroup, GroupPermission, UserSectionAccess
from sqlalchemy import func

with app.app_context():
    print("=" * 60)
    print("1. SYSTEM USERS (user table)")
    print("=" * 60)
    users = User.query.order_by(User.id).all()
    for u in users:
        person_name = ''
        if u.person_id:
            p = Verantwoordelijke.query.get(u.person_id)
            person_name = f' -> person: {p.naam}' if p else f' -> person #{u.person_id} (DELETED!)'
        print(f'  ID={u.id:3d}  username={u.username:15s}  role={u.role:12s}  active={u.is_active_user}  person_id={u.person_id}{person_name}')

    print()
    print("=" * 60)
    print("2. PERSONS (client table) — all active")
    print("=" * 60)
    persons = Verantwoordelijke.query.filter_by(is_active=True).order_by(Verantwoordelijke.id).all()
    for p in persons:
        group_name = ''
        if p.group_id:
            g = ResponsibleGroup.query.get(p.group_id)
            group_name = g.name if g else f'GROUP#{p.group_id}(DELETED)'
        else:
            group_name = '(no group)'
        print(f'  ID={p.id:3d}  naam={p.naam:15s}  group={group_name:15s}  access={p.access_level}')

    print()
    print("=" * 60)
    print("3. RESPONSIBLE GROUPS")
    print("=" * 60)
    groups = ResponsibleGroup.query.order_by(ResponsibleGroup.id).all()
    for g in groups:
        members = Verantwoordelijke.query.filter_by(group_id=g.id, is_active=True).all()
        member_names = ', '.join(m.naam for m in members) or '(empty)'
        print(f'  ID={g.id}  name={g.name:15s}  level={g.access_level:12s}  members: {member_names}')

    print()
    print("=" * 60)
    print("4. TECHNICIAN GROUP MEMBERS")
    print("=" * 60)
    tech_group = ResponsibleGroup.query.filter_by(access_level='technician').first()
    if tech_group:
        techs = Verantwoordelijke.query.filter_by(group_id=tech_group.id, is_active=True).all()
        if techs:
            for t in techs:
                print(f'  {t.naam} (ID={t.id})')
        else:
            print("  (none — correctly moved to Technische dienst)")
    else:
        print("  Technician group not found!")

    print()
    print("=" * 60)
    print("5. CHECK: Tim, Thijs, Lukash existence")
    print("=" * 60)
    for name in ['Tim', 'Thijs', 'Lukash', 'Lukas', 'Hashim', 'Dina']:
        p = Verantwoordelijke.query.filter_by(naam=name).first()
        u = User.query.filter(func.lower(User.username) == name.lower()).first()
        status = []
        if p: status.append(f'PERSON exists (ID={p.id})')
        if u: status.append(f'USER exists (ID={u.id}, role={u.role})')
        print(f'  {name:10s}: {"; ".join(status) if status else "CLEAN"}')

    print()
    print("=" * 60)
    print("6. MIGRATION MARKERS")
    print("=" * 60)
    for v in ['user_cleanup_v10', 'user_cleanup_v11']:
        m = UserSectionAccess.query.filter_by(user_id=0, section_key=v).first()
        print(f'  {v}: {"EXISTS" if m else "not found"}')

    print()
    print("Done.")
