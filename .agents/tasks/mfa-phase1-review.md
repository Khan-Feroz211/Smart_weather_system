# MFA Phase 1 review — schema, encryption helper, rotation CLI (additive only)

Phase 1 adds ten `mfa_*` / `trusted_devices` tables to `supabase/schema.sql`, 29 thin accessors to
`db.py`, a `mfa_crypto.py` MultiFernet helper with an importable startup guard, a
`scripts/rotate_mfa_key.py` rotation CLI, four new hermetic test modules (54 tests), and two exact pins
in `requirements.txt`. Everything is appended: the diff of the four modified tracked files
(`app_clean.py` +11, `db.py` +370, `requirements.txt` +6, `supabase/schema.sql` +205) contains
**zero deletions or modifications of existing lines**, `public.users.totp_secret` is untouched and still
plaintext by design, and no existing test was skipped, weakened or deleted. Secret hygiene holds: every
`print`/`logger`/exception path in the new code emits variable names, integer counts, or a table-plus-row-id,
and the tests assert that. The one production-visible behaviour change is intended — with `DEBUG` falsey
the app now refuses to start without `MFA_ENCRYPTION_KEY`, `MFA_IP_PEPPER` and a strong `SECRET_KEY`.

Watch for: that same guard is keyed on `DEBUG` alone, and `.env.example` documents neither new variable
(confirmed) — three existing test modules import `app_clean` and set no `DEBUG` (confirmed), so the
suite's current pass depends on the untracked local `.env`; the Postgres DDL has never been executed,
including the `DO $$ … FOREACH … REVOKE` block (confirmed from the report's "could not be verified");
and all 370 lines of `db.py` SQL are unexercised (confirmed).

**Verdict**: APPROVED

## High-level view

The additive-only boundary is respected cleanly. The schema lands as a self-contained section 10 using
only `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` plus `COMMENT ON`, which is the file's
own re-runnable convention, so no new migration mechanism was invented. Append-only intent for
`mfa_events` is documented in both the table comment and the `db.py` banner, and enforced structurally by
shipping only `mfa_log_event` (INSERT) and `mfa_recent_events` (SELECT) — no update or delete helper
exists. New tables get RLS enabled with no policy and no grant, which is stricter than section 9 and
correct for backend-owned second-factor material.

The crypto design is sound: `MFA_ENCRYPTION_KEY` is a comma-separated list, the first key encrypts via
`MultiFernet`, all keys decrypt, and the MultiFernet is cached keyed on the raw env string so a key change
takes effect without a restart. Malformed keys are reported by list *position*, never value. The rotation
script's idempotence comes from a primary-only decrypt probe rather than ciphertext comparison, which is
the right call given Fernet tokens are non-deterministic, and `--force` is required for a single-key run
with the reasoning documented.

The startup guard's gating is the main open question. `validate_mfa_config()` collects all problems,
reuses `auth._KNOWN_WEAK_SECRETS` and `auth.resolve_secret_key()`'s prefix rule instead of duplicating a
list, and defaults `strict` to `not auth.is_debug()`. That makes it strict in every environment where
`DEBUG`/`FLASK_DEBUG` is absent — including a fresh clone or CI with no `.env` — where
`tests/test_agri_advisor_v6.py`, `tests/test_backend_gaps_and_sockets.py` and
`tests/test_disease_detection_dl.py` would now raise `MfaConfigError` at import. Neither new variable
appears in `.env.example`.

Verification evidence is credible and specific: identical scoped before/after counts (79 tests, 4
failures / 1 error / 13 skips, all traced to pre-existing missing `pyotp`/`qrcode`/`psycopg2`), 54 new
tests green, `CREATE TABLE IF NOT EXISTS` count 27 → 37, and 29 new `db.py` names importable. The gaps
are honestly reported rather than papered over: no Postgres execution, no `import app_clean`, no DB-backed
accessor or `main()` run.

<details>
<summary>Issues (8)</summary>

1. **`.env.example` missing the newly required variables** — the app now refuses to start outside DEBUG
   without `MFA_ENCRYPTION_KEY` and `MFA_IP_PEPPER`, but neither is documented in `.env.example`. Add both
   with a generation command and the comma-separated rotation format.
2. **Startup strictness keyed on `DEBUG` only** — `tests/test_agri_advisor_v6.py`,
   `tests/test_backend_gaps_and_sockets.py` and `tests/test_disease_detection_dl.py` import `app_clean`
   and set no `DEBUG`, so a clean checkout or CI run without `.env` fails at import. Set `DEBUG=True`
   (or the two MFA vars) explicitly in those modules or in `tests/conftest.py`.
3. **Postgres DDL never executed** — section 10 including the `DO $$ … FOREACH t IN ARRAY … EXECUTE
   format('REVOKE ALL …')` block is inspection-only. Run `psql -f supabase/schema.sql` against staging
   before deploy; the `REVOKE` needs the connecting role to own the tables.
4. **370 lines of `db.py` SQL unproven** — uuid casts, `%s::jsonb`, `ON CONFLICT … RETURNING` and
   `rowcount` paths have only been imported, never run. Cover them with an integration test in Phase 2
   before any route depends on them.
5. **Status/state strings unvalidated in the accessors** — `mfa_outbox_mark_sent`,
   `mfa_challenge_insert` and `mfa_reset_request_set_status` pass caller strings straight through and rely
   on the DB CHECK to reject typos at runtime. Validate against a module-level tuple of allowed values.
6. **Encoding asymmetry in the rotation path** — `decrypts_with_primary` encodes with `errors="ignore"`
   while `decrypt_secret` uses `errors="strict"`, so a non-ASCII stored value surfaces as an uncaught
   `UnicodeEncodeError` instead of the clean `RotationError`. Use the same strict encoding in both and
   treat a failure as "not primary".
7. **Test-only SQLite DDL can drift from section 10** — `MFA_SQLITE_DDL` in
   `tests/test_mfa_schema_sqlite.py` is a hand translation with nothing checking it stays in step with
   `supabase/schema.sql`. Add an assertion over the table/index name lists parsed from the real schema file.
8. **`rotate_mfa_key.main()` DB path untested** — only `--help`, the single-key refusal and the pure
   `rotate_rows` core are covered; the `db.transaction()` wrapper and the dry-run `conn.rollback()` inside
   a context manager that then commits are unexercised. Exercise `main()` once a DB is reachable.

</details>

<details>
<summary>Details</summary>

## Additive-only boundary

The full diff of tracked files is append-only. `supabase/schema.sql` gains lines 633+ and nothing before
line 630 moves; `db.py` gains everything after `query_df` with `connect()`, `transaction()` and
`_get_pool()` untouched; `requirements.txt` gains a trailing commented block with no existing pin
reordered; `app_clean.py` gains an 11-line block after `app.register_blueprint(admin_routes.admin_bp)`
and line 73's weak `SECRET_KEY` fallback is verifiably not edited, carrying a `TODO(mfa)` that points at
`docs/mfa_threat_model.md`. `public.users.totp_secret` appears nowhere in the new SQL except as a
commented statement that it is deliberately not migrated, and the rotation script's docstring repeats it.
No session key, route, limiter attribute or `totp_auth.verify_code` signature is referenced.

The other dirty paths in `git status` (`edge_case_hardening.py`'s `age > self.ttl` → `age >= self.ttl`,
`package.json`, `models/*`, `scripts/clean.js`, `scripts/run-python.js`, `test_endpoints.py`) are
unrelated pre-existing VPM-track work, matching the report's claim — I read those diffs and they contain
nothing MFA-related.

## Startup guard and the test-environment coupling

```
auth.is_debug()  ->  DEBUG or FLASK_DEBUG truthy
                     |
   falsey ----------> strict=True  -> MfaConfigError raised at import of app_clean
   truthy ----------> strict=False -> logger.warning, returns problem list
```

`.env` holds `SECRET_KEY, OPENWEATHER_API_KEY, WEATHER_PROVIDER, DEBUG, SENSOR_MODE, TWILIO_*,
NOTIFICATION_ENABLED` (key names only — no value was read) and so has neither MFA variable. The suite
therefore passes locally only because `DEBUG` is present and truthy there. In CI, a container, or a fresh
clone, the three `app_clean`-importing test modules hit the strict path. That is the brief's intended
production behaviour, not a regression in the change itself, but the gating signal and the missing
`.env.example` entries make it an accident waiting to happen. The cheap fix is explicit test env plus
documented variables, not loosening the guard.

Reusing `auth._KNOWN_WEAK_SECRETS` and the `smart_weather`/`change`/`your`/`secret` prefix rule is the
right instinct — one list, one place — at the cost of reaching into a private name, which will break
silently if `auth` renames it.

## Secret hygiene

Inspected every output path. `mfa_crypto` emits: the config summary (variable names and reasons),
`"{ENCRYPTION_KEY_ENV} entry #N is not a valid Fernet key"` (position, never value, with `from None` so
no chained `ValueError` can carry the key into a traceback), and
`"ciphertext could not be decrypted with any configured … key"`. `rotate_mfa_key.py` prints four integer
counts and, on hard failure, `"{table} row {row_id} could not be decrypted…"` — an id, no value. Tests
assert all of it (`test_garbage_key_message_has_no_key_material`,
`test_message_names_variables_but_no_values`, `test_printed_counts_contain_no_secret_material`). `ip_hash`
and `opaque_hash` never return or log their input; `token_hash` is unpeppered SHA-256, justified in the
docstring by the ≥256-bit input entropy.

One design note: `opaque_hash` routes low-entropy values (UA family, IP prefix) through the same
`MFA_IP_PEPPER` as `ip_hash`. Sharing one pepper across field types means a pepper compromise exposes all
pseudonymous audit fields at once, and identical values in different columns produce identical digests.
Acceptable for Phase 1, worth a domain separator (`hmac(pepper, "ua:" + value)`) when these get wired.

## Schema correctness

All ten tables are present with the columns, CHECKs, `UNIQUE (user_id, type, label)`,
`PRIMARY KEY (factor_id, time_step)` and `UNIQUE (user_id)` on pending enrolments that the plan called
for, plus 29 indexes covering every named lookup column and partial indexes for active factors, unused
codes and pending outbox/reset rows. FK delete behaviour is deliberate and consistent:
`ON DELETE CASCADE` everywhere except `mfa_events.user_id`, which is `SET NULL` so the audit trail
survives user deletion, with `actor` kept as free-form text for the same reason.

The file has no dialect split, and the plan's decision to keep the SQLite translation test-only rather
than add a second production schema is the right one. The cost is the drift risk in issue 7: the test
proves *a* DDL creates those tables, not that `supabase/schema.sql` does.

The `DO $$ … FOREACH t IN ARRAY ARRAY[…] LOOP EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL
SECURITY') … format('REVOKE ALL ON public.%I FROM anon, authenticated')` block parses as valid PL/pgSQL by
inspection and uses `%I` quoting correctly, but it has never run. `ALTER TABLE` and `REVOKE` both require
ownership, so this is where a first `psql` run is most likely to fail.

## Rotation design

`--force` for the single-key case is justified in the docstring: with one key a non-decrypting row means
the old key is already gone, so refusing turns a silent data-loss path into a loud one.

Two rough edges. The table/column names are interpolated into f-string SQL; they come only from the
module-level `TARGETS` constant today, so there is no injection path, but a future caller passing
`targets` from config would create one. And the dry-run calls `conn.rollback()` inside
`db.transaction()`, which then commits on exit — harmless because `dry_run=True` issues no `UPDATE`, but
the safety rests on that rather than on the rollback.

## Test coverage

54 new tests across four hermetic modules: `tempfile.mkdtemp` + `addCleanup(shutil.rmtree)`,
`mock.patch.dict(os.environ, …, clear=True)`, locally generated throwaway keys and secrets that are never
printed, no network, no DB, no dev credentials, no `load_dotenv`. The four required scenarios are all
present — plaintext bytes absent from the raw temp `.db` file while the row count is 1 and the ciphertext
still decrypts; startup fails with `MFA_ENCRYPTION_KEY` absent at `DEBUG=False` and returns `[]` when
clean; a value encrypted under key A decrypts under `"B,A"` and `rotate_rows` re-encrypts it to decrypt
under `"B"` alone; the DDL applies, re-applies as a no-op, and every constraint bites.

Not tested: any Postgres execution of section 10; any `db.py` accessor against a real database;
`import app_clean` (only `ast.parse`, because the DL/GEE/psycopg2 deps are absent from `.venv`);
`rotate_mfa_key.main()`'s database path; and the concurrency claim behind
`mfa_try_consume_totp_step` — `INSERT … ON CONFLICT DO NOTHING RETURNING` is the right primitive, but
nothing yet proves two concurrent verifications cannot both win.

## Dependencies

`cryptography==50.0.2` and `argon2-cffi==25.1.0` are exact pins in a new trailing block; both are
confirmed installed at those exact versions in `.venv` (verified via `pip show`), so the pins are real and
Python 3.11-compatible rather than guessed. Argon2id is `PasswordHasher`'s default, matching the
`mfa_recovery_codes.code_hash` comment. Only these two were installed, correctly avoiding a bare
`pip install -r requirements.txt`.

## TODO markers

Ambiguous decisions carry markers and are listed in the report: `ip_hash` unwired pending the
trusted-proxy / `X-Forwarded-For` decision (`TODO(mfa)` in `mfa_crypto.py` citing
`docs/mfa_threat_model.md`), the `mfa_outbox` payload shape (`TODO(mfa)` in the table comment, same
reference), and `app_clean.py:73`'s weak fallback (`TODO(mfa)` next to the guard). Assumed CHECK
vocabularies for `mfa_login_challenges.state`, `mfa_reset_requests.status` and
`mfa_attempts.attempt_type` are recorded in the report's decision table but live in the schema as bare
constraints without a `TODO` pointing at the threat model — a minor documentation asymmetry rather than a
gap.

</details>

<details>
<summary>File map</summary>

- `supabase/schema.sql` — appended section 10: 10 tables, 29 indexes, comments, RLS-on/no-policy block
- `db.py` — appended section "10. MFA (Phase 1) accessors": 29 functions, no existing line changed
- `mfa_crypto.py` *(new)* — MultiFernet encrypt/decrypt, `decrypts_with_primary`, `ip_hash`/`opaque_hash`/`token_hash`, `validate_mfa_config`
- `scripts/rotate_mfa_key.py` *(new)* — idempotent rotation CLI with `--dry-run` / `--force`, counts-only output
- `app_clean.py` — one 11-line additive guard block after the blueprint registration
- `requirements.txt` — two exact pins in a new trailing block
- `tests/test_mfa_crypto.py`, `tests/test_mfa_startup_config.py`, `tests/test_mfa_schema_sqlite.py`, `tests/test_rotate_mfa_key.py` *(new)* — 54 hermetic tests
- Unrelated pre-existing dirt: `edge_case_hardening.py`, `package.json`, `models/*`, `scripts/clean.js`, `scripts/run-python.js`, `test_endpoints.py`

Full diff: `git diff` plus the untracked files above.

</details>
