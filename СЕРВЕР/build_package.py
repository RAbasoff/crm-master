# -*- coding: utf-8 -*-
"""Сборка пакета для сервера компании в папку СЕРВЕР/CRM_Server.

Источник — текущий проект. В папку СЕРВЕР кладётся ТОЛЬКО рантайм
(без .git, тестов, архивов, локальных данных). Сам проект остаётся
работоспособным — файлы копируются, не перемещаются.
"""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'СЕРВЕР' / 'CRM_Server'

INCLUDE = [
    'app.py', 'models.py', 'schema.py', 'config.py', 'utils.py',
    'security.py', 'logs.py', 'pdf_utils.py', 'push.py', 'wsgi.py',
    'requirements.txt', 'runtime.txt',
    'blueprints', 'templates', 'translations', 'vendor_fpdf',
]
# static: без uploads (данные компании)
STATIC_EXCLUDE = {'uploads'}

EXCLUDE_DIRS = {'__pycache__', '.git', 'node_modules', 'instance', 'backups'}


def copy_tree(src: Path, dst: Path, exclude_dirs=(), static_skip=None):
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name in exclude_dirs or item.name.startswith('.'):
                continue
            if static_skip and item.name in static_skip:
                continue
            copy_tree(item, dst / item.name, exclude_dirs, static_skip)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    for name in INCLUDE:
        src = ROOT / name
        if not src.exists():
            print('skip (missing)', name)
            continue
        if name == 'static':
            copy_tree(src, OUT / 'static', EXCLUDE_DIRS, STATIC_EXCLUDE)
        else:
            copy_tree(src, OUT / name, EXCLUDE_DIRS)
        print('copied', name)

    # static отдельно (без uploads)
    copy_tree(ROOT / 'static', OUT / 'static', EXCLUDE_DIRS, STATIC_EXCLUDE)
    (OUT / 'static' / 'uploads').mkdir(parents=True, exist_ok=True)

    # шаблон .env для сервера
    env = OUT / '.env.example'
    env.write_text(
        'SECRET_KEY=CHANGE_ME_long_random_string\n'
        'VAPID_PUBLIC_KEY=\n'
        'VAPID_PRIVATE_KEY=\n'
        'APP_TIMEZONE=Europe/Brussels\n'
        'DATABASE_URL=\n', encoding='utf-8')

    # install helper
    (OUT / 'install_server.sh').write_text(
        '#!/usr/bin/env bash\n'
        '# Установка на Linux-сервер компании\n'
        'set -e\n'
        'DEST=${1:-/opt/crm}\n'
        'mkdir -p "$DEST" /var/lib/crm/static/uploads\n'
        'cp -r ./* "$DEST/"\n'
        '[ -f "$DEST/.env" ] || cp "$DEST/.env.example" "$DEST/.env"\n'
        'pip install -r "$DEST/requirements.txt"\n'
        'echo "Installed to $DEST. Edit .env, point nginx->gunicorn wsgi:app"\n',
        encoding='utf-8')

    print('---')
    print('PACKAGE', OUT)
    print('files', sum(1 for _ in OUT.rglob('*') if _.is_file()))


if __name__ == '__main__':
    main()
