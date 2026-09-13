# Сессия 2026-09-09: Исправление production-проблем CRM

## Проблема
- Техники висят в списке «Ответственные»
- Lukash не удалён, Tim в пользователях
- Maico/Aris/Filip были system users, а должны быть только персонами
- Maico/Filip отсутствовали в workers (Technische dienst)
- В заявках (faults) нельзя было выбрать оборудование — только машины

## Архитектура
### System users (user table) — только 3:
1. admin (Руслан) — Администратор
2. director — Директор
3. technician — Technicien

### Persons (client table):
- Directeur → Director group
- Technicus, Maico, Aris, Filip → (no group) = Technische dienst
- Bartek, Pablo, Javier, Hashem, Paulina → User group

### Workers (Monteur table):
- Aristidis, Maico, Filip, Ruslan — активные работники

## Изменения

### Миграции (utils.py)
1. Marker v10→v11→v12 — перезапуск миграции
2. Сначала User, потом persons (FK-порядок)
3. Case-insensitive удаление (func.lower)
4. Очистка User.person_id перед удалением person
5. Удаление maico/aris/filip из user table
6. Только director + technician как system users
7. Peter, Rusln в names_to_remove

### Заявки + оборудование (fault reports)
- `equipment_id` добавлен в FaultReport (nullable)
- `machine_id` стал nullable (был required)
- Миграция: `ALTER TABLE fault_report ADD COLUMN equipment_id`
- `target_name` property — единый доступ к имени машины/оборудования
- Dropdown: оптгруппы «🏭 Машины» и «⚙️ Оборудование»
- Штрихкод: M00001=машина, E00001=оборудование
- QR: `{type:'equipment', id:N}`
- Обновлены все шаблоны и routes

### Workers (fix_now.py)
- Добавлены Maico, Filip, Aris в workers table

## Статус: запушено (8074fcd)
