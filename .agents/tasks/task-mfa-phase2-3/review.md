# TOTP replay protection and DB-backed MFA lockout (Phase 2 + 3)

Phase 2 replaces the bool-returning `totp_auth.verify_code` on the three real 2FA routes with a new `verify_totp` that walks the ±1-step window itself, compares each candidate with `hmac.compare_digest`, and claims the matched `(factor_id, time_step)` through `db.mfa_try_consume_totp_step` — a single `INSERT ... ON CONFLICT DO NOTHING RETURNING` inside `db.transaction()`, so the insert *is* the acceptance decision and there is no read-then-write race. Phase 3 adds `mfa_rate_limit.py`, which counts failures from the `mfa_attempts` table (authoritative across workers), locks an account for 15 minutes at 5 failures, doubles the duration per prior lockout in 24 h up to 240 minutes, and never hard-blocks an IP. Decision D8 is applied: `login_limiter.reset()` no longer fires at password success when an MFA step follows, in both `auth.py` and `admin_routes.py`. The security-critical invariants in the brief hold — atomic claim, matched step never revealed, no early exit in the compare, malformed input rejected with zero DB calls inside `verify_totp`, generic lockout text, no raw IP / secret / code anywhere in a log, flash, template or `mfa_events` row, no IP hard-block, patchable 2-second delay, `users.totp_secret` not migrated, `verify_code` bool contract intact, no test deleted or weakened (135 tests, the pre-existing `test_admin_security.py:430` psycopg2 error still the only non-pass).

**Watch for:** the escalating lockout is advertised but not enforced — `_account_decision` only looks back `MFA_WINDOW_MINUTES`, so once the failure rows age out of the 15-minute window the account unlocks regardless of the computed expiry. Measured: a second lockout reports 30.02 minutes and is already open at +16 minutes (confirmed by execution). The doubling chain to 240 minutes is therefore dead code, and the report's "verified with an injected clock" only verified the advertised number. Also confirmed: `/login` records password failures with `user_id=None`, so D6's "password failures count toward the per-account limit" never happens in production, and `MFA_IP_FAILURE_LIMIT` is a knob no code path reads.

**Verdict**: NEEDS_CHANGES

## High-level view

The claim is one statement, the window is walked in the verifier because `pyotp.verify()` cannot report which step matched, the comparison loop has no `break` on either match or mismatch, and the return dict is `{ok, reason, drift_notice}` with the step neither returned nor logged. The 20-round two-thread race against a real sqlite file is the only proof available here: `tests/fake_db.py:120` reports "newly inserted" for every `INSERT ... RETURNING`, so route tests cannot establish atomicity.

Failure modes are split in opposite directions: the replay store fails **closed** (unresolvable anchor or a throwing `consume_step` ⇒ `reason="locked"`, no acceptance, `replay_store_unavailable` logged), while the rate limiter fails **open** (any DB error ⇒ `locked=False`, in-memory `_RateLimiter` still throttling). Both match the plan, and the routes collapse `invalid`, `replay` and `locked` onto the one pre-existing generic message and status.

The lockout arithmetic is where this falls down. The threshold, the "failures since last success" reset, the `mfa_lockout`-derived doubling exponent and the 240-minute cap are all implemented, but the enforcement query is scoped to `now - WINDOW_MINUTES`, so no lockout can outlive the window that produced it. Everything past 15 minutes — the entire escalation ladder — is computed, reported in `retry_after_seconds`, and then not applied. A related weakness sits next to it: `_prior_lockouts_24h` reads only the 200 newest `mfa_events` rows, and every verification now writes an event, so on a busy account the `mfa_lockout` rows that drive the doubling can be pushed out of view.

Per-account protection covers TOTP guessing only. `/login` writes its password failures with `user_id=None` because no profile is resolved on a wrong password, so those rows feed the per-IP delay (inert until `MFA_IP_PEPPER` is set) and nothing else; password guessing is still bounded only by the per-process `login_limiter`. The capability exists and is tested — just never exercised by production code.

The new unauthenticated write path deserves a retention decision. Every failed `/login` inserts an `mfa_attempts` row, and `login_limiter` keys on `ip|email`, so varying the email makes insertion effectively unbounded. Only `mfa_used_totp_steps` carries a pruning `TODO(mfa)`.

Scope discipline held: `supabase/schema.sql`, `mfa_crypto.py` and `app_clean.py`'s secret-key handling are dirty from Phase 0/1 and were not touched here, `secret_encrypted` stays NULL on the anchor row, and the only lines removed from the test suite are the two `verify_code` patches that D1 requires rewriting.

<details>
<summary>Issues (12)</summary>

1. **Lockout cannot outlive the attempt window** — `mfa_rate_limit._account_decision` reads attempts only since `now - WINDOW_MINUTES`, so a 30/60/120/240-minute lockout silently ends after 15 minutes (measured: advertised 30.02 min, open at +16 min). Evaluate the expiry against a lookback of at least `LOCKOUT_MAX_MINUTES` while still counting the threshold inside `WINDOW_MINUTES`, and add a test that re-checks *after* the window has passed.
2. **`retry_after_seconds` overstates the enforced wait** — same root cause; the value is never rendered today, so fix it with issue 1 before anything consumes it.
3. **Doubling exponent can be lost on a chatty account** — `_prior_lockouts_24h` reads `reader(user_id, 200)`, and every verification now writes an `mfa_events` row, so `mfa_lockout` rows can fall outside the 200 newest. Filter by event type in SQL or raise the limit.
4. **Unparseable `created_at` counts as in-window** — `_prior_lockouts_24h` increments on `stamp is None`. Conservative direction, but decide it explicitly rather than by accident.
5. **Password failures never reach the per-account lockout** — `/login` passes `user_id=None`, so D6's "password failures count" is theory-only and the report states it as shipped. Either resolve the id on failure or correct the claim in the report.
6. **`MFA_IP_FAILURE_LIMIT` is a dead knob** — defined, refreshed and returned by `refresh_config()`, read only by a test. The name implies an IP block that deliberately does not exist. Remove it or mark it test-only.
7. **`mfa_attempts` has no retention and an unauthenticated writer** — one row per failed `/login`, and `login_limiter` is keyed on `ip|email`, so varying the email makes growth unbounded. Add a pruning `TODO(mfa)` at minimum.
8. **Malformed codes still cost DB work at the route level** — `verify_totp` is clean, but the handlers run `mfa_rate_limit.check()` and the anchor upsert before the format guard. Gate on `totp_auth.canonical_code(code)` at the top of the three handlers.
9. **`session["mfa_clock_drift"]` is never cleared** — the Bootstrap dismiss is client-side only, so the notice returns on every dashboard load until logout.
10. **`/2fa/setup` records success before the secret is persisted** — `record_success("totp", ...)` runs ahead of the profile load and `UPDATE`, so a failed save still resets the per-account counter. Move it after the write succeeds.
11. **No test covers the `/2fa/setup` lockout path** — the documented 429-vs-redirect deviation lives there and is unasserted, unlike `/2fa/verify` and `/2fa/disable`.
12. **Limiter-key fallback can miss** — `/2fa/verify` rebuilds `f"{ip}|{profile_email}"` while `/login` keys on the submitted lowercased email; a stored email with different case leaves the counter unreset on pre-D8 sessions.

</details>

<details>
<summary>Details</summary>

### The lockout that expires early

`_account_decision` does two things with one query: it counts the failures that establish the threshold, and it finds the `newest_failure` from which the expiry is measured.

```python
since = now - timedelta(minutes=WINDOW_MINUTES)
rows = reader(since, user_id, None, 200) or []
failures, newest_failure = _failures_since_last_success(rows)
if failures < MAX_ATTEMPTS or newest_failure is None:
    return _OPEN
prior = _prior_lockouts_24h(user_id, now, recent_events=recent_events)
expires = newest_failure + timedelta(minutes=_lockout_minutes(max(0, prior - 1)))
```

Both uses share the 15-minute lookback. The moment the fifth failure is older than `WINDOW_MINUTES`, `rows` is empty, `failures` is 0, and the function returns `_OPEN` without ever computing `expires`. The lockout therefore always ends at `last_failure + WINDOW_MINUTES`, whatever `_lockout_minutes` says.

Measured against the real sqlite store, with five failures, a 21-minute gap, then five more:

```
lockout 1 advertised min: 15.02
lockout 2 advertised min: 30.02     <- doubling worked
  +10min locked=True  retry_s=1201
  +14min locked=True  retry_s=961
  +16min locked=False retry_s=0     <- 14 minutes of the lockout never applied
  +29min locked=False retry_s=0
```

`test_second_lockout_within_24h_lasts_longer` compares `retry_after_seconds` immediately after the fifth failure, when the rows are still inside the window, so it passes on the advertised number. `test_lockout_expires_once_the_window_passes` checks at `WINDOW + LOCKOUT + 1` minutes, where the two expiries coincide because the defaults are both 15. Neither test can see the gap. The practical effect is a steady 5 guesses per 15 minutes forever instead of the intended backoff to 4 hours — roughly 480 guesses a day against a 6-digit code rather than 30.

The fix is a wider lookback for the expiry evaluation (`max(WINDOW_MINUTES, LOCKOUT_MAX_MINUTES)`) while keeping the threshold count scoped to the window, plus a test that advances the clock past `WINDOW_MINUTES` but not past the doubled duration.

### Atomicity and the step that stays hidden

`db.mfa_try_consume_totp_step` is a single statement in one transaction, and `verify_totp` treats its boolean as the decision:

```python
claimed = consume(factor_id, matched, user_id)   # INSERT ... ON CONFLICT DO NOTHING RETURNING
...
if not claimed:
    _log(log_event, "totp_replay_rejected", user_id)
    return _result(False, "replay")
```

No `SELECT` precedes it, so there is nothing to race. The sqlite proof uses `BEGIN IMMEDIATE` + `PRIMARY KEY (factor_id, time_step)` + `IntegrityError` as the equivalent gate, one connection per thread, a `Barrier(2)` release and 20 rounds asserting exactly one `ok`, one `replay`, and one row. The `PRAGMA synchronous=OFF` / `journal_mode=MEMORY` trade recorded in the report touches durability only, not the locking and conflict detection the test exercises.

The accepted event distinguishes `totp_verify_ok_exact` from `totp_verify_ok_skew` — one bit of skew in the audit log, no sign and no step number, which is what D3 asks for.

### Fail-closed verification against a fail-open limiter

A partial DB outage removes the lockout (`_account_decision` returns `_OPEN` on `DatabaseUnavailable` or any exception) while the replay path refuses to accept anything (`auth._verify_totp_for_user` never even calls `verify_totp` when the anchor is unresolvable). The two can therefore disagree about the same outage: if the profile query happens to work while `mfa_attempts` does not, login proceeds unthrottled up to the point where the code check rejects everything.

One consequence of the fail-closed anchor is a new hard dependency: every `/2fa/verify` now performs an upsert + select transaction against `mfa_factors` before any code comparison. Combined with `mfa_rate_limit.check()`'s own query, a single malformed one-character code costs two or three round trips. `verify_totp`'s guard is correctly free of DB work (asserted via the store's empty `calls` list), but the route-level ordering gives that guard away. A `canonical_code` check at the top of each handler restores it.

### What the per-account counter actually counts

`_failures_since_last_success` walks newest-first and stops at the first success row, which implements "a success resets the counter" without deleting audit rows, and it does not filter `attempt_type`, so password failures *would* count. In production they do not:

```python
except LoginError as exc:
    login_limiter.hit(key)
    mfa_rate_limit.record_failure("password", user_id=None, ip_hash=ip_hash)
```

With `user_id=None` the row is invisible to every per-account query, and the per-IP path it does feed never locks and is inert while `MFA_IP_PEPPER` is unset. `test_failed_password_attempts_count_toward_the_same_limit` passes a `user_id` and so tests a path no caller takes. The `TODO(mfa)` is at the call site and the deviation is in the report, but the report's D6 section still states the behaviour as shipped.

`record_success("password", ...)` sits on the same branch as `login_limiter.reset(key)`, so a phished password cannot reset the TOTP counter — the deviation from the plan's literal wording is the safer reading. D8 carries the key in the signed pending dict and resets only after `start_session`, in both `auth.py` and `admin_routes.py`. The `/2fa/verify` fallback that rebuilds the key from `pending["email"]` will miss if the stored email's case differs from what was submitted at `/login`, which only affects sessions created before this change.

### Per-IP posture

`Decision.locked` is set only by the per-account branch; `_ip_slowdown` returns a bool and calls the patchable module-level `_sleep`. `IP_FAILURE_LIMIT` exists, is refreshed, is returned by `refresh_config()` — and is read by nothing but `test_ip_is_never_hard_blocked`. An operator who sets `MFA_IP_FAILURE_LIMIT` expecting an IP cap gets silence, which is the opposite of the carrier-NAT reasoning the module docstring explains. The double-delay defect the report describes in §3.1 is fixed: the routes pass `apply_delay=False` to `record_failure` because they already paid in `check()`, with two regression tests pinning both halves.

### Test coverage

135 tests, one non-pass, and that non-pass is the pre-existing `test_admin_security.py:430` `TypeError` from the absent psycopg2 — not fixed, not skipped, not weakened. The only lines removed from `tests/test_auth_2fa.py` are the two `verify_code` patches D1 requires rewriting, an import that gained a name, and a fixture tuple that gained `satellite_map`; existing assertions were extended (`assertNotIn("mfa_clock_drift", s)`, the generic-text check on the invalid-code response). New coverage is strong on the replay semantics, the no-DB guard, the generic-response equivalence between `invalid` and `replay`, the fail-closed anchor, the 429 on a locked `/2fa/verify` with `verify_totp` asserted *not* called, and the D8 counter behaviour in both directions.

Not tested: enforcement of a lockout after `WINDOW_MINUTES` has elapsed (the gap in issue 1); the `/2fa/setup` lockout path, including its documented redirect-instead-of-429; `_prior_lockouts_24h` when `mfa_events` holds more than 200 rows; and anything against real PostgreSQL — `db.mfa_get_or_create_totp_factor_id`, `db.mfa_recent_attempts` and the `ON CONFLICT (user_id, type, label)` anchor upsert have still never executed, which the report flags correctly as needing a staging `psql` run.

### Scope

`supabase/schema.sql`, `app_clean.py` and `requirements.txt` are dirty from Phase 0/1 — the schema hunk is the section 10 block with its own "Phase 1: schema only" header, `app_clean.py` is the `resolve_secret_key()` C1 fix that `context.json` records as already landed, and `edge_case_hardening.py`, `models/*`, `package.json` and `scripts/*` are the unrelated pre-existing dirt the brief says to leave alone. Nothing in this diff migrates, copies, encrypts or clears `users.totp_secret`; the anchor row carries `type='totp'`, a label, and `secret_encrypted` left NULL. `verify_code` changed only its docstring.

</details>

<details>
<summary>File map</summary>

- `totp_auth.py` — `canonical_code`, `verify_totp`, drift derivation helpers; `verify_code` docstring only
- `db.py` — appended `mfa_get_or_create_totp_factor_id`, `mfa_recent_attempts`; retention `TODO(mfa)` on `mfa_try_consume_totp_step`
- `mfa_rate_limit.py` (new) — per-account lockout, per-IP delay, env config, `refresh_config`
- `auth.py` — `_verify_totp_for_user`, `mfa_limiter`, limiter gating in the three 2FA routes, password attempt recording, D8 counter deferral, drift session flag, `_inject` key
- `admin_routes.py` — `login_limiter.reset` moved below the 2FA branch, `limiter_key=key` passed through
- `templates/dashboard.html` — one additive clock-drift alert block
- `tests/mfa_sqlite_store.py` (new) — hermetic real-sqlite store with db.py-compatible signatures
- `tests/test_totp_replay.py` (new) — 16 tests incl. the 20-round two-thread race
- `tests/test_mfa_rate_limit.py` (new) — 27 tests, `_sleep` patched throughout
- `tests/test_auth_2fa.py` — `verify_totp` patches, Phase 2/3 route tests (41 → 49)
- Dirty but not from this phase: `supabase/schema.sql`, `app_clean.py`, `requirements.txt`, `edge_case_hardening.py`, `models/*`, `package.json`, `scripts/*`

Full diff: `git --no-pager diff` plus the untracked files in `git status --short`.

</details>
