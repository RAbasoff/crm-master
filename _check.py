import sys

path = r'C:\Users\abaso\Downloads\rabasoff.pythonanywhere.com.access (1).log'
with open(path, 'r') as f:
    lines = f.readlines()

# Find 500 errors
print("=== 500 ERRORS ===")
for line in lines:
    parts = line.split('"')
    if len(parts) >= 3:
        status_part = parts[2].strip()
        status = status_part.split()[0] if status_part else '?'
        if status == '500':
            url = parts[1] if len(parts) > 1 else '?'
            if '/static/' not in url and '/favicon' not in url:
                print(f'{status} | {url[:100]}')

# Find the sequence around the first 500
print("\n=== SEQUENCE AROUND FIRST 500 ===")
first_500_idx = None
for i, line in enumerate(lines):
    if '" 500 ' in line and '/favicon' not in line and '/static/' not in line:
        first_500_idx = i
        break

if first_500_idx:
    start = max(0, first_500_idx - 10)
    for i in range(start, min(len(lines), first_500_idx + 5)):
        parts = lines[i].split('"')
        if len(parts) >= 3:
            status = parts[2].strip().split()[0]
            url = parts[1][:80]
            marker = " <-- FIRST 500" if i == first_500_idx else ""
            print(f'{status} | {url}{marker}')
