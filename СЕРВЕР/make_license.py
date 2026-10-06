# -*- coding: utf-8 -*-
"""Генератор лицензии ProMaster — запускаете ВЫ, срок задаёте сами.

Пример:
  python make_license.py "My Company" 2027-12-31 15
"""
import sys
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
from license import make_license

client = sys.argv[1] if len(sys.argv) > 1 else 'Company'
expire = sys.argv[2] if len(sys.argv) > 2 else '2027-12-31'
seats = int(sys.argv[3]) if len(sys.argv) > 3 else 15
key = make_license(client, expire, seats)
print(key)
