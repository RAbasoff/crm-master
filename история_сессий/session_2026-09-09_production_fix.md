# Сессия 2026-09-09: Исправление production-проблем CRM

## Проблема
Онлайн-версия (PythonAnywhere) не обновилась:
- Техники висят в списке «Ответственные»
- Lukash не удалён из persons
- Tim до сих пор в пользователях (нужно удалить)
- Maico/Aris/Filip были системными пользователями, а должны быть только персонами

## Архитектура (правильная)
### Системные пользователи (user table) — только 4:
1. admin (Руслан) — Администратор
2. director — Директор
3. technician — Technicien
4. user — User (generic)

### Персоны (client table) — Technische dienst:
- Maico, Aris, Filip и др. — только в client, без system accounts

## Изменения

### Fix 1: Bump marker v10→v11→v12 (utils.py:715)
Каждое изменение миграции — новый маркер, чтобы перезапустить на продакшене.

### Fix 2: Порядок удаления — сначала User, потом persons
FK-ссылки переписываются на admin перед удалением.

### Fix 3: Case-insensitive удаление (func.lower)

### Fix 4: Очистка User.person_id перед удалением person

### Fix 5: Удаление tech system users (maico, aris, filip)
- Из таблицы user удаляются maico, aris, filip
- FK-ссылки переписываются на admin
- Worker-привязки очищаются
- Персоны в client остаются (в Technische dienst)

### Fix 6: Создаются только director + technician system users
- Убрано создание maico/aris/filip как system users

### Fix 7: Добавлен Peter в names_to_remove
- Удалён с продакшена по просьбе пользователя

## Статус: v12 — готов к push
