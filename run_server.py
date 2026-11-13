# -*- coding: utf-8 -*-
"""ProMaster — веб-сервер для сети компании.

Запуск (от Администратора):
    python run_server.py

Пользователи заходят в браузере:  http://IP_СЕРВЕРА:8080

Если порт 8080 занят — укажите другой через переменную PORT:
    PowerShell:  $env:PORT=80; python run_server.py
    Порт 80: адрес без двоеточия  http://IP_СЕРВЕРА
"""
import os
import sys
import webbrowser
from threading import Timer

# Порт: настраивается через переменную окружения PORT (по умолчанию 8080)
PORT = int(os.environ.get('PORT', 8080))

# Один раз вывести адреса в консоль
def _print_urls():
    import socket
    host = socket.gethostbyname(socket.gethostname())
    suffix = '' if PORT == 80 else f':{PORT}'
    print('')
    print('=' * 50)
    print(f'ProMaster запущен как веб-сервер (порт {PORT})')
    print(f'  На этом компьютере:  http://127.0.0.1{suffix}')
    print(f'  Для сотрудников:     http://{host}{suffix}')
    print(f'  (адрес можно раздать в чат / на бумаге)')
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
        suffix = '' if PORT == 80 else f':{PORT}'
        Timer(1.0, lambda: webbrowser.open('http://127.0.0.1' + suffix)).start()
    except Exception:
        pass

    _print_urls()
    # 0.0.0.0 — слушать всю сеть, чтобы пользователи могли зайти
    app.run(host='0.0.0.0', port=PORT, debug=False)
