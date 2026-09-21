"""
Generate yearly maintenance calendar — REALISTIC schedules.
Run on PA: cd ~/crm-master && python generate_calendar.py

Schedule:
- Messen (blade replacement): every 2 weeks on TUESDAY
- Smering Vulbus 1 & 2: every WEEK on TUESDAY
- Other machines: monthly inspection + quarterly + yearly
"""
import sqlite3
import os
from datetime import datetime, timedelta

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
YEAR_START = datetime(2026, 10, 1)
YEAR_END = datetime(2027, 10, 1)

def next_weekday(start, weekday):
    """Find next occurrence of weekday (0=Mon, 1=Tue...) from start."""
    days_ahead = weekday - start.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return start + timedelta(days=days_ahead)

def generate_weekly(start, end, weekday):
    """Generate every-week dates on given weekday."""
    dates = []
    d = next_weekday(start, weekday)
    while d < end:
        dates.append(d)
        d += timedelta(weeks=1)
    return dates

def generate_biweekly(start, end, weekday):
    """Generate every-2-weeks dates on given weekday."""
    dates = []
    d = next_weekday(start, weekday)
    while d < end:
        dates.append(d)
        d += timedelta(weeks=2)
    return dates

def generate_monthly(start, end, day=1):
    """Generate monthly dates."""
    dates = []
    d = start.replace(day=min(day, 28))
    while d < end:
        dates.append(d)
        month = d.month + 1
        year = d.year
        if month > 12:
            month = 1
            year += 1
        d = d.replace(year=year, month=month, day=min(day, 28))
    return dates

def generate_interval(start, end, months, day=1):
    """Generate dates every N months."""
    dates = []
    d = start.replace(day=min(day, 28))
    while d < end:
        dates.append(d)
        month = d.month + months
        year = d.year
        while month > 12:
            month -= 12
            year += 1
        d = d.replace(year=year, month=month, day=min(day, 28))
    return dates

def generate_plans():
    plans = []
    TUESDAY = 1  # Tuesday = weekday 1

    # === 1. MESSEN VERVANGEN — every 2 weeks on TUESDAY ===
    # Machines with blades: Vulbus 1,2 + snijmachines + others with Messen
    messen_machines = [5, 6, 7, 12, 13, 15, 16, 17, 18, 23, 27, 28]
    for mid in messen_machines:
        for d in generate_biweekly(YEAR_START, YEAR_END, TUESDAY):
            plans.append({
                'machine_id': mid,
                'title': 'Messen vervangen (dinsdag)',
                'maintenance_type': 'preventive',
                'planned_start': d.strftime('%Y-%m-%d'),
                'status': 'planned',
                'recurrence': 'biweekly',
                'description': 'Vervang messen — elke 2 weken dinsdag',
            })

    # === 2. SMERING VULBUS 1 & 2 — every WEEK on TUESDAY ===
    vulbus_machines = [5, 6]
    for mid in vulbus_machines:
        for d in generate_weekly(YEAR_START, YEAR_END, TUESDAY):
            plans.append({
                'machine_id': mid,
                'title': 'Smering & inspectie (dinsdag)',
                'maintenance_type': 'preventive',
                'planned_start': d.strftime('%Y-%m-%d'),
                'status': 'planned',
                'recurrence': 'weekly',
                'description': 'Wekelijkse smering — elke dinsdag',
            })

    # === 3. ALL OTHER MACHINES — monthly inspection ===
    all_machine_ids = get_all_machine_ids()
    assigned_special = set(messen_machines)  # already scheduled above
    
    for mid in all_machine_ids:
        if mid in assigned_special:
            # Monthly dieptereiniging for meat machines
            for d in generate_interval(YEAR_START, YEAR_END, 3, day=15):
                plans.append({
                    'machine_id': mid,
                    'title': 'Dieptereiniging & inspectie',
                    'maintenance_type': 'corrective',
                    'planned_start': d.strftime('%Y-%m-%d'),
                    'status': 'planned',
                    'recurrence': 'none',
                    'description': 'Grondige reiniging — elke 3 maanden',
                })
        else:
            # Monthly inspection for all other machines
            for d in generate_monthly(YEAR_START, YEAR_END, day=1):
                plans.append({
                    'machine_id': mid,
                    'title': 'Maandelijkse inspectie',
                    'maintenance_type': 'preventive',
                    'planned_start': d.strftime('%Y-%m-%d'),
                    'status': 'planned',
                    'recurrence': 'monthly',
                    'description': 'Maandelijkse controle',
                })

    # === 4. YEARLY — all machines ===
    for mid in all_machine_ids:
        plans.append({
            'machine_id': mid,
            'title': 'Jaarlijks groot onderhoud',
            'maintenance_type': 'preventive',
            'planned_start': '2027-07-01',
            'status': 'planned',
            'recurrence': 'yearly',
            'description': 'Jaarlijks groot onderhoud',
        })

    return plans

def get_all_machine_ids():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute('SELECT id FROM machine ORDER BY id')
    ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids

def main():
    plans = generate_plans()
    all_ids = get_all_machine_ids()

    print(f"=== Generating maintenance calendar (REAL schedules) ===")
    print(f"Machines in DB: {len(all_ids)}")
    print(f"Total events: {len(plans)}")
    print()

    # Summary by type
    from collections import Counter
    by_title = Counter(p['title'] for p in plans)
    print("By type:")
    for title, cnt in by_title.most_common():
        print(f"  {title}: {cnt}")

    print()
    by_machine = Counter(p['machine_id'] for p in plans)
    print("By machine:")
    for mid in sorted(by_machine.keys()):
        print(f"  Machine {mid}: {by_machine[mid]} events")

    # Insert into DB
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("DELETE FROM maintenance_plan WHERE status = 'planned'")
    deleted = cur.rowcount
    print(f"\n=== DB: deleted {deleted} old planned entries ===")

    inserted = 0
    for p in plans:
        cur.execute(
            "INSERT INTO maintenance_plan (machine_id, title, maintenance_type, planned_start, status, recurrence, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (p['machine_id'], p['title'], p['maintenance_type'], p['planned_start'], p['status'], p['recurrence'], p['description'])
        )
        inserted += 1

    conn.commit()
    conn.close()

    print(f"Inserted {inserted} maintenance events")
    print(f"\nSchedule:")
    print(f"  Messen: every 2 weeks TUESDAY (12 machines)")
    print(f"  Smering Vulbus: every WEEK TUESDAY (2 machines)")
    print(f"  Monthly inspection: all other machines")
    print(f"  Yearly: all machines (July 2027)")
    print(f"\nOpen: https://rabasoff.pythonanywhere.com/maintenance-calendar")

if __name__ == '__main__':
    main()
