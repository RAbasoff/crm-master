"""
Restore production database from git history.
Run this on PythonAnywhere: python fix_prod.py
"""
import subprocess, os, shutil

DB_PATH = 'instance/werkplaats.db'
BACKUP_PATH = 'instance/werkplaats_current.db'

# Backup current (broken) DB
if os.path.exists(DB_PATH):
    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"Backed up current DB to {BACKUP_PATH}")

# Extract old DB from git commit 55c6d76 (Add database for transfer)
result = subprocess.run(['git', 'show', '55c6d76:instance/werkplaats.db'], capture_output=True)
if result.returncode == 0:
    with open(DB_PATH, 'wb') as f:
        f.write(result.stdout)
    print(f"Restored DB from commit 55c6d76 ({len(result.stdout)} bytes)")
else:
    print(f"Error: {result.stderr.decode()}")
    exit(1)

# Verify
import sqlite3
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
for t in ['client', 'user', 'gas_cylinder', 'warehouse_item', 'fault_report', 'machine']:
    cur.execute(f'SELECT COUNT(*) FROM {t}')
    print(f"  {t}: {cur.fetchone()[0]} rows")
conn.close()

print("\nDone! Reload the web app on PythonAnywhere.")
print("The app will run migrations automatically to add new columns.")
