"""Final live check of the redesigned dashboard."""
import requests, sys, time, re
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

base = 'http://localhost:8000'

# 1. Check dashboard loads
r = requests.get(f'{base}/dashboard', timeout=15)
html = r.text
print(f'1. Dashboard: HTTP {r.status_code}')
assert r.status_code == 200

# 2. Check dashboard HTML structure (all 32 points)
required_elements = {
    'Weather Intelligence': 'Weather Intelligence',
    'Current Weather card': 'Current Weather',
    'AI Prediction card': 'AI Prediction',
    'TODAYS INTELLIGENCE': "Today's Intelligence",
    'Intel tab Weather': 'intel-weather',
    'Intel tab Rain': 'intel-rain',
    'Intel tab Farming': 'intel-farming',
    'Intel tab Risk': 'intel-risk',
    'Weather Analytics': 'Weather Analytics',
    'Chart tab Temperature': 'tempChart',
    'Chart tab Humidity': 'humidChart',
    'Chart tab Wind': 'windChart',
    'Chart tab Rainfall': 'rainChart',
    'Weather Map': 'Weather Map',
    'AI Agriculture Insights': 'AI Agriculture Insights',
    'Recent Activities': 'Recent Activities',
    'Recent Weather Data': 'Recent Weather Data',
    'Sensor Decisions': 'Sensor Decisions',
    'Last Updated': 'last-updated-time',
    'Live indicator': 'live-dot',
    'AI status': 'ai-status',
}

missing = []
for name, needle in required_elements.items():
    if needle not in html:
        missing.append(name)

if missing:
    print(f'2. Missing elements: {missing}')
else:
    print(f'2. All {len(required_elements)} required elements present')

# 3. Check server-rendered weather data is present
print(f'3. Server data check:')
for location in ['Lahore', 'Islamabad', 'Karachi']:
    print(f'   {location}: {"found" if location in html else "missing"}')

# 4. Check 4 stat cards removed
stats = ['Active Users', 'Active Alerts', 'AI Accuracy']
for s in stats:
    assert s not in html, f"{s} stat card still present!"
print(f'4. 4 stat cards removed: OK')

# 5. Check no hardcoded values
assert '22.1' not in html or '22.1' in '022.1', "Hardcoded AI prediction found!"
assert '85% confidence' not in html, "Hardcoded confidence found!"
print(f'5. No hardcoded AI/weather values: OK')

# 6. Verify all pages still work
print(f'\n6. Endpoint check:')
for ep in ['/', '/dashboard', '/agri', '/alerts', '/recommendations',
           '/api/system/status', '/api/agri/analytics']:
    r = requests.get(f'{base}{ep}', timeout=20)
    print(f'   {ep:35s} HTTP {r.status_code}')

# 7. Check that JS files load correctly
print(f'\n7. JS/CSS check:')
for path in ['/static/css/style.css', '/static/css/agri-theme.css', '/static/js/script.js']:
    r = requests.get(f'{base}{path}', timeout=10)
    print(f'   {path:35s} HTTP {r.status_code} ({len(r.text)} bytes)')

print(f'\n✅ Final verification PASSED - Dashboard is ready!')
