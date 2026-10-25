# -*- coding: utf-8 -*-
"""Файловое логирование ProMaster — схема как в Рекламации:
logs/app.log (общий) + logs/error.log (ошибки).
Формат: YYYY-MM-DD HH:MM:SS [LEVEL] logger: message
"""
import logging
import os
from logging.handlers import RotatingFileHandler

_LOGGER_SET = False


def setup_file_logging(base_dir=None, max_bytes=2_000_000, backup_count=5):
    """Инициализация один раз. Пишет в <base>/logs/app.log и error.log."""
    global _LOGGER_SET
    if _LOGGER_SET:
        return logging.getLogger('app')
    base = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(base, 'logs')
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass

    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    app_h = RotatingFileHandler(os.path.join(log_dir, 'app.log'), maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
    app_h.setLevel(logging.INFO)
    app_h.setFormatter(fmt)
    root.addHandler(app_h)

    err_h = RotatingFileHandler(os.path.join(log_dir, 'error.log'), maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
    err_h.setLevel(logging.ERROR)
    err_h.setFormatter(fmt)
    root.addHandler(err_h)

    # в консоль (если есть)
    try:
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        root.addHandler(ch)
    except Exception:
        pass

    _LOGGER_SET = True
    log = logging.getLogger('app')
    log.info('Логирование инициализировано')
    return log


def log_info(msg, name='app'):
    logging.getLogger(name).info(msg)


def log_error(msg, name='app'):
    logging.getLogger(name).error(msg)


def log_warning(msg, name='app'):
    logging.getLogger(name).warning(msg)


def read_log_tail(filename='app.log', lines=200, base_dir=None):
    """Последние N строк лога."""
    base = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, 'logs', filename)
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            all_lines = f.readlines()
        return [ln.rstrip('\n') for ln in all_lines[-int(lines):]]
    except Exception:
        return []


def log_file_path(filename='app.log', base_dir=None):
    base = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'logs', filename)
