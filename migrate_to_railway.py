"""Migrate data from SQLite to Railway MySQL.
Run locally: python migrate_to_railway.py
Requires: pip install pymysql
"""
import sqlite3, os, sys, json
from datetime import datetime

SQLITE_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'werkplaats.db')
EXPORT_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'data_export.json')

def export_sqlite():
    """Export all data from SQLite to JSON."""
    print(f"Exporting from {SQLITE_PATH}...")
    if not os.path.exists(SQLITE_PATH):
        print("ERROR: SQLite database not found!")
        return False
    
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"Found {len(tables)} tables: {', '.join(tables)}")
    
    data = {}
    for table in tables:
        try:
            cursor.execute(f"SELECT * FROM [{table}]")
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            data[table] = {'columns': columns, 'rows': [list(row) for row in rows]}
            if rows:
                print(f"  {table}: {len(rows)} rows")
        except Exception as e:
            print(f"  {table}: ERROR - {e}")
    
    conn.close()
    
    with open(EXPORT_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, default=str, ensure_ascii=False)
    
    print(f"\nExported to {EXPORT_PATH} ({os.path.getsize(EXPORT_PATH)} bytes)")
    return True


def import_to_mysql(mysql_url):
    """Import JSON data to MySQL."""
    import pymysql
    
    print(f"\nImporting to MySQL...")
    
    # Parse URL: mysql+pymysql://user:pass@host:port/db
    from urllib.parse import urlparse
    parsed = urlparse(mysql_url.replace('mysql+pymysql://', 'mysql://'))
    
    conn = pymysql.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip('/'),
        charset='utf8mb4'
    )
    cursor = conn.cursor()
    
    with open(EXPORT_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    for table, info in data.items():
        columns = info['columns']
        rows = info['rows']
        if not rows:
            continue
        
        # Build INSERT statement
        placeholders = ', '.join(['%s'] * len(columns))
        col_names = ', '.join([f'`{c}`' for c in columns])
        sql = f"INSERT IGNORE INTO `{table}` ({col_names}) VALUES ({placeholders})"
        
        success = 0
        errors = 0
        for row in rows:
            try:
                # Convert empty strings to None for nullable fields
                cleaned = [None if v == '' else v for v in row]
                cursor.execute(sql, cleaned)
                success += 1
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  {table} warning: {str(e)[:80]}")
        
        conn.commit()
        if success > 0:
            print(f"  {table}: {success} rows imported, {errors} warnings")
    
    conn.close()
    print("\n=== IMPORT COMPLETE ===")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'export':
        export_sqlite()
    elif len(sys.argv) > 1 and sys.argv[1] == 'import':
        mysql_url = sys.argv[2] if len(sys.argv) > 2 else os.environ.get('DATABASE_URL', '')
        if not mysql_url:
            print("Usage: python migrate_to_railway.py import <mysql_url>")
            print("Or set DATABASE_URL environment variable")
            sys.exit(1)
        import_to_mysql(mysql_url)
    else:
        print("Usage:")
        print("  1. python migrate_to_railway.py export     # Export SQLite to JSON (run locally)")
        print("  2. python migrate_to_railway.py import <mysql_url>  # Import to Railway MySQL")
        print("")
        print("Or run on Railway with DATABASE_URL env var set automatically.")
