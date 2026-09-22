#!/usr/bin/env python3
"""
Create (or promote) an administrator.

    python scripts/create_admin.py                       # uses ADMIN_EMAIL from .env, prompts for a password
    python scripts/create_admin.py --email me@example.com --username boss
    python scripts/create_admin.py --revoke --email me@example.com

What it does
  1. Creates the account in Supabase Auth (email pre-confirmed) - or, if the email already
     exists, RESETS its password to the one you type (so nobody can pre-register your admin
     email and keep access).
  2. Creates / links the profile row in public.users and sets role = 'admin'.
  3. Adds an active row to public.admin_users.
The password is typed at a hidden prompt: it is never stored by this app and never passed on the
command line.  Needs SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_DB_URL.
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

import db  # noqa: E402
import supabase_client as sb  # noqa: E402


def die(msg):
    print(f"✗ {msg}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Create or revoke a Smart Weather administrator")
    ap.add_argument("--email", default=sb.admin_email(), help="admin email (default: ADMIN_EMAIL)")
    ap.add_argument("--username", default=None, help="display name (default: part before @)")
    ap.add_argument("--revoke", action="store_true", help="remove admin rights instead of granting them")
    args = ap.parse_args()

    email = (args.email or "").strip().lower()
    if "@" not in email:
        die("Provide --email or set ADMIN_EMAIL in .env")
    if not sb.is_configured(require_service_key=True):
        die("SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    if not db.is_configured():
        die("SUPABASE_DB_URL must be set in .env")

    try:
        if args.revoke:
            with db.connect() as conn:
                row = conn.execute("SELECT id::text FROM users WHERE lower(email) = %s", (email,)).fetchone()
                if not row:
                    die("No profile with that email.")
                conn.execute("UPDATE users SET role = 'user' WHERE id = %s::uuid", (row[0],))
                conn.execute("UPDATE admin_users SET is_active = FALSE WHERE user_id = %s::uuid", (row[0],))
                conn.commit()
            print(f"✓ Admin rights revoked for {email}")
            return

        password = getpass.getpass("Admin password (min 12 characters, typed hidden): ")
        if len(password) < 12:
            die("Password must be at least 12 characters.")
        if password != getpass.getpass("Repeat password: "):
            die("Passwords do not match.")
        username = args.username or email.split("@")[0]

        existing = sb.admin_find_user_by_email(email)
        if existing:
            sb.admin_update_user(existing["id"], password=password, email_confirm=True)
            auth_id, meta = existing["id"], existing.get("user_metadata") or {}
            print("• Existing Supabase Auth account found - password reset and email confirmed.")
        else:
            created = sb.admin_create_user(email, password, {"username": username}, confirm_email=True)
            auth_id, meta = created["id"], {"username": username}
            print("• Supabase Auth account created.")

        with db.connect() as conn:
            pid = conn.execute("SELECT public.ensure_profile(%s::uuid, %s, %s::jsonb, TRUE)",
                               (auth_id, email, db.safe_json(meta or {"username": username}))).fetchone()[0]
            if pid is None:
                die("Could not create the profile row (email conflict with an unlinked profile?).")
            conn.execute("UPDATE users SET role = 'admin', is_active = TRUE WHERE id = %s::uuid", (str(pid),))
            conn.execute("INSERT INTO admin_users (user_id, role, is_active) VALUES (%s::uuid, 'admin', TRUE) "
                         "ON CONFLICT (user_id) DO UPDATE SET is_active = TRUE, role = 'admin'", (str(pid),))
            conn.execute("INSERT INTO admin_audit_log (admin_id, action, target_type, target_id, details) "
                         "VALUES (NULL, 'admin_bootstrap', 'user', %s, %s::jsonb)",
                         (str(pid), db.safe_json({"email": email, "via": "scripts/create_admin.py"})))
            conn.commit()
        print(f"✓ {email} is now an administrator.\n  Sign in at /admin/login")
    except sb.SupabaseError as exc:
        die(f"Supabase Auth error: {exc}")
    except db.DatabaseUnavailable as exc:
        die(f"Database unreachable: {exc}")


if __name__ == "__main__":
    main()
