import re, os

issues = []
suggestions = []

# 1. Check app.py size
size = os.path.getsize('app.py')
if size > 50000:
    issues.append(f'app.py is {size/1024:.0f}KB - consider splitting into blueprints')

# 2. Check for missing error handling
with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

bare_except = content.count('except Exception:')
if bare_except > 5:
    issues.append(f'{bare_except} bare except blocks - errors silently swallowed')

# 3. Count routes
routes = re.findall(r"@app\.route\('", content)
print(f'Total routes: {len(routes)}')

# 4. Count templates
templates = [f for f in os.listdir('templates') if f.endswith('.html')]
print(f'Templates: {len(templates)}')

# 5. Check translations
with open('translations/ru/LC_MESSAGES/messages.po', 'r', encoding='utf-8') as f:
    po = f.read()
    empty = po.count('msgstr ""')
    print(f'Untranslated Russian strings: {empty}')

# 6. Check for missing imports
if 'from flask_socketio' in content:
    issues.append('flask_socketio imported but chat module removed')

# 7. Check models
with open('models.py', 'r', encoding='utf-8') as f:
    models = f.read()
    model_count = len(re.findall(r'class \w+\(db\.Model\)', models))
    print(f'Models: {model_count}')

print('\n=== ISSUES ===')
for i in issues:
    print(f'  {i}')

print('\n=== SUGGESTIONS ===')
suggestions = [
    'Split app.py into blueprints (auth, warehouse, machines, faults, etc.)',
    'Add API rate limiting to prevent abuse',
    'Add database backup automation',
    'Add email notifications for critical faults',
    'Add WhatsApp integration for notifications',
    'Add 2FA (two-factor authentication)',
    'Add dark mode persistence (save to user profile)',
    'Add export to PDF for all reports',
    'Add drag-and-drop file upload',
    'Add real-time dashboard with WebSocket updates',
    'Add machine maintenance calendar with reminders',
    'Add consumable replacement tracking with notifications',
    'Add barcode scanning for machine identification on floor map',
    'Add photo gallery for each machine',
    'Add document attachments to faults and work orders',
    'Add time tracking per machine/operation',
    'Add cost tracking per fault/maintenance',
    'Add customer portal for external users',
    'Add mobile app (PWA improvements)',
    'Add offline mode for floor plan',
]
for s in suggestions:
    print(f'  + {s}')
