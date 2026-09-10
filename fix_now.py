#!/usr/bin/env python3
"""One-shot fix: add Maico/Filip to workers + clean Rusln."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('FLASK_APP', 'app')

from app import app
from models import db, User, Verantwoordelijke, Monteur

with app.app_context():
    # 1. Add Maico and Filip to workers table (if not already there)
    tech_workers = [
        ('Maico', 'Mechanica'),
        ('Filip', 'Mechanica'),
        ('Aris', 'Mechanica'),  # Aristidis - check if already exists
    ]
    for naam, spec in tech_workers:
        existing = Monteur.query.filter_by(naam=naam).first()
        if existing:
            print(f"Worker '{naam}' already exists (ID={existing.id})")
        else:
            w = Monteur(naam=naam, specialisatie=spec, actief=True)
            db.session.add(w)
            print(f"Created worker '{naam}'")

    db.session.commit()

    # 2. Clean up 'Rusln' typo person (if still exists)
    rusln = Verantwoordelijke.query.filter_by(naam='Rusln').first()
    if rusln:
        from models import Machine, Equipment
        Machine.query.filter_by(responsible_person_id=rusln.id).update({'responsible_person_id': None})
        Equipment.query.filter_by(responsible_person_id=rusln.id).update({'responsible_person_id': None})
        User.query.filter_by(person_id=rusln.id).update({'person_id': None})
        db.session.delete(rusln)
        db.session.commit()
        print("Deleted typo person 'Rusln'")

    # 3. Verify workers
    print("\n--- WORKERS (Monteur table) ---")
    for w in Monteur.query.order_by(Monteur.naam).all():
        status = "active" if w.actief else "inactive"
        print(f"  ID={w.id:3d}  naam={w.naam:15s}  spec={w.specialisatie or '-':15s}  {status}")

    print("\nDone.")
