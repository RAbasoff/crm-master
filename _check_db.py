import sqlite3
conn = sqlite3.connect(r'D:\ИИ\CRM_Мастерская\instance\werkplaats.db')
c = conn.cursor()

c.execute("SELECT id, title, planned_start, recurrence_type, recurrence_end, status FROM maintenance_plan")
rows = c.fetchall()
print(f"Total plans: {len(rows)}")
for r in rows:
    print(f"  #{r[0]}: {r[1]} | start={r[2]} | rec_type={r[3]} | rec_end={r[4]} | status={r[5]}")

print()
c.execute("SELECT id, naam, position FROM client WHERE is_active=1 OR is_active IS NULL LIMIT 10")
rows = c.fetchall()
print(f"Active clients: {len(rows)}")
for r in rows:
    print(f"  #{r[0]}: {r[1]} ({r[2]})")

print()
c.execute("SELECT id, name, responsible_person_id, responsible_user_id FROM machine WHERE responsible_person_id IS NOT NULL LIMIT 10")
rows = c.fetchall()
print(f"Machines with responsible_person: {len(rows)}")
for r in rows:
    print(f"  Machine #{r[0]}: {r[1]} -> person={r[2]} user={r[3]}")

conn.close()
