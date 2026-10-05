# -*- coding: utf-8 -*-
"""ProMaster User Manual (RU) — PDF with logo cover + watermark."""
import os
from fpdf import FPDF

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, 'static', 'docs')
os.makedirs(OUT_DIR, exist_ok=True)
LOGO = os.path.join(BASE, 'logo', 'Logo.jpeg')
WATERMARK = os.path.join(BASE, 'static', 'docs', 'watermark.png')
FONT = os.path.join(BASE, 'static', 'fonts', 'DejaVuSans.ttf')
OUT = os.path.join(OUT_DIR, 'ProMaster_Rukovodstvo_RU.pdf')
W = 180  # usable width mm


def ensure_watermark():
    """Полупрозрачный полностраничный водяной знак из логотипа."""
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
        # Водяной знак на весь лист (A4 210x297), под текстом
        self.image(wm, x=5, y=25, w=200)
        self.set_font('DejaVu', '', 8)
        self.set_text_color(150, 150, 150)
        self.set_xy(15, 8)
        self.cell(W, 5, 'ProMaster CRM — Руководство пользователя', align='C')
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


pdf = ManualPDF()
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
pdf.cell(W, 10, 'Руководство пользователя', align='C', new_x='LMARGIN', new_y='NEXT')
pdf.set_font('DejaVu', '', 12)
pdf.set_text_color(90, 90, 90)
pdf.cell(W, 8, 'Система управления технической службой мастерской', align='C', new_x='LMARGIN', new_y='NEXT')
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
    'Документ описывает работу с системой ProMaster: заявки о неисправностях, '
    'технические наряды (TWO), склад, графики, учёт времени и настройки.\n'
    'Для механиков, начальника технической службы и администратора.', align='C')

# TOC
pdf.add_page()
pdf.h1('Содержание')
for t in [
    '1. О программе',
    '2. Роли и права доступа',
    '3. Вход в систему',
    '4. Рабочий стол (дашборд)',
    '5. Заявки о неисправностях',
    '6. Технические наряды (TWO)',
    '7. Учёт времени и график',
    '8. Склад и запчасти',
    '9. Станки и оборудование',
    '10. Инженерные сети',
    '11. Техническое обслуживание',
    '12. Сообщения и уведомления',
    '13. Настройки и администрирование',
    '14. Установка на телефон',
    '15. Типовые вопросы',
]:
    pdf.bullet(t)

# 1
pdf.add_page()
pdf.h1('1. О программе')
pdf.body('ProMaster — система управления технической службой предприятия. '
         'Она объединяет учёт неисправностей, ремонтные работы, склад запчастей, '
         'техническое обслуживание, инженерные сети и рабочее время персонала.')
pdf.h2('Назначение')
for t in [
    'Быстрая фиксация и обработка заявок о неисправностях',
    'Планирование и контроль ремонтных работ (TWO)',
    'Учёт запчастей и движения материалов на складе',
    'Контроль ТО станков и инженерных систем',
    'Учёт рабочего времени, графиков и субботних смен',
    'Уведомления механикам на телефон (Push, вибрация, звук)',
]:
    pdf.bullet(t)
pdf.h2('Ключевые возможности')
for t in [
    'Роли: администратор, начальник технической службы, механик',
    'История статусов по каждой заявке (дата, время, исполнитель)',
    'Работа с телефона как с приложением (PWA)',
    'Согласование нарядов, отчёты по часам команды',
    'Интерфейс: RU / NL / PL / EN',
]:
    pdf.bullet(t)

# 2
pdf.add_page()
pdf.h1('2. Роли и права доступа')
pdf.table(
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
    ],
    [70, 35, 35, 40],
)
pdf.body('Механик работает с заявками и ремонтами, но не закрывает их и не видит '
         'финансовые и системные разделы. Начальник ТС назначает, согласует и закрывает. '
         'Администратор управляет пользователями и системой.')

# 3
pdf.add_page()
pdf.h1('3. Вход в систему')
pdf.steps([
    'Откройте адрес программы в браузере или с иконки на телефоне.',
    'Введите логин и пароль, нажмите «Войти».',
    'При необходимости смените пароль при первом входе.',
])
pdf.h2('Рабочее время')
pdf.body('Для механиков доступ может быть ограничен графиком. Вне часов система '
         'показывает сообщение о доступе только в рабочее время. Исключение 24/7 '
         'настраивает администратор в карточке пользователя.')
pdf.h2('Язык')
pdf.body('Кнопки RU / NL / PL / EN в верхней панели переключают интерфейс.')

# 4
pdf.add_page()
pdf.h1('4. Рабочий стол (дашборд)')
pdf.h2('Механик')
for t in [
    'Быстрые кнопки: новая заявка, наряды TWO',
    'Панель «Учёт времени»: ПРИХОД / УХОД и текущее время',
    'Последние заявки и назначенные работы',
]:
    pdf.bullet(t)
pdf.h2('Начальство')
for t in [
    'Статистика по приоритетам и проблемным станкам',
    'Просроченные наряды, требующие внимания',
    'Графики и аналитика за период',
]:
    pdf.bullet(t)

# 5
pdf.add_page()
pdf.h1('5. Заявки о неисправностях')
pdf.h2('Создание заявки')
pdf.steps([
    'Заявки → «Новая заявка» или кнопка на дашборде.',
    'Заголовок и описание проблемы.',
    'Станок / оборудование и приоритет.',
    'Фото и видео при необходимости.',
    'Сохранить — механики получат уведомление.',
])
pdf.h2('Статусы')
pdf.table(['Статус', 'Значение'], [
    ['Новая', 'Заявка создана'],
    ['Принята', 'Механик принял'],
    ['Диагностика', 'Выясняется причина'],
    ['В работе', 'Идёт ремонт'],
    ['Пауза', 'Остановка + причина'],
    ['Ожидание запчасти', 'Нужна деталь'],
    ['Тестирование', 'Проверка после ремонта'],
    ['Устранена', 'Механик завершил'],
    ['Отказано', 'Отклонена с причиной'],
    ['Закрыта', 'Начальство подтвердило'],
], [55, 125])
pdf.h2('Таймер работ')
pdf.steps([
    'Кнопка «Начать работы» запускает отсчёт.',
    'Одновременно — только одна работа у механика.',
    '«Окончить работы» фиксирует время в истории.',
])
pdf.h2('Пауза и закрытие')
pdf.bullet('На паузе обязательно опишите причину.')
pdf.bullet('Механик ставит «Устранена», начальство — «Закрыть».')
pdf.bullet('Механик не может закрыть заявку сам.')

# 6
pdf.add_page()
pdf.h1('6. Технические наряды (TWO)')
pdf.steps([
    'TWO → «Новый наряд».',
    'Описание работы, станок или участок.',
    'Назначение механиков и плановая дата.',
    'Чек-лист работ, фото.',
])
pdf.h2('Согласование')
pdf.bullet('«На согласование» → проверка начальником ТС / админом.')
pdf.bullet('Согласование или отклонение с причиной.')
pdf.h2('Выполнение')
pdf.bullet('Чек-лист, результат, время, фото.')
pdf.bullet('Подпись завершает наряд. Можно распечатать бланк.')

# 7
pdf.add_page()
pdf.h1('7. Учёт времени и график')
pdf.bullet('ПРИХОД / УХОД — на дашборде и в «Учёте времени».')
pdf.bullet('Часы и сверхурочные считаются автоматически (норма 8 ч).')
pdf.bullet('График: дни недели и часы — назначает начальство.')
pdf.bullet('Рабочие субботы назначает начальник ТС / админ.')
pdf.bullet('Отчёты: по сотруднику и по команде (CSV).')

# 8
pdf.add_page()
pdf.h1('8. Склад и запчасти')
pdf.bullet('Просмотр: название, артикул, количество, место. У механика цены скрыты.')
pdf.bullet('Приход / расход кнопкой «+» с проверкой остатка.')
pdf.bullet('Резервы под заказ или станок.')
pdf.bullet('Списание запчастей в отчёте о работе по заявке.')
pdf.h2('Начальство')
pdf.bullet('Цены, инвентаризация, импорт CSV, удаление, закупки.')

# 9
pdf.add_page()
pdf.h1('9. Станки и оборудование')
pdf.bullet('Каталог станков: характеристики, фото, документы, история.')
pdf.bullet('QR-код на станке — быстрый доступ с телефона.')
pdf.bullet('Mule Maintenance, заточка ножей, прочие устройства.')

# 10
pdf.add_page()
pdf.h1('10. Инженерные сети')
pdf.bullet('Газ: баллоны, компоненты, заказы, сканирование.')
pdf.bullet('Сжатый воздух: точки, регуляторы, карта, фото (клик — полный размер).')
pdf.bullet('Вода: точки и схема, фото.')
pdf.bullet('Электричество: щиты, автоматы, розетки, планы.')

# 11
pdf.add_page()
pdf.h1('11. Техническое обслуживание')
pdf.bullet('Календарь ТО и планы с периодичностью.')
pdf.bullet('Из плана — создание TWO.')
pdf.bullet('Ремонты оборудования с фото и актами.')

# 12
pdf.add_page()
pdf.h1('12. Сообщения и уведомления')
pdf.bullet('Чат и внутренние сообщения.')
pdf.bullet('Уведомления о заявках, TWO, согласованиях, складе.')
pdf.bullet('На телефоне — вибрация и звук при новом событии.')
pdf.bullet('Тест оповещения — График работ (только админ).')

# 13
pdf.add_page()
pdf.h1('13. Настройки и администрирование')
pdf.bullet('Пользователи: учётки, роли, разделы, сброс пароля, 24/7.')
pdf.bullet('«Сменить пользователя» — вход от лица сотрудника (только админ).')
pdf.bullet('Права групп и индивидуальные доступы.')
pdf.bullet('Резервное копирование базы, журнал аудита, экспорт CSV.')

# 14
pdf.add_page()
pdf.h1('14. Установка на телефон')
pdf.h2('Android (Chrome)')
pdf.steps([
    'Откройте сайт.',
    'Меню ⋮ → «Установить приложение».',
    'Иконка ProMaster появится на рабочем столе.',
])
pdf.h2('iPhone (Safari)')
pdf.steps([
    'Откройте сайт в Safari.',
    '«Поделиться» → «На экран "Домой"».',
    '«Добавить».',
])
pdf.bullet('Разрешите уведомления — заявки будут приходить со звуком.')
pdf.bullet('Часть данных доступна офлайн; синхронизация при появлении сети.')

# 15
pdf.add_page()
pdf.h1('15. Типовые вопросы')
pdf.h2('Не приходит уведомление')
pdf.bullet('Проверьте установку приложения и разрешение на уведомления.')
pdf.bullet('Админ: График работ → «Тест».')
pdf.h2('Не пускает вне часов')
pdf.bullet('Ограничение по графику. Нужен доступ 24/7 от администратора.')
pdf.h2('Не могу закрыть заявку')
pdf.bullet('Закрытие — только начальник ТС / админ. Поставьте «Устранена».')
pdf.h2('Фото не открывается')
pdf.bullet('Кликните по миниатюре — откроется полный размер.')
pdf.h2('Рабочая суббота')
pdf.bullet('Назначает начальник ТС / админ: График → «Рабочие субботы».')
pdf.h2('Забыл пароль')
pdf.bullet('Администратор сбрасывает пароль в разделе «Пользователи».')

pdf.ln(8)
pdf.set_x(15)
pdf.set_font('DejaVu', '', 9)
pdf.set_text_color(120, 120, 120)
pdf.multi_cell(W, 5, u'— Конец руководства —\nProMaster 2.12 · A.B.A.S.O.F.F.')

pdf.output(OUT)
print('WROTE', OUT, os.path.getsize(OUT), 'bytes', 'pages', pdf.page_no())
