# -*- coding: utf-8 -*-
"""Инструкция для новичка: сервер, GitHub, ключи — PDF в СЕРВЕР."""
import os
from pathlib import Path
from fpdf import FPDF

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / 'logo' / 'Logo.jpeg'
WATERMARK = ROOT / 'static' / 'docs' / 'watermark.png'
FONT = ROOT / 'static' / 'fonts' / 'DejaVuSans.ttf'
OUT = ROOT / 'СЕРВЕР' / 'Инструкция_простыми_словами.pdf'
W = 180


class Doc(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        if WATERMARK.exists():
            self.image(str(WATERMARK), x=5, y=25, w=200)
        self.set_font('DejaVu', '', 8)
        self.set_text_color(150, 150, 150)
        self.set_xy(15, 8)
        self.cell(W, 5, 'ProMaster — инструкция простыми словами', align='C')
        self.line(15, 14, 195, 14)
        self.set_text_color(0, 0, 0)
        self.set_xy(15, 18)

    def footer(self):
        self.set_xy(15, -15)
        self.set_font('DejaVu', '', 8)
        self.set_text_color(130, 130, 130)
        self.cell(W, 8, f'Стр. {self.page_no()}', align='C')

    def _r(self):
        self.set_x(15)

    def h1(self, t):
        self._r(); self.ln(3)
        self.set_font('DejaVu', '', 17)
        self.set_text_color(26, 26, 46)
        self.multi_cell(W, 9, t)
        self.set_draw_color(91, 156, 246)
        self.line(15, self.get_y() + 1, 195, self.get_y() + 1)
        self.ln(4)

    def h2(self, t):
        self._r(); self.ln(2)
        self.set_font('DejaVu', '', 12)
        self.set_text_color(40, 70, 140)
        self.multi_cell(W, 7, t)
        self.ln(1)

    def p(self, t):
        self._r()
        self.set_font('DejaVu', '', 10)
        self.set_text_color(40, 40, 40)
        self.multi_cell(W, 6, t)
        self.ln(1)

    def li(self, t):
        self._r()
        self.set_font('DejaVu', '', 10)
        self.multi_cell(W, 6, u'\u2022  ' + t)

    def step(self, n, t):
        self._r()
        self.set_font('DejaVu', '', 10)
        self.set_fill_color(91, 156, 246)
        self.multi_cell(W, 6, f'{n}. {t}')

    def code(self, t):
        self._r()
        self.set_font('DejaVu', '', 9)
        self.set_fill_color(245, 247, 250)
        self.multi_cell(W, 6, t, fill=True)
        self.ln(1)


pdf = Doc()
pdf.alias_nb_pages()
pdf.set_auto_page_break(True, margin=20)
pdf.add_font('DejaVu', '', str(FONT))

# COVER
pdf.add_page()
if LOGO.exists():
    pdf.image(str(LOGO), x=75, y=30, w=60)
pdf.set_y(100)
pdf.set_font('DejaVu', '', 24)
pdf.set_text_color(26, 26, 46)
pdf.cell(W, 12, 'ProMaster — простыми словами', align='C', new_x='LMARGIN', new_y='NEXT')
pdf.set_font('DejaVu', '', 13)
pdf.set_text_color(90, 90, 90)
pdf.cell(W, 8, 'Как поставить программу на сервер и обновлять её', align='C', new_x='LMARGIN', new_y='NEXT')
pdf.ln(8)
pdf.set_font('DejaVu', '', 10)
pdf.set_text_color(120, 120, 120)
pdf.multi_cell(W, 5, 'Инструкция для тех, кто НЕ программист.\nВсё по шагам, без сложных слов.', align='C')

# TOC
pdf.add_page()
pdf.h1('Содержание')
for t in [
    '1. Что есть что (3 простых слова)',
    '2. Что где лежит',
    '3. Где запускать команды',
    '4. Установка на сервер (способ A — Setup.exe)',
    '5. Как обновлять программу',
    '6. Лицензионный ключ (генератор)',
    '7. Если что-то сломалось',
    '8. Частые вопросы',
]:
    pdf.li(t)

pdf.add_page()
pdf.h1('1. Что есть что (3 простых слова)')
pdf.h2('GitHub')
pdf.p('Это «облако для кода». Там хранится программа (как папка в интернете). '
      'Вы и разработчик меняете код здесь — чтобы не потерять и чтобы было видно, что менялось.')
pdf.h2('Сервер')
pdf.p('Компьютер, который стоит у вас на предприятии (или в дата-центре). '
      'На нём программа работает каждый день. Сотрудники открывают её в браузере.')
pdf.h2('SSH')
pdf.p('Удалённый «пульт управления» сервером. Через SSH можно дать команду серверу '
      'из дома или офиса. Не обязательно, если вы ставите программу через установщик.')

pdf.add_page()
pdf.h1('2. Что где лежит')
pdf.li('На GitHub — «ядро»: весь код программы (это файлы .py и папки).')
pdf.li('В папке СЕРВЕР на вашем ПК — установочный файл и генератор ключей.')
pdf.li('На сервере — программа + ДАННЫЕ: база, фотографии, настройки.')
pdf.p('Главное правило: ДАННЫЕ (база, фото) не отправляем в GitHub и не затираем при обновлении.')

pdf.add_page()
pdf.h1('3. Где запускать команды')
pdf.p('Есть два «компьютера», их легко перепутать:')
pdf.li('ВАШ ПК (где вы сейчас) — здесь вы правите код, собираете установщик.')
pdf.li('СЕРВЕР — здесь программа работает для сотрудников.')
pdf.h2('Как понять, где я?')
pdf.li('Окно «PowerShell» / «Командная строка» на вашем ПК = ВАШ ПК.')
pdf.li('Если подключились по SSH (PuTTY / Windows Terminal) = вы «внутри» СЕРВЕРА.')
pdf.p('Все команды в начале инструкции с пометкой «на вашем ПК» — запускать на своём компьютере. '
      'Команды «на сервере» — только через SSH или на самом сервере.')

pdf.add_page()
pdf.h1('4. Установка на сервер (способ A — Setup.exe)')
pdf.p('Это самый простой путь. Ничего сложного.')
pdf.step(1, 'На своём ПК: в папке СЕРВЕР запустить Генератор ключа.bat → сгенерировать ключ (укажете срок сами).')
pdf.step(2, 'Скопировать файл ProMaster_Server_Setup_2.12.exe на сервер (флешка, сеть, любой способ).')
pdf.step(3, 'На сервере: запустить установщик от имени администратора.')
pdf.step(4, 'После установки: открыть .env и вписать SECRET_KEY (случайная строка).')
pdf.step(5, 'Запустить программу. Появится страница «Требуется лицензия» → вставить ключ.')
pdf.step(6, 'Сотрудники открывают адрес программы в браузере.')
pdf.p('Готово. Дальше только обновления (следующий раздел).')

pdf.add_page()
pdf.h1('5. Как обновлять программу')
pdf.step(1, 'Разработчик (или вы) меняет код.')
pdf.step(2, 'На вашем ПК: python СЕРВЕР\\build_package.py — собирается новый пакет.')
pdf.step(3, 'Снова делается Setup.exe (или копируются файлы кода на сервер).')
pdf.step(4, 'На сервере запускается установка / замена файлов.')
pdf.p('НЕ удалять на сервере папки: instance, static\\uploads, backups, файл .env — это ДАННЫЕ.')
pdf.p('Если после обновления что-то не работает — вернуть старый пакет (он у вас сохраняется).')

pdf.add_page()
pdf.h1('6. Лицензионный ключ')
pdf.h2('Как выдать ключ клиенту')
pdf.step(1, 'Открыть «Генератор ключа» (двойной клик).')
pdf.step(2, 'Вписать имя компании и СРОК действия (вы решаете: месяц, год и т.д.).')
pdf.step(3, 'Нажать «Сгенерировать» — ключ скопируется.')
pdf.step(4, 'Передать ключ клиенту.')
pdf.h2('Что делает ключ')
pdf.li('Ключ действителен → программа работает.')
pdf.li('Срок закончился → программа снова спрашивает ключ.')
pdf.li('Ключ неверный → вводит нельзя, программа не работает.')
pdf.p('Ключ подписывается секретом — подделать его нельзя. Храните свой секрет в тайне.')

pdf.add_page()
pdf.h1('7. Если что-то сломалось')
pdf.li('Белая страница / ошибка — обновите браузер, проверьте, работает ли сервер.')
pdf.li('Не открывается программа — на сервере должен работать процесс «python wsgi.py» или сервис.')
pdf.li('Не принимает ключ — проверьте срок и лишние пробелы (лучше вставлять целиком).')
pdf.li('После обновления пропали данные — значит затёрли папку instance или uploads. '
       'Восстановите из backup.')
pdf.p('Файл README.md в папке СЕРВЕР — короткая памятка. Этот PDF — подробная.')

pdf.add_page()
pdf.h1('8. Частые вопросы')
pdf.h2('Можно без интернета?')
pdf.p('Да. Программа работает в локальной сети. Нужен только доступ к серверу.')
pdf.h2('Нужно ли обновлять телефон?')
pdf.p('Нет. Телефон открывает ту же программу через браузер / иконку.')
pdf.h2('Что если сервер сломается?')
pdf.p('Есть резервная копия базы и фото — восстановление занимает минуты.')
pdf.h2('Кто может выдавать ключи?')
pdf.p('Только вы (владелец генератора). Ключ — это ваша «печать».')

pdf.ln(6)
pdf.set_x(15)
pdf.set_font('DejaVu', '', 9)
pdf.set_text_color(120, 120, 120)
pdf.multi_cell(W, 5, '— Конец —\nProMaster 2.12 · A.B.A.S.O.F.F.')

pdf.output(str(OUT))
print('WROTE', OUT, os.path.getsize(OUT))
