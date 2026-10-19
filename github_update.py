# -*- coding: utf-8 -*-
"""ProMaster — обновление кода с GitHub (для сервера компании).

Не трогает данные: instance/, static/uploads/, .env, backups/.
Запуск:
    python github_update.py            # проверка + обновление
    python github_update.py --check    # только проверить
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

# Папка с программой = родительская для этого файла (или CWD, если запущено из exe)
APP_ROOT = Path(os.environ.get('PROMASTER_ROOT') or Path(sys.argv[0]).resolve().parent)
if (APP_ROOT / 'app.py').exists() is False and (APP_ROOT.parent / 'app.py').exists():
    APP_ROOT = APP_ROOT.parent

GITHUB_REPO = os.environ.get('PROMASTER_GITHUB_REPO', 'RAbasoff/crm-master')
GITHUB_BRANCH = os.environ.get('PROMASTER_GITHUB_BRANCH', 'master')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN') or os.environ.get('PROMASTER_GITHUB_TOKEN') or ''

# Что обновляем (код). Данные НЕ входят.
UPDATE_INCLUDE = {
    'app.py', 'models.py', 'schema.py', 'config.py', 'utils.py',
    'security.py', 'logs.py', 'pdf_utils.py', 'push.py', 'wsgi.py',
    'run_server.py', 'license.py', 'github_update.py', 'requirements.txt',
    'runtime.txt',
}
UPDATE_DIRS = {
    'blueprints', 'templates', 'translations', 'vendor_fpdf', 'static',
}
# Внутри static не трогаем uploads
SKIP_NAMES = {
    '__pycache__', '.git', 'instance', 'backups', 'logs', 'node_modules',
    'dist', 'build', 'СЕРВЕР', 'installer', 'deploy', 'история_сессий',
    '.venv', 'venv',
}
STATIC_SKIP = {'uploads'}

LOG_DIR = APP_ROOT / 'logs'
LOG_FILE = LOG_DIR / 'github_update.log'


def log(msg: str) -> str:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass
    return line


def _headers() -> dict:
    h = {
        'User-Agent': 'ProMaster-Updater',
        'Accept': 'application/vnd.github+json',
    }
    if GITHUB_TOKEN:
        h['Authorization'] = f'Bearer {GITHUB_TOKEN}'
    return h


def get_local_version() -> str:
    try:
        cfg = (APP_ROOT / 'config.py').read_text(encoding='utf-8', errors='ignore')
        m = re.search(r"APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", cfg)
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        vf = APP_ROOT / 'СЕРВЕР' / 'VERSION.txt'
        if vf.exists():
            return vf.read_text(encoding='utf-8').strip()
    except Exception:
        pass
    return '0.0.0'


def _fetch_url(url: str, timeout: int = 30) -> bytes:
    req = Request(url, headers=_headers())
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def get_remote_info() -> dict:
    """Версия и коммит с GitHub. {'version', 'sha', 'date', 'message', 'url'}"""
    out = {
        'version': '',
        'sha': '',
        'date': '',
        'message': '',
        'url': f'https://github.com/{GITHUB_REPO}/tree/{GITHUB_BRANCH}',
    }
    api = f'https://api.github.com/repos/{GITHUB_REPO}/commits/{GITHUB_BRANCH}'
    try:
        data = json.loads(_fetch_url(api).decode('utf-8'))
        out['sha'] = (data.get('sha') or '')[:7]
        commit = data.get('commit') or {}
        out['date'] = (commit.get('committer') or {}).get('date', '')
        out['message'] = (commit.get('message') or '').split('\n')[0][:120]
        out['url'] = data.get('html_url') or out['url']
    except Exception as e:
        log(f'remote commit error: {e}')

    raw = f'https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/config.py'
    try:
        cfg = _fetch_url(raw).decode('utf-8', errors='ignore')
        m = re.search(r"APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", cfg)
        if m:
            out['version'] = m.group(1)
    except Exception as e:
        log(f'remote version error: {e}')
    return out


def _ver_tuple(v: str):
    parts = re.findall(r'\d+', v or '0')
    return tuple(int(x) for x in (parts + ['0', '0', '0'])[:3])


def is_newer(remote_v: str, local_v: str) -> bool:
    if not remote_v:
        return False
    return _ver_tuple(remote_v) > _ver_tuple(local_v)


def check_updates() -> dict:
    """Сравнить локальную версию с GitHub. Без изменения файлов."""
    local = get_local_version()
    remote = get_remote_info()
    update_available = is_newer(remote['version'], local) or bool(
        remote['sha'] and remote.get('message') is not None and False
    )
    # Даже если версия равна — можно показать, что на GitHub новее коммит
    # (решает пользователь). Флаг update_available = версия строго новее.
    update_available = is_newer(remote['version'], local)
    return {
        'ok': True,
        'local_version': local,
        'remote_version': remote['version'] or local,
        'sha': remote['sha'],
        'date': remote['date'],
        'message': remote['message'],
        'url': remote['url'],
        'update_available': update_available,
        'checked_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def _should_copy(rel: Path) -> bool:
    parts = set(rel.parts)
    if parts & SKIP_NAMES:
        return False
    if rel.name.startswith('.') and rel.name not in {'.env.example'}:
        return False
    if 'uploads' in rel.parts:
        return False
    return True


def _copy_code_from(src: Path, dst: Path) -> int:
    n = 0
    for path in src.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if not _should_copy(rel):
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        n += 1
    return n


def _download_zip(tmp: Path) -> Path | None:
    """Скачать архив ветки. Для приватного репо нужен GITHUB_TOKEN."""
    urls = [
        f'https://api.github.com/repos/{GITHUB_REPO}/zipball/{GITHUB_BRANCH}',
        f'https://codeload.github.com/{GITHUB_REPO}/zip/refs/heads/{GITHUB_BRANCH}',
    ]
    zip_path = tmp / 'update.zip'
    for url in urls:
        try:
            log(f'download {url}')
            data = _fetch_url(url, timeout=120)
            zip_path.write_bytes(data)
            return zip_path
        except Exception as e:
            log(f'download failed {url}: {e}')
    return None


def apply_update() -> dict:
    """Обновить код из GitHub. Данные не трогает."""
    result = {
        'ok': False, 'files': 0, 'method': '', 'local_version': get_local_version(),
        'message': '', 'restart_hint': True,
    }
    local = get_local_version()
    remote = get_remote_info()
    result['remote_version'] = remote['version'] or local
    result['sha'] = remote['sha']

    if not is_newer(remote['version'], local) and remote['version']:
        # Всё равно можно обновиться до последнего коммита, если просят force
        result['message'] = f'Уже последняя версия ({local}). Коммит GitHub: {remote["sha"]}'
        result['ok'] = True
        result['skipped'] = True
        return result

    # 1) git pull, если есть .git и git
    git_dir = APP_ROOT / '.git'
    if git_dir.exists() and shutil.which('git'):
        try:
            log('git pull origin ' + GITHUB_BRANCH)
            p = subprocess.run(
                ['git', 'pull', 'origin', GITHUB_BRANCH],
                cwd=str(APP_ROOT), capture_output=True, text=True, timeout=120,
            )
            log(p.stdout or '')
            if p.returncode == 0:
                result['ok'] = True
                result['method'] = 'git'
                result['message'] = 'Код обновлён через git pull'
                result['local_version'] = get_local_version()
                return result
            log(f'git pull failed: {p.stderr}')
        except Exception as e:
            log(f'git error: {e}')

    # 2) zip с GitHub
    with tempfile.TemporaryDirectory(prefix='promaster_upd_') as td:
        tmp = Path(td)
        zp = _download_zip(tmp)
        if not zp or not zp.exists():
            result['message'] = (
                'Не удалось скачать обновление. Проверьте интернет и GITHUB_TOKEN '
                '(для приватного репозитория).'
            )
            return result
        try:
            with zipfile.ZipFile(zp, 'r') as zf:
                zf.extractall(tmp / 'src')
        except Exception as e:
            result['message'] = f'Архив повреждён: {e}'
            return result
        # GitHub zipball: папка user-repo-sha/
        roots = [p for p in (tmp / 'src').iterdir() if p.is_dir()]
        if not roots:
            result['message'] = 'Пустой архив обновления'
            return result
        src_root = roots[0]
        n = _copy_code_from(src_root, APP_ROOT)
        result['ok'] = True
        result['method'] = 'zip'
        result['files'] = n
        result['message'] = f'Обновлено файлов: {n}'
        result['local_version'] = get_local_version()
        log(result['message'])
        return result


def restart_hint() -> str:
    return (
        'Перезапустите службу / python run_server.py, '
        'или на PythonAnywhere: touch wsgi.py'
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='ProMaster GitHub updater')
    ap.add_argument('--check', action='store_true', help='только проверить обновления')
    ap.add_argument('--json', action='store_true', help='вывод JSON')
    args = ap.parse_args(argv)

    if args.check:
        info = check_updates()
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(f"Локальная версия:  {info['local_version']}")
            print(f"Версия на GitHub:  {info['remote_version']}")
            print(f"Коммит:            {info['sha']}  {info['date']}")
            print(f"Изменения:         {info['message']}")
            if info['update_available']:
                print('ДОСТУПНО ОБНОВЛЕНИЕ. Запустите: python github_update.py')
            else:
                print('Обновлений нет.')
        return 0

    res = apply_update()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(res.get('message') or '')
        if res.get('ok') and not res.get('skipped'):
            print(restart_hint())
    return 0 if res.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
