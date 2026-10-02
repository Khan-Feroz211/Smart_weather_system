import requests, re

s = requests.Session()
r = s.get('http://localhost:8000/admin/login', timeout=10)
csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
token = csrf.group(1) if csrf else ''

r2 = s.post('http://localhost:8000/admin/login', data={
    'csrf_token': token,
    'email': 'professionalskill.pkmona@gmail.com',
    'password': 'test12345'
}, timeout=15, allow_redirects=False)

print(f'Status: {r2.status_code}')

# Search for error/flash messages
# Bootstrap alert divs
alerts = re.findall(r'alert[^<>]*>(.*?)</div>', r2.text, re.DOTALL | re.IGNORECASE)
print(f'\nAlert messages:')
for a in alerts:
    clean = re.sub(r'<[^>]+>', '', a).strip()
    if clean:
        print(f'  -> {clean}')

# Look for specific error text
for term in ['not configured', 'keys missing', 'unreachable', 'supabase', 'database', 'error', 'sign-in failed', 'Sign-in']:
    if term.lower() in r2.text.lower():
        # Find context
        idx = r2.text.lower().find(term.lower())
        context = r2.text[max(0, idx-50):idx+100]
        clean_context = re.sub(r'<[^>]+>', ' ', context).strip()
        print(f'\n  Found "{term}" in: {clean_context}')

# Also check the full body for flash messages
flash_matches = re.findall(r'flash[^"]*"([^"]*)"', r2.text)
if flash_matches:
    print(f'\nFlash: {flash_matches}')
