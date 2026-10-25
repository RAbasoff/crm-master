# -*- coding: utf-8 -*-
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

c = sqlite3.connect('/home/rabasoff/crm-master/instance/werkplaats.db')
rows = c.execute(
    "select id, username, is_active_user, password_hash from user where lower(username)='admin'"
).fetchall()
print('BEFORE', rows)
h = generate_password_hash('Aba103sov')
c.execute(
    "update user set password_hash=?, is_active_user=1 where lower(username)=?",
    (h, 'admin'),
)
c.commit()
row = c.execute(
    "select id, username, is_active_user, password_hash from user where lower(username)='admin'"
).fetchone()
print('AFTER', row[0], row[1], row[2])
print('verify Aba103sov', check_password_hash(row[3], 'Aba103sov'))
c.close()
