import requests, re

s = requests.Session()
r = s.get('http://localhost:8000/admin/login', timeout=10)
csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
token = csrf.group(1) if csrf else ''
print(f'CSRF token: {token[:20]}...' if token else 'No CSRF token')

# Try to submit login form
r2 = s.post('http://localhost:8000/admin/login', data={
    'csrf_token': token,
    'email': 'professionalskill.pkmona@gmail.com',
    'password': 'test12345'
}, timeout=15, allow_redirects=False)

print(f'Login POST status: {r2.status_code}')
if r2.status_code == 302:
    loc = r2.headers.get('Location', '')
    print(f'Redirect to: {loc}')

# Follow redirect
if r2.status_code == 302:
    next_url = r2.headers.get('Location', 'http://localhost:8000/admin/login')
    r3 = s.get(next_url, timeout=10)
    if next_url.startswith('/'):
        r3 = s.get(f'http://localhost:8000{next_url}', timeout=10)

    # Search for error messages
    for pattern in ['not configured', 'Supabase', 'keys missing', 'unreachable', 'Invalid', 'error', 'alert-danger']:
        if pattern.lower() in r3.text.lower():
            # Find flash messages
            matches = re.findall(r'alert[^>]*>(.*?)</div>', r3.text, re.DOTALL)
            for m in matches:
                clean = re.sub(r'<[^>]+>', '', m).strip()
                if clean:
                    print(f'Flash message: {clean}')
            break
    else:
        # Check for any visible error
        if 'error' in r3.text.lower():
            matches = re.findall(r'alert[^>]*>(.*?)</div>', r3.text, re.DOTALL)
            for m in matches:
                clean = re.sub(r'<[^>]+>', '', m).strip()
                if clean:
                    print(f'Message: {clean}')
