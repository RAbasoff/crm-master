# -*- coding: utf-8 -*-
"""PA/Linux scheduled task: evening DB backup at 23:59.
PythonAnywhere → Tasks → Schedule:
  /home/rabasoff/crm-master/daily_backup.py
  Time: 23:59  (или 22:59 UTC, если сервер в UTC)
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Minimal import of backup helper without full Flask app if possible
try:
    from app import backup_database, maybe_evening_backup
    maybe_evening_backup()
    print('daily_backup: maybe_evening_backup done', datetime.now().isoformat())
except Exception as e:
    # Fallback: plain file copy
    import shutil
    root = os.path.dirname(os.path.abspath(__file__))
    db = os.path.join(root, 'instance', 'werkplaats.db')
    bdir = os.path.join(root, 'backups')
    os.makedirs(bdir, exist_ok=True)
    if os.path.exists(db):
        dst = os.path.join(bdir, 'werkplaats_%s.db' % datetime.now().strftime('%Y%m%d'))
        shutil.copy2(db, dst)
        print('daily_backup fallback copy:', dst, 'err_was', e)
    else:
        print('daily_backup: no db', e)
