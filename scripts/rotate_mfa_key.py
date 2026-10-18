#!/usr/bin/env python3
"""
Re-encrypt stored MFA secrets to the current primary MFA_ENCRYPTION_KEY.

    python scripts/rotate_mfa_key.py --dry-run     # report only, writes nothing
    python scripts/rotate_mfa_key.py               # re-encrypt in one transaction
    python scripts/rotate_mfa_key.py --force       # allow a single-key run (see below)

Rotation procedure
  1. Generate a new key:
         python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  2. Set MFA_ENCRYPTION_KEY="<newkey>,<oldkey>" (NEW FIRST) and restart the app.
     Everything already stored still decrypts; new writes use the new key.
  3. Run this script. It decrypts each stored secret with whichever configured
     key works and re-encrypts it with the primary (first) key.
  4. When the script reports 0 rows left to re-encrypt, drop <oldkey> from
     MFA_ENCRYPTION_KEY and restart again.

Columns covered: mfa_factors.secret_encrypted and
mfa_pending_enrollments.secret_encrypted.  public.users.totp_secret is NOT
touched -- it is still plaintext and is reserved as a migration source for a
later phase.

Why --force is required for a single key
  With only one key configured, every row already decrypts under the primary,
  so a normal run is a no-op -- and if a row does NOT decrypt, the old key has
  already been dropped and the data is unrecoverable by this script. Refusing
  makes the "I forgot to put the old key in the list" mistake loud instead of
  silent. --force runs anyway (useful to re-key rows after a restore).

Output is counts only. No secret, ciphertext, key or row payload is ever
printed, and ids appear only in a hard-failure message.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

import mfa_crypto  # noqa: E402

# (table, id column, ciphertext column)
TARGETS = (
    ("mfa_factors", "id", "secret_encrypted"),
    ("mfa_pending_enrollments", "id", "secret_encrypted"),
)


class RotationError(RuntimeError):
    """A row could not be decrypted with any configured key."""


def die(msg):
    print(f"\u2717 {msg}")
    sys.exit(1)


def rotate_rows(conn, placeholder="%s", dry_run=False, targets=TARGETS):
    """Re-encrypt every ciphertext column in `targets` to the primary key.

    `conn` is any DB-API connection (db.Connection with placeholder "%s", or a
    sqlite3 connection with "?").  Pure and transaction-agnostic: the caller
    owns commit/rollback.

    Returns a dict of counts: scanned / already_current / reencrypted /
    skipped_null.  Raises RotationError (naming the table and row id, never a
    value) when a row decrypts under no configured key.
    """
    counts = {"scanned": 0, "already_current": 0, "reencrypted": 0, "skipped_null": 0}
    for table, id_col, col in targets:
        cur = conn.execute(f"SELECT {id_col}, {col} FROM {table}")
        rows = [(r[0], r[1]) for r in cur.fetchall()]
        for row_id, token in rows:
            counts["scanned"] += 1
            if not token:
                counts["skipped_null"] += 1
                continue
            if mfa_crypto.decrypts_with_primary(token):
                counts["already_current"] += 1
                continue
            try:
                plaintext = mfa_crypto.decrypt_secret(token)
            except mfa_crypto.MfaCryptoError as exc:
                raise RotationError(
                    f"{table} row {row_id} could not be decrypted with any configured "
                    f"{mfa_crypto.ENCRYPTION_KEY_ENV} key ({exc}). "
                    "Add the old key back to the list and re-run."
                ) from None
            new_token = mfa_crypto.encrypt_secret(plaintext)
            del plaintext
            if not dry_run:
                conn.execute(
                    f"UPDATE {table} SET {col} = {placeholder} WHERE {id_col} = {placeholder}",
                    (new_token, row_id),
                )
            counts["reencrypted"] += 1
    return counts


def _print_counts(counts, dry_run):
    print("  rows scanned          :", counts["scanned"])
    print("  already current key   :", counts["already_current"])
    print("  re-encrypted" + (" (dry run)" if dry_run else "          ") + ":",
          counts["reencrypted"])
    print("  skipped (no secret)   :", counts["skipped_null"])


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Re-encrypt stored MFA secrets to the primary MFA_ENCRYPTION_KEY")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the same counts without writing anything")
    ap.add_argument("--force", action="store_true",
                    help="run even when MFA_ENCRYPTION_KEY holds only one key")
    args = ap.parse_args(argv)

    try:
        keys = mfa_crypto._load_keys()
    except mfa_crypto.MfaCryptoError as exc:
        die(str(exc))

    if len(keys) < 2 and not args.force:
        die(f"{mfa_crypto.ENCRYPTION_KEY_ENV} holds only one key, so there is nothing to "
            "rotate from. Set it to \"<newkey>,<oldkey>\" (new first) and re-run, or pass "
            "--force to re-key against the single configured key anyway.")

    import db  # imported late so --help works without the driver

    try:
        with db.transaction() as conn:
            counts = rotate_rows(conn, placeholder="%s", dry_run=args.dry_run)
            if args.dry_run:
                conn.rollback()
    except RotationError as exc:
        die(str(exc))
    except db.DatabaseUnavailable as exc:
        die(f"Database unreachable: {exc}")

    print("\u2713 MFA key rotation " + ("dry run complete" if args.dry_run else "complete"))
    _print_counts(counts, args.dry_run)
    if not args.dry_run and counts["reencrypted"] == 0 and counts["scanned"]:
        print("  Nothing left to rotate: it is safe to drop the old key from "
              f"{mfa_crypto.ENCRYPTION_KEY_ENV}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
