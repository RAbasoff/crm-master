"""
Check maintenance calendar data on PythonAnywhere.
Run this on PA: python check_pa_calendar.py
"""
import sqlite3
conn = sqlite3.connect('instance/werkplaats.db')
cur = conn.cursor()

print("=== Maintenance Plans ===")
cur.execute('SELECT id, machine_id, title, status, planned_start, recurrence, maintenance_type FROM maintenance_plan ORDER BY id')
for r in cur.fetchall():
    print(f"  ID={r[0]} machine={r[1]} title={r[2]} status={r[3]} start={r[4]} recur={r[5]} type={r[6]}")

print("\n=== Maintenance Records ===")
cur.execute('SELECT id, machine_id, maintenance_type, description, date_performed, next_maintenance FROM maintenance_record ORDER BY id')
for r in cur.fetchall():
    print(f"  ID={r[0]} machine={r[1]} type={r[2]} desc={str(r[3])[:50]} performed={r[4]} next={r[5]}")

print("\n=== Machine Parts ===")
cur.execute('SELECT id, machine_id, name, category, next_replacement, next_maintenance FROM machine_part ORDER BY id')
for r in cur.fetchall():
    print(f"  ID={r[0]} machine={r[1]} name={r[2]} cat={r[3]} next_repl={r[4]} next_maint={r[5]}")

print("\n=== Part Maintenance Log ===")
cur.execute('SELECT id, part_id, action, description, date FROM part_maintenance_log ORDER BY id')
for r in cur.fetchall():
    desc = str(r[3])[:50] if r[3] else "-"
    print(f"  ID={r[0]} part={r[1]} action={r[2]} desc={desc} date={r[4]}")

print("\n=== Machines ===")
cur.execute('SELECT id, name, machine_type FROM machine ORDER BY id')
for r in cur.fetchall():
    print(f"  ID={r[0]} name={r[1]} type={r[2]}")

conn.close()
