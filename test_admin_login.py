import requests, time

time.sleep(2)

# 1. Get the login page to see if it loads and get CSRF token
r = requests.get('http://localhost:8000/admin/login', timeout=10)
print(f"GET /admin/login: {r.status_code}")
html = r.text

# Check if there's a CSRF token
csrf_token = ''
import re
csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', html)
if csrf_match:
    csrf_token = csrf_match.group(1)
    print(f"CSRF token found: {csrf_token[:20]}...")
else:
    # Check for hidden csrf input
    csrf_match2 = re.search(r'csrf_token.*?value="([^"]+)"', html)
    if csrf_match2:
        csrf_token = csrf_match2.group(1)
        print(f"CSRF token (alt): {csrf_token[:20]}...")
    else:
        print("No CSRF token found in login form")

# Check what form fields exist
form_fields = re.findall(r'name="([^"]+)"', html)
print(f"Form fields: {form_fields}")

# Check for any error messages
error_match = re.search(r'class="alert[^"]*alert-danger[^"]*".*?>(.*?)</div>', html, re.DOTALL)
if error_match:
    print(f"Error message: {error_match.group(1).strip()}")
else:
    print("No error message on login page")

# Check for any text about Supabase
if 'supabase' in html.lower() or 'key' in html.lower() and 'missing' in html.lower():
    print("Supabase/key-related text found in page")
    
# Print relevant portions
if 'not configured' in html.lower():
    print("Found 'not configured' message")
if 'cannot reach' in html.lower():
    print("Found 'cannot reach' message")

print()

# Try to see if admin login page has specific content
# Look for the form action
form_action = re.search(r'<form[^>]*action="([^"]+)"', html)
if form_action:
    print(f"Form action: {form_action.group(1)}")
form_method = re.search(r'<form[^>]*method="([^"]+)"', html)
if form_method:
    print(f"Form method: {form_method.group(1)}")

# Show a snippet around the form
form_start = html.find('<form')
if form_start >= 0:
    print(f"\nForm snippet:\n{html[form_start:form_start+500]}")
