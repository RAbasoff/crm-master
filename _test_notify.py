import sqlite3, os
from datetime import datetime

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'werkplaats.db')
conn = sqlite3.connect(db_path)
c = conn.cursor()

today = datetime.now().strftime('%d-%m-%Y')
now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

# Send test notifications to all active users
c.execute("SELECT id, display_name, username FROM user WHERE is_active_user = 1")
users = c.fetchall()
print(f"Active users: {len(users)}")

for u in users:
    c.execute("""
        INSERT INTO notification (user_id, title, message, type, link, is_read, created_at)
        VALUES (?, ?, ?, ?, ?, 0, ?)
    """, (u[0], 'ТЕСТ: ЗАМЕНА НОЖЕЙ', f'Запланирована замена ножей на {today}. Проверьте календарь ТО.', 'warning', '/maintenance-calendar', now))
    print(f"  -> Sent to {u[1] or u[2]} (#{u[0]})")

conn.commit()
conn.close()
print(f"\nDone! {len(users)} test notifications sent.")
