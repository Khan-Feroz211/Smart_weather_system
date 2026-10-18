# MFA Phase 1 — Implementation Plan (schema + encryption), ADDITIVE ONLY

Inputs read: `.agents/tasks/mfa-phase0-audit.md` (available — its findings drive this plan),
`docs/mfa_threat_model.md` (exists), `auth.py`, `totp_auth.py`, `admin_routes.py`, `db.py`,
`supabase/schema.sql`, `notifications.py`, `edge_case_hardening_v2.py`, `tests/test_auth_2fa.py`,
`tests/conftest.py`, `app_clean.py`, `requirements.txt`, `scripts/create_admin.py`.

## Secret hygiene — applies to EVERY item below

- Never open, read, print or quote `.env`. Reference env var NAMES only: `MFA_ENCRYPTION_KEY`,
  `MFA_IP_PEPPER`, `SECRET_KEY`, `DEBUG`, `SUPABASE_DB_URL`.
- Never log, print or put in an exception message: a TOTP secret, ciphertext, recovery code,
  challenge/trusted-device token, password, or a raw IP. Error messages describe the *shape* of the
  problem ("MFA_ENCRYPTION_KEY is not a valid Fernet key") never the value.
- Test fixtures use locally generated throwaway values (`Fernet.generate_key()`,
  `secrets.token_hex`) and never print them.
- No new outbound network calls anywhere in this phase.
- Anything ambiguous: pick the safest default and leave `# TODO(mfa): ... see docs/mfa_threat_model.md`.

## Decisions recorded (made here, not left to the implementer)

1. **DDL location and dialect: Postgres only, in `supabase/schema.sql`.** The file is a single
   Postgres script with no dialect split (`uuid`, `gen_random_uuid()`, `timestamptz NOT NULL DEFAULT now()`,
   `DO $$` blocks). `db.py` is Postgres-only (psycopg2 pool; `connect()` raises `DatabaseUnavailable`
   when `SUPABASE_DB_URL` is unset, db.py:284-285) and the `smart_weather.db` SQLite store is
   hand-maintained by `app_clean.py:1095-1135`, not by `db.py`. So: the new `mfa_*` tables go in
   `supabase/schema.sql` as the authoritative definition, **no SQLite backend is added to `db.py`**,
   and the "secret is not plaintext in the DB file" test uses its own hermetic temp-`sqlite3` fixture
   with a test-only SQLite translation of the same DDL.
2. **Migration mechanism: the existing idempotent-script convention.** `schema.sql` is documented as
   re-runnable; column migrations use `DO $$ ... information_schema` guards (section 9a, lines 592-632).
   New *tables* therefore need no `DO` block — `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`
   is exactly the file's convention. No second migration mechanism is invented, no migration runner
   is added.
3. **Encryption helper module name: `mfa_crypto.py` at the repo root.** The project is flat
   (`auth.py`, `totp_auth.py`, `db.py`, `notifications.py` all at root) and already uses the
   `<feature>_<role>.py` shape (`totp_auth.py`, `supabase_client.py`). `mfa_crypto.py` matches.
4. **Startup guard lives in `mfa_crypto.validate_mfa_config()`** — a plain importable function with a
   `strict` parameter, so it is directly unit-testable without a Flask app.
5. **db.py accessors: a new, clearly-fenced section appended to `db.py`.** `db.py` today contains only
   generic infrastructure (`connect`, `transaction`, `as_utc`, `safe_json`, `query_df`) and no
   table-specific accessors. The brief asks for them in `db.py`, so they go at the end under a
   `# 10. MFA (Phase 1) accessors` banner, built on `transaction()` / `connect()`, `%s` params and
   `as_utc`. No existing function signature or behaviour changes.
6. **Recovery-code hashing: `argon2-cffi` (Argon2id)**, preferred by the brief; `PasswordHasher`
   defaults are used (Argon2id). Phase 1 only adds the dependency and the `code_hash text` column —
   issuing/verifying codes is a later phase.
7. **Rotation script testability:** `scripts/rotate_mfa_key.py` separates a pure core
   `rotate_rows(conn, placeholder, crypto, dry_run)` from `main()`, which opens the real connection via
   `db.transaction()`. The `placeholder` argument (`"%s"` for Postgres, `"?"` for sqlite3) lets the
   test drive the same core code against a temp SQLite DB with no DB server.

## ADDITIVE-ONLY boundary — where to STOP and report

The coder must **not** touch any of these, and must stop and report instead of guessing if a step
seems to need it:

- `public.users.totp_secret` (schema.sql:52 and the 9a block at :609-613) — plaintext today
  (audit finding C4). It is a **migration source for a later phase**. Do not alter, drop, encrypt,
  re-type, backfill or stop selecting it. `auth.py:629/687/709`, `_PROFILE_SQL` at `auth.py:156`, and
  `admin_routes.py:170` keep working untouched.
- The legacy SQLite duplicate at `app_clean.py:1116` — leave alone entirely.
- Existing session keys `pending_2fa`, `_2fa_setup_secret`, `uid`, `admin_at`, `_csrf`; existing routes;
  `auth._RateLimiter` / `login_limiter` attribute names; `totp_auth.verify_code`'s signature.
- `app_clean.py:73` (`app.secret_key = os.environ.get('SECRET_KEY', 'smart_weather_ai_2024')`) —
  audit finding C1, being decided separately by the user. **Do not edit that line.** Item 8 below wires
  the new guard as a separate additive block; if that turns out to require changing line 73,
  **STOP and report**.
- Any existing test: no skipping, weakening, deleting or re-asserting.

## Baseline and verification commands (from the audit — do not deviate)

`unittest discover -s tests` does **not** finish (discovery imports the DL/GEE/network modules), and the
baseline is **not green**: `pyotp`, `qrcode`, `psycopg2` are listed in `requirements.txt` but absent from
`.venv`, giving 4 failures + 13 skips in `tests.test_auth_2fa`, and 2 environmental errors in
`tests.test_admin_security`. Those are pre-existing; do not fix them in this phase.

Use this scoped command (PowerShell, repo root) for the before/after baseline, and record its output:

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m unittest tests.test_auth_2fa tests.test_admin_security -v
```

New test modules are run individually, e.g.:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_mfa_crypto tests.test_mfa_startup_config tests.test_mfa_schema_sqlite tests.test_rotate_mfa_key -v
```

New modules are named `tests/test_mfa_*.py` / `tests/test_rotate_mfa_key.py` so that
`python -m unittest discover -s tests -v` *would* discover them (default pattern `test*.py`,
`tests/__init__.py` exists) even though the full discovery run is impractical here.

---

# Implementation Plan

- [ ] 1. Pin the two new dependencies in `requirements.txt`, appended as a new commented block at the
      end; do not touch or reorder any existing line. `cryptography==50.0.2` (the version already
      installed in `.venv`) and `argon2-cffi==25.1.0` (current stable on PyPI, `requires_python>=3.8`,
      so Python 3.11-compatible). No other package is added in this phase.
      Files: `requirements.txt`
      Verify: `.\.venv\Scripts\python.exe -m pip install cryptography==50.0.2 argon2-cffi==25.1.0`
      succeeds and `.\.venv\Scripts\python.exe -c "import cryptography, argon2"` exits 0.
      Do NOT run a bare `pip install -r requirements.txt` as part of this item (it would pull the
      heavy DL/GEE stack); install just these two.

- [ ] 2. Add the Phase 1 MFA tables to `supabase/schema.sql` as a new section
      `-- 10. MFA / 2FA (Phase 1: schema only — no app code reads these yet)` appended after the
      existing section 9a (ends line 632). Follow the file's conventions exactly: `public.` prefix,
      `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`, `user_id uuid REFERENCES public.users (id) ON DELETE CASCADE`,
      `timestamptz NOT NULL DEFAULT now()` for `created_at`, nullable `timestamptz` for
      `last_used_at` / `used_at` / `disabled_at` / `revoked_at`, `text ... CHECK (x IN (...))` for
      constrained values, `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` only, and a
      `COMMENT ON TABLE/COLUMN` for anything non-obvious. Tables, with the exact columns from the brief:
      `mfa_factors` (+ `UNIQUE(user_id, type, label)`, `type CHECK (type IN ('totp','webauthn','recovery'))`,
      `secret_encrypted text`, `credential_id text`, `public_key text`, `sign_count bigint NOT NULL DEFAULT 0`,
      `transports text`), `mfa_recovery_codes` (`code_hash text NOT NULL` — comment: Argon2id, never plain text),
      `mfa_used_totp_steps` (`PRIMARY KEY (factor_id, time_step)`, `time_step bigint`),
      `mfa_attempts` (`user_id` nullable, `ip_hash text`, `attempt_type text`, `success boolean NOT NULL`),
      `mfa_events` (append-only audit: `event_type`, `actor`, `ip_hash`, `user_agent_hash`, `created_at`),
      `trusted_devices` (`token_hash`, `ua_family`, `ip_prefix_hash`, `device_label`, `expires_at`, `revoked_at`),
      `mfa_login_challenges` (`challenge_id text PRIMARY KEY` — random 256-bit hex minted by the app,
      `state text CHECK (...)`, `attempts integer NOT NULL DEFAULT 0`, `expires_at`),
      `mfa_pending_enrollments` (`UNIQUE (user_id)` → at most one per user, `secret_encrypted`, `expires_at`),
      `mfa_outbox` (queued lost-device requests for offline use: `user_id`, `kind`, `payload jsonb`,
      `status text CHECK (...)`, `created_at`, `sent_at`), `mfa_reset_requests`
      (`cancel_token_hash`, `confirm_token_hash`, `requested_at`, `eligible_at`, `status`, `used_at`).
      Indexes on every lookup column named in the brief: `user_id` on all user-scoped tables,
      `expires_at` on `mfa_login_challenges` / `mfa_pending_enrollments` / `trusted_devices`,
      `created_at DESC` on `mfa_attempts` / `mfa_events` / `mfa_recovery_codes`, plus
      `mfa_attempts (ip_hash, created_at DESC)` and `trusted_devices (token_hash)`.
      Add an explicit comment on `mfa_events` documenting the **append-only** intent: the app issues
      INSERT and SELECT only; no UPDATE/DELETE except a later-phase retention job. Add no RLS policy
      and no grant for these tables, so they stay backend-only — state that in a comment (this is
      deliberately stricter than section 9's `authenticated` read policies).
      No existing line in the file changes.
      Files: `supabase/schema.sql`
      Verify: the SQL is not executed here (no DB is reachable: `psycopg2` is absent). Item 6's test
      proves the DDL parses and creates by applying the SQLite translation; additionally confirm the
      file still ends with balanced `DO $$ ... END $$;` blocks by eye, and that
      `.\.venv\Scripts\python.exe -c "print(open('supabase/schema.sql',encoding='utf-8').read().count('CREATE TABLE IF NOT EXISTS'))"`
      increased by exactly 10.

- [ ] 3. Create `mfa_crypto.py` at the repo root with the MultiFernet helper. Module docstring explains
      the key format and rotation procedure. Contents: `_load_keys()` reading `MFA_ENCRYPTION_KEY` as a
      comma-separated list (strip whitespace, drop empties) where the **first key encrypts and all keys
      decrypt**; `get_multifernet()` returning `MultiFernet([Fernet(k) for k in keys])` (cached per
      key-string so rotation in tests takes effect), raising `MfaCryptoError` (a new local exception) with
      a value-free message when the var is missing or a key is malformed; `encrypt_secret(plaintext) -> str`
      and `decrypt_secret(token) -> str` (str in/str out, UTF-8, `InvalidToken` surfaced as
      `MfaCryptoError("ciphertext could not be decrypted with any configured key")`);
      `ip_hash(ip) -> str` = hex HMAC-SHA256 of the normalised IP keyed by `MFA_IP_PEPPER`, raising
      `MfaCryptoError` if the pepper is unset — **not wired into any request path this phase**
      (`# TODO(mfa): wire ip_hash once the trusted-proxy / X-Forwarded-For decision is made —
      see docs/mfa_threat_model.md`, audit conflict #8). Module-level `logger = logging.getLogger(__name__)`
      is permitted but must never be passed a plaintext, ciphertext, key or IP.
      Files: `mfa_crypto.py`
      Verify: `.\.venv\Scripts\python.exe -c "import mfa_crypto"` exits 0 (item 5 adds the real tests).

- [ ] 4. Add `validate_mfa_config(strict=None)` to `mfa_crypto.py`. It collects problems rather than
      failing on the first: `MFA_ENCRYPTION_KEY` present and every key parseable, `MFA_IP_PEPPER`
      present and non-trivial, and `SECRET_KEY` present, >=32 chars and not a known-weak/default value —
      reuse `auth._KNOWN_WEAK_SECRETS` and the `smart_weather`/`change`/`your`/`secret` prefix rule from
      `auth.resolve_secret_key()` (`auth.py:68-88`) rather than duplicating a new list, importing `auth`
      lazily inside the function to avoid an import cycle. `strict` defaults to `not auth.is_debug()`.
      When `strict` and there are problems: raise `MfaConfigError(RuntimeError)` whose message lists the
      offending **variable names and reasons only** (never values). When not strict: `logger.warning`
      the same name-only summary and return the problem list. Returns `[]` when clean.
      Files: `mfa_crypto.py`
      Verify: `.\.venv\Scripts\python.exe -c "import mfa_crypto; print(mfa_crypto.validate_mfa_config(strict=False))"`
      prints a list of variable names with no values (item 5 adds the real tests).

- [ ] 5. Add `tests/test_mfa_crypto.py` and `tests/test_mfa_startup_config.py`, following the
      `tests/test_auth_2fa.py` layout (stdlib `unittest`, `sys.path.insert` of the repo root as
      `conftest.py`/that file already do, `unittest.mock.patch.dict(os.environ, ...)` for every env var —
      **never the real `.env`**, no network, no DB). `test_mfa_crypto.py`: round-trip
      encrypt/decrypt; ciphertext differs from plaintext and differs between calls; missing var,
      empty var, and garbage key each raise `MfaCryptoError` and the assertion checks the message does
      **not** contain the key material; `decrypt_secret` on a foreign token raises `MfaCryptoError`;
      MultiFernet rotation — a value encrypted under key A decrypts when the env is `"B,A"`, and
      `MultiFernet.rotate` output decrypts under `"B"` alone; `ip_hash` is stable, differs per pepper,
      is not the input IP, and raises without `MFA_IP_PEPPER`.
      `test_mfa_startup_config.py`: with `DEBUG=False` and `MFA_ENCRYPTION_KEY` absent,
      `validate_mfa_config()` raises `MfaConfigError`; with all three vars valid it returns `[]`;
      with `DEBUG=True` and everything missing it does not raise and returns a non-empty list;
      a weak/default `SECRET_KEY` is rejected when `DEBUG=False`; and one test asserts the raised
      message contains the variable names but none of the fixture values.
      Files: `tests/test_mfa_crypto.py`, `tests/test_mfa_startup_config.py`
      Verify: `$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -m unittest tests.test_mfa_crypto tests.test_mfa_startup_config -v`
      — all tests pass.

- [ ] 6. Add `tests/test_mfa_schema_sqlite.py` with the hermetic temp-SQLite fixture (decision 1).
      Keep the SQLite translation of the Phase 1 DDL in this test module as a module-level constant
      (test-only; no second production schema file), covering the same 10 tables/columns/indexes with
      SQLite types (`text` PKs instead of `uuid`/`gen_random_uuid()`, `CURRENT_TIMESTAMP` instead of
      `now()`), and add a comment pointing at `supabase/schema.sql` section 10 as authoritative so the
      two are kept in step. Tests: the DDL applies cleanly to a fresh temp DB and re-applying it is a
      no-op (idempotence); the expected table and index names exist in `sqlite_master`; the
      `mfa_factors` UNIQUE(user_id, type, label), the `mfa_used_totp_steps` composite PK and the
      `mfa_pending_enrollments` UNIQUE(user_id) all reject a duplicate with `sqlite3.IntegrityError`;
      the `type` CHECK rejects an unknown factor type. **The plaintext test:** generate a throwaway
      base32-looking secret, store `mfa_crypto.encrypt_secret(secret)` into
      `mfa_factors.secret_encrypted` and `mfa_pending_enrollments.secret_encrypted`, close the
      connection, then read the temp `.db` file as raw bytes and assert the secret's ASCII bytes are
      absent while the row count is 1 — and assert the plaintext is never printed. Use
      `tempfile.mkdtemp` + `addCleanup` for teardown.
      Files: `tests/test_mfa_schema_sqlite.py`
      Verify: `.\.venv\Scripts\python.exe -m unittest tests.test_mfa_schema_sqlite -v` — all pass,
      and the temp directory is gone afterwards.

- [ ] 7. Append the MFA accessor section to `db.py` (decision 5), after `query_df` (ends line 468), under
      a banner comment matching the file's existing `# ---` section style and a note that these are
      Phase 1 additions not yet called by any route. Functions, all using `with transaction() as conn:`
      or `with connect() as conn:`, `%s` placeholders, `as_utc()` for timestamp parameters, returning
      `Row`/`None`/`list` like the rest of the module, and raising nothing new (`DatabaseUnavailable`
      propagates as everywhere else): `mfa_insert_factor`, `mfa_get_factors(user_id, include_disabled=False)`,
      `mfa_disable_factor`, `mfa_touch_factor_last_used`; `mfa_insert_recovery_codes(user_id, hashes)`,
      `mfa_get_unused_recovery_codes`, `mfa_mark_recovery_code_used`;
      `mfa_try_consume_totp_step(factor_id, time_step)` → `INSERT ... ON CONFLICT DO NOTHING` returning
      `True` when the row was newly inserted (the replay primitive a later phase uses);
      `mfa_record_attempt`, `mfa_count_recent_attempts(..., since)`; `mfa_log_event` (INSERT only —
      no update/delete helper is provided, enforcing the append-only intent, with a comment saying so);
      `trusted_device_insert`, `trusted_device_find_by_token_hash`, `trusted_device_revoke`;
      `mfa_challenge_insert`, `mfa_challenge_get`, `mfa_challenge_bump_attempts`, `mfa_challenge_set_state`,
      `mfa_challenge_delete_expired`; `mfa_pending_enrollment_upsert` (`ON CONFLICT (user_id) DO UPDATE`),
      `mfa_pending_enrollment_get`, `mfa_pending_enrollment_delete`;
      `mfa_outbox_enqueue`, `mfa_outbox_claim_pending`, `mfa_outbox_mark_sent`;
      `mfa_reset_request_insert`, `mfa_reset_request_get_active`, `mfa_reset_request_set_status`.
      Every docstring states that the function never logs its arguments. No existing line in `db.py`
      changes — in particular `connect()`, `transaction()` and `_get_pool()` are untouched.
      Files: `db.py`
      Verify: `.\.venv\Scripts\python.exe -c "import db; print(len([n for n in dir(db) if n.startswith(('mfa_','trusted_device_'))]))"`
      imports cleanly (no psycopg2 needed at import time) and prints the expected count; then the
      baseline command `$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -m unittest tests.test_auth_2fa tests.test_admin_security -v`
      shows the **same** 4 failures / 13 skips / 2 errors as the audit baseline — no new ones.

- [ ] 8. Wire the startup guard into `app_clean.py` as one additive block immediately after the existing
      `auth.init_app(app)` / `app.register_blueprint(admin_routes.admin_bp)` lines (around line 88):
      `import mfa_crypto` then `mfa_crypto.validate_mfa_config()` — which, per item 4, is strict only
      when `DEBUG`/`FLASK_DEBUG` is falsey, so `DEBUG=True` local runs and the whole existing test suite
      (`tests/test_auth_2fa.py:16` sets `DEBUG=True`) behave exactly as before. **Do not touch
      `app_clean.py:73`** (audit finding 6 / C1); add
      `# TODO(mfa): SECRET_KEY still falls back to a weak constant at app_clean.py:73; resolve_secret_key()
      is dead code. Rewiring is deferred pending the user's decision — see docs/mfa_threat_model.md`.
      If the wiring cannot be done without editing line 73, **STOP and report** — leave the importable
      check plus item 5's tests in place and record the wiring as deferred.
      Note for the release: with `DEBUG=False` a deployment that has not set `MFA_ENCRYPTION_KEY`,
      `MFA_IP_PEPPER` and a strong `SECRET_KEY` will now refuse to start. That is the brief's explicit
      requirement and the only production-visible behaviour change in Phase 1; call it out in the
      final report.
      Files: `app_clean.py`
      Verify: `.\.venv\Scripts\python.exe -c "import ast; ast.parse(open('app_clean.py',encoding='utf-8').read())"`
      exits 0, and `$env:DEBUG="True"; .\.venv\Scripts\python.exe -m unittest tests.test_auth_2fa -v`
      is unchanged from the baseline. A full app import is not attempted (the DL/GEE/psycopg2 deps are
      absent in `.venv`); state that in the report.

- [ ] 9. Create `scripts/rotate_mfa_key.py`, following `scripts/create_admin.py`'s conventions
      (shebang, usage-example docstring, `sys.path.insert` of the repo root, optional `dotenv`
      `load_dotenv()`, `argparse`, `die()` on error). Docstring documents the rotation procedure:
      generate a new Fernet key, set `MFA_ENCRYPTION_KEY="<new>,<old>"`, run the script, then drop the
      old key once the printed re-encrypted count equals the row count. Structure: pure
      `rotate_rows(conn, placeholder, dry_run)` that, for `mfa_factors` and `mfa_pending_enrollments`,
      selects `(id, secret_encrypted)` where the column is not null, decrypts with the MultiFernet
      (all keys), re-encrypts with the current primary, and writes back — skipping rows already under
      the primary key (so re-running is **idempotent**; detect with `MultiFernet.rotate`/a primary-only
      `Fernet.decrypt` probe, not by comparing ciphertext); plus `main()` that opens
      `db.transaction()` with `placeholder="%s"` so the whole run commits or rolls back as one
      transaction. Flags: `--dry-run` (no writes, same counts printed), `--force` (required when
      `MFA_ENCRYPTION_KEY` holds only one key — refuse otherwise, with the reason and the documented
      meaning of `--force`). Output is **counts only**: scanned / already-current / re-encrypted /
      skipped-null, never an id-plus-secret pair and never any key or ciphertext. If a row cannot be
      decrypted with any key, fail clearly with the table name and row id, no values, non-zero exit.
      Files: `scripts/rotate_mfa_key.py`
      Verify: `.\.venv\Scripts\python.exe scripts\rotate_mfa_key.py --help` exits 0; running it without
      `--force` under a single-key `MFA_ENCRYPTION_KEY` exits non-zero with the refusal message
      (item 10 covers the behavioural tests).

- [ ] 10. Add `tests/test_rotate_mfa_key.py`, hermetic, driving item 9's `rotate_rows` against a temp
      SQLite DB (reuse item 6's DDL constant by importing it from `tests.test_mfa_schema_sqlite`, with a
      `try/except ImportError` fallback to a plain-module import so both discovery and direct
      `python -m unittest tests.test_rotate_mfa_key` work) and `placeholder="?"`. Tests: a secret stored
      under old key A is readable and re-encrypted to primary B when the env is `"B,A"`, and the rotated
      value then decrypts under `"B"` alone; a second run reports zero re-encryptions (idempotence);
      `--dry-run`/`dry_run=True` leaves the stored ciphertext byte-identical; a row that decrypts under
      no key produces a clear failure naming the table but no value; single-key refusal without
      `--force`; and a test capturing stdout asserts no fixture secret, ciphertext or key appears in the
      printed output. No `db.connect()` is called (psycopg2 is absent) — the test only uses the pure core.
      Files: `tests/test_rotate_mfa_key.py`
      Verify: `.\.venv\Scripts\python.exe -m unittest tests.test_rotate_mfa_key -v` — all pass.

- [ ] 11. Final check and report. Re-run the new modules together and the scoped baseline, and write the
      summary: what was added, the one production-visible change (item 8), the deferred items
      (`app_clean.py:73` rewiring, `ip_hash` not wired, `users.totp_secret` still plaintext and reserved
      as a later-phase migration source), and the exact commands run with their counts.
      Files: none (report only)
      Verify: `$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -m unittest tests.test_mfa_crypto tests.test_mfa_startup_config tests.test_mfa_schema_sqlite tests.test_rotate_mfa_key -v`
      passes with zero failures, and
      `.\.venv\Scripts\python.exe -m unittest tests.test_auth_2fa tests.test_admin_security -v` matches
      the audit baseline exactly (4 failures / 13 skips in the first, 2 errors in the second — no new
      failures). Confirm `git status` shows only the files listed above as modified/added and that
      `.env` is untouched.

## Known gaps and assumptions

- `mfa_outbox`'s payload shape is unspecified in the brief; assumed `kind text` + `payload jsonb` +
  `status text CHECK (status IN ('pending','sent','failed','cancelled'))`. TODO marker added.
- `mfa_events.actor` is assumed to be a free-form text actor reference (`user:<uuid>`, `admin:<uuid>`,
  `system`) rather than an FK, so events survive user deletion. TODO marker added.
- `mfa_login_challenges.state` values assumed `('pending','verified','expired','failed')`.
- The `.venv` cannot reach a database, so none of the Postgres DDL is executed in this phase; the
  SQLite translation in `tests/test_mfa_schema_sqlite.py` is the only automated proof the DDL is
  well-formed. Flagged for a human/psql run before deployment.
