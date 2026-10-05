# -*- coding: utf-8 -*-
"""ProMaster User Manual — RU / NL / EN / PL PDFs (logo cover + watermark)."""
import os
from fpdf import FPDF

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, 'static', 'docs')
os.makedirs(OUT_DIR, exist_ok=True)
LOGO = os.path.join(BASE, 'logo', 'Logo.jpeg')
WATERMARK = os.path.join(BASE, 'static', 'docs', 'watermark.png')
FONT = os.path.join(BASE, 'static', 'fonts', 'DejaVuSans.ttf')
W = 180


def ensure_watermark():
    if os.path.exists(WATERMARK):
        return WATERMARK
    from PIL import Image
    im = Image.open(LOGO).convert('RGBA').resize((1200, 1200), Image.Resampling.LANCZOS)
    white = Image.new('RGBA', im.size, (255, 255, 255, 255))
    wm = Image.blend(white, im, 22 / 255.0)
    wm.convert('RGB').save(WATERMARK, optimize=True)
    return WATERMARK


class ManualPDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        wm = ensure_watermark()
        self.image(wm, x=5, y=25, w=200)
        self.set_font('DejaVu', '', 8)
        self.set_text_color(150, 150, 150)
        self.set_xy(15, 8)
        self.cell(W, 5, self.header_title, align='C')
        self.set_draw_color(200, 200, 200)
        self.line(15, 14, 195, 14)
        self.set_text_color(0, 0, 0)
        self.set_xy(15, 18)

    def footer(self):
        self.set_xy(15, -15)
        self.set_font('DejaVu', '', 8)
        self.set_text_color(130, 130, 130)
        self.cell(W, 8, self.footer_label.format(n=self.page_no()), align='C')

    def _reset(self):
        self.set_x(15)

    def h1(self, text):
        self._reset()
        self.ln(4)
        self.set_font('DejaVu', '', 18)
        self.set_text_color(26, 26, 46)
        self.multi_cell(W, 10, text)
        self.set_draw_color(91, 156, 246)
        self.line(15, self.get_y() + 1, 195, self.get_y() + 1)
        self.ln(5)
        self._reset()

    def h2(self, text):
        self._reset()
        self.ln(2)
        self.set_font('DejaVu', '', 13)
        self.set_text_color(40, 70, 140)
        self.multi_cell(W, 8, text)
        self.ln(1)
        self._reset()

    def body(self, text):
        self._reset()
        self.set_font('DejaVu', '', 10)
        self.set_text_color(40, 40, 40)
        self.multi_cell(W, 6, text)
        self.ln(2)
        self._reset()

    def bullet(self, text):
        self._reset()
        self.set_font('DejaVu', '', 10)
        self.set_text_color(40, 40, 40)
        self.multi_cell(W, 6, u'\u2022  ' + text)

    def steps(self, items):
        self.set_font('DejaVu', '', 10)
        self.set_text_color(40, 40, 40)
        for i, t in enumerate(items, 1):
            self._reset()
            self.multi_cell(W, 6, f'{i}. {t}')
        self.ln(2)
        self._reset()

    def table(self, headers, rows, col_w):
        self._reset()
        self.set_font('DejaVu', '', 9)
        self.set_fill_color(26, 26, 46)
        self.set_text_color(255, 255, 255)
        for i, h in enumerate(headers):
            self.cell(col_w[i], 8, h, border=1, fill=True)
        self.ln()
        self.set_text_color(30, 30, 30)
        fill = False
        for row in rows:
            if self.get_y() > 265:
                self.add_page()
                self._reset()
                self.set_font('DejaVu', '', 9)
                self.set_fill_color(26, 26, 46)
                self.set_text_color(255, 255, 255)
                for i, h in enumerate(headers):
                    self.cell(col_w[i], 8, h, border=1, fill=True)
                self.ln()
                self.set_text_color(30, 30, 30)
                fill = False
            if fill:
                self.set_fill_color(245, 247, 250)
            else:
                self.set_fill_color(255, 255, 255)
            self.set_font('DejaVu', '', 8)
            for i, cell in enumerate(row):
                self.cell(col_w[i], 7, str(cell)[:55], border=1, fill=True)
            self.ln()
            fill = not fill
        self.ln(3)
        self._reset()


# ═══════════════════ CONTENT ═══════════════════

CONTENT = {
'ru': {
 'file': 'ProMaster_Rukovodstvo_RU.pdf',
 'header_title': 'ProMaster CRM — Руководство пользователя',
 'footer_label': 'Страница {n}',
 'cover_title': 'ProMaster CRM',
 'cover_sub': 'Руководство пользователя',
 'cover_desc': 'Система управления технической службой мастерской',
 'cover_note': 'Документ описывает работу с системой ProMaster: заявки о неисправностях, технические наряды (TWO), склад, графики, учёт времени и настройки.\nДля механиков, начальника технической службы и администратора.',
 'toc': ['1. О программе', '2. Роли и права доступа', '3. Вход в систему', '4. Рабочий стол (дашборд)',
         '5. Заявки о неисправностях', '6. Технические наряды (TWO)', '7. Учёт времени и график',
         '8. Склад и запчасти', '9. Станки и оборудование', '10. Инженерные сети',
         '11. Техническое обслуживание', '12. Сообщения и уведомления', '13. Настройки и администрирование',
         '14. Установка на телефон', '15. Типовые вопросы'],
 'sections': [
  ('1. О программе', [
    ('body', 'ProMaster — система управления технической службой предприятия. Она объединяет учёт неисправностей, ремонтные работы, склад запчастей, техническое обслуживание, инженерные сети и рабочее время персонала.'),
    ('h2', 'Назначение'),
    ('bullet', 'Быстрая фиксация и обработка заявок о неисправностях'),
    ('bullet', 'Планирование и контроль ремонтных работ (TWO)'),
    ('bullet', 'Учёт запчастей и движения материалов на складе'),
    ('bullet', 'Контроль ТО станков и инженерных систем'),
    ('bullet', 'Учёт рабочего времени, графиков и субботних смен'),
    ('bullet', 'Уведомления механикам на телефон (Push, вибрация, звук)'),
    ('h2', 'Ключевые возможности'),
    ('bullet', 'Роли: администратор, начальник технической службы, механик'),
    ('bullet', 'История статусов по каждой заявке (дата, время, исполнитель)'),
    ('bullet', 'Работа с телефона как с приложением (PWA)'),
    ('bullet', 'Согласование нарядов, отчёты по часам команды'),
    ('bullet', 'Интерфейс: RU / NL / PL / EN'),
  ]),
  ('2. Роли и права доступа', [
    ('body', 'В системе три основные роли. Механик работает с заявками и ремонтами, начальство закрывает заявки и согласует наряды, администратор управляет системой.'),
    ('table', (
      ['Действие', 'Механик', 'Нач. ТС', 'Админ'],
      [
        ['Заявки: создание и работа', 'Да', 'Да', 'Да'],
        ['RESOLVED (устранена)', 'Да', 'Да', 'Да'],
        ['CLOSED (закрыть)', 'Нет', 'Да', 'Да'],
        ['TWO: работа и чек-лист', 'Да', 'Да', 'Да'],
        ['TWO: согласование', 'Нет', 'Да', 'Да'],
        ['Склад: наличие и движение', 'Да', 'Да', 'Да'],
        ['Склад: цены и удаление', 'Нет', 'Да', 'Да'],
        ['Закупки', 'Нет', 'Да', 'Да'],
        ['Аналитика', 'Нет', 'Да', 'Да'],
        ['Настройки и пользователи', 'Нет', 'Нет', 'Да'],
        ['Приход / уход', 'Да', 'Да', 'Да'],
      ], [70, 35, 35, 40])),
  ]),
  ('3. Вход в систему', [
    ('steps', ['Откройте адрес программы или иконку на телефоне.', 'Введите логин и пароль, нажмите «Войти».', 'При необходимости смените пароль при первом входе.']),
    ('h2', 'Рабочее время'),
    ('body', 'Для механиков доступ может быть ограничен графиком. Вне часов — сообщение о доступе только в рабочее время. Исключение 24/7 настраивает администратор.'),
    ('h2', 'Язык'),
    ('body', 'Кнопки RU / NL / PL / EN в верхней панели переключают интерфейс.'),
  ]),
  ('4. Рабочий стол (дашборд)', [
    ('h2', 'Механик'),
    ('bullet', 'Быстрые кнопки: новая заявка, наряды TWO'),
    ('bullet', 'Панель «Учёт времени»: ПРИХОД / УХОД и текущее время'),
    ('bullet', 'Последние заявки и назначенные работы'),
    ('h2', 'Начальство'),
    ('bullet', 'Статистика по приоритетам и проблемным станкам'),
    ('bullet', 'Просроченные наряды, требующие внимания'),
    ('bullet', 'Графики и аналитика за период'),
  ]),
  ('5. Заявки о неисправностях', [
    ('h2', 'Создание заявки'),
    ('steps', ['Заявки → «Новая заявка».', 'Заголовок и описание проблемы.', 'Станок / оборудование и приоритет.', 'Фото и видео при необходимости.', 'Сохранить — механики получат уведомление.']),
    ('h2', 'Статусы'),
    ('table', (['Статус', 'Значение'], [
      ['Новая', 'Заявка создана'], ['Принята', 'Механик принял'], ['Диагностика', 'Выясняется причина'],
      ['В работе', 'Идёт ремонт'], ['Пауза', 'Остановка + причина'], ['Ожидание запчасти', 'Нужна деталь'],
      ['Тестирование', 'Проверка после ремонта'], ['Устранена', 'Механик завершил'],
      ['Отказано', 'Отклонена с причиной'], ['Закрыта', 'Начальство подтвердило'],
    ], [55, 125])),
    ('h2', 'Таймер работ'),
    ('steps', ['«Начать работы» запускает отсчёт.', 'Одновременно — только одна работа.', '«Окончить работы» фиксирует время.']),
    ('h2', 'Пауза и закрытие'),
    ('bullet', 'На паузе обязательно опишите причину.'),
    ('bullet', 'Механик ставит «Устранена», начальство — «Закрыть».'),
    ('bullet', 'Механик не может закрыть заявку сам.'),
  ]),
  ('6. Технические наряды (TWO)', [
    ('steps', ['TWO → «Новый наряд».', 'Описание, станок или участок.', 'Назначение механиков и дата.', 'Чек-лист, фото.']),
    ('h2', 'Согласование'),
    ('bullet', '«На согласование» → проверка начальником / админом.'),
    ('bullet', 'Согласование или отклонение с причиной.'),
    ('h2', 'Выполнение'),
    ('bullet', 'Чек-лист, результат, время, фото.'),
    ('bullet', 'Подпись завершает наряд. Печать бланка.'),
  ]),
  ('7. Учёт времени и график', [
    ('bullet', 'ПРИХОД / УХОД — на дашборде и в «Учёте времени».'),
    ('bullet', 'Часы и сверхурочные считаются автоматически (норма 8 ч).'),
    ('bullet', 'График: дни и часы — назначает начальство.'),
    ('bullet', 'Рабочие субботы назначает начальник ТС / админ.'),
    ('bullet', 'Отчёты: по сотруднику и по команде (CSV).'),
  ]),
  ('8. Склад и запчасти', [
    ('bullet', 'Просмотр: название, артикул, количество. У механика цены скрыты.'),
    ('bullet', 'Приход / расход кнопкой «+» с проверкой остатка.'),
    ('bullet', 'Резервы под заказ или станок.'),
    ('bullet', 'Списание запчастей в отчёте о работе.'),
    ('h2', 'Начальство'),
    ('bullet', 'Цены, инвентаризация, импорт CSV, удаление, закупки.'),
  ]),
  ('9. Станки и оборудование', [
    ('bullet', 'Каталог станков: характеристики, фото, документы, история.'),
    ('bullet', 'QR-код на станке — быстрый доступ с телефона.'),
    ('bullet', 'Mule Maintenance, заточка ножей, прочие устройства.'),
  ]),
  ('10. Инженерные сети', [
    ('bullet', 'Газ: баллоны, компоненты, заказы, сканирование.'),
    ('bullet', 'Сжатый воздух: точки, регуляторы, карта, фото.'),
    ('bullet', 'Вода: точки и схема, фото.'),
    ('bullet', 'Электричество: щиты, автоматы, розетки, планы.'),
  ]),
  ('11. Техническое обслуживание', [
    ('bullet', 'Календарь ТО и планы с периодичностью.'),
    ('bullet', 'Из плана — создание TWO.'),
    ('bullet', 'Ремонты оборудования с фото и актами.'),
  ]),
  ('12. Сообщения и уведомления', [
    ('bullet', 'Чат и внутренние сообщения.'),
    ('bullet', 'Уведомления о заявках, TWO, согласованиях, складе.'),
    ('bullet', 'На телефоне — вибрация и звук.'),
    ('bullet', 'Тест оповещения — График работ (только админ).'),
  ]),
  ('13. Настройки и администрирование', [
    ('bullet', 'Пользователи: учётки, роли, разделы, сброс пароля, 24/7.'),
    ('bullet', '«Сменить пользователя» — вход от лица сотрудника (только админ).'),
    ('bullet', 'Права групп и индивидуальные доступы.'),
    ('bullet', 'Резервное копирование базы, журнал аудита, экспорт CSV.'),
  ]),
  ('14. Установка на телефон', [
    ('h2', 'Android (Chrome)'),
    ('steps', ['Откройте сайт.', 'Меню ⋮ → «Установить приложение».', 'Иконка ProMaster на рабочем столе.']),
    ('h2', 'iPhone (Safari)'),
    ('steps', ['Откройте сайт в Safari.', '«Поделиться» → «На экран "Домой"».', '«Добавить».']),
    ('bullet', 'Разрешите уведомления — заявки будут со звуком.'),
    ('bullet', 'Часть данных доступна офлайн.'),
  ]),
  ('15. Типовые вопросы', [
    ('h2', 'Не приходит уведомление'),
    ('bullet', 'Проверьте установку приложения и разрешения.'),
    ('h2', 'Не пускает вне часов'),
    ('bullet', 'Ограничение по графику. Нужен доступ 24/7 от администратора.'),
    ('h2', 'Не могу закрыть заявку'),
    ('bullet', 'Закрытие — только начальник ТС / админ. Поставьте «Устранена».'),
    ('h2', 'Фото не открывается'),
    ('bullet', 'Кликните по миниатюре — откроется полный размер.'),
    ('h2', 'Рабочая суббота'),
    ('bullet', 'Назначает начальник ТС / админ: График → «Рабочие субботы».'),
    ('h2', 'Забыл пароль'),
    ('bullet', 'Администратор сбрасывает пароль в «Пользователях».'),
  ]),
 ],
 'end': u'— Конец руководства —\nProMaster 2.12 · A.B.A.S.O.F.F.',
},

'en': {
 'file': 'ProMaster_User_Manual_EN.pdf',
 'header_title': 'ProMaster CRM — User Manual',
 'footer_label': 'Page {n}',
 'cover_title': 'ProMaster CRM',
 'cover_sub': 'User Manual',
 'cover_desc': 'Workshop technical service management system',
 'cover_note': 'This document describes ProMaster: fault reports, technical work orders (TWO), warehouse, schedules, time tracking and settings.\nFor mechanics, head of technical service and administrators.',
 'toc': ['1. About the program', '2. Roles and access rights', '3. Sign in', '4. Dashboard',
         '5. Fault reports', '6. Technical work orders (TWO)', '7. Time tracking and schedule',
         '8. Warehouse and spare parts', '9. Machines and equipment', '10. Utility systems',
         '11. Maintenance (PM)', '12. Messages and notifications', '13. Settings and administration',
         '14. Install on phone', '15. FAQ'],
 'sections': [
  ('1. About the program', [
    ('body', 'ProMaster is a management system for a company technical service. It covers fault reports, repairs, spare parts warehouse, maintenance, utility systems and staff working time.'),
    ('h2', 'Purpose'),
    ('bullet', 'Fast logging and handling of equipment faults'),
    ('bullet', 'Planning and control of repair work (TWO)'),
    ('bullet', 'Spare parts and material movement tracking'),
    ('bullet', 'Maintenance control for machines and utility systems'),
    ('bullet', 'Working time, schedules and Saturday shifts'),
    ('bullet', 'Push notifications to phones (vibration and sound)'),
    ('h2', 'Key features'),
    ('bullet', 'Roles: administrator, head of technical service, mechanic'),
    ('bullet', 'Status history per fault (date, time, user)'),
    ('bullet', 'Works on phone as an app (PWA)'),
    ('bullet', 'Work order approval, team hours reports'),
    ('bullet', 'Interface: RU / NL / PL / EN'),
  ]),
  ('2. Roles and access rights', [
    ('body', 'Three main roles. Mechanics work with faults and repairs; management closes faults and approves orders; the administrator runs the system.'),
    ('table', (['Action', 'Mechanic', 'Head TS', 'Admin'], [
      ['Faults: create and work', 'Yes', 'Yes', 'Yes'],
      ['RESOLVED', 'Yes', 'Yes', 'Yes'],
      ['CLOSED', 'No', 'Yes', 'Yes'],
      ['TWO: work and checklist', 'Yes', 'Yes', 'Yes'],
      ['TWO: approval', 'No', 'Yes', 'Yes'],
      ['Warehouse: stock and moves', 'Yes', 'Yes', 'Yes'],
      ['Warehouse: prices and delete', 'No', 'Yes', 'Yes'],
      ['Purchasing', 'No', 'Yes', 'Yes'],
      ['Analytics', 'No', 'Yes', 'Yes'],
      ['Settings and users', 'No', 'No', 'Yes'],
      ['Clock in / out', 'Yes', 'Yes', 'Yes'],
    ], [70, 35, 35, 40])),
  ]),
  ('3. Sign in', [
    ('steps', ['Open the app URL or home-screen icon.', 'Enter username and password, click Sign in.', 'Change password on first login if prompted.']),
    ('h2', 'Working hours'),
    ('body', 'Mechanics may be limited to their schedule. Outside hours the program shows a work-hours message. Admin can grant 24/7 access.'),
    ('h2', 'Language'),
    ('body', 'Use RU / NL / PL / EN buttons in the top bar to switch language.'),
  ]),
  ('4. Dashboard', [
    ('h2', 'Mechanic'),
    ('bullet', 'Quick actions: new fault, TWO orders'),
    ('bullet', 'Time panel: CLOCK IN / OUT and current time'),
    ('bullet', 'Recent faults and assigned jobs'),
    ('h2', 'Management'),
    ('bullet', 'Priority stats and problem machines'),
    ('bullet', 'Overdue work orders'),
    ('bullet', 'Schedules and period analytics'),
  ]),
  ('5. Fault reports', [
    ('h2', 'Create a fault'),
    ('steps', ['Faults → New fault.', 'Title and problem description.', 'Machine / equipment and priority.', 'Photos and videos if needed.', 'Save — mechanics get a notification.']),
    ('h2', 'Statuses'),
    ('table', (['Status', 'Meaning'], [
      ['New', 'Report created'], ['Accepted', 'Taken by mechanic'], ['Diagnosis', 'Finding the cause'],
      ['In progress', 'Repair running'], ['Paused', 'Stopped + reason'], ['Waiting for part', 'Need a part'],
      ['Testing', 'Check after repair'], ['Resolved', 'Mechanic finished'],
      ['Rejected', 'Rejected with reason'], ['Closed', 'Management confirmed'],
    ], [55, 125])),
    ('h2', 'Work timer'),
    ('steps', ['Start work starts the clock.', 'Only one active job at a time.', 'End work saves the duration.']),
    ('h2', 'Pause and close'),
    ('bullet', 'Pause requires a written reason.'),
    ('bullet', 'Mechanic sets Resolved; head sets Closed.'),
    ('bullet', 'Mechanics cannot close faults themselves.'),
  ]),
  ('6. Technical work orders (TWO)', [
    ('steps', ['TWO → New order.', 'Description, machine or area.', 'Assign mechanics and date.', 'Checklist, photos.']),
    ('h2', 'Approval'),
    ('bullet', 'Submit for approval → checked by head / admin.'),
    ('bullet', 'Approve or reject with a reason.'),
    ('h2', 'Execution'),
    ('bullet', 'Checklist, result, time, photos.'),
    ('bullet', 'Signature completes the order. Printable form.'),
  ]),
  ('7. Time tracking and schedule', [
    ('bullet', 'CLOCK IN / OUT on dashboard and Time tracking.'),
    ('bullet', 'Hours and overtime calculated (standard 8h).'),
    ('bullet', 'Schedule days and hours set by management.'),
    ('bullet', 'Working Saturdays assigned by head / admin.'),
    ('bullet', 'Reports: per employee and team (CSV).'),
  ]),
  ('8. Warehouse and spare parts', [
    ('bullet', 'View: name, part number, quantity. Prices hidden for mechanics.'),
    ('bullet', 'Incoming / outgoing with stock check.'),
    ('bullet', 'Reservations for orders or machines.'),
    ('bullet', 'Parts deducted in work reports.'),
    ('h2', 'Management'),
    ('bullet', 'Prices, inventory, CSV import, delete, purchasing.'),
  ]),
  ('9. Machines and equipment', [
    ('bullet', 'Catalogue: specs, photos, documents, history.'),
    ('bullet', 'QR code on machine for quick phone access.'),
    ('bullet', 'Mule Maintenance, knife sharpening, other devices.'),
  ]),
  ('10. Utility systems', [
    ('bullet', 'Gas: cylinders, components, orders, scanning.'),
    ('bullet', 'Compressed air: points, regulators, map, photos.'),
    ('bullet', 'Water: points and scheme, photos.'),
    ('bullet', 'Electricity: cabinets, breakers, outlets, plans.'),
  ]),
  ('11. Maintenance (PM)', [
    ('bullet', 'Maintenance calendar and plans with intervals.'),
    ('bullet', 'Create TWO from a plan.'),
    ('bullet', 'Equipment repairs with photos and reports.'),
  ]),
  ('12. Messages and notifications', [
    ('bullet', 'Chat and internal messages.'),
    ('bullet', 'Alerts for faults, TWO, approvals, warehouse.'),
    ('bullet', 'Phone vibration and sound.'),
    ('bullet', 'Test push — Schedule (admin only).'),
  ]),
  ('13. Settings and administration', [
    ('bullet', 'Users: accounts, roles, sections, password reset, 24/7.'),
    ('bullet', 'Switch user — view as employee (admin only).'),
    ('bullet', 'Group permissions and individual access.'),
    ('bullet', 'Database backup, audit log, CSV export.'),
  ]),
  ('14. Install on phone', [
    ('h2', 'Android (Chrome)'),
    ('steps', ['Open the site.', 'Menu → Install app.', 'ProMaster icon on the home screen.']),
    ('h2', 'iPhone (Safari)'),
    ('steps', ['Open the site in Safari.', 'Share → Add to Home Screen.', 'Add.']),
    ('bullet', 'Allow notifications for sound alerts.'),
    ('bullet', 'Some data works offline.'),
  ]),
  ('15. FAQ', [
    ('h2', 'No notification'),
    ('bullet', 'Check app install and notification permissions.'),
    ('h2', 'Blocked outside hours'),
    ('bullet', 'Schedule limit. Admin can grant 24/7.'),
    ('h2', 'Cannot close a fault'),
    ('bullet', 'Closing is for head TS / admin. Set Resolved instead.'),
    ('h2', 'Photo will not open'),
    ('bullet', 'Click the thumbnail to open full size.'),
    ('h2', 'Working Saturday'),
    ('bullet', 'Assigned by head / admin: Schedule → Working Saturdays.'),
    ('h2', 'Forgot password'),
    ('bullet', 'Admin resets the password under Users.'),
  ]),
 ],
 'end': u'— End of manual —\nProMaster 2.12 · A.B.A.S.O.F.F.',
},

'nl': {
 'file': 'ProMaster_Gebruikershandleiding_NL.pdf',
 'header_title': 'ProMaster CRM — Gebruikershandleiding',
 'footer_label': 'Pagina {n}',
 'cover_title': 'ProMaster CRM',
 'cover_sub': 'Gebruikershandleiding',
 'cover_desc': 'Systeem voor technische dienst van de werkplaats',
 'cover_note': 'Dit document beschrijft ProMaster: storingen, technische werkorders (TWO), magazijn, roosters, urenregistratie en instellingen.\nVoor monteurs, hoofd technische dienst en beheerders.',
 'toc': ['1. Over het programma', '2. Rollen en rechten', '3. Inloggen', '4. Dashboard',
         '5. Storingsmeldingen', '6. Technische werkorders (TWO)', '7. Uren en rooster',
         '8. Magazijn en onderdelen', '9. Machines en apparatuur', '10. Utilities',
         '11. Onderhoud (PM)', '12. Berichten en meldingen', '13. Instellingen en beheer',
         '14. Installeren op telefoon', '15. Veelgestelde vragen'],
 'sections': [
  ('1. Over het programma', [
    ('body', 'ProMaster is een beheersysteem voor de technische dienst. Het omvat storingsmeldingen, reparaties, magazijn, onderhoud, nutsvoorzieningen en werktijd.'),
    ('h2', 'Doel'),
    ('bullet', 'Snelle registratie en afhandeling van storingen'),
    ('bullet', 'Planning en controle van reparaties (TWO)'),
    ('bullet', 'Onderdelen en magazijnmutaties'),
    ('bullet', 'Onderhoudscontrole machines en utilities'),
    ('bullet', 'Werktijd, roosters en zaterdagdiensten'),
    ('bullet', 'Push-meldingen op telefoon (tril en geluid)'),
    ('h2', 'Belangrijkste functies'),
    ('bullet', 'Rollen: beheerder, hoofd technische dienst, monteur'),
    ('bullet', 'Statusgeschiedenis per storing (datum, tijd, gebruiker)'),
    ('bullet', 'Werkt op telefoon als app (PWA)'),
    ('bullet', 'Goedkeuring werkorders, teamurenrapporten'),
    ('bullet', 'Interface: RU / NL / PL / EN'),
  ]),
  ('2. Rollen en rechten', [
    ('body', 'Drie hoofdrollen. Monteurs werken aan storingen; leiding sluit storingen en keurt orders goed; beheerder beheert het systeem.'),
    ('table', (['Actie', 'Monteur', 'Hoofd TD', 'Admin'], [
      ['Storingen: maken en werken', 'Ja', 'Ja', 'Ja'],
      ['RESOLVED (opgelost)', 'Ja', 'Ja', 'Ja'],
      ['CLOSED (sluiten)', 'Nee', 'Ja', 'Ja'],
      ['TWO: werk en checklist', 'Ja', 'Ja', 'Ja'],
      ['TWO: goedkeuring', 'Nee', 'Ja', 'Ja'],
      ['Magazijn: voorraad', 'Ja', 'Ja', 'Ja'],
      ['Magazijn: prijzen/verwijderen', 'Nee', 'Ja', 'Ja'],
      ['Inkoop', 'Nee', 'Ja', 'Ja'],
      ['Analytics', 'Nee', 'Ja', 'Ja'],
      ['Instellingen en gebruikers', 'Nee', 'Nee', 'Ja'],
      ['Klok in / uit', 'Ja', 'Ja', 'Ja'],
    ], [70, 35, 35, 40])),
  ]),
  ('3. Inloggen', [
    ('steps', ['Open de URL of het icoon op de telefoon.', 'Gebruikersnaam en wachtwoord, klik Inloggen.', 'Wijzig het wachtwoord bij eerste login indien gevraagd.']),
    ('h2', 'Werktijden'),
    ('body', 'Monteurs kunnen beperkt zijn tot rooster. Buiten uren een melding. Beheerder kan 24/7 toegang geven.'),
    ('h2', 'Taal'),
    ('body', 'Knoppen RU / NL / PL / EN in de bovenbalk wisselen de taal.'),
  ]),
  ('4. Dashboard', [
    ('h2', 'Monteur'),
    ('bullet', 'Snelkoppelingen: nieuwe storing, TWO'),
    ('bullet', 'Tijdpaneel: KOMEN / GAAN en huidige tijd'),
    ('bullet', 'Recente storingen en toegewezen werk'),
    ('h2', 'Leiding'),
    ('bullet', 'Statistiek prioriteiten en probleemmachines'),
    ('bullet', 'Achterstallige werkorders'),
    ('bullet', 'Roosters en periodieke analytics'),
  ]),
  ('5. Storingsmeldingen', [
    ('h2', 'Storing aanmaken'),
    ('steps', ['Storingen → Nieuwe storing.', 'Titel en omschrijving.', 'Machine / apparatuur en prioriteit.', 'Foto’s en video’s.', 'Opslaan — monteurs krijgen melding.']),
    ('h2', 'Statussen'),
    ('table', (['Status', 'Betekenis'], [
      ['Nieuw', 'Melding aangemaakt'], ['Geaccepteerd', 'Door monteur overgenomen'], ['Diagnose', 'Oorzaak zoeken'],
      ['In behandeling', 'Reparatie loopt'], ['Gepauzeerd', 'Gestopt + reden'], ['Wachten op onderdeel', 'Onderdeel nodig'],
      ['Testen', 'Controle na reparatie'], ['Opgelost', 'Monteur klaar'],
      ['Geweigerd', 'Geweigerd met reden'], ['Gesloten', 'Leiding bevestigd'],
    ], [55, 125])),
    ('h2', 'Werktimer'),
    ('steps', ['Werk starten zet de klok aan.', 'Slechts één actieve taak tegelijk.', 'Werk beëindigen bewaart de duur.']),
    ('h2', 'Pauze en sluiten'),
    ('bullet', 'Pauze vereist een reden.'),
    ('bullet', 'Monteur zet Opgelost; leiding zet Gesloten.'),
    ('bullet', 'Monteurs kunnen storingen niet zelf sluiten.'),
  ]),
  ('6. Technische werkorders (TWO)', [
    ('steps', ['TWO → Nieuwe order.', 'Omschrijving, machine of zone.', 'Monteurs en datum.', 'Checklist, foto’s.']),
    ('h2', 'Goedkeuring'),
    ('bullet', 'Indienen ter goedkeuring → controle door leiding / admin.'),
    ('bullet', 'Goedkeuren of weigeren met reden.'),
    ('h2', 'Uitvoering'),
    ('bullet', 'Checklist, resultaat, tijd, foto’s.'),
    ('bullet', 'Handtekening rondt de order af. Afdrukbaar formulier.'),
  ]),
  ('7. Uren en rooster', [
    ('bullet', 'KOMEN / GAAN op dashboard en Urenregistratie.'),
    ('bullet', 'Uren en overuren automatisch (norm 8 uur).'),
    ('bullet', 'Roosterdagen en tijden door leiding.'),
    ('bullet', 'Werkzaterdagen door hoofd TD / admin.'),
    ('bullet', 'Rapporten: per medewerker en team (CSV).'),
  ]),
  ('8. Magazijn en onderdelen', [
    ('bullet', 'Overzicht: naam, artikelnr, aantal. Prijzen verborgen voor monteur.'),
    ('bullet', 'In / uit met voorraadcontrole.'),
    ('bullet', 'Reserveringen voor order of machine.'),
    ('bullet', 'Onderdelen afgeboekt in werkrapport.'),
    ('h2', 'Leiding'),
    ('bullet', 'Prijzen, inventarisatie, CSV-import, verwijderen, inkoop.'),
  ]),
  ('9. Machines en apparatuur', [
    ('bullet', 'Catalogus: specs, foto’s, documenten, historie.'),
    ('bullet', 'QR-code op machine voor snelle toegang.'),
    ('bullet', 'Mule Maintenance, messenslijpen, overige apparaten.'),
  ]),
  ('10. Utilities', [
    ('bullet', 'Gas: flessen, componenten, orders, scannen.'),
    ('bullet', 'Perslucht: punten, regelaars, kaart, foto’s.'),
    ('bullet', 'Water: punten en schema, foto’s.'),
    ('bullet', 'Elektra: kasten, zekeringen, wandcontactdozen, plattegronden.'),
  ]),
  ('11. Onderhoud (PM)', [
    ('bullet', 'Onderhoudskalender en plannen met intervallen.'),
    ('bullet', 'TWO maken vanuit een plan.'),
    ('bullet', 'Reparaties met foto’s en rapporten.'),
  ]),
  ('12. Berichten en meldingen', [
    ('bullet', 'Chat en interne berichten.'),
    ('bullet', 'Meldingen: storingen, TWO, goedkeuringen, magazijn.'),
    ('bullet', 'Tril en geluid op telefoon.'),
    ('bullet', 'Testmelding — Rooster (alleen admin).'),
  ]),
  ('13. Instellingen en beheer', [
    ('bullet', 'Gebruikers: accounts, rollen, secties, wachtwoord reset, 24/7.'),
    ('bullet', 'Gebruiker wisselen — bekijken als medewerker (admin).'),
    ('bullet', 'Groepsrechten en individuele toegang.'),
    ('bullet', 'Database-backup, auditlog, CSV-export.'),
  ]),
  ('14. Installeren op telefoon', [
    ('h2', 'Android (Chrome)'),
    ('steps', ['Open de site.', 'Menu → App installeren.', 'ProMaster-icoon op startscherm.']),
    ('h2', 'iPhone (Safari)'),
    ('steps', ['Open de site in Safari.', 'Delen → Zet op beginscherm.', 'Toevoegen.']),
    ('bullet', 'Sta meldingen toe voor geluid.'),
    ('bullet', 'Deel van de data werkt offline.'),
  ]),
  ('15. Veelgestelde vragen', [
    ('h2', 'Geen melding'),
    ('bullet', 'Controleer app-installatie en meldingsrechten.'),
    ('h2', 'Geblokkeerd buiten uren'),
    ('bullet', 'Roosterbeperking. Admin kan 24/7 geven.'),
    ('h2', 'Kan storing niet sluiten'),
    ('bullet', 'Sluiten is voor hoofd TD / admin. Zet Opgelost.'),
    ('h2', 'Foto opent niet'),
    ('bullet', 'Klik de thumbnail voor volledige grootte.'),
    ('h2', 'Werkzaterdag'),
    ('bullet', 'Door hoofd TD / admin: Rooster → Werkzaterdagen.'),
    ('h2', 'Wachtwoord vergeten'),
    ('bullet', 'Admin reset het wachtwoord bij Gebruikers.'),
  ]),
 ],
 'end': u'— Einde handleiding —\nProMaster 2.12 · A.B.A.S.O.F.F.',
},

'pl': {
 'file': 'ProMaster_Instrukcja_PL.pdf',
 'header_title': 'ProMaster CRM — Instrukcja użytkownika',
 'footer_label': 'Strona {n}',
 'cover_title': 'ProMaster CRM',
 'cover_sub': 'Instrukcja użytkownika',
 'cover_desc': 'System zarządzania służbą techniczną warsztatu',
 'cover_note': 'Dokument opisuje ProMaster: zgłoszenia usterek, zlecenia techniczne (TWO), magazyn, grafiki, ewidencję czasu i ustawienia.\nDla mechaników, kierownika służby technicznej i administratorów.',
 'toc': ['1. O programie', '2. Role i uprawnienia', '3. Logowanie', '4. Pulpit',
         '5. Zgłoszenia usterek', '6. Zlecenia techniczne (TWO)', '7. Czas pracy i grafik',
         '8. Magazyn i części', '9. Maszyny i urządzenia', '10. Instalacje',
         '11. Utrzymanie ruchu (PM)', '12. Wiadomości i powiadomienia', '13. Ustawienia i administracja',
         '14. Instalacja na telefonie', '15. Najczęstsze pytania'],
 'sections': [
  ('1. O programie', [
    ('body', 'ProMaster to system zarządzania służbą techniczną firmy. Obejmuje zgłoszenia usterek, naprawy, magazyn części, UR, instalacje oraz czas pracy personelu.'),
    ('h2', 'Przeznaczenie'),
    ('bullet', 'Szybkie zgłaszanie i obsługa usterek'),
    ('bullet', 'Planowanie i kontrola napraw (TWO)'),
    ('bullet', 'Ewidencja części i ruchów magazynowych'),
    ('bullet', 'Kontrola UR maszyn i instalacji'),
    ('bullet', 'Czas pracy, grafiki i soboty robocze'),
    ('bullet', 'Powiadomienia push na telefon (wibracja i dźwięk)'),
    ('h2', 'Kluczowe możliwości'),
    ('bullet', 'Role: administrator, kierownik służby technicznej, mechanik'),
    ('bullet', 'Historia statusów zgłoszenia (data, godzina, wykonawca)'),
    ('bullet', 'Praca na telefonie jak w aplikacji (PWA)'),
    ('bullet', 'Akceptacja zleceń, raporty godzin zespołu'),
    ('bullet', 'Interfejs: RU / NL / PL / EN'),
  ]),
  ('2. Role i uprawnienia', [
    ('body', 'Trzy główne role. Mechanik pracuje przy usterkach; kierownictwo zamyka zgłoszenia i akceptuje zlecenia; administrator zarządza systemem.'),
    ('table', (['Czynność', 'Mechanik', 'Kier. ST', 'Admin'], [
      ['Zgłoszenia: tworzenie i praca', 'Tak', 'Tak', 'Tak'],
      ['RESOLVED (usunięta)', 'Tak', 'Tak', 'Tak'],
      ['CLOSED (zamknięcie)', 'Nie', 'Tak', 'Tak'],
      ['TWO: praca i lista', 'Tak', 'Tak', 'Tak'],
      ['TWO: akceptacja', 'Nie', 'Tak', 'Tak'],
      ['Magazyn: stany i ruchy', 'Tak', 'Tak', 'Tak'],
      ['Magazyn: ceny i usuwanie', 'Nie', 'Tak', 'Tak'],
      ['Zakupy', 'Nie', 'Tak', 'Tak'],
      ['Analityka', 'Nie', 'Tak', 'Tak'],
      ['Ustawienia i użytkownicy', 'Nie', 'Nie', 'Tak'],
      ['Wejście / wyjście', 'Tak', 'Tak', 'Tak'],
    ], [70, 35, 35, 40])),
  ]),
  ('3. Logowanie', [
    ('steps', ['Otwórz adres programu lub ikonę na telefonie.', 'Wpisz login i hasło, kliknij Zaloguj.', 'Przy pierwszym logowaniu zmień hasło, jeśli system poprosi.']),
    ('h2', 'Godziny pracy'),
    ('body', 'Dostęp mechaników może być ograniczony grafikiem. Poza godzinami — komunikat o pracy w godzinach. Administrator może przyznać dostęp 24/7.'),
    ('h2', 'Język'),
    ('body', 'Przyciski RU / NL / PL / EN na górnym pasku zmieniają język.'),
  ]),
  ('4. Pulpit', [
    ('h2', 'Mechanik'),
    ('bullet', 'Szybkie akcje: nowe zgłoszenie, TWO'),
    ('bullet', 'Panel czasu: PRZYJŚCIE / WYJŚCIE i bieżąca godzina'),
    ('bullet', 'Ostatnie zgłoszenia i przydzielone prace'),
    ('h2', 'Kierownictwo'),
    ('bullet', 'Statystyki priorytetów i problematyczne maszyny'),
    ('bullet', 'Przeterminowane zlecenia'),
    ('bullet', 'Grafiki i analityka za okres'),
  ]),
  ('5. Zgłoszenia usterek', [
    ('h2', 'Tworzenie zgłoszenia'),
    ('steps', ['Zgłoszenia → Nowe.', 'Tytuł i opis problemu.', 'Maszyna / urządzenie i priorytet.', 'Zdjęcia i wideo jeśli trzeba.', 'Zapisz — mechanicy dostaną powiadomienie.']),
    ('h2', 'Statusy'),
    ('table', (['Status', 'Znaczenie'], [
      ['Nowe', 'Zgłoszenie utworzone'], ['Przyjęte', 'Przejęte przez mechanika'], ['Diagnoza', 'Ustalanie przyczyny'],
      ['W pracy', 'Trwa naprawa'], ['Pauza', 'Przerwa + powód'], ['Część', 'Potrzebna część'],
      ['Testy', 'Sprawdzenie po naprawie'], ['Usunięta', 'Mechanik zakończył'],
      ['Odrzucona', 'Odrzucona z powodem'], ['Zamknięta', 'Potwierdzone przez kierownictwo'],
    ], [55, 125])),
    ('h2', 'Licznik pracy'),
    ('steps', ['Rozpocznij pracę — start zegara.', 'Tylko jedna aktywna praca naraz.', 'Zakończ pracę — zapis czasu.']),
    ('h2', 'Pauza i zamknięcie'),
    ('bullet', 'Pauza wymaga opisanego powodu.'),
    ('bullet', 'Mechanik ustawia Usunięta; kierownictwo — Zamknięta.'),
    ('bullet', 'Mechanik nie zamyka zgłoszeń samodzielnie.'),
  ]),
  ('6. Zlecenia techniczne (TWO)', [
    ('steps', ['TWO → Nowe zlecenie.', 'Opis, maszyna lub obszar.', 'Przydział mechaników i data.', 'Lista kontrolna, zdjęcia.']),
    ('h2', 'Akceptacja'),
    ('bullet', 'Wyślij do akceptacji → sprawdza kierownik / admin.'),
    ('bullet', 'Akceptacja lub odrzucenie z powodem.'),
    ('h2', 'Wykonanie'),
    ('bullet', 'Lista kontrolna, wynik, czas, zdjęcia.'),
    ('bullet', 'Podpis zamyka zlecenie. Formularz do druku.'),
  ]),
  ('7. Czas pracy i grafik', [
    ('bullet', 'PRZYJŚCIE / WYJŚCIE na pulpicie i w Ewidencji czasu.'),
    ('bullet', 'Godziny i nadgodziny liczone automatycznie (norma 8 h).'),
    ('bullet', 'Dni i godziny grafiku ustala kierownictwo.'),
    ('bullet', 'Soboty robocze przydziela kierownik / admin.'),
    ('bullet', 'Raporty: pracownika i zespołu (CSV).'),
  ]),
  ('8. Magazyn i części', [
    ('bullet', 'Podgląd: nazwa, numer, ilość. Ceny ukryte dla mechanika.'),
    ('bullet', 'Przyjęcie / wydanie ze sprawdzeniem stanu.'),
    ('bullet', 'Rezerwacje pod zlecenie lub maszynę.'),
    ('bullet', 'Części rozliczane w raporcie pracy.'),
    ('h2', 'Kierownictwo'),
    ('bullet', 'Ceny, inwentaryzacja, import CSV, usuwanie, zakupy.'),
  ]),
  ('9. Maszyny i urządzenia', [
    ('bullet', 'Katalog: dane, zdjęcia, dokumenty, historia.'),
    ('bullet', 'Kod QR na maszynie — szybki dostęp z telefonu.'),
    ('bullet', 'Mule Maintenance, ostrzenie noży, pozostałe urządzenia.'),
  ]),
  ('10. Instalacje', [
    ('bullet', 'Gaz: butle, elementy, zamówienia, skanowanie.'),
    ('bullet', 'Sprężone powietrze: punkty, reduktory, mapa, zdjęcia.'),
    ('bullet', 'Woda: punkty i schemat, zdjęcia.'),
    ('bullet', 'Prąd: rozdzielnice, wyłączniki, gniazdka, plany.'),
  ]),
  ('11. Utrzymanie ruchu (PM)', [
    ('bullet', 'Kalendarz UR i plany z okresowością.'),
    ('bullet', 'Tworzenie TWO z planu.'),
    ('bullet', 'Naprawy urządzeń ze zdjęciami i protokołami.'),
  ]),
  ('12. Wiadomości i powiadomienia', [
    ('bullet', 'Czat i wiadomości wewnętrzne.'),
    ('bullet', 'Alerty: zgłoszenia, TWO, akceptacje, magazyn.'),
    ('bullet', 'Wibracja i dźwięk na telefonie.'),
    ('bullet', 'Test powiadomienia — Grafik (tylko admin).'),
  ]),
  ('13. Ustawienia i administracja', [
    ('bullet', 'Użytkownicy: konta, role, sekcje, reset hasła, 24/7.'),
    ('bullet', 'Przełącz użytkownika — podgląd jako pracownik (admin).'),
    ('bullet', 'Uprawnienia grup i indywidualne.'),
    ('bullet', 'Kopia zapasowa, dziennik audytu, eksport CSV.'),
  ]),
  ('14. Instalacja na telefonie', [
    ('h2', 'Android (Chrome)'),
    ('steps', ['Otwórz stronę.', 'Menu → Zainstaluj aplikację.', 'Ikona ProMaster na pulpicie.']),
    ('h2', 'iPhone (Safari)'),
    ('steps', ['Otwórz stronę w Safari.', 'Udostępnij → Do ekranu początkowego.', 'Dodaj.']),
    ('bullet', 'Zezwól na powiadomienia (dźwięk).'),
    ('bullet', 'Część danych działa offline.'),
  ]),
  ('15. Najczęstsze pytania', [
    ('h2', 'Brak powiadomienia'),
    ('bullet', 'Sprawdź instalację aplikacji i uprawnienia powiadomień.'),
    ('h2', 'Blokada poza godzinami'),
    ('bullet', 'Ograniczenie grafiku. Admin może dać 24/7.'),
    ('h2', 'Nie mogę zamknąć zgłoszenia'),
    ('bullet', 'Zamykanie — kierownik / admin. Ustaw Usunięta.'),
    ('h2', 'Zdjęcie się nie otwiera'),
    ('bullet', 'Kliknij miniaturę — otworzy się pełny rozmiar.'),
    ('h2', 'Sobota robocza'),
    ('bullet', 'Przydziela kierownik / admin: Grafik → Soboty robocze.'),
    ('h2', 'Zapomniałem hasła'),
    ('bullet', 'Administrator resetuje hasło w Użytkownikach.'),
  ]),
 ],
 'end': u'— Koniec instrukcji —\nProMaster 2.12 · A.B.A.S.O.F.F.',
},
}


def build(lang, data):
    pdf = ManualPDF()
    pdf.header_title = data['header_title']
    pdf.footer_label = data['footer_label']
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_font('DejaVu', '', FONT)

    # cover
    pdf.add_page()
    if os.path.exists(LOGO):
        pdf.image(LOGO, x=75, y=28, w=60)
    pdf.set_y(95)
    pdf.set_font('DejaVu', '', 28)
    pdf.set_text_color(26, 26, 46)
    pdf.cell(W, 14, data['cover_title'], align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('DejaVu', '', 16)
    pdf.cell(W, 10, data['cover_sub'], align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('DejaVu', '', 12)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(W, 8, data['cover_desc'], align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(12)
    pdf.set_text_color(40, 40, 40)
    pdf.set_font('DejaVu', '', 11)
    pdf.cell(W, 8, 'ProMaster 2.12', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(W, 8, 'A.B.A.S.O.F.F. ProMaster', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(16)
    pdf.set_font('DejaVu', '', 9)
    pdf.set_text_color(120, 120, 120)
    pdf.set_x(15)
    pdf.multi_cell(W, 5, data['cover_note'], align='C')

    # toc
    pdf.add_page()
    pdf.h1(data['toc'][0] if False else ('Содержание' if lang == 'ru' else
            'Contents' if lang == 'en' else 'Inhoud' if lang == 'nl' else 'Spis treści'))
    for t in data['toc']:
        pdf.bullet(t)

    for title, blocks in data['sections']:
        pdf.add_page()
        pdf.h1(title)
        for kind, *args in blocks:
            if kind == 'body':
                pdf.body(args[0])
            elif kind == 'h2':
                pdf.h2(args[0])
            elif kind == 'bullet':
                pdf.bullet(args[0])
            elif kind == 'steps':
                pdf.steps(args[0])
            elif kind == 'table':
                t = args[0]
                if len(t) == 2:
                    headers, rows = t
                    col_w = [70, 35, 35, 40] if len(headers) == 4 else [55, 125]
                else:
                    headers, rows, col_w = t
                pdf.table(headers, rows, col_w)

    pdf.ln(8)
    pdf.set_x(15)
    pdf.set_font('DejaVu', '', 9)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(W, 5, data['end'])

    out = os.path.join(OUT_DIR, data['file'])
    pdf.output(out)
    print('WROTE', data['file'], os.path.getsize(out), 'pages', pdf.page_no())


for lang in ('ru', 'en', 'nl', 'pl'):
    build(lang, CONTENT[lang])
