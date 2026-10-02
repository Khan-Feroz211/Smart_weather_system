import requests, re, time

time.sleep(2)

s = requests.Session()
r = s.get('http://localhost:8000/admin/login', timeout=10)
csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
token = csrf.group(1) if csrf else ''

# Try login with admin email
r2 = s.post('http://localhost:8000/admin/login', data={
    'csrf_token': token,
    'email': 'professionalskill.pkmona@gmail.com',
    'password': 'test12345'
}, timeout=15, allow_redirects=False)

print(f"Status: {r2.status_code}")

# Get all flash messages
alerts = re.findall(r'alert[^<>]*>(.*?)</div>', r2.text, re.DOTALL | re.IGNORECASE)
for a in alerts:
    clean = re.sub(r'<[^>]+>', '', a).strip()
    if clean:
        print(f"Message: {clean}")

# Check if it's a Supabase auth issue or a DB issue
if 'Invalid email or password' in r2.text:
    print("-> Supabase auth rejected credentials")
elif 'unreachable' in r2.text.lower() or 'could not be linked' in r2.text.lower():
    print("-> Supabase auth OK, but database issue")
elif 'deactivated' in r2.text.lower():
    print("-> Account exists but deactivated")
elif 'insufficient permissions' in r2.text.lower():
    print("-> Auth OK, but user is not an admin")
else:
    print("-> Checking for 'Invalid credentials or insufficient permissions'")
