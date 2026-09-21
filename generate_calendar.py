"""
Generate yearly maintenance calendar for CRM_Mастерская.
Run on PA or locally: python generate_calendar.py

Creates MaintenancePlan entries for all machines for 12 months.
"""
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# --- CONFIGURATION ---
# Machine ID → maintenance schedule
SCHEDULE = {
    # machine_id: [(title, type, interval_months, start_date)]
    5: [
        ('Messen vervangen', 'preventive', 2, '2026-10-01'),      # Every 2 months
        ('Smering & inspectie', 'preventive', 3, '2026-11-01'),    # Every 3 months
        ('Jaarlijks groot onderhoud', 'preventive', 12, '2026-08-01'),  # Yearly
    ],
    6: [
        ('Messen vervangen', 'preventive', 2, '2026-10-01'),
        ('Smering & inspectie', 'preventive', 3, '2026-11-01'),
        ('Jaarlijks groot onderhoud', 'preventive', 12, '2026-08-01'),
    ],
    7: [
        ('Messen vervangen', 'preventive', 2, '2026-10-01'),
        ('Smering & inspectie', 'preventive', 3, '2026-11-01'),
        ('Jaarlijks groot onderhoud', 'preventive', 12, '2026-08-01'),
    ],
    1: [
        ('Algemeen onderhoud', 'preventive', 6, '2026-10-01'),     # Every 6 months
        ('Inspectie veer', 'preventive', 3, '2026-11-01'),          # Every 3 months
    ],
}

MONTHS_AHEAD = 12  # Generate for 12 months

def generate():
    """Generate SQL inserts for maintenance plans."""
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    
    plans = []
    today = datetime.now().date()
    end_date = today + relativedelta(months=MONTHS_AHEAD)
    
    for machine_id, items in SCHEDULE.items():
        for title, mtype, interval_months, start_str in items:
            start = datetime.strptime(start_str, '%Y-%m-%d').date()
            current = start
            
            while current <= end_date:
                if current >= today - timedelta(days=30):  # Include recent past
                    plans.append({
                        'machine_id': machine_id,
                        'title': title,
                        'maintenance_type': mtype,
                        'planned_start': current.isoformat(),
                        'status': 'planned',
                        'recurrence': 'none',
                        'description': f'{title} - machine {machine_id}',
                    })
                current += relativedelta(months=interval_months)
    
    return plans

def generate_sql(plans):
    """Generate SQL INSERT statements."""
    lines = []
    for p in plans:
        lines.append(
            f"INSERT INTO maintenance_plan (machine_id, title, maintenance_type, planned_start, status, recurrence, description) "
            f"VALUES ({p['machine_id']}, '{p['title']}', '{p['maintenance_type']}', '{p['planned_start']}', '{p['status']}', '{p['recurrence']}', '{p['description']}');"
        )
    return '\n'.join(lines)

if __name__ == '__main__':
    plans = generate()
    print(f"=== Generated {len(plans)} maintenance events for {MONTHS_AHEAD} months ===\n")
    
    # Print summary
    from collections import Counter
    by_machine = Counter(p['machine_id'] for p in plans)
    for mid, cnt in sorted(by_machine.items()):
        print(f"  Machine {mid}: {cnt} events")
    
    print(f"\n=== SQL (run on PA) ===\n")
    print("-- First, delete old planned entries (keep completed)")
    print("DELETE FROM maintenance_plan WHERE status = 'planned';\n")
    print(generate_sql(plans))
    
    # Also save as file
    with open('calendar_year.sql', 'w', encoding='utf-8') as f:
        f.write("-- Maintenance calendar for 12 months\n")
        f.write("-- Run on PA: sqlite3 instance/werkplaats.db < calendar_year.sql\n\n")
        f.write("DELETE FROM maintenance_plan WHERE status = 'planned';\n\n")
        f.write(generate_sql(plans))
    print(f"\nSaved to calendar_year.sql")
