import re

path = r'C:\Users\abaso\Downloads\rabasoff.pythonanywhere.com.access (2).log'
with open(path, 'r', encoding='utf-8', errors='replace') as f:
    for line in f:
        if '" 500 ' in line or '" 403 ' in line:
            parts = line.split('"')
            if len(parts) >= 3:
                status = parts[2].strip().split()[0]
                url = parts[1] if len(parts) > 1 else ''
                if '/static/' not in url and '/favicon' not in url:
                    print(f'{status} | {url[:100]}')
