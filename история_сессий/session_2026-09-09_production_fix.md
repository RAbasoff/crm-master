# Сессия 2026-09-09: Исправление production-проблем CRM

## Проблема
Онлайн-версия (PythonAnywhere) не обновилась:
- Техники висят в списке «Ответственные»
- Lukash не удалён из persons
- Tim до сих пор в пользователях (нужно удалить)

## Диагностика
- Миграция `run_data_migrations()` в `utils.py` имела маркер `user_cleanup_v10`
- Маркер уже стоял на продакшене → миграция пропускалась, даже при обновлении кода
- Порядок операций был неправильный: удаление persons ДО очистки User.person_id → FK-ошибка на PostgreSQL
- Tim удалялся только lowercase — если User запись с заглавной 'Tim', не удалялась

## Изменения

### Fix 1: Bump migration marker (utils.py:715)
- Маркер: `user_cleanup_v10` → `user_cleanup_v11`
- Позволяет миграции запуститься заново на продакшене

### Fix 2: Порядок удаления — сначала User, потом persons (utils.py:746-788)
- Старый порядок: persons → users (FK-ссылка User.person_id ломалась)
- Новый порядок: users → persons (сначала удаляем User, потом person)
- Добавлено: очистка FK перед удалением User (reporter_id, technician_id и т.д.)

### Fix 3: Case-insensitive удаление User (utils.py:747-748)
- `func.lower(User.username)` — ищет 'tim', 'Tim', 'TIM' и т.д.
- Расширен список: ['tim', 'thijs', 'user', 'tech', 'Tim', 'Thijs']

### Fix 4: Очистка User.person_id перед удалением person (utils.py:782)
- Добавлено: `User.query.filter_by(person_id=p.id).update({'person_id': None})`
- Защита от FK-ошибки даже если User запись не была удалена на шаге 7a

### Fix 5: Импорт func (utils.py:563)
- `from sqlalchemy import text, func` — нужен для case-insensitive поиска

## Статус: код исправлен, готов к push
