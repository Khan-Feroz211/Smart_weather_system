# MFA Phase 1 — Implementation Report (schema + encryption), ADDITIVE ONLY

Iteration: **first** (`.agents/tasks/mfa-phase1-review.json` does not exist).
Inputs read before editing: `.agents/tasks/mfa-phase1-impl-plan.md`, `.agents/tasks/mfa-phase0-audit.md`
(present — its findings drove the decisions), `docs/mfa_threat_model.md` (present),
`auth.py`, `totp_auth.py`, `admin_routes.py`, `db.py`, `supabase/schema.sql`,
`notifications.py`, `edge_case_hardening_v2.py`, `tests/test_auth_2fa.py`, `tests/conftest.py`,
`app_clean.py`, `requirements.txt`, `scripts/create_admin.py`.

`.env` was never opened, read, printed or quoted. Only environment variable **names** appear anywhere
in the new code, tests and this report. No new outbound network call was introduced.
Nothing was committed (per the brief).

---

## 1. Files created

| File | Purpose |
|---|---|
| `mfa_crypto.py` | Fernet/MultiFernet encryption helper, `ip_hash`/`opaque_hash`/`token_hash`, and the importable startup guard `validate_mfa_config()` |
| `scripts/rotate_mfa_key.py` | Idempotent key-rotation CLI (`--dry-run`, `--force`) |
| `tests/test_mfa_crypto.py` | 20 tests: round-trip, key errors, MultiFernet rotation, ip/token hashing |
| `tests/test_mfa_startup_config.py` | 14 tests: strict/lenient startup validation |
| `tests/test_mfa_schema_sqlite.py` | 13 tests: DDL applies/idempotent, constraints, **secret not plaintext in the DB file** |
| `tests/test_rotate_mfa_key.py` | 7 tests: rotation, idempotence, dry-run, failure clarity, output hygiene |
| `.agents/tasks/mfa-phase1-report.md` | this report |

## 2. Files changed

| File | Change |
|---|---|
| `supabase/schema.sql` | **Appended** new section `-- 10. MFA / 2FA (Phase 1 …)`. No existing line touched. |
| `db.py` | **Appended** section `# 10. MFA (Phase 1) accessors` after `query_df`. No existing function, signature or behaviour changed; `connect()`, `transaction()`, `_get_pool()` untouched. |
| `app_clean.py` | Added one additive block after `app.register_blueprint(admin_routes.admin_bp)`: `import mfa_crypto` + `mfa_crypto.validate_mfa_config()` plus a TODO. **Line 73 (`app.secret_key = …`) was NOT edited.** |
| `requirements.txt` | Appended a new commented block with two pins. No existing line removed, altered or reordered. |

Other files reported dirty by `git status` (`edge_case_hardening.py`, `models/*`, `package.json`,
`scripts/clean.js`, `scripts/run-python.js`, `test_endpoints.py`, `.coverage`, `_nul`) were already
modified/untracked before this work and were not touched.

## 3. Chosen module names

- Encryption helper: **`mfa_crypto.py`** at the repo root (the project is flat and already uses the
  `<feature>_<role>.py` shape: `totp_auth.py`, `supabase_client.py`).
- Rotation script: **`scripts/rotate_mfa_key.py`** (as specified), following `scripts/create_admin.py`
  conventions (shebang, usage docstring, `sys.path.insert`, optional `load_dotenv`, `argparse`, `die()`).
- Startup guard: **`mfa_crypto.validate_mfa_config(strict=None)`** — a plain importable function, directly
  unit-testable without a Flask app.

## 4. Schema objects added (`supabase/schema.sql`, section 10)

Postgres-only, matching the file's existing conventions (`public.` prefix,
`uuid PRIMARY KEY DEFAULT gen_random_uuid()`, `timestamptz NOT NULL DEFAULT now()`,
`text … CHECK (x IN (…))`, `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`,
`COMMENT ON TABLE/COLUMN`). The file has **no dialect split**, so the SQLite path the tests exercise is
covered by a clearly-marked test-only translation (`MFA_SQLITE_DDL` in `tests/test_mfa_schema_sqlite.py`)
rather than by a second production schema — see §8 for why.

**10 tables** (verified: `CREATE TABLE IF NOT EXISTS` count went 27 → 37):

1. `mfa_factors` — id, user_id, type `CHECK (totp|webauthn|recovery)`, secret_encrypted, label,
   credential_id, public_key, sign_count, transports, created_at, last_used_at, disabled_at,
   `UNIQUE (user_id, type, label)`
2. `mfa_recovery_codes` — id, user_id, code_hash (Argon2id, commented "NEVER plain text"), used_at, created_at
3. `mfa_used_totp_steps` — user_id, factor_id, time_step, used_at, `PRIMARY KEY (factor_id, time_step)`
4. `mfa_attempts` — id, user_id (nullable), ip_hash, attempt_type (CHECK), success, created_at
5. `mfa_events` — id, event_type, actor, user_id, ip_hash, user_agent_hash, created_at — **append-only**
6. `trusted_devices` — id, user_id, token_hash, ua_family, ip_prefix_hash, device_label, created_at,
   expires_at, revoked_at
7. `mfa_login_challenges` — challenge_id `text PRIMARY KEY` (app-minted random 256-bit hex), user_id,
   state (CHECK), attempts, expires_at, created_at
8. `mfa_pending_enrollments` — id, user_id `UNIQUE` (at most one per user), secret_encrypted, created_at, expires_at
9. `mfa_outbox` — id, user_id, kind, payload jsonb, status (CHECK), created_at, sent_at
10. `mfa_reset_requests` — id, user_id, cancel_token_hash, confirm_token_hash, requested_at, eligible_at,
    status (CHECK), used_at

**29 indexes**, covering every lookup column named in the brief: `user_id` on all user-scoped tables;
`expires_at` on `mfa_login_challenges`, `mfa_pending_enrollments`, `trusted_devices`;
`created_at DESC` on `mfa_attempts`, `mfa_events`, `mfa_recovery_codes`, `mfa_factors`,
`mfa_outbox`, `trusted_devices`, `mfa_used_totp_steps`, plus `mfa_attempts (ip_hash, created_at DESC)`,
`trusted_devices (token_hash)` and partial indexes for active factors / unused codes / pending rows.

Plus one `DO $$ … END $$;` block that enables RLS on the 10 new tables and re-revokes `anon` /
`authenticated`. **No policy and no GRANT** is declared for them, so they are backend-owner-only —
deliberately stricter than section 9's `authenticated` read policies, and commented as such.

### Migration mechanism
No second mechanism was invented. `supabase/schema.sql` is the project's documented, re-runnable
migration script (column migrations use `DO $$ … information_schema` guards in section 9a); new *tables*
need only `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, which is exactly that convention.
`db.py` has no migration runner to hook into (it is a connection/transaction layer only), and the
`smart_weather.db` SQLite store is hand-maintained by `app_clean.py` — both left untouched.

## 5. `db.py` accessors added (29 new names)

All built on the existing `transaction()` / `connect()` helpers with `%s` placeholders and `as_utc()` for
timestamp parameters; every docstring states the function never logs its arguments.

`mfa_insert_factor`, `mfa_get_factors`, `mfa_disable_factor`, `mfa_touch_factor_last_used`,
`mfa_insert_recovery_codes`, `mfa_get_unused_recovery_codes`, `mfa_mark_recovery_code_used`,
`mfa_try_consume_totp_step` (the replay primitive: `INSERT … ON CONFLICT DO NOTHING RETURNING`,
True only when newly inserted), `mfa_record_attempt`, `mfa_count_recent_attempts`,
`mfa_log_event` (INSERT only), `mfa_recent_events` (SELECT only),
`trusted_device_insert`, `trusted_device_find_by_token_hash`, `trusted_device_revoke`,
`mfa_challenge_insert`, `mfa_challenge_get`, `mfa_challenge_bump_attempts`, `mfa_challenge_set_state`,
`mfa_challenge_delete_expired`, `mfa_pending_enrollment_upsert` (`ON CONFLICT (user_id) DO UPDATE`),
`mfa_pending_enrollment_get`, `mfa_pending_enrollment_delete`,
`mfa_outbox_enqueue`, `mfa_outbox_claim_pending`, `mfa_outbox_mark_sent`,
`mfa_reset_request_insert`, `mfa_reset_request_get_active`, `mfa_reset_request_set_status`.

**Append-only enforcement:** no update/delete helper exists for `mfa_events`, and the section comment
says so explicitly; a retention job in a later phase is the only thing permitted to delete.

## 6. Encryption helper (`mfa_crypto.py`)

- `MFA_ENCRYPTION_KEY` is a comma-separated Fernet key list; the **first key encrypts, all keys decrypt**
  (`MultiFernet`), which is what makes rotation possible.
- `encrypt_secret(str) -> str`, `decrypt_secret(str) -> str`, `decrypts_with_primary(token) -> bool`
  (idempotence probe for the rotation script), `get_multifernet()` (cached per key-string so a key change
  takes effect without a restart), `primary_fernet()`, `reset_cache()`.
- Malformed keys raise `MfaCryptoError` naming the variable and the **position** of the bad entry
  (`entry #2`), never the value. An undecryptable token raises
  `"ciphertext could not be decrypted with any configured MFA_ENCRYPTION_KEY key"`.
- `ip_hash(ip)` = hex HMAC-SHA256 keyed by `MFA_IP_PEPPER`; `opaque_hash(value)` for UA family / IP prefix;
  `token_hash(token)` = SHA-256 for high-entropy tokens. **`ip_hash` is not wired into any request path
  this phase** (see deferred items).
- `validate_mfa_config(strict=None)` collects *all* problems: `MFA_ENCRYPTION_KEY` present and every key
  parseable, `MFA_IP_PEPPER` present and ≥16 chars, `SECRET_KEY` present, ≥32 chars and not known-weak —
  reusing `auth._KNOWN_WEAK_SECRETS` and `auth.resolve_secret_key()`'s prefix rule (`auth` imported lazily
  to avoid an import cycle) rather than duplicating a list. `strict` defaults to `not auth.is_debug()`:
  strict raises `MfaConfigError`, lenient logs a `logger.warning` and returns the problem list. Returns `[]`
  when clean. Messages contain variable **names and reasons only**.
- Wired into the existing startup path in `app_clean.py` right after `auth.init_app(app)` /
  `app.register_blueprint(admin_routes.admin_bp)`. `app_clean.py:73` was not touched, and the wiring did
  not require touching it.

### One production-visible behaviour change
With `DEBUG`/`FLASK_DEBUG` falsey, a deployment that has not set `MFA_ENCRYPTION_KEY`, `MFA_IP_PEPPER`
and a strong `SECRET_KEY` **will now refuse to start**. That is the brief's explicit requirement and is
the only production-visible change in Phase 1. `DEBUG=True` local runs and the whole existing test suite
(`tests/test_auth_2fa.py` sets `DEBUG=True`) only get a name-only warning — confirmed by the unchanged
baseline below.

## 7. Rotation script (`scripts/rotate_mfa_key.py`)

Covers `mfa_factors.secret_encrypted` and `mfa_pending_enrollments.secret_encrypted`.
Pure core `rotate_rows(conn, placeholder, dry_run, targets)` (caller owns the transaction) plus
`main()` which wraps the whole run in a single `db.transaction()`. Skips rows that already decrypt under
the primary key, so re-running is **idempotent** (detected with a primary-only decrypt probe, not by
comparing non-deterministic ciphertext). `--dry-run` writes nothing and prints the same counts.
`--force` is required when only one key is configured, with the reason documented in the docstring: with a
single key there is nothing to rotate *from*, and a row that fails to decrypt means the old key is already
gone — refusing makes the "I forgot the old key" mistake loud instead of silent.
Output is **counts only** (scanned / already-current / re-encrypted / skipped-null); a row that decrypts
under no key fails with a non-zero exit naming the table and row id and **no values**. The docstring
documents the full procedure: set `MFA_ENCRYPTION_KEY="newkey,oldkey"`, run the script, then drop the old key.

## 8. Dependencies pinned and how the versions were verified

```
cryptography==50.0.2
argon2-cffi==25.1.0
```

- `cryptography==50.0.2` — the version **already installed** in `.venv`
  (`.venv\Scripts\python.exe -m pip list`).
- `argon2-cffi==25.1.0` — not installed; `pip index versions argon2-cffi` reported
  `argon2-cffi (25.1.0)` as the current stable. Installed into `.venv` 3.11.9 successfully
  (`argon2-cffi-25.1.0`, `argon2-cffi-bindings-26.1.0` wheels), which is the Python 3.11 compatibility
  confirmation. Argon2id is `PasswordHasher`'s default, which is what `mfa_recovery_codes.code_hash` is
  documented to hold.
- A bare `pip install -r requirements.txt` was deliberately **not** run (it would pull the heavy DL/GEE
  stack); only these two packages were installed. `pyotp`, `qrcode` and `psycopg2` remain absent from
  `.venv` — pre-existing, and the cause of the pre-existing failures below.

## 9. Verification — real numbers

Interpreter for every command: `d:\SWS\Smart_weather_system\.venv\Scripts\python.exe` (Python 3.11.9),
with `$env:PYTHONIOENCODING="utf-8"`.

### Baseline BEFORE changes (scoped)
```
.\.venv\Scripts\python.exe -m unittest tests.test_auth_2fa tests.test_admin_security -v
-> Ran 79 tests in 6.457s
   FAILED (failures=4, errors=1, skipped=13)
```
Pre-existing, all traced to missing `pyotp` / `qrcode` / `psycopg2` in `.venv`:
`Test2FASetup.test_setup_page_requires_pending_state`, `Test2FAVerify.test_empty_code_shows_error`,
`TestSignupWith2FA.test_signup_with_valid_phone_and_2fa`,
`TestSignupWith2FA.test_signup_success_sets_two_factor_pending`,
plus the error `TestConfigSafety.test_connection_execute_wraps_rows_consistently`
(the KNOWN pre-existing failure named in the brief). None were fixed, skipped, weakened or deleted.

### AFTER changes — same scoped command
```
-> Ran 79 tests in 4.628s
   FAILED (failures=4, errors=1, skipped=13)
```
**Identical to baseline — no new failures.**

### AFTER changes — full discovery (it does finish, contrary to the audit's timeout)
```
.\.venv\Scripts\python.exe -m unittest discover -s tests
-> Ran 308 tests in 636.372s   (~10.6 minutes)
   FAILED (failures=4, errors=1, skipped=14)
```
The 5 non-passing outcomes are exactly the pre-existing ones listed above. The 54 new tests are
discovered (names match the default `test*.py` pattern) and all pass.
Note: no *before* number exists for full discovery — the audit aborted that run at 10 minutes, so only the
scoped command has a true before/after pair.

### New tests
```
.\.venv\Scripts\python.exe -m unittest tests.test_mfa_crypto tests.test_mfa_startup_config \
    tests.test_mfa_schema_sqlite tests.test_rotate_mfa_key -v
-> Ran 54 tests in 9.943s
   OK
```
Covering the four required cases: a stored secret's plaintext bytes are **absent** from the raw temp
`.db` file while the row count is 1 and the ciphertext still decrypts; startup fails without
`MFA_ENCRYPTION_KEY` at `DEBUG=False` and returns `[]` when all three vars are valid; a value encrypted
under key A still decrypts under `"B,A"` and `rotate_rows` re-encrypts it so it decrypts under `"B"`
alone (temp DB); the new tables are created, re-applying the DDL is a no-op, all constraints bite, and the
existing `users` table still works. All tests are hermetic: `tempfile.mkdtemp` + cleanup,
`mock.patch.dict(os.environ, …)`, locally generated throwaway keys/secrets that are never printed, no
network, no database, no dev credentials.

### Import checks
```
python -c "import mfa_crypto, db; print(len([n for n in dir(db) if n.startswith(('mfa_','trusted_device_'))]))"
-> imports ok 29
python -c "import ast; ast.parse(open('app_clean.py',encoding='utf-8').read())"  -> exit 0
python scripts\rotate_mfa_key.py --help                                          -> exit 0
python -c "print(open('supabase/schema.sql').read().count('CREATE TABLE IF NOT EXISTS'))" -> 37 (was 27)
```
A full `import app_clean` was **not** attempted: the DL/GEE/psycopg2 dependencies are absent from `.venv`,
so the module cannot be imported standalone here. The added block was validated by `ast.parse` plus the
unchanged `tests.test_auth_2fa` result; see "could not be verified" below.

### Secret-hygiene inspection
Every `print(` / `logger.` call in `mfa_crypto.py` and `scripts/rotate_mfa_key.py` was inspected:
the only outputs are (a) the name-only configuration-problem summary, (b) integer counts, (c) a failure
message naming a table and row id. No secret, TOTP secret, ciphertext, key, recovery code, token, password
or raw IP is logged, printed or placed in an exception message anywhere in the new code — and the tests
assert this (`test_garbage_key_message_has_no_key_material`,
`test_message_names_variables_but_no_values`, `test_undecryptable_row_fails_clearly_without_values`,
`test_printed_counts_contain_no_secret_material`). `.env` was never read.

## 10. Safest-default decisions and TODO markers

| Decision / TODO | Where |
|---|---|
| `ip_hash` implemented but **not wired** into any request path — `auth.client_ip()` has no X-Forwarded-For handling, so a trusted-proxy decision must come first or every proxied user collapses into one bucket | `# TODO(mfa)` in `mfa_crypto.py`, referencing `docs/mfa_threat_model.md` |
| `mfa_outbox` payload shape unspecified → assumed `kind text` + `payload jsonb` + `status CHECK (pending/sent/failed/cancelled)` | `TODO(mfa)` in the `COMMENT ON TABLE public.mfa_outbox` |
| `mfa_events.actor` is free-form text (`user:<uuid>`, `admin:<uuid>`, `system`) not an FK, so the audit trail survives user deletion; `user_id` is `ON DELETE SET NULL` for the same reason | `COMMENT ON COLUMN public.mfa_events.actor` |
| `mfa_login_challenges.state` assumed `('pending','verified','expired','failed')`; `mfa_reset_requests.status` assumed `('pending','cancelled','completed','expired')` | CHECK constraints in section 10 |
| `mfa_attempts.attempt_type` constrained to `('password','totp','recovery','webauthn','reset')` (brief left it open) | CHECK constraint |
| New tables get **RLS on, no policy, no grant** (backend-only) — stricter than section 9 | comment in section 10 |
| `MFA_IP_PEPPER` minimum length set at 16 chars, `SECRET_KEY` at 32 (matching `auth.resolve_secret_key()`) | `mfa_crypto._MIN_*` constants |
| `app_clean.py:73`'s weak `SECRET_KEY` fallback left exactly as-is; `auth.resolve_secret_key()` remains dead code | `# TODO(mfa)` next to the new guard block |
| Postgres-only DDL with a test-only SQLite translation, rather than adding a SQLite backend to `db.py` | module docstring of `tests/test_mfa_schema_sqlite.py` |

## 11. Deferred to a later phase

- **Migration of the existing TOTP secrets.** `public.users.totp_secret` is still **plaintext** and was
  deliberately not migrated, re-encrypted, re-typed, dropped or removed from `_PROFILE_SQL`. It remains the
  live store for the current flow and is reserved as the migration source for a later phase, once the
  Phase 0 audit's storage shape is confirmed. The legacy SQLite duplicate at `app_clean.py:1116` was also
  left untouched.
- Rewiring `app_clean.py:73` to `auth.resolve_secret_key()` (audit finding C1) — pending the user's decision.
- Wiring `ip_hash` into the request path, after the trusted-proxy / `X-Forwarded-For` decision.
- All *behaviour*: no route, session key, limiter or template reads or writes the new tables yet. Replay
  protection, DB-backed lockout, recovery-code issuance/verification, trusted devices, the server-side
  challenge state machine, the reset flow and the outbox worker are Phases 2–6.
- Argon2id hashing of recovery codes: only the dependency and the `code_hash` column exist in Phase 1.
- Executing the Postgres DDL against a real database.

## 12. Could not be verified

1. **The Postgres DDL was never executed.** `psycopg2` is absent from `.venv` and no database is reachable,
   so section 10 has only been validated by inspection (balanced `DO $$ … END $$;`, +10 `CREATE TABLE`)
   and by the test-only SQLite translation. **A human should run
   `psql "$SUPABASE_DB_URL" -f supabase/schema.sql` before deployment.**
2. **`db.py`'s new accessors were never executed against a database** — only imported. Their SQL is
   unproven at runtime; nothing calls them yet, so the blast radius is nil until Phase 2.
3. **`import app_clean` was not run** (missing DL/GEE/psycopg2 deps). The new startup block is verified by
   `ast.parse` and by the unchanged `tests.test_auth_2fa` baseline, not by a real app boot.
4. **`scripts/rotate_mfa_key.py`'s `main()` DB path was not executed** against a real database; only
   `--help`, the single-key refusal, the missing-variable exit and the pure `rotate_rows` core are tested.
5. The `.venv` still lacks `pyotp`, `qrcode` and `psycopg2`, so the 5 pre-existing non-passing tests cannot
   be re-evaluated here. They were left exactly as found.
