"""Repair corrupted SQLite database on PythonAnywhere.
Run: cd ~/crm-master && python repair_db.py
"""
import sqlite3, os, shutil
from datetime import datetime

DB_DIR = '/home/rabasoff/crm-master/instance'
DB_PATH = os.path.join(DB_DIR, 'werkplaats.db')
BACKUP_PATH = os.path.join(DB_DIR, f'werkplaats_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db')
DUMP_PATH = os.path.join(DB_DIR, 'werkplaats_dump.sql')
NEW_PATH = os.path.join(DB_DIR, 'werkplaats_new.db')

print(f"=== SQLite Database Repair ===")
print(f"Database: {DB_PATH}")

if not os.path.exists(DB_PATH):
    print("ERROR: Database file not found!")
    exit(1)

# 1. Backup current database
print(f"\n1. Backing up to: {BACKUP_PATH}")
shutil.copy2(DB_PATH, BACKUP_PATH)
print(f"   Backup created: {os.path.getsize(BACKUP_PATH)} bytes")

# 2. Dump the database to SQL
print(f"\n2. Dumping database to SQL...")
try:
    source = sqlite3.connect(DB_PATH)
    with open(DUMP_PATH, 'w', encoding='utf-8') as f:
        for line in source.iterdump():
            f.write(line + '\n')
    source.close()
    print(f"   Dump created: {os.path.getsize(DUMP_PATH)} bytes")
except Exception as e:
    print(f"   WARNING: Dump had errors: {e}")
    print(f"   Trying to continue with partial dump...")

# 3. Create new database from dump
print(f"\n3. Creating new database from dump...")
try:
    if os.path.exists(NEW_PATH):
        os.remove(NEW_PATH)
    new_db = sqlite3.connect(NEW_PATH)
    with open(DUMP_PATH, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    new_db.executescript(sql_content)
    new_db.close()
    print(f"   New database: {os.path.getsize(NEW_PATH)} bytes")
except Exception as e:
    print(f"   ERROR creating new database: {e}")
    print(f"   Restoring backup...")
    shutil.copy2(BACKUP_PATH, DB_PATH)
    exit(1)

# 4. Replace old database with new one
print(f"\n4. Replacing database...")
os.rename(DB_PATH, DB_PATH + '.corrupt')
os.rename(NEW_PATH, DB_PATH)
print(f"   Done! New database is at: {DB_PATH}")
print(f"   Corrupt database saved as: {DB_PATH}.corrupt")

# 5. Verify
print(f"\n5. Verifying...")
try:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("SELECT COUNT(*) FROM user")
    count = cursor.fetchone()[0]
    print(f"   Users table: {count} rows")
    cursor = conn.execute("PRAGMA integrity_check")
    result = cursor.fetchone()[0]
    print(f"   Integrity check: {result}")
    conn.close()
    print(f"\n=== REPAIR SUCCESSFUL ===")
except Exception as e:
    print(f"   Verification failed: {e}")
    print(f"\n=== REPAIR FAILED - restore backup manually ===")
    print(f"   cp {BACKUP_PATH} {DB_PATH}")
