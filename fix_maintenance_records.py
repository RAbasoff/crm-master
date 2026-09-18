"""Fix stale MaintenanceRecord next_maintenance values on production."""
import sys
sys.path.insert(0, '.')
from app import app, db
from models import MaintenanceRecord
from datetime import datetime

with app.app_context():
    # Find records with next_maintenance set (from old buggy code)
    records = MaintenanceRecord.query.filter(
        MaintenanceRecord.next_maintenance.isnot(None)
    ).all()
    print('Records with next_maintenance set:', len(records))
    for r in records:
        print('  ID=%d machine=%d date_performed=%s next_maintenance=%s desc=%s' % (
            r.id, r.machine_id, r.date_performed, r.next_maintenance, r.description[:50] if r.description else ''))
    
    # Clear next_maintenance for all records (these are from old buggy code)
    count = 0
    for r in records:
        r.next_maintenance = None
        count += 1
    
    if count > 0:
        db.session.commit()
        print('\nFixed %d records (next_maintenance cleared)' % count)
    else:
        print('\nNo records to fix')