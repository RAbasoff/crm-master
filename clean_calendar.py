"""Delete all generated maintenance plans. Run on PA or locally."""
import sqlite3, os
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
conn = sqlite3.connect(DB)
cur = conn.cursor()

# Count before
cur.execute('SELECT COUNT(*) FROM maintenance_plan')
before = cur.fetchone()[0]

# Delete all planned entries
cur.execute("DELETE FROM maintenance_plan WHERE status = 'planned'")
deleted_planned = cur.rowcount

# Delete all completed entries that were generated (have description containing 'completed by')
cur.execute("DELETE FROM maintenance_plan")
deleted_all = cur.rowcount

conn.commit()

cur.execute('SELECT COUNT(*) FROM maintenance_plan')
after = cur.fetchone()[0]

print(f"Before: {before} plans")
print(f"Deleted planned: {deleted_planned}")
print(f"Deleted ALL: {deleted_all}")
print(f"After: {after} plans")

# Also clean up orphaned MaintenanceRecords that were auto-generated
cur.execute("SELECT COUNT(*) FROM maintenance_record")
mr_before = cur.fetchone()[0]
cur.execute("DELETE FROM maintenance_record WHERE description LIKE '%completed by%' AND description LIKE '%calendar%'")
mr_deleted = cur.rowcount
conn.commit()
print(f"\nMaintenanceRecords: deleted {mr_deleted} calendar-generated (from {mr_before} total)")

conn.close()
print("\nDone! Calendar is clean.")
