import sqlite3

db = r'D:\ИИ\CRM_Мастерская\instance\werkplaats.db'
c = sqlite3.connect(db)
cur = c.cursor()

# Find users to delete
cur.execute("SELECT id, username, display_name, role FROM user WHERE username IN ('aristidis','maico','filip','ruslan')")
users = cur.fetchall()
print(f"Users to delete: {users}")

for uid, username, display_name, role in users:
    print(f"\nDeleting user #{uid}: {username} ({display_name})...")
    
    # Null out FK references
    updates = [
        ("worker", "user_id"), ("factory_section", "responsible_user_id"),
        ("machine", "responsible_user_id"), ("machine_part", "responsible_user_id"),
        ("maintenance_plan", "created_by"), ("maintenance_plan", "responsible_user_id"),
        ("part_maintenance_log", "performed_by"), ("machine_document", "uploaded_by"),
        ("maintenance_record", "performed_by"), ("invoice", "signed_by"),
        ("invoice", "created_by"), ("fault_report", "technician_id"),
        ("purchase_request", "reviewer_id"), ("technical_work_order", "created_by"),
        ("audit_log", "user_id"), ("time_entry", "approved_by"),
        ("vacation", "approved_by"), ("cylinder_log", "performed_by"),
        ("cylinder_order", "ordered_by"), ("weekend_shift", "created_by"),
    ]
    for table, col in updates:
        try:
            cur.execute(f"UPDATE [{table}] SET [{col}]=NULL WHERE [{col}]=?", (uid,))
            if cur.rowcount > 0:
                print(f"  {table}.{col}: {cur.rowcount} nullified")
        except:
            pass

    # Delete rows with NOT NULL FK
    deletes = [
        ("message", "sender_id"), ("message", "receiver_id"),
        ("notification", "user_id"), ("time_entry", "user_id"),
        ("vacation", "user_id"), ("work_schedule", "user_id"),
        ("work_report", "technician_id"), ("fault_report", "reporter_id"),
        ("purchase_request", "requester_id"),
    ]
    for table, col in deletes:
        try:
            cur.execute(f"DELETE FROM [{table}] WHERE [{col}]=?", (uid,))
            if cur.rowcount > 0:
                print(f"  {table}.{col}: {cur.rowcount} deleted")
        except:
            pass

    # Association tables
    for table, col in [("user_machine", "user_id"), ("fault_technicians", "technician_id"),
                        ("two_workers", "worker_id"), ("user_section_access", "user_id")]:
        try:
            cur.execute(f"DELETE FROM [{table}] WHERE [{col}]=?", (uid,))
            if cur.rowcount > 0:
                print(f"  {table}: {cur.rowcount} deleted")
        except:
            pass

    # Delete user
    cur.execute("DELETE FROM user WHERE id=?", (uid,))
    print(f"  User deleted: {cur.rowcount}")

c.commit()

# Verify
cur.execute("SELECT id, username, display_name, role, is_active_user FROM user")
print(f"\nRemaining users:")
for r in cur.fetchall():
    print(f"  #{r[0]}: {r[1]} ({r[2]}) role={r[3]} active={r[4]}")

c.close()
print("\nDONE")
