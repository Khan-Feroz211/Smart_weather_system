# MFA Phase 2 + 3 — implementation report

Scope: plan steps 1-14 of `.agents/tasks/mfa-phase2-3-plan.md`.
Phase 2 (TOTP replay protection) landed as FEAT-001; Phase 3 (DB-backed rate
limiting and lockout) landed as FEAT-002. **Nothing is committed** — the working
tree is the deliverable and the user reviews it.

Interpreter for every number below: `d:\SWS\Smart_weather_system\.venv\Scripts\python.exe`
(3.11.9) with `$env:PYTHONIOENCODING="utf-8"`. `.env` was never opened; only env
var **names** appear in this report.

---

## 1. Files

**Created**

| File | Purpose |
|---|---|
| `mfa_rate_limit.py` | Phase 3 limiter: per-account lockout + per-IP slowdown, counted from `mfa_attempts` |
| `tests/mfa_sqlite_store.py` | Hermetic real-sqlite store with db.py-compatible accessor signatures (the concurrency proof and the injected clock both need it) |
| `tests/test_totp_replay.py` | Phase 2 tests, incl. the two-thread race (16 tests) |
| `tests/test_mfa_rate_limit.py` | Phase 3 tests (27 tests) |
| `.agents/tasks/mfa-phase2-3-report.md` | this file |

**Changed**

| File | Change |
|---|---|
| `db.py` | §10: `mfa_get_or_create_totp_factor_id`, `mfa_recent_attempts` appended; `mfa_recent_events` gained an additive `event_type=None` filter; `TODO(mfa)` retention notes on `mfa_try_consume_totp_step` and `mfa_record_attempt` |
| `totp_auth.py` | `canonical_code`, `verify_totp` + helpers added; `verify_code` behaviour untouched (docstring only) |
| `auth.py` | `_verify_totp_for_user`, `mfa_limiter`, limiter wiring in the three 2FA routes, `password` attempt recording on `/login`, D8 counter deferral, `_set_pending_2fa(..., limiter_key=)`, drift session flag, `_inject` key; review pass: `canonical_code` gates at the top of the three handlers, one-shot drift flag, `record_success` moved below the setup write, lowercased limiter-key fallback |
| `admin_routes.py` | 3 lines: `login_limiter.reset(key)` moved below the 2FA branch; `limiter_key=key` passed into `_set_pending_2fa` |
| `templates/dashboard.html` | one additive clock-drift alert block |
| `tests/test_auth_2fa.py` | `verify_code` patches rewritten to `verify_totp`; Phase 2 + Phase 3 route tests added (41 → 49); review pass: +6 (malformed-code gates on all three routes, one-shot drift notice, `/2fa/setup` lockout, success-after-persist) → 55 |
| `mfa_rate_limit.py` | cross-FEAT integration fix: `apply_delay` on `check()` / `record_failure()` — see §3.1; review pass: lockout expiry evaluated over the wider lookback, SQL-filtered lockout-event read, `IP_FAILURE_LIMIT` removed, `ATTEMPT_ROW_LIMIT` / `EVENT_ROW_LIMIT` named — see §3.2 |
| `tests/mfa_sqlite_store.py` | `recent_events` mirrors db.py's new `event_type` filter |
| `tests/test_mfa_rate_limit.py` | review pass: +2 tests (lockout enforced past the window, doubling survives a chatty event log); `test_ip_is_never_hard_blocked` no longer references the removed constant (27 → 29) |

**Deliberately untouched** (confirmed with `git diff`): `supabase/schema.sql`,
`mfa_crypto.py`, `app_clean.py`, `db.connect()` / `db.transaction()`,
`users.totp_secret` handling, and the names/APIs of `auth.login_limiter`,
`auth.signup_limiter`, `auth._RateLimiter`.
`app_clean.py` and `supabase/schema.sql` show as dirty in `git status` — that is
Phase 0/1 work that was already uncommitted when Phase 2/3 started, not an edit
from these phases.

---

## 2. Decisions D1-D8 as implemented

**D1 — legacy wrapper + new rich function.** `totp_auth.verify_code(secret, code,
valid_window=1) -> bool` is behaviourally byte-for-byte what it was (only its
docstring changed, marking it legacy/compat-only). `totp_auth.verify_totp(...) ->
{"ok", "reason", "drift_notice"}` carries the replay logic. All three real call
sites in `auth.py` were migrated; `admin_routes.py` never called `verify_code`.

**D2 — replay anchor row, not a migration.** `db.mfa_get_or_create_totp_factor_id`
lazily creates one `mfa_factors` row per user (`type='totp'`,
`label='legacy:users.totp_secret'`, `secret_encrypted = NULL`) and returns its id
as the `factor_id` that keys `mfa_used_totp_steps`. No secret is copied, encrypted,
moved or cleared. Enrolment (`/2fa/setup`) verifies with `factor_id=None` (no
replay store yet). Anchor resolution **fails closed**: `auth._verify_totp_for_user`
logs `replay_store_unavailable` and returns `reason="locked"` without calling
`verify_totp`.

**D3 — clock drift derived from `mfa_events`, no schema change.** Every accepted
code logs `totp_verify_ok_exact` or `totp_verify_ok_skew`; `verify_totp` reads
back `mfa_recent_events(user_id, 25)`, counts the leading run of `_skew`, and at
>= 5 logs `clock_drift_suspected` once. One indexed SELECT per *successful*
verification; never on failure, never per request. The per-user window is never
widened.

**D4 — drift notice on the dashboard.** `session["mfa_clock_drift"]` is set right
after `start_session(...)`; `auth._inject` exposes `mfa_clock_drift_notice` (session
read only, no DB); `templates/dashboard.html` renders one dismissible alert.
`TODO(mfa)`: move it to a real MFA settings page when one exists.

**D5 — `verify_totp` contract.** Implemented exactly as specified: format guard
first with **zero DB calls and no event**; candidate steps `[s-1, s, s+1]`;
per-step `hmac.compare_digest` with **no early break**; injectable
`consume_step` / `log_event` / `recent_events` hooks defaulting to the `db`
functions; `reason` in `("ok", "invalid", "replay", "locked", "expired_pending")`;
the matched step is never returned, logged or hinted at.
`canonical_code` requires exactly `digits` ASCII `0`-`9` (`str.isdigit` is
deliberately avoided — it accepts Arabic-Indic digits).

**D6 — `mfa_rate_limit.py`.** Env-backed config (`MFA_MAX_ATTEMPTS=5`,
`MFA_LOCKOUT_MINUTES=15`, `MFA_IP_SLOWDOWN_AFTER=10`, `MFA_LOCKOUT_MAX_MINUTES=240`,
plus `MFA_WINDOW_MINUTES=15`), `Decision(locked,
retry_after_seconds, delay_applied)`, `GENERIC_LOCKOUT_MESSAGE`, module-level
`_sleep = time.sleep` (patchable), `IP_SLOWDOWN_SECONDS = 2.0`, `hash_ip()`
returning `None` on `MfaCryptoError`, injectable db hooks, and `check()` /
`record_failure()` / `record_success()`. There is no per-IP failure *limit*
setting: no code path hard-blocks an address, so a knob implying one would
mislead an operator (§3.2, issue 6).
Per-account counting = failures recorded **after the user's last success**; the
threshold is counted inside `MFA_WINDOW_MINUTES` while the expiry is evaluated
over a `max(MFA_WINDOW_MINUTES, MFA_LOCKOUT_MAX_MINUTES)` lookback, so a doubled
lockout is actually enforced past the window that produced it (§3.2, issue 1).
The query does **not** filter `attempt_type`, so a `password` failure counts
toward the per-account limit **as soon as it carries a `user_id`** — which
`/login` does not do today, see the deviation note below. Doubling exponent =
`mfa_lockout` events for that user in the last 24 h read from
`db.mfa_recent_events(user_id, limit, event_type)`, filtered in SQL so the busier
event types cannot starve it; `record_failure` logs `mfa_lockout` exactly
when a failure crosses the threshold. Per-IP: delay at
`>= MFA_IP_SLOWDOWN_AFTER` failures, **never** `locked`, and applied once per
request — `check()` and `record_failure()` take `apply_delay` so a route that
gates on `check()` and then records the failure does not pay it twice (§3.1).
Any DB error ⇒ warning + `locked=False`. `auth._RateLimiter` is kept, and `auth.mfa_limiter =
_RateLimiter(10, 300)` is the cheap pre-DB throttle that short-circuits *before*
`mfa_rate_limit.check()` runs a query. `mfa_rate_limit` does not import `auth`.

**D7 — concurrency proof with real threads and real sqlite.**
`tests/mfa_sqlite_store.py` reuses `MFA_SQLITE_DDL` from
`tests/test_mfa_schema_sqlite.py` (one DDL translation, not two), one connection
per thread, `BEGIN IMMEDIATE` + `INSERT` + `sqlite3.IntegrityError` as the atomic
gate. Two threads released by a `threading.Barrier(2)`, 20 rounds, asserting
exactly one `{"ok": True}`, one `{"ok": False, "reason": "replay"}`, and exactly
one row for that step.

**D8 — login counter reset moved to after MFA.** `auth.py` no longer resets
`login_limiter` at password success when an MFA step follows; the key travels in
the pending dict (`_set_pending_2fa(..., limiter_key=None)`, additive keyword-only)
and is reset after `start_session` in `/2fa/verify` and `/2fa/setup`.
`admin_routes.py` mirrors it: `auth.login_limiter.reset(key)` now sits *below* the
`profile.get("totp_secret")` branch.

---

## 3.1 Cross-FEAT integration pass (after both features landed)

FEAT-001 and FEAT-002 both edit `/2fa/verify`, `/2fa/setup` and `/2fa/disable`, so
the four seams between them were audited directly in the source after both had
landed. Three were already correct; one was a defect and was fixed.

| Seam | Finding |
|---|---|
| Both features edit the same three routes | **Correct.** All three follow one order: `hash_ip()` → `mfa_key` → in-memory throttle `or` `mfa_rate_limit.check()` → verify → `record_failure` / `record_success` → `mfa_limiter.reset`. |
| The limiter must not double-count a failure `verify_totp` already recorded | **Correct, no double count.** `verify_totp` only ever writes `mfa_events` (via `log_event`) and `mfa_used_totp_steps` (via `consume_step`); it never writes `mfa_attempts`. The lockout counter reads `mfa_attempts` for failures and `mfa_events` only for `mfa_lockout`, which `verify_totp` never emits. One rejected code therefore produces exactly one `mfa_attempts` failure row and one `mfa_limiter.hit`. |
| `mfa_crypto.ip_hash` raises when `MFA_IP_PEPPER` is unset and must degrade to `ip_hash=None` | **Correct.** Every route goes through `mfa_rate_limit.hash_ip()`, which catches the error and returns `None`; `_ip_slowdown` then returns `False` on a falsey `ip_hash`. No route calls `mfa_crypto.ip_hash` directly. |
| `login_limiter.reset` exactly once, after MFA succeeds | **Correct.** `/login` resets only on the branch where no MFA step follows; `admin_routes.py` resets only below the `totp_secret` branch; `/2fa/verify` and `/2fa/setup` each reset once, after `start_session`. No path resets twice and no path resets before MFA. |

**Defect found and fixed — the per-IP slowdown was applied twice per rejected
code.** The routes gate on `mfa_rate_limit.check()` before verifying, and
`record_failure()` ended with its own `check()` call. Both ran `_ip_slowdown`, so
once an `ip_hash` passed `MFA_IP_SLOWDOWN_AFTER` a single rejected code slept
`2 x IP_SLOWDOWN_SECONDS` = 4 s and issued two redundant `mfa_recent_attempts`
reads. D6 defines the slowdown as 2 seconds, so 4 s was an accident of how the two
features composed, not a designed behaviour.

Fix (surgical, 4 call sites): `check(..., apply_delay=True)` and
`record_failure(..., apply_delay=True)` gained that one keyword, and the three 2FA
routes pass `apply_delay=False` because they have already paid the delay in their
own `check()`. `/login` records a failed password **without** gating on `check()`
first, so it keeps the default and its slowdown is unchanged. All three routes
already discarded `record_failure`'s return value, so nothing downstream changed.
Two regression tests were added (`test_one_request_pays_the_delay_once`,
`test_record_failure_still_delays_on_its_own_by_default`).

Net user-visible effect: a failed 2FA code behind a set `MFA_IP_PEPPER` and a
flooded `ip_hash` bucket now waits the documented 2 s instead of 4 s. Nothing else
changes — no message, status code, lockout threshold or duration is affected, and
the delay is inert in this environment because the pepper is unset.

**Audited and deliberately left alone:**

* `totp_auth._drift_suspected` reads `row["event_type"]` while
  `mfa_rate_limit._field` tolerates both mapping and tuple rows. With `psycopg2`
  present `db.CursorWrapper` wraps every row in `db.Row`, which supports key
  access, so the drift read is correct in production. Hardening it further would
  be defensive code beyond this phase, and drift detection is advisory only.
* `/2fa/setup` answers a lockout with a redirect + the generic flash rather than
  HTTP 429 (plan step 10 says 429 for "the two page routes"). Its POST handler
  redirects on *every* outcome so the follow-up GET can re-render the QR from the
  stashed secret; returning 429 there would need a different response shape. The
  security requirements are met either way — generic message, pending state not
  consumed, nothing revealed — so this is left as a deliberate, documented
  deviation rather than a behaviour change made inside the loop.
* `/2fa/verify` falls back to a reconstructed `f"{ip}|{email}"` limiter key when
  the pending dict has none (a session created before D8 landed); `/2fa/setup`
  simply skips the reset in that case. The asymmetry is harmless — the in-memory
  window expires in 300 s — and matches what FEAT-002 recorded.

---

## 3.2 Review-fix pass (`.agents/tasks/task-mfa-phase2-3/review.md`, 12 findings)

All twelve findings were addressed. Nothing was committed.

**1 + 2 — a lockout could not outlive the attempt window (the real defect).**
`_account_decision` read `mfa_attempts` only since `now - MFA_WINDOW_MINUTES` and
measured the expiry from the newest failure in that same slice, so once the rows
that established a lockout aged out of the 15-minute window the account unlocked —
whatever duration had been computed. The 30/60/120/240-minute escalation was
computed, reported in `retry_after_seconds`, and never applied (the review
measured a lockout advertised as 30.02 min that was open again at +16 min).

Fix: the read now uses a `max(WINDOW_MINUTES, LOCKOUT_MAX_MINUTES)` lookback,
while the **threshold** is still a question about `WINDOW_MINUTES`. Each failure in
the run since the last success is tested as a crossing point — `bisect` counts how
many failures fall in the window ending at it — and the latest `crossing + duration`
wins. `retry_after_seconds` is derived from that same expiry, so the advertised
wait is the enforced wait. `record_failure`'s "did this failure cross the
threshold" read stays scoped to the window, unchanged.
New test: `test_a_longer_lockout_is_enforced_after_the_attempt_window_passes`
(locked at `second + WINDOW + 1`, open once the advertised wait is over).

*User-visible effect, and why it was not escalated:* a repeat offender is now
locked out for the 30/60/…/240 minutes the plan's D6 already specifies and that
the lockout response already advertised, instead of silently 15. This restores
the documented behaviour rather than choosing new behaviour.

**3 — the doubling exponent could be lost on a chatty account.**
`_prior_lockouts_24h` read the 200 newest `mfa_events` rows of *any* type, and
every verification writes one, so the `mfa_lockout` rows could be pushed out of
view. `db.mfa_recent_events` gained an additive third parameter
`event_type=None` that filters in SQL (`tests/mfa_sqlite_store.py` mirrors it),
and the limiter passes `LOCKOUT_EVENT`. New test:
`test_doubling_survives_a_chatty_event_log` (one lockout row + 220 other rows).

**4 — unparseable `created_at`.** Decided explicitly and commented in both places:
an undated row counts toward the threshold (longer lockout = the safe direction
for an audit-log glitch) but can never anchor an expiry, because there is no
instant to measure from. `_failures_since_last_success` now returns
`(timestamps, undated)` to make that split explicit.

**5 — the report's D6 claim.** Corrected above: password failures are recorded
with `user_id=None` and therefore do **not** reach the per-account lockout today.
No code change — resolving the profile id for a failed password would change what
`/login` does on an unauthenticated path, so it stays the user's decision.

**6 — `MFA_IP_FAILURE_LIMIT` was a dead knob.** Removed, along with
`IP_FAILURE_LIMIT` and its entry in `refresh_config()`. Nothing read it but one
test, and its name advertised an IP cap that deliberately does not exist.
`test_ip_is_never_hard_blocked` now piles up `IP_SLOWDOWN_AFTER * 3` failures
instead of comparing against the removed constant — same assertion, no weakening.

**7 — `mfa_attempts` retention.** `TODO(mfa)` added to `db.mfa_record_attempt`
noting the unauthenticated writer (one row per failed `/login`, and
`login_limiter` keys on `ip|email`, so varying the email makes insertion
effectively unbounded) and that only the window plus the lockout tail is ever
read. Pruning belongs with the `mfa_used_totp_steps` job in a later phase.

**8 — malformed codes cost DB work at the route level.** `verify_totp`'s guard
was free of DB work, but the three handlers ran `mfa_rate_limit.check()` (and
`/2fa/verify` the profile load and anchor upsert) first. All three now gate on
`totp_auth.canonical_code(code)` at the top, hit the in-memory `mfa_limiter`, and
return the **same** generic text and status as a wrong code. Measured by tests
asserting `FakeDB`'s statement log is empty and that `mfa_rate_limit.check` /
`totp_auth.verify_totp` were never called. A malformed code is therefore no longer
counted in `mfa_attempts` — by design (plan D5: no security signal, cheapest DoS
vector); the in-process throttle still counts it.

**9 — the drift notice never cleared.** `session["mfa_clock_drift"]` is now
consumed by `auth._inject` (`session.pop`), so the notice is one-shot instead of
returning on every dashboard load until logout; a fresh run of skew-only codes
raises it again. New test: `test_drift_notice_is_shown_once_not_on_every_page_load`.

**10 — `/2fa/setup` recorded success before the secret was persisted.**
`record_success("totp", ...)` moved below the `UPDATE users SET totp_secret`
commit, so a failed save no longer resets the per-account counter. New test:
`test_setup_records_success_only_after_the_secret_is_saved` (the override raises
on the UPDATE; `record_success` is asserted not called).

**11 — `/2fa/setup`'s lockout path was untested.** `test_setup_on_a_locked_account_changes_nothing`
pins the documented redirect-instead-of-429 deviation: generic message, no code
compared, pending state and stashed secret intact, no `totp_secret` write.

**12 — limiter-key fallback could miss.** `/2fa/verify`'s fallback now rebuilds
the key as `f"{ip}|{email.strip().lower()}"`, matching how `/login` builds it, so
a stored address with different case still clears the right counter. Only affects
sessions created before D8 landed.

---

## 3. Behaviour changes (plan §1, as shipped)

1. `/2fa/verify` and `/2fa/disable` reject a code already used for its time step
   (previously replayable for ~90 s — audit C3). `/2fa/setup` is enrolment and is
   not replay-protected (`factor_id=None`).
2. The three 2FA routes are rate-limited and lockable. On lockout:
   `/2fa/verify` → **429** + `GENERIC_LOCKOUT_MESSAGE`, `/2fa/setup` → redirect +
   the same message, `/2fa/disable` → redirect + the same message and **no
   change** to the account. The pending 2FA state is **not** consumed, and no
   response distinguishes "account does not exist", "account has no 2FA",
   "wrong code" or "locked" beyond the existing generic texts.
3. Wrong-length / non-numeric / non-ASCII-digit codes are rejected before any DB
   work — inside `verify_totp` (asserted via the test store's empty `calls` list)
   and, since the review pass, at the top of all three route handlers too, so no
   profile load, limiter read or anchor upsert happens either (asserted via
   `FakeDB`'s empty statement log). They are counted by the in-process
   `mfa_limiter` but not written to `mfa_attempts`.
4. `login_limiter.reset()` no longer fires at password success when an MFA step
   follows (`auth.py`, `admin_routes.py`) — the audit C2 enabler.
5. `_set_pending_2fa` gained keyword-only `limiter_key=None`, and the pending dict
   gained an optional `limiter_key` key. Existing assertions on that dict were
   extended, never removed.
6. A row may now be created in `mfa_factors` (anchor, `secret_encrypted = NULL`)
   for an existing 2FA user on their first verification after this phase.
7. New `mfa_events.event_type` values: `totp_verify_failed`,
   `totp_verify_ok_exact`, `totp_verify_ok_skew`, `totp_replay_rejected`,
   `clock_drift_suspected`, `replay_store_unavailable`, `mfa_lockout`.
   `event_type` is free text (schema.sql:722) — no DDL change.
8. `mfa_attempts` rows are now written on the password path too
   (`attempt_type='password'`, already allowed by the CHECK constraint).

### Deviations from the literal plan (each with its reason)

* **`password` success is recorded only when no MFA step follows.** A success row
  resets the per-account counter (D6), so writing one at password success would
  hand a phished password unlimited code guesses — exactly the hole D8 closes for
  `login_limiter`. The `record_success("password", ...)` call therefore sits on the
  same line of control flow as `login_limiter.reset(key)`.
* **`password` failures are recorded with `user_id=None`, so they do NOT reach the
  per-account lockout today.** `/login` has no profile id when the password is
  wrong, and resolving one would add a query on an unauthenticated path. Those
  rows feed the per-IP counter only, and that counter is inert while
  `MFA_IP_PEPPER` is unset — so **password guessing is still bounded only by the
  per-process `auth.login_limiter`**, not by the DB-backed lockout. The
  per-account mechanism *can* count `password` failures (the query does not
  filter `attempt_type`) and `test_failed_password_attempts_count_toward_the_same_limit`
  proves it, but no production caller supplies the `user_id` that would exercise
  it. D6's "failed password attempts are included" is therefore a capability, not
  a shipped behaviour; closing the gap means resolving the profile id on a failed
  password, which is a product decision left to the user. `TODO(mfa)` at the call
  site.
* **`mfa_rate_limit.refresh_config()`** was added as the supported entry point for
  re-reading the env-backed tunables (plan step 9 asks for env overrides to be
  honoured "via `mock.patch.dict` + the module's reload/refresh entry point").
* **`auth.mfa_limiter` is wired in, not just declared.** It is checked before
  `mfa_rate_limit.check()` in all three routes, which is what makes it a *pre-DB*
  throttle, and it is reset on success alongside `login_limiter`.
* **`tests/mfa_sqlite_store.py` sets `PRAGMA synchronous=OFF` and
  `journal_mode=MEMORY`.** One fsync per INSERT cost ~13 ms and made the Phase 3
  module take 16.8 s. Locking and the `PRIMARY KEY` conflict — the atomic gate the
  concurrency test proves — are unaffected; only crash durability on a temp file
  that is deleted at teardown is traded away. `tests.test_totp_replay` went from
  ~6 s to ~3 s and was re-run three times to confirm the race is still not flaky.
* **`tests/mfa_sqlite_store.py` extras**: `count_recent_attempts`, a `created_at=`
  kwarg on `record_attempt` / `log_event` (so Phase 3 can inject a fixed clock
  instead of sleeping), `ORDER BY created_at DESC, rowid DESC` (sqlite
  `CURRENT_TIMESTAMP` has one-second granularity, so same-second rows would
  otherwise tie in an arbitrary order), and foreign keys left at sqlite's default
  (off), matching `tests/test_mfa_schema_sqlite.py`.
* **`tests/test_auth_2fa.py` fixture additions**: the endpoint name
  `satellite_map` and a test-only `/_probe/dashboard` route that renders the real
  `dashboard.html` (the fixture's `dashboard` rule is a stub lambda, so the drift
  notice could not otherwise be asserted end-to-end). No existing assertion was
  changed.
* **`verify_totp(now=...)`** accepts a datetime or epoch seconds; the plan only
  named `now=None`, and the tests need a fixed instant.

### Privacy note on `limiter_key`

`limiter_key` is the existing `f"{client_ip()}|{email}"` string. It is kept in the
signed session cookie inside the pending-2FA dict, which already carried the same
user's `email` and `username`, and it is never written to the database, a log or a
flash message. Everything that reaches `mfa_attempts` / `mfa_events` is
`ip_hash` only.

---

## 4. `TODO(mfa)` markers left (all reference `docs/mfa_threat_model.md`)

| Where | What |
|---|---|
| `totp_auth.verify_totp` (step 3), `auth.setup_2fa` | enrolment is not replay-protected — no factor row exists yet |
| `mfa_rate_limit` module docstring | `auth.client_ip()` has no X-Forwarded-For handling, so behind a proxy every user collapses into one `ip_hash` bucket |
| `mfa_rate_limit._account_decision` | DB-unavailable fallback degrades open (the in-memory limiter still throttles) |
| `auth._inject` | the drift notice lives on the dashboard until a real MFA settings page exists (D4) |
| `db.mfa_try_consume_totp_step` | `mfa_used_totp_steps` rows are never pruned — a retention job belongs to a later phase |
| `db.mfa_record_attempt` | `mfa_attempts` has no retention and an unauthenticated writer (one row per failed `/login`) — same retention job |
| `auth.login` (password failure) | resolving the profile id for a failed password would let those failures count toward the per-account lockout |

---

## 5. Measured numbers

Scoped command: `python -m unittest <modules>`; `$env:PYTHONIOENCODING="utf-8"` set
for every run. The single pre-existing error is
`tests/test_admin_security.py:430 test_connection_execute_wraps_rows_consistently`
(`TypeError`, psycopg2 absent). It was **never** fixed, skipped or weakened and
remains the only non-pass everywhere below.

| Run | Result |
|---|---|
| Baseline before Phase 2 (`test_auth_2fa` + `test_admin_security`) | Ran **79**, FAILED (errors=1) |
| After Phase 2 / FEAT-001, same two modules | Ran **84**, FAILED (errors=1) |
| Before Phase 3's edits, four modules | Ran **122** in 52.6 s, FAILED (errors=1) |
| After Phase 3 / FEAT-002, four modules | Ran **133** in 35.8 s, FAILED (errors=1) — only the psycopg2 error |
| After the §3.1 integration pass, four modules (`test_auth_2fa` + `test_admin_security` + `test_totp_replay` + `test_mfa_rate_limit`) | Ran **135** in 32.7 s, FAILED (errors=1) — only the psycopg2 error. +2 = the two per-IP delay regression tests |
| **After the §3.2 review-fix pass, same four modules** | **Ran 143 in 22.6 s, FAILED (errors=1)** — only the psycopg2 error. +8 = 6 route tests + 2 lockout/event tests |
| `tests.test_mfa_rate_limit` | Ran **29** in **1.6 s**, OK (27 in 2.8 s before the review pass; 16.8 s before the sqlite pragma fix) |
| `tests.test_totp_replay` | Ran 16 in 1.3 s, OK — the 20-round two-thread race is not flaky (it was also run 3x after the integration pass) |
| Phase 1 regression set (`test_mfa_crypto`, `test_mfa_startup_config`, `test_mfa_schema_sqlite`, `test_rotate_mfa_key`) | Ran **54** in 11.2 s, **OK** (unchanged after the review pass) |
| `python -c "import db, totp_auth, auth, mfa_rate_limit"` | `imports ok`; `refresh_config()` → `{WINDOW 15, MAX_ATTEMPTS 5, LOCKOUT 15, LOCKOUT_MAX 240, IP_SLOWDOWN_AFTER 10}` (no import cycle) |

Per-module counts after the review-fix pass: `test_auth_2fa` 55,
`test_admin_security` 43, `test_totp_replay` 16, `test_mfa_rate_limit` 29.

The 2-second per-IP delay is never actually waited on: `mfa_rate_limit._sleep` is
patched to a list's `append` for the whole Phase 3 module, and the test asserts the
recorded value equals `IP_SLOWDOWN_SECONDS`.

`unittest discover -s tests` was **not** run — it imports the DL/GEE modules and
takes ~10.6 minutes.

---

## 6. What could NOT be verified here

1. **No PostgreSQL and no psycopg2 in this environment.** The SQL text of
   `db.mfa_get_or_create_totp_factor_id`, `db.mfa_recent_attempts` and the
   anchor-row `ON CONFLICT (user_id, type, label)` has **never executed against a
   real database**. `FakeDB` only checks placeholder/parameter counts. Before
   deploy a human must run
   `psql "$SUPABASE_DB_URL" -f supabase/schema.sql` and then exercise `/login`,
   `/2fa/verify`, `/2fa/setup` and `/2fa/disable` in staging, including a real
   lockout and its expiry.
2. **FakeDB is not evidence of atomicity.** `tests/fake_db.py:120` returns a
   non-empty cursor for `INSERT ... RETURNING`, so under FakeDB
   `db.mfa_try_consume_totp_step` always reports "newly inserted". The route tests
   prove wiring only; the atomicity proof is the sqlite two-thread race (D7).
3. **`MFA_IP_PEPPER` is unset here**, so `mfa_crypto.ip_hash` raises and
   `hash_ip()` returns `None` — the per-IP slowdown is inert in this environment
   and in any deployment that leaves the pepper unset. The per-account control is
   unaffected. Set `MFA_IP_PEPPER` in staging/production to enable the slowdown.
4. **Multi-worker behaviour is untested.** The claim that the DB-backed counter is
   authoritative across workers and restarts follows from the counter living in
   `mfa_attempts`, but no multi-worker run was performed.
5. **The doubling chain and the post-window enforcement were verified with an
   injected clock**, not with real elapsed time; a 24-hour real-time test is out
   of scope. The sqlite store exercises the same SQL shapes as `db.py`, but the
   wider lookback query itself has still never run against PostgreSQL.

---

## 7. Open decision for the user (not decided here)

**X-Forwarded-For / trusted proxy.** `auth.client_ip()` returns
`request.remote_addr` with no `X-Forwarded-For` handling, by instruction. Behind a
reverse proxy or CDN that means every request shares one `ip_hash`, so the per-IP
slowdown would throttle everyone once the bucket fills — and, conversely, a
spoofed header would let an attacker pick their own bucket if the header were
trusted blindly. Nothing here hard-blocks an IP, so the blast radius is a 2-second
delay, and the per-account lockout is the primary control. A trusted-proxy policy
(which hop to trust, how many to strip) remains an open decision —
`docs/mfa_threat_model.md:135`.

**Should a failed password count toward the per-account lockout?** It currently
cannot: `/login` records the failure with `user_id=None` because no profile is
resolved for a wrong password (§3.2, issue 5). Making it count means looking up
the account on an unauthenticated path — one extra query per failed login, and a
new way for an attacker to lock a victim's account out with deliberately wrong
passwords. Password guessing stays bounded by `auth.login_limiter` (8 attempts /
300 s per `ip|email`, per process) until this is decided.

Related deployment context: carrier NAT is the norm in rural Pakistan, so one
`ip_hash` can represent thousands of unrelated farmers. That is why the per-IP
control is a delay and **never** a block, and why the per-account counter is the
primary defence.
