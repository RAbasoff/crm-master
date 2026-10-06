# -*- coding: utf-8 -*-
"""Генератор лицензионных ключей ProMaster.

Использование:
  python ГенераторКлючей.py "ООО Мастерская" 2027-12-31 15

Аргументы: клиент, срок (ГГГГ-ММ-ДД), кол-во мест (по желанию).
"""
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from license import make_license, parse_license


def generate():
    client = ent_client.get().strip() or 'Company'
    expire = ent_expire.get().strip() or '2027-12-31'
    try:
        seats = int(ent_seats.get() or '15')
    except ValueError:
        seats = 15
    key = make_license(client, expire, seats)
    out.delete('1.0', tk.END)
    out.insert(tk.END, key)
    try:
        root.clipboard_clear()
        root.clipboard_append(key)
    except Exception:
        pass
    ok, info = parse_license(key)
    status.config(text=('Ключ действителен до ' + str(info.get('expire'))) if ok else str(info))


def copy_key():
    txt = out.get('1.0', tk.END).strip()
    if not txt:
        messagebox.showinfo('Пусто', 'Сначала сгенерируйте ключ')
        return
    try:
        root.clipboard_clear()
        root.clipboard_append(txt)
        status.config(text='Скопировано в буфер обмена')
    except Exception as e:
        status.config(text=str(e))


root = tk.Tk()
root.title('ProMaster — генератор ключей')
root.geometry('560x420')
root.configure(bg='#0f1629')

style = ttk.Style()
style.theme_use('clam')

frm = tk.Frame(root, bg='#0f1629', padx=16, pady=16)
frm.pack(fill='both', expand=True)

tk.Label(frm, text='Лицензия ProMaster', bg='#0f1629', fg='#5b9cf6',
         font=('Segoe UI', 16, 'bold')).pack(anchor='w')

tk.Label(frm, text='Клиент / компания', bg='#0f1629', fg='#ccc').pack(anchor='w', pady=(12, 2))
ent_client = tk.Entry(frm, font=('Segoe UI', 11))
ent_client.pack(fill='x')
ent_client.insert(0, 'A.B.A.S.O.F.F.')

tk.Label(frm, text='Срок действия (ГГГГ-ММ-ДД) — задаёте вы', bg='#0f1629', fg='#ccc').pack(anchor='w', pady=(10, 2))
ent_expire = tk.Entry(frm, font=('Segoe UI', 11))
ent_expire.pack(fill='x')
ent_expire.insert(0, '2027-12-31')

tk.Label(frm, text='Мест (пользователей)', bg='#0f1629', fg='#ccc').pack(anchor='w', pady=(10, 2))
ent_seats = tk.Entry(frm, font=('Segoe UI', 11))
ent_seats.pack(fill='x')
ent_seats.insert(0, '15')

btn_row = tk.Frame(frm, bg='#0f1629')
btn_row.pack(fill='x', pady=12)
tk.Button(btn_row, text='Сгенерировать ключ', command=generate,
          bg='#5b9cf6', fg='white', font=('Segoe UI', 11, 'bold'),
          relief='flat', padx=16, pady=8).pack(side='left')
tk.Button(btn_row, text='Скопировать', command=copy_key,
          bg='#2c3e50', fg='white', font=('Segoe UI', 11),
          relief='flat', padx=16, pady=8).pack(side='left', padx=8)

tk.Label(frm, text='Ключ:', bg='#0f1629', fg='#ccc').pack(anchor='w')
out = tk.Text(frm, height=5, font=('Consolas', 10), wrap='word',
              bg='#161b22', fg='#e6edf3', insertbackground='white')
out.pack(fill='both', expand=True)

status = tk.Label(frm, text='', bg='#0f1629', fg='#27ae60', font=('Segoe UI', 10))
status.pack(anchor='w', pady=8)

if len(sys.argv) >= 3:
    ent_client.delete(0, tk.END)
    ent_client.insert(0, sys.argv[1])
    ent_expire.delete(0, tk.END)
    ent_expire.insert(0, sys.argv[2])
    if len(sys.argv) > 3:
        ent_seats.delete(0, tk.END)
        ent_seats.insert(0, sys.argv[3])
    generate()

root.mainloop()
