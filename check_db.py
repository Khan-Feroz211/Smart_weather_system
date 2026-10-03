import os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

import supabase_client as sb
import db

print("=== Supabase Configuration ===")
print(f"SUPABASE_URL: {'SET' if sb.url() else 'MISSING'}")
print(f"SUPABASE_ANON_KEY: {'SET' if sb.anon_key() else 'MISSING'}")
print(f"SUPABASE_SERVICE_ROLE_KEY: {'SET' if sb.service_key() else 'MISSING'}")
print(f"ADMIN_EMAIL: {sb.admin_email() or 'MISSING'}")
print(f"is_configured(): {sb.is_configured()}")
print(f"is_configured(require_service_key=True): {sb.is_configured(require_service_key=True)}")
print()

# Try database connection
try:
    ok, msg = db.ping()
    print(f"=== Database Connection ===")
    print(f"DB ping: ok={ok}, msg={msg}")
except Exception as e:
    print(f"DB ping error: {type(e).__name__}: {e}")
print()

# If DB is OK, check admin user
if ok:
    try:
        with db.connect() as conn:
            email = sb.admin_email()
            print(f"=== Looking up user: {email} ===")
            rows = conn.execute(
                "SELECT id, email, username, role, is_active FROM users WHERE email = %s",
                (email,)
            ).fetchall()
            if rows:
                for r in rows:
                    print(f"  Found: id={r['id']}, username={r['username']}, role={r['role']}, is_active={r['is_active']}")
            else:
                print(f"  No user found with email={email}")
            
            # Check admin_users table for this user
            admin_rows = conn.execute(
                "SELECT user_id, is_active, created_at FROM admin_users WHERE user_id IN "
                "(SELECT id FROM users WHERE email = %s)",
                (email,)
            ).fetchall()
            print(f"\n=== Admin users entries ===")
            if admin_rows:
                for r in admin_rows:
                    print(f"  {dict(r)}")
            else:
                print("  No admin_users entries found for this user!")
    except Exception as e:
        print(f"DB query error: {type(e).__name__}: {e}")
