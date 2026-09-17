from app import app

with app.test_client() as c:
    c.post('/login', data={'username': 'admin', 'password': 'admin', 'csrf_token': 'test'}, follow_redirects=True)
    routes = ['/chat/', '/chat/start/1', '/chat/api/unread']
    for r in routes:
        try:
            resp = c.get(r)
            print(f'{r}: {resp.status_code}')
            if resp.status_code != 200 and resp.status_code != 302:
                print(f'  Body: {resp.data[:300].decode(errors="replace")}')
        except Exception as e:
            print(f'{r}: ERROR - {type(e).__name__}: {e}')
