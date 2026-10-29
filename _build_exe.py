# -*- coding: utf-8 -*-
"""Set version 2.15.7 in server_setup.iss and compile Setup EXE."""
from pathlib import Path
import subprocess
import shutil
import re

ROOT = Path(r'D:\ИИ\CRM_Мастерская')
iss = ROOT / 'СЕРВЕР' / 'server_setup.iss'
t = iss.read_text(encoding='utf-8')
t = re.sub(r'#define MyAppVersion "[^"]+"', '#define MyAppVersion "2.15.7"', t)
iss.write_text(t, encoding='utf-8')
print('iss version ->', [l for l in t.splitlines() if 'MyAppVersion' in l][0])

# find ISCC
cands = [
    r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    r'C:\Program Files\Inno Setup 6\ISCC.exe',
    r'C:\Program Files (x86)\Inno Setup 5\ISCC.exe',
]
iscc = None
for c in cands:
    if Path(c).exists():
        iscc = c
        break
if not iscc:
    # search
    r = subprocess.run(['where', '/R', 'C:\\Program Files (x86)', 'ISCC.exe'], capture_output=True, text=True, timeout=30)
    print('search', r.stdout[:300], r.stderr[:200])
    for line in (r.stdout or '').splitlines():
        if line.strip().endswith('ISCC.exe'):
            iscc = line.strip()
            break

print('ISCC', iscc)
if iscc:
    r = subprocess.run([iscc, str(iss)], cwd=str(ROOT / 'СЕРВЕР'), capture_output=True, text=True, timeout=180)
    print('rc', r.returncode)
    print(r.stdout[-1500:] if r.stdout else '')
    print(r.stderr[-500:] if r.stderr else '')
else:
    print('ISCC not found')

# list output
for p in (ROOT / 'СЕРВЕР').glob('ProMaster_Server_Setup_2.15.6*'):
    print('OUT', p.name, p.stat().st_size)
print('DONE')
