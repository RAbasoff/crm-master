#!/usr/bin/env python3
"""One-shot fix: delete old marker, run cleanup, verify result."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('FLASK_APP', 'app')

from app import app
from models import db, User, Verantwoordelijke, UserSectionAccess
from sqlalchemy import func

with app.app_context():
    # 1. Delete old markers so migration can re-run
    for v in ['user_cleanup_v10', 'user_cleanup_v11', 'user_cleanup_v12']:
        m = UserSectionAccess.query.filter_by(user_id=0, section_key=v).first()
        if m:
            db.session.delete(m)
            print(f"Deleted marker: {v}")
    db.session.commit()

    # 2. Run the data migration
    from utils import run_data_migrations
    run_data_migrations()

    # 3. Clean up 'Rusln' typo person
    rusln = Verantwoordelijke.query.filter_by(naam='Rusln').first()
    if rusln:
        from models import Machine, Equipment
        Machine.query.filter_by(responsible_person_id=rusln.id).update({'responsible_person_id': None})
        Equipment.query.filter_by(responsible_person_id=rusln.id).update({'responsible_person_id': None})
        User.query.filter_by(person_id=rusln.id).update({'person_id': None})
        db.session.delete(rusln)
        db.session.commit()
        print("Deleted typo person 'Rusln'")

    # 4. Verify
    print("\n--- USERS ---")
    for u in User.query.order_by(User.id).all():
        print(f"  {u.username:15s}  role={u.role}")
    print("\n--- PERSONS ---")
    for p in Verantwoordelijke.query.filter_by(is_active=True).order_by(Verantwoordelijke.id).all():
        print(f"  {p.naam:15s}  group_id={p.group_id}")
    print("\nDone.")
