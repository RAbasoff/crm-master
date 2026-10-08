# -*- coding: utf-8 -*-
"""Обновление версии + запись описания в CHANGELOG.txt.

Запуск: python update_version.py "что добавлено/изменено"
"""
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VER_FILES = [
    ROOT / 'config.py',
    ROOT / 'СЕРВЕР' / 'server_setup.iss',
    ROOT / 'templates' / 'base.html',
    ROOT / 'templates' / 'login.html',
    ROOT / 'docs' / 'build_manual_admin_ru.py',
    ROOT / 'docs' / 'build_manuals_all.py',
    ROOT / 'docs' / 'build_manual_simple.py',
]
CHANGELOG = ROOT / 'СЕРВЕР' / 'CHANGELOG.txt'
VER_HINT = ROOT / 'СЕРВЕР' / 'VERSION.txt'


def read_version():
    text = (ROOT / 'config.py').read_text(encoding='utf-8')
    m = re.search(r"APP_VERSION\s*=\s*'([^']+)'", text)
    return m.group(1) if m else '2.12.0'


def bump_version(v):
    parts = v.split('.')
    while len(parts) < 3:
        parts.append('0')
    parts[2] = str(int(parts[2]) + 1)
    return '.'.join(parts)


def set_version(v):
    cfg = ROOT / 'config.py'
    t = cfg.read_text(encoding='utf-8')
    t = re.sub(r"APP_VERSION\s*=\s*'[^']+'", f"APP_VERSION = '{v}'", t)
    cfg.write_text(t, encoding='utf-8')
    for p in VER_FILES:
        if not p.exists() or p.name == 'config.py':
            continue
        t = p.read_text(encoding='utf-8')
        nt = re.sub(r'2\.12(\.\d+)?', v, t)
        nt = re.sub(r'#define MyAppVersion "[^"]+"', f'#define MyAppVersion "{v}"', nt)
        if nt != t:
            p.write_text(nt, encoding='utf-8')
    VER_HINT.write_text(v + '\n', encoding='utf-8')


def add_changelog(v, desc):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    entry = f'{v} | {now}\n{desc.strip()}\n\n'
    old = CHANGELOG.read_text(encoding='utf-8') if CHANGELOG.exists() else 'ProMaster — история обновлений\n\n'
    CHANGELOG.write_text(entry + old, encoding='utf-8')


def main():
    desc = sys.argv[1] if len(sys.argv) > 1 else ''
    if not desc:
        desc = input('Описание обновления: ').strip()
    if not desc:
        print('Нет описания — отмена')
        sys.exit(1)
    old = read_version()
    new = bump_version(old)
    set_version(new)
    add_changelog(new, desc)
    print('Версия', old, '->', new)
    print('Записано в', CHANGELOG)


if __name__ == '__main__':
    main()
