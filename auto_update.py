#!/usr/bin/env python3
"""Auto-update script for PythonAnywhere scheduled task.
Pulls latest code and touches WSGI file to trigger reload.
Run this daily via PythonAnywhere Tasks tab.
"""
import subprocess
import os
from datetime import datetime

PROJECT_DIR = os.path.expanduser('~/crm-master')
LOG_FILE = os.path.expanduser('~/crm-master/logs/auto_update.log')

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

try:
    # Pull latest code
    result = subprocess.run(
        ['git', 'pull', 'origin', 'master'],
        cwd=PROJECT_DIR,
        capture_output=True, text=True, timeout=60
    )
    log(f"git pull: {result.stdout.strip()}")
    if result.returncode != 0:
        log(f"ERROR: {result.stderr.strip()}")
    else:
        if 'Already up to date' not in result.stdout:
            # Code changed — touch WSGI to reload
            wsgi_files = [
                '/var/www/rabasoff_pythonanywhere_com_wsgi.py',
                os.path.expanduser('~/crm-master/wsgi.py'),
            ]
            for wsgi in wsgi_files:
                if os.path.exists(wsgi):
                    os.utime(wsgi)
                    log(f"Touched {wsgi} to trigger reload")
                    break
            log("Auto-update complete — app reloaded")
        else:
            log("Already up to date, no reload needed")
except Exception as e:
    log(f"EXCEPTION: {e}")
