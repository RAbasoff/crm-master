"""
Generate yearly maintenance calendar — PROPERLY SPACED.
Each machine: weekly, monthly, quarterly, semi-annual, yearly.
Events spread across different days so workload is even.

Run on PA: cd ~/crm-master && python generate_calendar.py
"""
import sqlite3, os
from datetime import datetime, timedelta

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
YEAR_START = datetime(2026, 10, 1)
YEAR_END = datetime(2027, 10, 1)
TUESDAY = 1

def next_weekday(start, weekday):
    days_ahead = weekday - start.weekday()
    if days_ahead <= 0: days_ahead += 7
    return start + timedelta(days=days_ahead)

def get_all_machine_ids():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute('SELECT id FROM machine ORDER BY id')
    ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids

def generate_plans():
    plans = []
    all_ids = get_all_machine_ids()
    n = len(all_ids)

    # Machines that need blade replacement (meat processing + cutting)
    BLADE_MACHINES = {5, 6, 7, 12, 13, 15, 16, 17, 18, 23, 27, 28}
    # Vulbus weekly lubrication
    VULBUS = {5, 6}

    for idx, mid in enumerate(all_ids):
        # ── STAGGER: each machine gets a different day-of-month ──
        # Machine index 0→1st, index 1→2nd, ... index 30→1st again (mod 28)
        day_of_month = (idx % 28) + 1  # 1..28

        # ── WEEKLY: different weekday per machine (spread Mon-Fri) ──
        # idx 0→Monday, 1→Tuesday, ... 4→Friday, 5→Monday, ...
        weekly_weekday = idx % 5  # 0=Mon..4=Fri

        # ── 1. WEEKLY inspection ──
        d = next_weekday(YEAR_START, weekly_weekday)
        while d < YEAR_END:
            plans.append({
                'machine_id': mid,
                'title': f'Wekelijkse inspectie',
                'maintenance_type': 'preventive',
                'planned_start': d.strftime('%Y-%m-%d'),
                'status': 'planned',
                'recurrence': 'weekly',
                'description': f'Wekelijkse controle — {"Mo.Di.Wo.Do.Vr"[weekly_weekday*2:weekly_weekday*2+2]}',
            })
            d += timedelta(weeks=1)

        # ── 2. BLADE replacement every 2 weeks (Tue) — only for blade machines ──
        if mid in BLADE_MACHINES:
            d = next_weekday(YEAR_START, TUESDAY)
            while d < YEAR_END:
                plans.append({
                    'machine_id': mid,
                    'title': 'Messen vervangen (dinsdag)',
                    'maintenance_type': 'preventive',
                    'planned_start': d.strftime('%Y-%m-%d'),
                    'status': 'planned',
                    'recurrence': 'biweekly',
                    'description': 'Vervang messen — elke 2 weken dinsdag',
                })
                d += timedelta(weeks=2)

        # ── 2b. VULBUS extra weekly lubrication (Tue) ──
        if mid in VULBUS:
            d = next_weekday(YEAR_START, TUESDAY)
            while d < YEAR_END:
                plans.append({
                    'machine_id': mid,
                    'title': 'Smering Vulbus (dinsdag)',
                    'maintenance_type': 'preventive',
                    'planned_start': d.strftime('%Y-%m-%d'),
                    'status': 'planned',
                    'recurrence': 'weekly',
                    'description': 'Wekelijkse smering — dinsdag',
                })
                d += timedelta(weeks=1)

        # ── 3. MONTHLY maintenance ──
        d = YEAR_START.replace(day=day_of_month)
        while d < YEAR_END:
            plans.append({
                'machine_id': mid,
                'title': 'Maandelijks onderhoud',
                'maintenance_type': 'preventive',
                'planned_start': d.strftime('%Y-%m-%d'),
                'status': 'planned',
                'recurrence': 'monthly',
                'description': f'Maandelijks onderhoud — dag {day_of_month}',
            })
            month = d.month + 1; year = d.year
            if month > 12: month = 1; year += 1
            d = d.replace(year=year, month=month, day=day_of_month)

        # ── 4. QUARTERLY deep maintenance ──
        # Spread: group A (idx%3==0) → Jan,Apr,Jul,Oct; B→Feb,May,Aug,Nov; C→Mar,Jun,Sep,Dec
        q_start_month = (idx % 3) + 1  # 1, 2, or 3
        for qm in range(q_start_month, 13, 3):
            try:
                qd = datetime(2026, qm, day_of_month)
                if YEAR_START <= qd < YEAR_END:
                    plans.append({
                        'machine_id': mid,
                        'title': 'Kwartaal onderhoud',
                        'maintenance_type': 'preventive',
                        'planned_start': qd.strftime('%Y-%m-%d'),
                        'status': 'planned',
                        'recurrence': 'quarterly',
                        'description': 'Grondig kwartaal onderhoud',
                    })
            except ValueError:
                pass
        # Repeat for 2027
        for qm in range(q_start_month, 13, 3):
            try:
                qd = datetime(2027, qm, day_of_month)
                if YEAR_START <= qd < YEAR_END:
                    plans.append({
                        'machine_id': mid,
                        'title': 'Kwartaal onderhoud',
                        'maintenance_type': 'preventive',
                        'planned_start': qd.strftime('%Y-%m-%d'),
                        'status': 'planned',
                        'recurrence': 'quarterly',
                        'description': 'Grondig kwartaal onderhoud',
                    })
            except ValueError:
                pass

        # ── 5. SEMI-ANNUAL ──
        # Spread: group A (idx%2==0) → Apr+Oct; B → May+Nov
        sa_months = [(idx % 2) + 4, (idx % 2) + 10]  # e.g. [4,10] or [5,11]
        for sam in sa_months:
            for say in [2026, 2027]:
                try:
                    sad = datetime(say, sam, day_of_month)
                    if YEAR_START <= sad < YEAR_END:
                        plans.append({
                            'machine_id': mid,
                            'title': 'Halfjaarlijks onderhoud',
                            'maintenance_type': 'preventive',
                            'planned_start': sad.strftime('%Y-%m-%d'),
                            'status': 'planned',
                            'recurrence': 'semiannual',
                            'description': 'Halfjaarlijks groot onderhoud',
                        })
                except ValueError:
                    pass

        # ── 6. YEARLY ──
        # Spread across year: group by idx%6 → Jan..Jun
        y_month = (idx % 6) + 1
        try:
            yd = datetime(2027, y_month, day_of_month)
            if YEAR_START <= yd < YEAR_END:
                plans.append({
                    'machine_id': mid,
                    'title': 'Jaarlijks groot onderhoud',
                    'maintenance_type': 'preventive',
                    'planned_start': yd.strftime('%Y-%m-%d'),
                    'status': 'planned',
                    'recurrence': 'yearly',
                    'description': 'Complete jaarlijkse revisie',
                })
        except ValueError:
            pass

    return plans

def main():
    plans = generate_plans()
    all_ids = get_all_machine_ids()

    print(f"=== Maintenance Calendar Generator ===")
    print(f"Machines: {len(all_ids)}")
    print(f"Events: {len(plans)}")
    print()

    from collections import Counter
    by_type = Counter(p['title'] for p in plans)
    print("By type:")
    for t, c in by_type.most_common():
        print(f"  {t}: {c}")

    print()
    by_machine = Counter(p['machine_id'] for p in plans)
    print("Events per machine (sample):")
    for mid in sorted(by_machine.keys())[:10]:
        print(f"  Machine {mid}: {by_machine[mid]}")
    print(f"  ... ({len(by_machine)} machines total)")

    # Count events per month to verify even distribution
    print()
    by_month = Counter()
    for p in plans:
        by_month[p['planned_start'][:7]] += 1
    print("Events per month:")
    for m in sorted(by_month.keys()):
        bar = '█' * (by_month[m] // 10)
        print(f"  {m}: {by_month[m]:4d} {bar}")

    # DB
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("DELETE FROM maintenance_plan WHERE status = 'planned'")
    deleted = cur.rowcount

    for p in plans:
        cur.execute(
            "INSERT INTO maintenance_plan (machine_id, title, maintenance_type, planned_start, status, recurrence, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (p['machine_id'], p['title'], p['maintenance_type'], p['planned_start'], p['status'], p['recurrence'], p['description'])
        )

    conn.commit()
    conn.close()
    print(f"\nDB: deleted {deleted}, inserted {len(plans)}")
    print(f"Done! https://rabasoff.pythonanywhere.com/maintenance-calendar")

if __name__ == '__main__':
    main()
