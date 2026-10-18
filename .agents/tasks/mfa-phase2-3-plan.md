# MFA Phase 2 + 3 — implementation plan (TOTP replay protection, DB-backed rate limiting/lockout)

Planning only; no code was written in this step. Grounded in files actually read:
`totp_auth.py`, `auth.py`, `admin_routes.py`, `db.py` (section 10 accessors), `mfa_crypto.py`,
`supabase/schema.sql` §10 (lines 635-840), `tests/test_auth_2fa.py`, `tests/fake_db.py`,
`tests/conftest.py`, `tests/test_mfa_schema_sqlite.py`, `docs/mfa_threat_model.md`,
`.agents/tasks/mfa-phase0-audit.md`, `.agents/tasks/mfa-phase1-report.md`,
`.agents/tasks/mfa-phase1-review.md`, `README.md` ("Running Tests").
`.env` was never opened; only env var **names** appear below.

Interpreter for every command: `d:\SWS\Smart_weather_system\.venv\Scripts\python.exe` (3.11.9),
with `$env:PYTHONIOENCODING="utf-8"`. Do NOT commit. Scoped test command only
(`unittest discover -s tests` takes ~10.6 min; run it at most once, at the very end, if at all).

---

## 0. Decisions made up front (each one is final — do not re-open)

### D1 — `verify_code` compatibility: thin legacy wrapper + a NEW rich function
`totp_auth.verify_code(secret, code, valid_window=1) -> bool` **keeps exactly its current pure,
DB-free behaviour** and gains a docstring marking it legacy/compat-only. A new
`totp_auth.verify_totp(...) -> dict` carries the replay logic.

Why: `tests/test_auth_2fa.py:105-113` (`TestTOTPModule`) calls `verify_code` directly with no DB
patched at all; making it DB-touching would either break those tests or force them to be weakened.
Keeping it pure, and migrating all three real call sites, is the only option that satisfies
"never weaken an assertion".

Call sites to migrate (verified by grep — `verify_code` appears in **exactly** these places):
| File:line | Route | Migration |
|---|---|---|
| `auth.py:618` | `/2fa/setup` POST | `verify_totp(secret, code, factor_id=None, user_id=pending["uid"])` — enrolment, see D2 note |
| `auth.py:688` | `/2fa/verify` POST | `verify_totp(secret, code, factor_id=<anchor>, user_id=profile["id"])` — full replay protection |
| `auth.py:714` | `/2fa/disable` POST | same as `/2fa/verify` (full replay protection) |
| `tests/test_auth_2fa.py:311,323` | `mock.patch.object(totp_auth, "verify_code", ...)` | rewrite to patch `verify_totp` and return `{"ok": True/False, "reason": ...}` |
`admin_routes.py` does **not** call `verify_code` (it only reads `profile["totp_secret"]` at
`admin_routes.py:169` and redirects into `auth.verify_2fa`), so no admin call site changes.

### D2 — legacy plaintext secret ⇒ a "replay anchor" row in `mfa_factors`
`supabase/schema.sql:694` declares `mfa_used_totp_steps.factor_id uuid NOT NULL REFERENCES
public.mfa_factors (id)`. A synthetic/derived uuid (e.g. uuid5 of the user id) therefore **cannot**
be used: the FK would reject it, and dropping the FK is a destructive schema edit this phase forbids.

Decision: lazily create **one anchor row per user** in `mfa_factors`
(`type='totp'`, `label='legacy:users.totp_secret'`, `secret_encrypted = NULL`) and use its id as
`factor_id`. `UNIQUE (user_id, type, label)` (schema.sql:668) makes
`INSERT ... ON CONFLICT (user_id, type, label) DO NOTHING` + `SELECT id` idempotent and race-safe.

This is **not** a migration of `users.totp_secret`: no secret is copied, encrypted, moved or cleared;
`secret_encrypted` stays NULL and `users.totp_secret` remains the one and only secret store. The
anchor is a replay-bookkeeping key, and the later migration phase can fill `secret_encrypted` on that
same row. Replay protection therefore works today without the migration — no conflict to report.

Enrolment (`/2fa/setup`) has no persisted secret and no anchor at code-check time, so it is verified
with `factor_id=None`: format guard + constant-time compare, **no** replay store. Replay there is
harmless (the code only proves possession before the secret is written, and the secret is then
written once). Mark with `TODO(mfa)` referencing `docs/mfa_threat_model.md`.

Fail-closed rule: if the anchor cannot be resolved (`db.DatabaseUnavailable` or any error),
`/2fa/verify` and `/2fa/disable` must NOT accept the code. Return `reason="locked"` (generic, already
in the required vocabulary) and log an `mfa_events` row `replay_store_unavailable`. Safest default;
no bypass is created because the profile load on those routes needs the same DB anyway.

### D3 — clock drift: derived from `mfa_events`, no schema change
Required: 5 **consecutive** off-by-one-only matches ⇒ `clock_drift_suspected` event + a profile
notice; never widen the window per user. Derive it instead of adding a counter column:

* on every accepted code log one event — `totp_verify_ok_exact` or `totp_verify_ok_skew`
  (no sign, no step number, no code);
* read back `db.mfa_recent_events(user_id, limit=25)` (already exists, `db.py:635`), keep only those
  two types, and count the **leading** run of `..._skew`;
* at >= 5 log `clock_drift_suspected` once (skip if a `clock_drift_suspected` event already appears in
  the inspected window) and set a session flag for the notice.

Justification: no additive column is needed, `mfa_events` is already indexed on `(user_id)` and
`(event_type, created_at DESC)` (schema.sql:737-739), the extra SELECT happens **only on a successful
verification** (never per request, never on failure), and the event log is the audit surface the threat
model (`docs/mfa_threat_model.md:99`) already names. A column would also have to live on
`mfa_factors`, which legacy users only have as an anchor row.

### D4 — where the drift notice surfaces
There is no MFA profile page: `/user/<int:user_id>` → `templates/profile.html`
(`app_clean.py:1997-2042`) is the **legacy SQLite** store keyed by an integer id, unrelated to the
Postgres profile UUID, and nothing renders the `/2fa/disable` form today. So:
set `session["mfa_clock_drift"] = True` in the route right **after** `start_session(...)` (which clears
the session), expose it as `mfa_clock_drift_notice` from the existing context processor
`auth._inject` (`auth.py:775`, one additive key, **no DB read**), and render one additive alert block in
`templates/dashboard.html` where flashes already render. `TODO(mfa)`: move it to a real MFA settings
page when Phase 4/5 adds one.

### D5 — `verify_totp` contract
```python
def verify_totp(secret, code, *, factor_id=None, user_id=None, now=None,
                valid_window=1, digits=6,
                consume_step=None,   # default db.mfa_try_consume_totp_step
                log_event=None,      # default db.mfa_log_event
                recent_events=None   # default db.mfa_recent_events
                ) -> dict
```
Returns `{"ok": bool, "reason": "ok"|"invalid"|"replay"|"locked"|"expired_pending",
"drift_notice": bool}`. It never returns, logs or hints at the matched step.
Order of operations (the cheap guard genuinely first):
1. pyotp missing / empty secret / `code` not exactly `digits` ASCII digits ⇒
   `{"ok": False, "reason": "invalid"}` with **zero DB calls and no event** (a required test asserts
   no DB access here; that deliberately overrides "log every failure" for malformed input, which
   carries no security signal and is the cheapest DoS vector);
2. candidate steps `[s-1, s, s+1]` for `s = totp.timecode(now)`; per-step expected code via
   `totp.generate_otp(step)`, compared with `hmac.compare_digest` — loop over **all** candidates with
   no early `break` on mismatch, so the compare count does not depend on which step matched;
3. no match ⇒ log `totp_verify_failed` (reason only) ⇒ `{"ok": False, "reason": "invalid"}`;
4. match and `factor_id is None` (enrolment) ⇒ log `totp_verify_ok_exact|_skew` ⇒ ok;
5. match and `factor_id` given ⇒ `consume_step(factor_id, matched_step, user_id)`;
   `False` ⇒ log `totp_replay_rejected` ⇒ `{"ok": False, "reason": "replay"}`;
   `True` ⇒ log exact/skew, run the D3 drift check ⇒ `{"ok": True, "reason": "ok"}`.
   `db.mfa_try_consume_totp_step` (`db.py:572`) is `INSERT ... ON CONFLICT DO NOTHING RETURNING` inside
   `db.transaction()` — the insert IS the acceptance decision, so there is no read-then-write race;
6. any `db.DatabaseUnavailable`/exception from the consume step ⇒ `{"ok": False, "reason": "locked"}`
   + `replay_store_unavailable` event (D2 fail-closed).
`valid_window` is honoured as ±1 step (equivalent to today's `pyotp` call) but the window is walked by
this function, because `pyotp.TOTP.verify()` cannot report **which** step matched (audit §3).

### D6 — `mfa_rate_limit.py` shape
Authoritative counting from `mfa_attempts`; `auth._RateLimiter` stays as the cheap pre-DB throttle
(not deleted, names unchanged). Public API:
```python
WINDOW_MINUTES / MAX_ATTEMPTS / LOCKOUT_MINUTES / LOCKOUT_MAX_MINUTES / IP_SLOWDOWN_AFTER  # env-backed
GENERIC_LOCKOUT_MESSAGE = "Too many attempts. Please wait and try again."  # no account/MFA hints
_sleep = time.sleep          # module-level, patchable: tests assert without waiting
IP_SLOWDOWN_SECONDS = 2.0
hash_ip(ip) -> str|None      # mfa_crypto.ip_hash; None on MfaCryptoError (pepper unset)
check(user_id=None, ip_hash=None) -> Decision   # namedtuple(locked, retry_after_seconds, delay_applied)
record_failure(attempt_type, user_id=None, ip_hash=None) -> Decision
record_success(attempt_type, user_id=None, ip_hash=None) -> None
```
* **Per-account**: all failed attempts for the user *after their last success* inside the window; at
  `>= MAX_ATTEMPTS` the factor is locked until `last_failure + duration`, where
  `duration = min(LOCKOUT_MINUTES * 2**prior_lockouts_24h, LOCKOUT_MAX_MINUTES)`. Counting failures
  only after the last success is what implements "a successful verification resets the counter"
  without deleting rows. Failed **password** attempts are included because the query does not filter
  `attempt_type` (`mfa_attempts.attempt_type` CHECK already allows `password`, schema.sql:706).
* `prior_lockouts_24h` = count of `mfa_lockout` events for that user in the last 24 h, derived from
  `db.mfa_recent_events` (same no-new-column argument as D3). `record_failure` logs `mfa_lockout`
  exactly when a failure crosses the threshold.
* **Per-IP**: failures for one `ip_hash` in the window; at `>= IP_SLOWDOWN_AFTER` call
  `_sleep(IP_SLOWDOWN_SECONDS)`; **never** returns `locked` for an IP. Module docstring states the
  carrier-NAT reasoning (rural Pakistan: one `ip_hash` can be thousands of farmers; the per-account
  counter is the primary control) per `docs/mfa_threat_model.md:102`.
* `client_ip()` (`auth.py:146`) is used **as-is**; add `TODO(mfa)` noting that behind a proxy every
  user collapses into one bucket (open decision, `docs/mfa_threat_model.md:135`). No trusted-proxy
  policy is invented here.
* `mfa_crypto.ip_hash` raises `MfaCryptoError` when `MFA_IP_PEPPER` is unset — true in the existing
  test environment — so `hash_ip` must swallow it and return `None`, leaving the per-account control
  active. Without this the whole existing 2FA route suite would error.
* `db.DatabaseUnavailable`/any DB error in `check`/`record_*` ⇒ log a warning and return
  `Decision(locked=False, ...)`; the in-memory `_RateLimiter` still throttles. No DB ⇒ the routes
  cannot load a profile anyway, so this opens no bypass. `TODO(mfa)`.
* No import of `auth` from `mfa_rate_limit` (auth imports it) — the raw IP string is passed in.

### D7 — concurrency test design (real threads, real sqlite, no mock)
`db.py` is Postgres-only and `psycopg2` is absent, so the atomic insert is exercised through a
**real sqlite3 file** via the injectable `consume_step` hook (D5):
new `tests/mfa_sqlite_store.py` reuses `MFA_SQLITE_DDL` from `tests/test_mfa_schema_sqlite.py` and
implements the five accessors Phase 2/3 need against a temp DB —
`try_consume_totp_step`, `record_attempt`, `recent_attempts`, `log_event`, `recent_events` — with one
**connection per thread** and `BEGIN IMMEDIATE` + `INSERT`/`IntegrityError` as the atomic gate
(`PRIMARY KEY (factor_id, time_step)` exists in the translation, test_mfa_schema_sqlite.py:74).
The test starts two `threading.Thread`s released by a `threading.Barrier(2)`, both calling
`totp_auth.verify_totp` with the same code and the sqlite-backed `consume_step`, collects both dicts,
and asserts exactly one `{"ok": True}` and one `{"ok": False, "reason": "replay"}`, and that the table
holds exactly one row for that step. Run the race ~20 times in a loop to make a lost race fail loudly.

### D8 — login-counter reset moves to after MFA
`auth.py:412` `login_limiter.reset(key)` on password success is the C2 enabler. Reset only when **no**
MFA step follows; when one does, carry the key in the pending dict (`_set_pending_2fa(..., limiter_key=None)`,
an additive keyword-only parameter) and reset it after `start_session` in `/2fa/verify` and `/2fa/setup`.
Mirror the same two-line change at `admin_routes.py:167` (reset only when `profile.get("totp_secret")`
is falsey), otherwise the admin path keeps the hole. `login_limiter` / `signup_limiter` attribute names
and `_RateLimiter`'s API are unchanged (`admin_routes.py:152,164,167`, `tests/test_auth_2fa.py:89`,
`tests/test_admin_security.py:65` poke them directly).

---

## 1. Exhaustive list of behaviour / signature changes (RULES compliance)

Additive (no existing signature changed):
* `totp_auth.verify_totp`, `totp_auth.canonical_code` (format guard) — new functions.
* `db.mfa_get_or_create_totp_factor_id(user_id, label=...)`, `db.mfa_recent_attempts(since, user_id=None, ip_hash=None, limit=200)` — new accessors appended to db.py §10.
* `mfa_rate_limit.py` — new module. `auth.mfa_limiter = _RateLimiter(10, 300)` — new attribute.
* `auth._inject` gains the key `mfa_clock_drift_notice`; `templates/dashboard.html` gains one alert block.
* New test modules + `tests/mfa_sqlite_store.py`.

Changed behaviour (each deliberate and required):
1. `/2fa/verify`, `/2fa/setup`, `/2fa/disable` now reject a code already used for its time step (`reason="replay"`) — previously replayable for ~90 s (C3).
2. Those three routes are now rate-limited and lockable; on lockout they return the generic message and HTTP 429 and do **not** reveal account/2FA existence (C2).
3. Wrong-length / non-numeric codes are rejected before any DB work (previously only truthiness, `totp_auth.py:72`).
4. `login_limiter.reset()` no longer happens at password success when an MFA step follows (D8) — `auth.py:412`, `admin_routes.py:167`.
5. `_set_pending_2fa` gains keyword-only `limiter_key=None`, and the pending dict gains an optional key. `tests/test_auth_2fa.py:271-279,340-348` assert on that dict — extend, never delete.
6. A row may now be created in `mfa_factors` (anchor, `secret_encrypted = NULL`) for an existing 2FA user on their first verification after this phase (D2).
7. `mfa_events` gains new `event_type` values: `totp_verify_failed`, `totp_verify_ok_exact`, `totp_verify_ok_skew`, `totp_replay_rejected`, `clock_drift_suspected`, `replay_store_unavailable`, `mfa_lockout`. `event_type` is free text (schema.sql:722) — no DDL change.
8. `mfa_attempts` rows are now written on the password path too (`attempt_type='password'`, allowed by the existing CHECK).

Not done (per RULES): `users.totp_secret` is not migrated/encrypted/dropped; `app_clean.py:73` secret-key handling untouched; `db.connect()` and the Postgres-only design untouched (no SQLite backend); no new external network call; no CSRF change (Phase 4/5); the pre-existing
`tests/test_admin_security.py:430` psycopg2 error is left exactly as found.

`TODO(mfa)` markers to leave, all referencing `docs/mfa_threat_model.md`: enrolment has no replay store (D2); `client_ip()` has no X-Forwarded-For handling (D6); DB-unavailable limiter fallback (D6); drift notice lives on the dashboard until a real MFA settings page exists (D4); replay-store rows in `mfa_used_totp_steps` are never pruned (add a retention job in a later phase).

---

## 2. File list

Create: `mfa_rate_limit.py`, `tests/mfa_sqlite_store.py`, `tests/test_totp_replay.py`, `tests/test_mfa_rate_limit.py`.
Modify: `totp_auth.py`, `db.py` (append to §10), `auth.py`, `admin_routes.py` (2 lines), `templates/dashboard.html` (one block), `tests/test_auth_2fa.py` (rewrite the two `verify_code` patches, add route tests).
Do **not** touch: `supabase/schema.sql`, `mfa_crypto.py`, `app_clean.py`, `db.connect()/transaction()`, `users.totp_secret`.

Baseline to re-measure before step 1 and compare after every step:
```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m unittest tests.test_auth_2fa tests.test_admin_security
```
Expected: 79 tests, **errors=1** only — `test_connection_execute_wraps_rows_consistently` (psycopg2 absent, pre-existing). Test count grows as new tests land in `test_auth_2fa.py`; the single pre-existing error must stay the only non-pass.

---

# Implementation Plan

- [ ] 1. Re-measure the scoped baseline and record the exact numbers (tests run, failures, errors, skips) before touching any file, so every later comparison is against a measured number rather than the brief's.
      Files: none (measurement only).
      Verify: `$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -m unittest tests.test_auth_2fa tests.test_admin_security` — 79 tests, errors=1 (`test_connection_execute_wraps_rows_consistently`), no other failure.

- [ ] 2. Append two accessors to `db.py` §10 following the existing style (`%s` placeholders, `transaction()`/`connect()`, docstring stating arguments are never logged): `mfa_get_or_create_totp_factor_id(user_id, label="legacy:users.totp_secret")` (INSERT ... ON CONFLICT (user_id, type, label) DO NOTHING, then SELECT `id::text`; `type='totp'`, `secret_encrypted` left NULL — D2) and `mfa_recent_attempts(since, user_id=None, ip_hash=None, limit=200)` (SELECT `attempt_type, success, created_at` WHERE `created_at >= %s` + optional filters, ORDER BY `created_at DESC` LIMIT, using `as_utc(since)` exactly like `mfa_count_recent_attempts`, db.py:599).
      Files: `db.py`
      Verify: `.\.venv\Scripts\python.exe -c "import db; print(db.mfa_get_or_create_totp_factor_id, db.mfa_recent_attempts)"` exits 0, then the scoped baseline command from step 1 is unchanged.

- [ ] 3. Add `verify_totp` (plus a small `canonical_code` format guard) to `totp_auth.py` exactly as specified in D5: cheap format guard with zero DB calls, candidate steps `[s-1, s, s+1]`, per-step `hmac.compare_digest` with no early break, injectable `consume_step`/`log_event`/`recent_events` hooks defaulting to the `db` functions, structured `{"ok", "reason", "drift_notice"}` return that never exposes the matched step, drift derivation per D3, and fail-closed `reason="locked"` + `replay_store_unavailable` event when the replay store errors. Leave `verify_code` byte-for-byte behaviourally identical, with a docstring marking it legacy/compat-only and pointing at `verify_totp`. Never log or interpolate the secret or the submitted code anywhere.
      Files: `totp_auth.py`
      Verify: `.\.venv\Scripts\python.exe -m unittest tests.test_auth_2fa` — unchanged result (`TestTOTPModule` still passes, proving `verify_code` is untouched in behaviour).

- [ ] 4. Create `tests/mfa_sqlite_store.py`: a hermetic real-sqlite store (temp dir per instance, `addCleanup`-friendly `close()`, `MFA_SQLITE_DDL` imported from `tests.test_mfa_schema_sqlite` so there is one DDL translation, not two) exposing `try_consume_totp_step(factor_id, time_step, user_id=None)` (per-thread connection, `BEGIN IMMEDIATE`, INSERT, `sqlite3.IntegrityError` ⇒ False), `record_attempt`, `recent_attempts`, `log_event`, `recent_events`, plus a `calls` list so tests can assert "no DB access". No network, no credentials, nothing printed.
      Files: `tests/mfa_sqlite_store.py`
      Verify: `.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'tests'); import mfa_sqlite_store as m; s=m.SqliteMfaStore(); print(s.try_consume_totp_step('f1',1,None), s.try_consume_totp_step('f1',1,None)); s.close()"` prints `True False`.

- [ ] 5. Write `tests/test_totp_replay.py` covering every Phase 2 required case against the step-4 store: same code twice ⇒ second rejected with `reason="replay"`; **two real threads** on one temp sqlite DB ⇒ exactly one `ok` and one `replay`, one row in `mfa_used_totp_steps`, looped ~20× (D7); a code from two steps ago rejected (`invalid`); a code from one step ago accepted exactly once; non-numeric and wrong-length input rejected with `store.calls == []` (no DB access at all); the failure path writes an `mfa_events` row whose recorded fields contain neither the submitted code nor the secret; and the `factor_id=None` enrolment path accepts without touching the replay store. Use a locally generated throwaway secret, never printed.
      Files: `tests/test_totp_replay.py`
      Verify: `.\.venv\Scripts\python.exe -m unittest tests.test_totp_replay -v` — all tests pass; re-run twice to confirm the concurrency test is not flaky.

- [ ] 6. Migrate the three `verify_code` call sites in `auth.py` to `verify_totp` (D1), resolving the anchor `factor_id` via `db.mfa_get_or_create_totp_factor_id` on `/2fa/verify` and `/2fa/disable` (fail closed ⇒ generic "Invalid code" flash, HTTP 401/redirect as today) and passing `factor_id=None` on `/2fa/setup`. Map `reason` to the existing generic user-facing messages — `replay`, `invalid` and `locked` must all render the same text and status as today's invalid-code path, so nothing new is revealed. Set `session["mfa_clock_drift"] = True` after `start_session(...)` when `drift_notice` is true, expose `mfa_clock_drift_notice` from `auth._inject`, and render one additive alert block in `templates/dashboard.html` (D4). Keep every other line of those routes as-is.
      Files: `auth.py`, `templates/dashboard.html`
      Verify: `.\.venv\Scripts\python.exe -m unittest tests.test_auth_2fa tests.test_admin_security` — same count/outcome as step 1 except the two `verify_code` patch tests, which are rewritten in step 7.

- [ ] 7. Rewrite the two `mock.patch.object(totp_auth, "verify_code", ...)` sites in `tests/test_auth_2fa.py:311,323` to patch `verify_totp` returning `{"ok": True, "reason": "ok", "drift_notice": False}` / `{"ok": False, "reason": "invalid", "drift_notice": False}`, and add route-level tests: a replayed code on `/2fa/verify` (patched `verify_totp` returning `reason="replay"`) gets the same status and the same generic text as an invalid code, and the drift notice appears on the dashboard when `drift_notice` is true. Keep every existing assertion; only the patched symbol and the new cases change.
      Files: `tests/test_auth_2fa.py`
      Verify: `.\.venv\Scripts\python.exe -m unittest tests.test_auth_2fa tests.test_admin_security` — only the pre-existing psycopg2 error remains; the test count is step 1's plus the new cases.

- [ ] 8. Create `mfa_rate_limit.py` exactly as specified in D6: env-backed config (`MFA_MAX_ATTEMPTS=5`, `MFA_LOCKOUT_MINUTES=15`, `MFA_IP_SLOWDOWN_AFTER=10`, `MFA_LOCKOUT_MAX_MINUTES=240`), `Decision` namedtuple, `GENERIC_LOCKOUT_MESSAGE`, patchable `_sleep`, `hash_ip` that returns `None` on `MfaCryptoError`, injectable db hooks mirroring step 4's store, per-account "failures since last success" counting (password failures included), doubling from `mfa_lockout` events in the last 24 h capped at `LOCKOUT_MAX_MINUTES`, per-IP delay with no hard block, and a module docstring carrying the carrier-NAT reasoning plus the `client_ip()`/X-Forwarded-For `TODO(mfa)`. Never store or log a raw IP — only `ip_hash`.
      Files: `mfa_rate_limit.py`
      Verify: `.\.venv\Scripts\python.exe -c "import mfa_rate_limit as m; print(m.MAX_ATTEMPTS, m.LOCKOUT_MINUTES, m.IP_SLOWDOWN_AFTER, m.LOCKOUT_MAX_MINUTES)"` prints `5 15 10 240`, and `import auth` still works (no import cycle).

- [ ] 9. Write `tests/test_mfa_rate_limit.py` covering every Phase 3 required case against the step-4 sqlite store, with `_sleep` patched to a recorder so nothing actually waits: lockout engages at the 5th failure in 15 min and has expired once the clock is advanced past the window; a second lockout within 24 h doubles the duration and a long chain is capped at 240 min; a success resets the counter (a later failure does not re-lock immediately); a shared `ip_hash` where account A is locked leaves account B unlocked (carrier-NAT); the IP delay fires on the 10th failure, is recorded, and `Decision.locked` stays False no matter how many IP failures accumulate; messages equal `GENERIC_LOCKOUT_MESSAGE` with no account/2FA hint; env overrides are honoured via `mock.patch.dict(os.environ, ...)` + the module's reload/refresh entry point. Inject a fixed `now` rather than sleeping.
      Files: `tests/test_mfa_rate_limit.py`
      Verify: `.\.venv\Scripts\python.exe -m unittest tests.test_mfa_rate_limit -v` — all pass, total runtime well under a second (proves the sleep is not real).

- [ ] 10. Wire the limiter into `auth.py`: add `mfa_limiter = _RateLimiter(10, 300)` as the cheap pre-DB throttle, and in `/2fa/verify`, `/2fa/setup` and `/2fa/disable` call `mfa_rate_limit.check(...)` before the code comparison (locked ⇒ generic message, 429 for the two page routes, redirect-with-generic-flash for `/2fa/disable`, and do **not** consume the pending state), `record_failure` on every rejected code and `record_success` on acceptance, plus `record_failure("password", ...)`/`record_success("password", ...)` around the `/login` password outcome. Apply D8: move the `login_limiter.reset(key)` at `auth.py:412` behind the "no MFA step follows" condition, add keyword-only `limiter_key` to `_set_pending_2fa`, and reset after `start_session` in both 2FA routes. Keep edits surgical — no other line of those routes changes.
      Files: `auth.py`
      Verify: `.\.venv\Scripts\python.exe -m unittest tests.test_auth_2fa tests.test_admin_security` — unchanged outcome (pre-existing error only); confirms the `MFA_IP_PEPPER`-unset and FakeDB paths degrade safely.

- [ ] 11. Apply the matching two-line deferral in `admin_routes.py:167` (reset `login_limiter` only when the profile has no `totp_secret`, so an admin with 2FA keeps the counter until MFA succeeds). Touch nothing else in that file.
      Files: `admin_routes.py`
      Verify: `.\.venv\Scripts\python.exe -m unittest tests.test_admin_security tests.test_auth_2fa` — unchanged outcome.

- [ ] 12. Add the route-level Phase 3 tests to `tests/test_auth_2fa.py`: a locked account on `/2fa/verify` gets 429 and the generic message with the pending state intact; the login counter is **not** reset when the login redirects into `/2fa/verify` (assert via `auth.login_limiter`'s state) and **is** reset after a successful MFA verification; and `/2fa/disable` on a locked account changes nothing. Patch `mfa_rate_limit.check`/`record_*` or point the module's db hooks at the step-4 store; never assert on a raw IP.
      Files: `tests/test_auth_2fa.py`
      Verify: `.\.venv\Scripts\python.exe -m unittest tests.test_auth_2fa tests.test_admin_security tests.test_totp_replay tests.test_mfa_rate_limit` — only the pre-existing psycopg2 error remains.

- [ ] 13. Confirm the Phase 1 suite is still green and do a secret-hygiene pass over every new/changed line: no `print`/`logger`/exception/template/flash path may contain a TOTP secret, a submitted code, a recovery code, a token, a password or a raw IP; `mfa_events` writes carry reasons and hashes only. Add the `TODO(mfa)` markers listed in §1 if any are still missing.
      Files: `totp_auth.py`, `mfa_rate_limit.py`, `auth.py`, `db.py`, `admin_routes.py` (review only)
      Verify: `.\.venv\Scripts\python.exe -m unittest tests.test_mfa_crypto tests.test_mfa_startup_config tests.test_mfa_schema_sqlite tests.test_rotate_mfa_key` — 54 tests, OK; plus the step-12 command still clean.

- [ ] 14. Write the phase report to `.agents/tasks/mfa-phase2-3-report.md`: files created/changed, the D1-D8 decisions as implemented, every behaviour change from §1, the `TODO(mfa)` list, measured before/after test numbers, what could not be verified (no Postgres: the new `db.py` SQL and the anchor-row `ON CONFLICT` are still unexecuted against a real database, so a human must run `psql "$SUPABASE_DB_URL" -f supabase/schema.sql` and exercise them in staging), and the open X-Forwarded-For decision. Do not commit anything.
      Files: `.agents/tasks/mfa-phase2-3-report.md`
      Verify: the report exists and its numbers match the step-12/13 command output pasted into it.

---

## 3. Gaps and assumptions (flagged rather than guessed)

1. No Postgres is reachable, so the two new accessors' SQL and the anchor-row `ON CONFLICT` are unproven at runtime — the same gap Phase 1's review raised (issue 4). Hermetic sqlite tests cover the logic; a staging `psql` run is required before deploy.
2. `mfa_used_totp_steps` grows without bound (no pruning helper exists, and the table has no TTL). Out of scope here; `TODO(mfa)` + a retention job in a later phase.
3. "Profile page" does not exist for MFA (D4) — the drift notice lands on the dashboard via the session flag.
4. `MFA_IP_PEPPER` is absent in the test environment, so `ip_hash` is `None` there and per-IP slowdown is inert under test unless explicitly peppered — tests set a throwaway pepper via `mock.patch.dict`.
5. Behind a proxy, `client_ip()` collapses every user into one `ip_hash` bucket. Used as-is by instruction; the per-account control is the primary defence. Open decision in `docs/mfa_threat_model.md:135`.
6. FakeDB (`tests/fake_db.py:120`) returns a non-empty cursor for `INSERT ... RETURNING`, so under FakeDB the replay primitive always reports "newly inserted" (accept). That is fine for route tests and is exactly why the real atomicity proof uses sqlite (D7) — note it in the report so nobody mistakes FakeDB for evidence.
