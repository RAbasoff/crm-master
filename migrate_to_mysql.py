"""Migrate SQLite database to MySQL on PythonAnywhere.
BEFORE running:
  1. Go to PA Databases tab → Create MySQL database: rabasoff$werkplaats
  2. Set your MySQL password below
  3. Run: cd ~/crm-master && python migrate_to_mysql.py
"""
import sqlite3, os, sys
from datetime import datetime

# ============================================================
# CONFIGURATION — SET YOUR MySQL PASSWORD HERE
# ============================================================
MYSQL_PASSWORD = 'YOUR_MYSQL_PASSWORD_HERE'  # <-- CHANGE THIS
MYSQL_DB = 'rabasoff$werkplaats'
MYSQL_HOST = 'rabasoff.mysql.pythonanywhere-services.com'
# ============================================================

DB_PATH = '/home/rabasoff/crm-master/instance/werkplaats.db'
DUMP_PATH = '/home/rabasoff/crm-master/instance/werkplaats_dump_mysql.sql'

def migrate():
    import pymysql
    
    print("=== SQLite → MySQL Migration ===")
    
    # 1. Dump SQLite to SQL
    print("\n1. Dumping SQLite database...")
    if not os.path.exists(DB_PATH):
        print(f"   ERROR: {DB_PATH} not found!")
        return False
    
    conn = sqlite3.connect(DB_PATH)
    with open(DUMP_PATH, 'w', encoding='utf-8') as f:
        for line in conn.iterdump():
            f.write(line + '\n')
    conn.close()
    print(f"   Dump: {os.path.getsize(DUMP_PATH)} bytes")
    
    # 2. Connect to MySQL
    print(f"\n2. Connecting to MySQL ({MYSQL_HOST})...")
    try:
        mysql_conn = pymysql.connect(
            host=MYSQL_HOST,
            user='rabasoff',
            password=MYSQL_PASSWORD,
            database=MYSQL_DB,
            charset='utf8mb4'
        )
        cursor = mysql_conn.cursor()
        print("   Connected!")
    except Exception as e:
        print(f"   ERROR: {e}")
        print("   Make sure you created the MySQL database on PA Databases tab")
        return False
    
    # 3. Read SQL dump and execute on MySQL
    print("\n3. Importing data to MySQL...")
    with open(DUMP_PATH, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    
    # Split by statements and execute one by one
    statements = []
    current = []
    for line in sql_content.split('\n'):
        if line.startswith('BEGIN TRANSACTION'):
            continue
        if line.startswith('COMMIT'):
            if current:
                statements.append('\n'.join(current))
                current = []
            continue
        if line.strip():
            current.append(line)
    if current:
        statements.append('\n'.join(current))
    
    success = 0
    errors = 0
    for i, stmt in enumerate(statements):
        try:
            # Convert SQLite syntax to MySQL
            stmt = stmt.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'INTEGER PRIMARY KEY AUTO_INCREMENT')
            stmt = stmt.replace('INTEGER PRIMARY KEY', 'INTEGER PRIMARY KEY')
            stmt = stmt.replace("DEFAULT CURRENT_TIMESTAMP", "DEFAULT CURRENT_TIMESTAMP")
            stmt = stmt.replace("'t'", "1").replace("'f'", "0")
            cursor.execute(stmt)
            success += 1
        except Exception as e:
            err_str = str(e)
            if 'already exists' in err_str or 'Duplicate' in err_str:
                success += 1  # skip duplicates
            else:
                errors += 1
                if errors <= 5:
                    print(f"   Warning: {err_str[:100]}")
    
    mysql_conn.commit()
    print(f"   Executed: {success} statements, {errors} warnings")
    
    # 4. Verify
    print("\n4. Verifying...")
    cursor.execute("SHOW TABLES")
    tables = cursor.fetchall()
    print(f"   Tables: {len(tables)}")
    for t in tables:
        cursor.execute(f"SELECT COUNT(*) FROM `{t[0]}`")
        count = cursor.fetchone()[0]
        if count > 0:
            print(f"   {t[0]}: {count} rows")
    
    mysql_conn.close()
    print(f"\n=== MIGRATION COMPLETE ===")
    print(f"Now update config.py with MySQL connection string")
    return True

if __name__ == '__main__':
    if MYSQL_PASSWORD == 'YOUR_MYSQL_PASSWORD_HERE':
        print("ERROR: Set MYSQL_PASSWORD in this script first!")
        print("Edit migrate_to_mysql.py and set your MySQL password from PA Databases tab")
        sys.exit(1)
    migrate()
