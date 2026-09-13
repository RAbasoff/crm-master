#!/usr/bin/env python3
"""End-to-end test: login as each role, create faults/TWO, check for errors."""
import requests, re, sys, time

BASE = 'https://rabasoff.pythonanywhere.com'

# Pages to check for each role (GET requests)
PAGES = {
    'user': ['/', '/faults/', '/notifications/unread', '/set_language/ru'],
    'director': ['/', '/faults/', '/two', '/stats/faults', '/tool-wear', '/notifications/unread'],
    'technician': ['/', '/faults/', '/two', '/tool-wear', '/notifications/unread'],
}

ERRORS_FOUND = []

def get_csrf(session, url):
    """Extract CSRF token from a form page."""
    r = session.get(url)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.text)
    if not m:
        m = re.search(r'csrf_token.*?value=["\']([^"\']+)', r.text)
    return m.group(1) if m else None

def check_page(session, path, role_name):
    """GET a page, check for 500/error/traceback."""
    url = BASE + path
    try:
        r = session.get(url, timeout=15, allow_redirects=False)
    except Exception as e:
        ERRORS_FOUND.append(f"[{role_name}] {path} -> EXCEPTION: {e}")
        return None
    
    if r.status_code == 500:
        ERRORS_FOUND.append(f"[{role_name}] {path} -> 500 Internal Server Error")
    elif r.status_code >= 400 and r.status_code != 404:
        ERRORS_FOUND.append(f"[{role_name}] {path} -> HTTP {r.status_code}")
    
    if r.status_code == 200:
        body = r.text
        if 'Traceback' in body or 'Internal Server Error' in body:
            ERRORS_FOUND.append(f"[{role_name}] {path} -> Traceback/error in HTML")
        if 'An error occurred' in body:
            ERRORS_FOUND.append(f"[{role_name}] {path} -> 'An error occurred' flash message")
    
    return r

def login(username, password):
    """Login and return session."""
    s = requests.Session()
    r = s.get(f'{BASE}/login')
    csrf = get_csrf(s, f'{BASE}/login')
    if not csrf:
        print(f"  ERROR: cannot get CSRF for login page. Status: {r.status_code}")
        return None
    r = s.post(f'{BASE}/login', data={
        'username': username, 'password': password, 'csrf_token': csrf
    }, allow_redirects=False)
    if r.status_code in (301, 302):
        print(f"  Logged in as {username} (redirected to {r.headers.get('Location', '?')})")
        return s
    elif r.status_code == 200:
        if 'Invalid credentials' in r.text or 'Неверные' in r.text:
            print(f"  FAILED login as {username}: invalid credentials")
            return None
        if 'change_password' in r.text or 'сменить пароль' in r.text.lower():
            print(f"  WARNING: {username} forced to change password")
            # Try to change password
            csrf2 = get_csrf(s, f'{BASE}/change_password')
            if csrf2:
                r2 = s.post(f'{BASE}/change_password', data={
                    'new_password': password, 'confirm_password': password, 'csrf_token': csrf2
                }, allow_redirects=False)
                if r2.status_code in (301, 302):
                    print(f"  Password change accepted (keeping same password)")
                    return s
            return None
        print(f"  UNKNOWN: login returned 200 for {username} (not redirect, not error)")
        return None
    else:
        print(f"  UNEXPECTED login response: {r.status_code}")
        return None


# ============================================================
# TEST 1: Login and page checks for each role
# ============================================================
print("=" * 60)
print("TEST 1: Login and page health check")
print("=" * 60)

USERS = {
    'user': ('user', 'user123'),
    'director': ('director', 'director123'),
    'technician': ('tech', 'tech123'),
}

sessions = {}
for role, (username, password) in USERS.items():
    print(f"\n--- {role.upper()} ({username}) ---")
    s = login(username, password)
    if s:
        sessions[role] = s
        for page in PAGES.get(role, []):
            check_page(s, page, role)
            time.sleep(0.3)

# ============================================================
# TEST 2: User creates a fault report
# ============================================================
fault_id = None
if 'user' in sessions:
    print(f"\n{'=' * 60}")
    print("TEST 2: User creates fault report")
    print("=" * 60)
    s = sessions['user']
    csrf = get_csrf(s, f'{BASE}/faults/new')
    if csrf:
        r = s.post(f'{BASE}/faults/new', data={
            'title': '[TEST] Тестовая заявка на неисправность',
            'description': 'Тестовая заявка, созданная автоматически. Игнорировать.',
            'priority': 'low',
            'machine_id': '1',
            'csrf_token': csrf,
        }, allow_redirects=False)
        print(f"  POST /faults/new -> {r.status_code}")
        if r.status_code in (301, 302):
            loc = r.headers.get('Location', '')
            print(f"  Redirect: {loc}")
            # Extract fault ID from redirect
            m = re.search(r'/(\d+)', loc)
            if m:
                fault_id = m.group(1)
                print(f"  Created fault #{fault_id}")
        elif r.status_code == 200:
            if 'error' in r.text.lower()[:5000]:
                ERRORS_FOUND.append("[user] POST /faults/new -> error in response")
    else:
        ERRORS_FOUND.append("[user] Cannot get CSRF for /faults/new")

# ============================================================
# TEST 3: Director creates TWO
# ============================================================
two_id = None
if 'director' in sessions:
    print(f"\n{'=' * 60}")
    print("TEST 3: Director creates TWO")
    print("=" * 60)
    s = sessions['director']
    # First check if director can access /two/new
    r = s.get(f'{BASE}/two/new', allow_redirects=False)
    print(f"  GET /two/new -> {r.status_code}")
    if r.status_code == 302:
        loc = r.headers.get('Location', '')
        print(f"  Redirect: {loc}")
        if 'login' in loc:
            ERRORS_FOUND.append("[director] CANNOT access /two/new — needs role 'admin' or 'technician'")
        elif 'index' in loc:
            ERRORS_FOUND.append("[director] BLOCKED from /two/new — role_required('admin','technician') excludes director")
    elif r.status_code == 200:
        csrf = get_csrf(s, f'{BASE}/two/new')
        if csrf:
            r = s.post(f'{BASE}/two/new', data={
                'description': '[TEST] Тестовое TWO от директора',
                'machine_id': '1',
                'planned_date': '2026-09-20',
                'status': 'draft',
                'notes': 'Тестовое TWO, созданное автоматически',
                'csrf_token': csrf,
            }, allow_redirects=False)
            print(f"  POST /two/new -> {r.status_code}")
            if r.status_code in (301, 302):
                loc = r.headers.get('Location', '')
                m = re.search(r'/(\d+)', loc)
                if m:
                    two_id = m.group(1)
                    print(f"  Created TWO #{two_id}")

# ============================================================
# TEST 4: Technician views faults and TWOs
# ============================================================
if 'technician' in sessions:
    print(f"\n{'=' * 60}")
    print("TEST 4: Technician views faults/TWOs")
    print("=" * 60)
    s = sessions['technician']
    
    # Check faults list
    r = check_page(s, '/faults/', 'technician')
    if r and r.status_code == 200 and fault_id:
        if f'[TEST]' in r.text or str(fault_id) in r.text:
            print(f"  Fault #{fault_id} visible in list")
        else:
            print(f"  Fault #{fault_id} NOT visible in list (might need filter)")
    
    # Check TWO list
    r = check_page(s, '/two', 'technician')
    if r and r.status_code == 200:
        if two_id and str(two_id) in r.text:
            print(f"  TWO #{two_id} visible in list")
    
    # Try to accept a fault if any open
    if fault_id:
        r = s.get(f'{BASE}/faults/{fault_id}', allow_redirects=False)
        if r.status_code == 200:
            csrf = get_csrf(s, f'{BASE}/faults/{fault_id}')
            if csrf:
                r2 = s.post(f'{BASE}/faults/{fault_id}/accept', data={
                    'csrf_token': csrf
                }, allow_redirects=False)
                print(f"  POST /faults/{fault_id}/accept -> {r2.status_code}")
                if r2.status_code in (301, 302):
                    print(f"  Fault accepted!")
                    ERRORS_FOUND.pop() if ERRORS_FOUND and 'accept' in str(ERRORS_FOUND[-1]) else None

# ============================================================
# TEST 5: Technician creates work report for fault
# ============================================================
if 'technician' in sessions and fault_id:
    print(f"\n{'=' * 60}")
    print("TEST 5: Technician creates work report for fault")
    print("=" * 60)
    s = sessions['technician']
    url = f'{BASE}/faults/{fault_id}/work-report'
    r = s.get(url, allow_redirects=False)
    print(f"  GET /faults/{fault_id}/work-report -> {r.status_code}")
    if r.status_code == 200:
        csrf = get_csrf(s, url)
        if csrf:
            r2 = s.post(url, data={
                'description': '[TEST] Отчёт по ремонту — тестовая запись',
                'hours_spent': '1.5',
                'materials_used': 'Тестовые материалы',
                'csrf_token': csrf,
            }, allow_redirects=False)
            print(f"  POST /faults/{fault_id}/work-report -> {r2.status_code}")
            if r2.status_code in (301, 302):
                print(f"  Work report created!")
            elif r2.status_code == 200:
                if 'error' in r2.text.lower()[:5000]:
                    ERRORS_FOUND.append(f"[tech] Work report creation error")

# ============================================================
# SUMMARY
# ============================================================
print(f"\n{'=' * 60}")
print("SUMMARY")
print("=" * 60)
if ERRORS_FOUND:
    print(f"\n{len(ERRORS_FOUND)} ERROR(S) FOUND:")
    for e in ERRORS_FOUND:
        print(f"  ✗ {e}")
else:
    print("\n✓ No errors found!")
print(f"\nFault ID: {fault_id}")
print(f"TWO ID: {two_id}")
print(f"Tested roles: {list(sessions.keys())}")
