# СЕРВЕР — развёртывание ProMaster на сервере компании

## 1. Как разделено

| Где | Что |
|-----|-----|
| **GitHub** (приватный) | Ядро: код `app.py`, `models.py`, `blueprints/`, `templates/`, `static/`, `translations/` |
| **СЕРВЕР/CRM_Server** | Готовый пакет для установки на сервере компании (копия кода) |
| **Сервер компании** | Код + **живые данные**: `instance/werkplaats.db`, `static/uploads/`, `.env`, бэкапы |

Правило: **в git — код**, на сервере — **код + данные**. Данные никогда не коммитятся.

## 2. Сборка пакета (когда нужно выложить)

```bat
cd D:\ИИ\CRM_Мастерская\СЕРВЕР
python build_package.py
```

Результат: `СЕРВЕР/CRM_Server\` — всё для сервера, без тестов и личных данных.

## 3. Установка на сервере компании (один раз)

```bash
# Linux-сервер
scp -r CRM_Server user@server:/tmp/crm
ssh user@server
cd /tmp/crm
sudo bash install_server.sh /opt/crm
# далее: /opt/crm/.env — SECRET_KEY, VAPID
# nginx → gunicorn wsgi:app; HTTPS обязателен
```

Windows-ПК без сервера: установщик `installer\CRM_Masterworkshop_*.setup.exe` (Inno Setup).

## 4. Как обновлять модули

### А. Обычное обновление (код)
1. Правите код на **этом компьютере** (D:\ИИ\CRM_Мастерская)
2. Проверка: `python _check_program.py`
3. `git add … && git commit && git push`
4. **PA (облако):** `ssh … "cd ~/crm-master && git pull && touch …wsgi"`
5. **Сервер компании:**
   ```bash
   python build_package.py
   scp -r СЕРВЕР/CRM_Server/* user@server:/opt/crm/
   ssh user@server "sudo systemctl restart crm"
   ```
   **Не затирать** на сервере: `instance/`, `static/uploads/`, `.env`, `backups/`

### Б. Только данные (экспорт/импорт)
Данные живут на сервере. Резервная копия:
```
/opt/crm/instance/werkplaats.db
/opt/crm/static/uploads/
```
копировать ежедневно на отдельный диск/облако.

### В. Модуль (например «Газ»)
Правите `blueprints/gas.py`, `templates/gas/*` → как в п. А (git → сборка → копия на сервер → restart).

## 5. Чего не делать
- Не коммитить `instance/`, `uploads/`, `.env`, ключи
- Не класть `.git` в публичную папку на сервере
- Не обновлять код на сервере «руками» — только через пакет/git
- Не затирать данные при обновлении (только файлы кода)

## 6. Быстрая проверка после обновления
1. `/login` открывается
2. Заявка создаётся
3. Баллоны / склад открываются
4. Кнопка «Тест» оповещений (если включён Push)
