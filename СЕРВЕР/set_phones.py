#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Set phones on server DB (twin of local). Safe to run repeatedly."""
import sqlite3
c = sqlite3.connect('/home/rabasoff/crm-master/instance/werkplaats.db')
phones = {
    'peter': '+32476325358',
    'tim': '+32475815481',
    'thijs': '+32473815482',
    'ruslan.abasoff': '+32489265211',
    'nikita': '+32479247603',
    'iqbal': '+32472606731',
    'admin': '+32489265211',
    'director': '+32489265211',
    'bartek': '+32472112395',
    'hashem': '+32465524852',
    'hashim': '+32465524852',
}
for uname, ph in phones.items():
    n = c.execute(
        'update user set phone=? where lower(username)=? or lower(display_name)=?',
        (ph, uname.lower(), uname.lower()),
    ).rowcount
    print('set', uname, ph, 'rows', n)
c.execute("update user set phone=? where id in (5,8)", ('+32489265211',))
c.execute("update user set phone=null where phone is null or trim(phone)='' or lower(trim(phone)) in ('none','null')")
c.commit()
print('WITH PHONES:')
for r in c.execute("select id,username,display_name,phone from user where is_active_user=1 and phone is not null and trim(phone)!='' order by id"):
    print(' ', r)
print('MISSING:')
for r in c.execute("select id,username,display_name,role from user where is_active_user=1 and (phone is null or trim(phone)='') order by id"):
    print(' ', r)
c.close()
