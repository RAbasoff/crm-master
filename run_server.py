# -*- coding: utf-8 -*-
"""ProMaster — веб-сервер для сети компании.

Запуск (от Администратора):
    python run_server.py

Пользователи заходят в браузере:  http://IP_СЕРВЕРА:8000
"""
import os
import sys
import webbrowser
from threading import Timer

# Один раз вывести адреса в консоль
def _print_urls():
    import socket
    host = socket.gethostbyname(socket.gethostname())
    print('')
    print('=' * 50)
    print('ProMaster запущен как веб-сервер')
    print(f'  На этом компьютере:  http://127.0.0.1:8000')
    print(f'  Для сотрудников:     http://{host}:8000')
    print('  (адрес можно раздать в чат / на бумаге)')
    print('=' * 50)
    print('')

if __name__ == '__main__':
    os.environ.setdefault('FLASK_ENV', 'production')
    os.environ.setdefault('APP_TIMEZONE', 'Europe/Brussels')
    from app import app
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

    # Открыть браузер через 1 сек (необязательно)
    try:
        Timer(1.0, lambda: webbrowser.open('http://127.0.0.1:8000')).start()
    except Exception:
        pass

    _print_urls()
    # 0.0.0.0 — слушать всю сеть, чтобы пользователи могли зайти
    app.run(host='0.0.0.0', port=8000, debug=False)
