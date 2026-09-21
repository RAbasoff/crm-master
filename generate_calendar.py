"""
Generate yearly maintenance calendar for ALL machines.
Run on PA: cd ~/crm-master && python generate_calendar.py
Or locally.
"""
import sqlite3
import os
from datetime import datetime, timedelta

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
YEAR_START = datetime(2026, 10, 1)
YEAR_END = datetime(2027, 10, 1)

# Group machines by type → assign maintenance intervals
# (machine_id, name, group) — auto-detected from DB
GROUPS = {
    'oven': {
        'ids': [8, 9, 10, 11],
        'plans': [
            ('Maandelijkse reiniging', 'preventive', 1),
            ('Kalibratie & inspectie', 'preventive', 3),
            ('Jaarlijks groot onderhoud', 'preventive', 12),
        ]
    },
    'labeling': {
        'ids': [1, 2, 3, 4],
        'plans': [
            ('Maandelijkse inspectie', 'preventive', 1),
            ('Smering & afstelling', 'preventive', 2),
            ('Jaarlijks groot onderhoud', 'preventive', 12),
        ]
    },
    'vlees_verwerking': {  # VEMAG, BOLDT, Worstmachine
        'ids': [5, 6, 12, 13, 15, 16, 17, 18],
        'plans': [
            ('Messen vervangen', 'preventive', 2),
            ('Smering & inspectie', 'preventive', 1),
            ('Dieptereiniging', 'corrective', 3),
            ('Jaarlijks groot onderhoud', 'preventive', 12),
        ]
    },
    'snijmachines': {  # TREIF, Slicer
        'ids': [23, 27, 28],
        'plans': [
            ('Messen vervangen', 'preventive', 2),
            ('Maandelijkse inspectie', 'preventive', 1),
            ('Kalibratie', 'preventive', 6),
            ('Jaarlijks groot onderhoud', 'preventive', 12),
        ]
    },
    'inpak': {  # Ypsilon, Magic, Penta Vac, etc
        'ids': [24, 26, 29, 36, 37, 38, 39, 40],
        'plans': [
            ('Maandelijkse inspectie', 'preventive', 1),
            ('Smering & afstelling', 'preventive', 2),
            ('Jaarlijks groot onderhoud', 'preventive', 12),
        ]
    },
    'transport': {  # Lifts, Trommels
        'ids': [7, 19, 20, 30],
        'plans': [
            ('Maandelijkse inspectie', 'preventive', 1),
            ('Smering & ketting inspectie', 'preventive', 3),
            ('Jaarlijks groot onderhoud', 'preventive', 12),
        ]
    },
    'detectie': {  # Metaaldetector, ISHIDA
        'ids': [21, 22, 31],
        'plans': [
            ('Maandelijkse test', 'preventive', 1),
            ('Kalibratie', 'preventive', 6),
            ('Jaarlijks certificering', 'preventive', 12),
        ]
    },
    'overig': {  # Gehaakt, Skin, ProSeal, Snelport, etc
        'ids': [14, 25, 32, 33, 34, 35, 41, 42],
        'plans': [
            ('Maandelijkse inspectie', 'preventive', 1),
            ('Jaarlijks onderhoud', 'preventive', 12),
        ]
    },
}

def get_all_machine_ids():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute('SELECT id FROM machine ORDER BY id')
    ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids

def generate_plans():
    plans = []
    assigned = set()
    
    for group_name, group in GROUPS.items():
        for mid in group['ids']:
            assigned.add(mid)
            for title, mtype, interval_months in group['plans']:
                # Start from different months to spread events
                offset = (mid % 3)  # stagger by machine ID
                start = YEAR_START + timedelta(days=offset * 10)
                
                current = start
                while current < YEAR_END:
                    plans.append({
                        'machine_id': mid,
                        'title': title,
                        'maintenance_type': mtype,
                        'planned_start': current.strftime('%Y-%m-%d'),
                        'status': 'planned',
                        'recurrence': 'none',
                        'description': f'{title}',
                    })
                    # Next occurrence
                    month = current.month + interval_months
                    year = current.year
                    while month > 12:
                        month -= 12
                        year += 1
                    day = min(current.day, 28)
                    current = current.replace(year=year, month=month, day=day)
    
    return plans

def main():
    all_ids = get_all_machine_ids()
    plans = generate_plans()
    
    # Find unassigned machines
    assigned = set()
    for g in GROUPS.values():
        assigned.update(g['ids'])
    unassigned = [i for i in all_ids if i not in assigned]
    
    print(f"=== Generating maintenance calendar ===")
    print(f"Machines in DB: {len(all_ids)}")
    print(f"Machines in groups: {len(assigned)}")
    if unassigned:
        print(f"Unassigned machines: {unassigned}")
    print(f"Total events: {len(plans)}")
    print()
    
    # Summary by machine
    from collections import Counter
    by_machine = Counter(p['machine_id'] for p in plans)
    for mid in sorted(by_machine.keys()):
        print(f"  Machine {mid}: {by_machine[mid]} events")
    
    # Generate SQL
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    
    # Get machine names for display
    cur.execute('SELECT id, name FROM machine')
    names = {r[0]: r[1] for r in cur.fetchall()}
    
    print(f"\n=== Inserting into database ===")
    
    # Delete old planned entries
    cur.execute("DELETE FROM maintenance_plan WHERE status = 'planned'")
    deleted = cur.rowcount
    print(f"Deleted {deleted} old planned entries")
    
    # Insert new plans
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
    print(f"\nDone! Calendar covers {YEAR_START.strftime('%b %Y')} - {YEAR_END.strftime('%b %Y')}")
    print(f"Open: https://rabasoff.pythonanywhere.com/maintenance-calendar")

if __name__ == '__main__':
    main()
