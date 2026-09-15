"""
Restore script for CRM_Мастерская
Usage:
  python restore_backup.py              — list available backups
  python restore_backup.py <filename>   — restore from backup
  python restore_backup.py latest       — restore latest backup
"""
import os
import sys
import shutil
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
DB_PATH = os.path.join(BASE_DIR, 'instance', 'werkplaats.db')

def list_backups():
    """List all available backups"""
    if not os.path.exists(BACKUP_DIR):
        print("No backups folder found.")
        return []
    backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')], reverse=True)
    if not backups:
        print("No backups found.")
        return []
    print("Available backups:")
    for i, f in enumerate(backups):
        path = os.path.join(BACKUP_DIR, f)
        size = os.path.getsize(path)
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        print("  [%d] %s (%s KB, %s)" % (i, f, size // 1024, mtime.strftime('%d-%m-%Y %H:%M')))
    return backups

def restore(backup_file):
    """Restore database from backup"""
    if not os.path.exists(backup_file):
        print("ERROR: Backup file not found: %s" % backup_file)
        return False
    
    # Create safety copy of current DB
    if os.path.exists(DB_PATH):
        safety = DB_PATH + '.safety_%s' % datetime.now().strftime('%Y%m%d_%H%M%S')
        shutil.copy2(DB_PATH, safety)
        print("Safety copy: %s" % safety)
    
    # Restore
    shutil.copy2(backup_file, DB_PATH)
    size = os.path.getsize(DB_PATH)
    print("RESTORED: %s (%s KB)" % (DB_PATH, size // 1024))
    
    # Clean WAL/SHM
    for ext in ('-wal', '-shm'):
        wal = DB_PATH + ext
        if os.path.exists(wal):
            os.remove(wal)
            print("Cleaned: %s" % wal)
    
    return True

def main():
    if len(sys.argv) < 2:
        # List mode
        list_backups()
        print()
        print("Usage:")
        print("  python restore_backup.py latest       — restore newest backup")
        print("  python restore_backup.py <filename>   — restore specific backup")
        return
    
    target = sys.argv[1]
    
    if target == 'latest':
        backups = list_backups()
        if not backups:
            return
        backup_file = os.path.join(BACKUP_DIR, backups[0])
    else:
        backup_file = os.path.join(BACKUP_DIR, target) if not os.path.isabs(target) else target
    
    # Confirm
    print("About to restore: %s" % backup_file)
    print("Current DB: %s" % DB_PATH)
    answer = input("Type 'yes' to confirm: ")
    if answer.lower() != 'yes':
        print("Cancelled.")
        return
    
    restore(backup_file)

if __name__ == '__main__':
    main()
