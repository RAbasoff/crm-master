# -*- coding: utf-8 -*-
"""ProMaster Admin Manual (RU) — installation, settings, operations."""
import os
from fpdf import FPDF

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, 'static', 'docs')
LOGO = os.path.join(BASE, 'logo', 'Logo.jpeg')
WATERMARK = os.path.join(BASE, 'static', 'docs', 'watermark.png')
FONT = os.path.join(BASE, 'static', 'fonts', 'DejaVuSans.ttf')
OUT = os.path.join(OUT_DIR, 'ProMaster_Rukovodstvo_Admin_RU.pdf')
W = 180


class AdminPDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        if os.path.exists(WATERMARK):
            self.image(WATERMARK, x=5, y=25, w=200)
        self.set_font('DejaVu', '', 8)
        self.set_text_color(150, 150, 150)
        self.set_xy(15, 8)
        self.cell(W, 5, 'ProMaster CRM — Руководство администратора', align='C')
        self.set_draw_color(200, 200, 200)
        self.line(15, 14, 195, 14)
        self.set_text_color(0, 0, 0)
        self.set_xy(15, 18)

    def footer(self):
        self.set_xy(15, -15)
        self.set_font('DejaVu', '', 8)
        self.set_text_color(130, 130, 130)
        self.cell(W, 8, f'Страница {self.page_no()}/{{nb}}', align='C')

    def _reset(self):
        self.set_x(15)

    def h1(self, text):
        self._reset(); self.ln(4)
        self.set_font('DejaVu', '', 18); self.set_text_color(26, 26, 46)
        self.multi_cell(W, 10, text)
        self.set_draw_color(91, 156, 246)
        self.line(15, self.get_y() + 1, 195, self.get_y() + 1)
        self.ln(5); self._reset()

    def h2(self, text):
        self._reset(); self.ln(2)
        self.set_font('DejaVu', '', 13); self.set_text_color(40, 70, 140)
        self.multi_cell(W, 8, text); self.ln(1); self._reset()

    def body(self, text):
        self._reset()
        self.set_font('DejaVu', '', 10); self.set_text_color(40, 40, 40)
        self.multi_cell(W, 6, text); self.ln(2); self._reset()

    def bullet(self, text):
        self._reset()
        self.set_font('DejaVu', '', 10); self.set_text_color(40, 40, 40)
        self.multi_cell(W, 6, u'\u2022  ' + text)

    def steps(self, items):
        self.set_font('DejaVu', '', 10); self.set_text_color(40, 40, 40)
        for i, t in enumerate(items, 1):
            self._reset()
            self.multi_cell(W, 6, f'{i}. {t}')
        self.ln(2); self._reset()

    def note(self, text):
        self._reset()
        self.set_fill_color(255, 248, 230)
        self.set_font('DejaVu', '', 9)
        self.set_text_color(120, 80, 20)
        self.multi_cell(W, 6, text, fill=True)
        self.ln(2)
        self.set_text_color(40, 40, 40)
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
                self.add_page(); self._reset()
                self.set_font('DejaVu', '', 9)
                self.set_fill_color(26, 26, 46)
                self.set_text_color(255, 255, 255)
                for i, h in enumerate(headers):
                    self.cell(col_w[i], 8, h, border=1, fill=True)
                self.ln(); self.set_text_color(30, 30, 30); fill = False
            self.set_fill_color(245, 247, 250) if fill else self.set_fill_color(255, 255, 255)
            self.set_font('DejaVu', '', 8)
            for i, cell in enumerate(row):
                self.cell(col_w[i], 7, str(cell)[:55], border=1, fill=True)
            self.ln(); fill = not fill
        self.ln(3); self._reset()


pdf = AdminPDF()
pdf.alias_nb_pages()
pdf.set_auto_page_break(auto=True, margin=20)
pdf.add_font('DejaVu', '', FONT)

# COVER
pdf.add_page()
if os.path.exists(LOGO):
    pdf.image(LOGO, x=75, y=28, w=60)
pdf.set_y(95)
pdf.set_font('DejaVu', '', 28)
pdf.set_text_color(26, 26, 46)
pdf.cell(W, 14, 'ProMaster CRM', align='C', new_x='LMARGIN', new_y='NEXT')
pdf.set_font('DejaVu', '', 16)
pdf.cell(W, 10, 'Руководство администратора', align='C', new_x='LMARGIN', new_y='NEXT')
pdf.set_font('DejaVu', '', 12)
pdf.set_text_color(90, 90, 90)
pdf.cell(W, 8, 'Установка · настройка · администрирование · сопровождение', align='C', new_x='LMARGIN', new_y='NEXT')
pdf.ln(12)
pdf.set_text_color(40, 40, 40)
pdf.set_font('DejaVu', '', 11)
pdf.cell(W, 8, 'Версия 2.12', align='C', new_x='LMARGIN', new_y='NEXT')
pdf.cell(W, 8, 'A.B.A.S.O.F.F. ProMaster', align='C', new_x='LMARGIN', new_y='NEXT')
pdf.ln(16)
pdf.set_font('DejaVu', '', 9)
pdf.set_text_color(120, 120, 120)
pdf.set_x(15)
pdf.multi_cell(W, 5,
    'Документ для администратора системы: развёртывание, пользователи и права, '
    'резервное копирование, Push-уведомления, PWA, типовые инциденты.', align='C')

# TOC
pdf.add_page()
pdf.h1('Содержание')
for t in [
    '1. Архитектура и состав системы',
    '2. Установка и развёртывание',
    '3. Первичная настройка после установки',
    '4. Пользователи, роли и права',
    '5. Графики, доступ 24/7, рабочие субботы',
    '6. Уведомления (Push) и PWA',
    '7. База данных и резервное копирование',
    '8. Безопасность',
    '9. Сопровождение и обновления',
    '10. Типовые инциденты и решения',
    '11. Контрольный чек-лист администратора',
]:
    pdf.bullet(t)

# 1
pdf.add_page()
pdf.h1('1. Архитектура и состав системы')
pdf.body('ProMaster — веб-приложение на Flask + SQLite (возможен MySQL). '
         'Клиенты — браузер или установленное PWA-приложение на телефоне.')
pdf.h2('Каталоги и файлы')
pdf.table(['Каталог / файл', 'Назначение'], [
    ['app.py, models.py, schema.py', 'Ядро приложения и миграции схемы'],
    ['blueprints/', 'Модули: заявки, TWO, склад, EGWL и др.'],
    ['templates/', 'Интерфейс (Jinja2)'],
    ['static/', 'CSS/JS, иконки, загрузки (uploads/)'],
    ['instance/werkplaats.db', 'База данных SQLite (ЖИВЫЕ ДАННЫЕ)'],
    ['instance/.vapid.json', 'Ключи Web Push (не в git)'],
    ['translations/', 'Переводы RU/NL/PL/EN'],
    ['backups/', 'Автоматические копии БД'],
], [55, 125])
pdf.h2('Модули интерфейса')
pdf.bullet('Производство: дашборд, план цеха, станки, EGWL, ТО, заявки, TWO')
pdf.bullet('Связь: чат, сообщения, уведомления')
pdf.bullet('Персонал: график, отпуска, учёт времени')
pdf.bullet('Бизнес: заказы, ответственные, работники, счета, подрядчики, склад')
pdf.bullet('Аналитика и Система — только для руководства/админа')
pdf.note('Механик не видит «Аналитику» и «Систему», не закрывает заявки, не видит цены склада.')

# 2
pdf.add_page()
pdf.h1('2. Установка и развёртывание')
pdf.h2('Варианты развёртывания')
pdf.table(['Способ', 'Когда использовать'], [
    ['PythonAnywhere (облако)', 'Пилот, 5–15 пользователей, быстрый старт'],
    ['Сервер компании (Linux)', 'Полный контроль, больше пользователей'],
    ['Windows-установщик (Inno)', 'Локальный ПК без сервера (1 рабочее место)'],
], [55, 125])
pdf.h2('PythonAnywhere')
pdf.steps([
    'Клонировать репозиторий: git pull в ~/crm-master',
    'Установить зависимости: pip install -r requirements.txt',
    'Настроить SECRET_KEY в переменных окружения',
    'Web → указать wsgi.py → Reload',
    'Проверить /login и создать администратора',
])
pdf.h2('Сервер компании (Linux)')
pdf.steps([
    'Python 3.11+, nginx → gunicorn → app:app',
    'Код в /opt/crm (права 750), данные в /var/lib/crm',
    'HTTPS обязателен для Push и PWA',
    'systemd-юнит для автозапуска',
    'Деплой: git pull / rsync, затем reload сервиса',
])
pdf.note('Не кладите .git и резервные копии БД в публичную веб-папку. '
         'Ключи SECRET_KEY и VAPID — только в env / instance/.')

# 3
pdf.add_page()
pdf.h1('3. Первичная настройка после установки')
pdf.steps([
    'Войти под admin (пароль из bootstrap / сменить немедленно).',
    'Создать пользователей: начальник ТС (director), механики (technician).',
    'Завести цеха (разделы), станки, оборудование.',
    'Настроить склад: группы, единицы, минимальные остатки.',
    'Проверить модули EGWL: электричество, газ, воздух, вода.',
    'Настроить график работы и рабочие субботы.',
    'Проверить Push: «График работ» → «Тест».',
    'Сделать первую резервную копию базы.',
])
pdf.h2('Обязательно сменить')
pdf.bullet('Пароль администратора')
pdf.bullet('Любые демо/тестовые пароли механиков')
pdf.bullet('SMTP-пароль почты (если используется)')

# 4
pdf.add_page()
pdf.h1('4. Пользователи, роли и права')
pdf.h2('Роли')
pdf.table(['Роль', 'Назначение', 'Может закрывать заявки'], [
    ['admin', 'Полный доступ, система, пользователи', 'Да'],
    ['director', 'Начальник ТС / гл. механик', 'Да'],
    ['technician', 'Механик', 'Нет'],
    ['user', 'Оператор / заявитель', 'Нет'],
], [35, 100, 45])
pdf.h2('Создание пользователя')
pdf.steps([
    'Система → Пользователи → «Новый».',
    'Логин, имя, роль (admin / director / technician / user).',
    'Разделы доступа (если нужны точечные права).',
    'При необходимости: «Доступ 24/7».',
    'Пароль: сгенерировать и передать сотруднику.',
])
pdf.h2('Права по разделам')
pdf.bullet('Групповые права (Responsible groups) — для логистики, качества и т.п.')
pdf.bullet('Индивидуальные UserSectionAccess — точечные исключения')
pdf.bullet('Декоратор section_access_required — серверная проверка')
pdf.h2('Смена пользователя (impersonation)')
pdf.bullet('Только admin. Кнопка «Сменить пользователя» слева от языков.')
pdf.bullet('Оранжевая плашка «Вы как: …» и кнопка «Назад к админу».')
pdf.bullet('Пока вы «под пользователем» — ограничение по часам не действует.')
pdf.note('Удаление пользователя НЕ удаляет его заявки — они переводятся на администратора '
         '(история сохраняется).')

# 5
pdf.add_page()
pdf.h1('5. Графики, доступ 24/7, рабочие субботы')
pdf.h2('График работ')
pdf.bullet('График работ → выбрать механика → создать график (дни, часы, перерыв).')
pdf.bullet('Активен только один график на пользователя.')
pdf.h2('Ограничение доступа по часам')
pdf.bullet('Для technician / Monteur действует блокировка вне графика.')
pdf.bullet('Исключение: флаг «24/7» в карточке пользователя.')
pdf.bullet('Auto-logout механика при бездействии 10 минут.')
pdf.h2('Рабочие субботы')
pdf.bullet('График → «Рабочие субботы»: выбрать механика и дату.')
pdf.bullet('Ставят только admin / director. Механик — только просмотр.')
pdf.bullet('В такую субботу механик может войти и работать (TWO, заявки).')

# 6
pdf.add_page()
pdf.h1('6. Уведомления (Push) и PWA')
pdf.h2('Что нужно для звука/вибрации на телефоне')
pdf.bullet('Приложение установлено с иконки (PWA).')
pdf.bullet('Пользователь разрешил уведомления.')
pdf.bullet('Ключи VAPID (instance/.vapid.json) на сервере.')
pdf.h2('Проверка доставки')
pdf.bullet('График работ → «Тест» (только admin) — выбрать механика или всех.')
pdf.h2('Установка на телефон')
pdf.bullet('Android: Chrome → «Установить приложение».')
pdf.bullet('iPhone: Safari → Поделиться → «На экран "Домой"». iOS 16.4+ для Push.')
pdf.note('Без HTTPS Push не работает. На PythonAnywhere HTTPS уже есть.')

# 7
pdf.add_page()
pdf.h1('7. База данных и резервное копирование')
pdf.h2('Где лежат данные')
pdf.bullet('SQLite: instance/werkplaats.db (или MySQL по DATABASE_URL)')
pdf.bullet('Загрузки: static/uploads/ (фото заявок, TWO и т.д.)')
pdf.h2('Резервная копия')
pdf.bullet('Настройки → «Backup» (только admin) — файл в backups/.')
pdf.bullet('Рекомендуется: копия БД + uploads ежедневно на отдельный диск/облако.')
pdf.h2('Восстановление')
pdf.bullet('Остановить сервис, заменить werkplaats.db копией, запустить.')
pdf.bullet('После восстановления — проверить /login и ключевые разделы.')
pdf.note('Обновление программы: git pull / rsync КОДА. Не затирать instance/ и uploads/.')

# 8
pdf.add_page()
pdf.h1('8. Безопасность')
pdf.bullet('SECRET_KEY — env / instance/.secret_key (не в git).')
pdf.bullet('Пароли хранятся хешем; password_plain не используется.')
pdf.bullet('HTTPS, SameSite cookies, CSRF-токены.')
pdf.bullet('Загрузка файлов — whitelist расширений (без .svg/.html).')
pdf.bullet('SSH только по ключу; на проде не держать .git с секретами.')
pdf.bullet('Журнал аудита действий пользователей.')
pdf.h2('Что проверять раз в месяц')
pdf.bullet('Неактивные учётки (снятые сотрудники)')
pdf.bullet('Свежие резервные копии')
pdf.bullet('Логи ошибок (server_err / journal)')

# 9
pdf.add_page()
pdf.h1('9. Сопровождение и обновления')
pdf.h2('Обновление на PythonAnywhere')
pdf.steps([
    'Сделать backup БД.',
    'ssh: cd ~/crm-master && git pull origin master',
    'pip install -r requirements.txt (если менялись зависимости)',
    'touch /var/www/<wsgi>.py — перезагрузка',
    'Проверить /login, заявки, TWO, склад',
])
pdf.h2('Версии')
pdf.bullet('APP_VERSION в config.py — отображается в интерфейсе.')
pdf.bullet('Миграции схемы выполняются автоматически при старте (schema.py).')

# 10
pdf.add_page()
pdf.h1('10. Типовые инциденты и решения')
pdf.table(['Симптом', 'Причина / решение'], [
    ['Механик не может войти вне часов', 'Нет флага 24/7 или нет графика'],
    ['Нет Push-уведомлений', 'Нет прав на уведомления / нет HTTPS / нет VAPID'],
    ['Фото не открываются', 'Нет клика (обновить) или файл не загружен'],
    ['Не закрывается заявка', 'CLOSED только admin / director'],
    ['Ошибка бэкапа', 'Права на папку backups / импорт backup_database'],
    ['Медленно открывается', 'Большая БД, фото в uploads; проверить диск'],
    ['Дубликаты номеров TWO', 'Сетевая гонка; обновить до версии с retry'],
], [60, 120])

# 11
pdf.add_page()
pdf.h1('11. Контрольный чек-лист администратора')
pdf.h2('При запуске')
pdf.bullet('□ Пароли admin и demo изменены')
pdf.bullet('□ Созданы director и механики')
pdf.bullet('□ Загружены станки и цеха')
pdf.bullet('□ Настроен склад и минимальные остатки')
pdf.bullet('□ Проверен Push-тест')
pdf.bullet('□ Включены резервные копии')
pdf.h2('Еженедельно')
pdf.bullet('□ Проверить заявки, требующие закрытия')
pdf.bullet('□ Проверить остатки склада (min)')
pdf.bullet('□ Просмотреть журнал аудита')
pdf.h2('Ежемесячно')
pdf.bullet('□ Архив / резервная копия на внешний носитель')
pdf.bullet('□ Учётки уволенных — деактивировать')
pdf.bullet('□ Обновить программу (git pull + reload)')

pdf.ln(8)
pdf.set_x(15)
pdf.set_font('DejaVu', '', 9)
pdf.set_text_color(120, 120, 120)
pdf.multi_cell(W, 5, u'— Конец руководства администратора —\nProMaster 2.12 · A.B.A.S.O.F.F.')

pdf.output(OUT)
print('WROTE', os.path.basename(OUT), os.path.getsize(OUT), 'pages', pdf.page_no())
