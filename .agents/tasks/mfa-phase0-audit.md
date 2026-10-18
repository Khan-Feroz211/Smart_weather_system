# MFA/2FA Phase 0 — Read-only security audit

Scope: `d:\SWS\Smart_weather_system`. No code was changed. The only file created outside this report is
`docs/mfa_threat_model.md`. No secret, TOTP secret, token or raw IP is reproduced anywhere below; `.env` was
never opened.

---

## 0. Summary first

The existing 2FA is a **thin TOTP veneer over an app whose session key defaults to a publicly known constant and
whose user-facing routes do not require login at all.** Four critical findings, in priority order:

| # | Finding | Rank |
|---|---------|------|
| C1 | `app_clean.py:73` sets `app.secret_key = os.environ.get('SECRET_KEY', 'smart_weather_ai_2024')`. `auth.resolve_secret_key()` exists but is **never called by the app**, and the fallback is a value explicitly listed in `auth._KNOWN_WEAK_SECRETS`. Anyone who knows the default can forge a signed cookie containing `uid` and skip password + 2FA entirely. | **critical** |
| C2 | `/2fa/verify` has **no rate limit and no attempt counter** (`auth.py:665-699`). `login_limiter.reset(key)` is called on password success (`auth.py:412`), so after one correct password an attacker gets unlimited 6-digit guesses against a `valid_window=1` (3-step) window. | **critical** |
| C3 | **No replay protection.** `totp_auth.verify_code` (`totp_auth.py:67-80`) delegates to `pyotp.TOTP.verify(..., valid_window=1)` and nothing records used steps. One captured/shoulder-surfed code is reusable for ~90 s across unlimited parallel requests. | **critical** |
| C4 | **TOTP secrets are stored in plain text** in `users.totp_secret` (`supabase/schema.sql:52`, `:611`; written at `auth.py:629`, read at `auth.py:687` / `admin_routes.py:170` via `_PROFILE_SQL` `auth.py:156`). Any DB read — insider, backup, leaked dump — yields permanent second-factor forgery for every enrolled user. | **critical** |

High findings: admin 2FA is effectively optional and, when enabled, **broken** (H1/H2 below); `/2fa/verify` and
`/2fa/setup` POST have **no CSRF token**; no recovery codes exist at all; pending-2FA state is cookie-only;
disabling 2FA needs no password step-up.

Undetermined: nothing material. Two items are *environment*-undetermined rather than code-undetermined — see §11.

---

## 1. Q1 — Routes that require login, and how the session is established

### Session establishment

`auth.start_session(profile, admin=False)` — `auth.py:342-355`:

```python
session.clear()
session["uid"] = profile["id"]
session.permanent = True
session["login_at"] = now
if admin:
    session["admin_at"] = now
    session["admin_seen"] = now
csrf_token()
```

Keys: `uid` (profile UUID), `login_at`, `admin_at`, `admin_seen`, `_csrf`, plus the transient
`pending_2fa` and `_2fa_setup_secret`.

Cookie flags — `auth.init_app` (`auth.py:800-812`): name `sw_session`, `HTTPONLY=True`,
`SAMESITE="Lax"`, `SECURE` = `SESSION_COOKIE_SECURE` env or `not is_debug()`, lifetime `SESSION_DAYS`
(default 7 days). **Rank: medium** — `SameSite=Lax` and a 7-day lifetime are weak for an admin panel;
the planned challenge cookie needs `SameSite=Strict`.

**There are no server-side session rows.** Everything is in the signed Flask cookie. `logout`
(`auth.py:529-544`) only calls `session.clear()`, so a copied cookie stays valid until expiry.
**Rank: high** — defeats logout-as-revocation and is a direct conflict with the Phase 4 requirement
"Logout invalidates the server-side session row".

Authorisation is re-derived per request from the DB by `current_user()` (`auth.py:160-191`, SQL at
`auth.py:152-158`), which is good: deactivation takes effect on the next request.

### Routes that actually require login

| Guard | Routes |
|---|---|
| `auth.admin_required` (`auth.py:229-249`) — the only real decorator in use | `/admin`, `/admin/dashboard` (`admin_routes.py:209-213`), `/admin/users` `:224`, `/admin/users/active` `:237`, `/admin/users/<id>` `:246`, `/admin/activity` `:258`, `/admin/weather` `:276`, `/admin/alerts` `:319`, `/admin/farms` `:349`, `/admin/fields` `:359`, `/admin/crops` `:370`, `/admin/crop-health` `:384`, `/admin/yield-forecasts` `:398`, `/admin/ai` `:414`, `/admin/analytics` `:464`, `/admin/settings` `:473`, and `/api/admin/*` `:491,500,509,516,523,531,538,545,554,562,586,612,625` |
| inline `current_user()` check | `/api/heartbeat` (`auth.py:751-776`), `/2fa/disable` (`auth.py:701-739`), `/2fa/send-test` (`auth.py:735-749`), `/dashboard/activity/<id>/delete` (`app_clean.py:1882-1889`) |
| `auth.login_required` (`auth.py:251-259`) | **defined but applied to zero routes** (repo-wide grep for `login_required` returns only the definition) |

So `/dashboard` (`app_clean.py:1803`), `/add_user` (`:1945`), `/farms/add` (`:2371`), `/fields/add` (`:2437`),
`/api/sensors/mode` (`:3139`), `/api/sensors/ingest` (`:3189`), `/api/experiments/*` (`:3647,3668,3683`),
`/api/system/cache/clear` (`:3956`) and the `api_routes.py` AgriAdvisor POSTs (`:118,195,239,279,306`) are
**unauthenticated**. **Rank: critical** (independent of MFA, but it makes the Phase 4 test "the state cannot be
skipped by requesting /dashboard directly" currently unsatisfiable without adding the decorator).

Admin sessions additionally get an absolute + idle timeout in `admin_state()` (`auth.py:198-227`):
`ADMIN_SESSION_HOURS` (8) and `ADMIN_IDLE_MINUTES` (60). **Rank: low** (this part is sound).

## 2. Q2 — How TOTP secrets are stored

**Plain text.** Column `public.users.totp_secret text` — `supabase/schema.sql:52` (CREATE TABLE) and
`:609-613` (idempotent migration, comment: "TOTP shared secret; NULL means 2FA is not set up"), with a partial
index at `:628-630`.

- Written: `auth.py:627-632` — `UPDATE users SET totp_secret = %s, two_factor_pending = FALSE WHERE id = %s::uuid`,
  where `%s` is the raw base32 string from `totp_auth.generate_secret()` (`auth.py:654`).
- Read: `_PROFILE_SQL` `auth.py:156` selects `u.totp_secret` on **every single request** via `current_user()`;
  consumed at `auth.py:687` (`/2fa/verify`), `auth.py:709` (`/2fa/disable`), `admin_routes.py:170` (`/admin/login`).
- Cleared: `auth.py:719-721` (`SET totp_secret = NULL`).
- A second, legacy copy of the column exists in the SQLite store: `app_clean.py:1116` adds
  `('totp_secret', 'TEXT DEFAULT NULL')` to `smart_weather.db`.

**Rank: critical** (C4). Aggravating: the secret is also in the **session cookie** during enrolment
(`session["_2fa_setup_secret"]`, `auth.py:651-653`) and rendered into the page/QR (`auth.py:656-663`).
Flask cookies are signed, not encrypted, so the enrolment secret is readable by anyone holding the cookie
or a proxy log. **Rank: high.**

Also note `_PROFILE_SQL` pulling the secret into `g._user` on every request widens exposure to any template or
log that dumps the profile row. **Rank: medium.**

## 3. Q3 — TOTP replay inside the same window

**Yes, replay is possible.** `totp_auth.py:67-80`:

```python
def verify_code(secret: str, code: str, valid_window: int = 1) -> bool:
    ...
    totp = pyotp.TOTP(secret)
    return totp.verify(code.strip(), valid_window=valid_window)
```

- `valid_window=1` → previous, current and next step all accepted → a code is live for ~90 s.
- There is **no used-step/nonce table anywhere**: no `mfa_used_totp_steps` equivalent, no `last_used_step`
  column in `supabase/schema.sql`, no nonce in session.
- `pyotp.verify` uses `hmac.compare_digest` internally, so the comparison itself is constant-time, but
  `verify_code` returns a bare bool and never tells the caller *which* step matched — so even a drop-in
  replay check is impossible without changing the signature.
- Input is not validated for length/charset before use (only truthiness, `totp_auth.py:72`).

**Rank: critical** (C3).

## 4. Q4 — Rate limiting on `/2fa/verify` and `/login`

| Route | Limited? | Evidence |
|---|---|---|
| `/login` | partially | `auth.py:143` `login_limiter = _RateLimiter(8, 300)`; checked `auth.py:400`, hit on failure `:409`, **reset on success `:412`**. Key is `f"{client_ip()}\|{email}"` (`auth.py:399`) |
| `/signup` | yes | `signup_limiter = _RateLimiter(5, 3600)`, `auth.py:442,469` |
| `/admin/login` | partially | `admin_routes.py:152,164,167`, key `f"admin\|{ip}\|{email}"` |
| **`/2fa/verify`** | **NO** | `auth.py:665-699` — no limiter reference, no attempt counter, no lockout |
| **`/2fa/setup`** (code submission) | **NO** | `auth.py:600-663` |
| `/2fa/disable` (code submission) | **NO** | `auth.py:701-733` |

No third-party limiter is registered: repo-wide grep for `Limiter` / `flask_limiter` / `ratelimit` returns
nothing, and `auth.init_app` (`auth.py:800-812`) registers only the blueprint, a context processor and an
`after_request` hook. The only limiter is `auth._RateLimiter` (`auth.py:120-141`) — an **in-memory,
per-process** `defaultdict(deque)`. Under the project's own multi-worker/`socketio` runner
(`app_clean.py:75`) each worker has its own counters, and a restart clears them.

**Rank for `/2fa/verify`: critical** (C2). **Rank for the in-memory limiter design: high** — it cannot
satisfy Phase 3 (persistent per-account lockout, doubling, 24-h memory) and must be replaced by the
DB-backed `mfa_attempts` table.

Keying on raw `client_ip()` (`auth.py:146-147`, `request.remote_addr`) also means **no `ip_hash`** today and
no `X-Forwarded-For` handling, so behind a proxy every user shares one key. **Rank: medium.**

## 5. Q5 — Recovery codes

**They do not exist.** Repo-wide grep for `recovery`, `backup_code`, `recovery_code` finds no table in
`supabase/schema.sql`, no column, no route, no helper in `auth.py` / `totp_auth.py`, and no test.

Consequence: a farmer who loses their phone is permanently locked out, because the only way to clear
`totp_secret` is `/2fa/disable`, which itself demands a valid TOTP code (`auth.py:714`). The only recovery path
is an operator running SQL by hand. **Rank: high** (availability + guarantees an out-of-band, unaudited
reset channel in practice).

## 6. Q6 — Is the 2FA pending state server-side?

**Client-side only.** `_set_pending_2fa` (`auth.py:550-566`):

```python
session.clear()
session["pending_2fa"] = {"uid": ..., "email": ..., "username": ...,
                          "step": step, "next": next_url, "created": time.time()}
```

Expiry is evaluated from the cookie's own `created` field (`auth.py:568-577`, `_PENDING_2FA_SECONDS = 600`,
`auth.py:548`). There is no `mfa_login_challenges` row, no challenge id, no server-side single-use marker,
no attempt counter. Implications:

- The state is only as trustworthy as `SECRET_KEY` — and see C1.
- The pending ticket is **replayable**: nothing is consumed on a failed verification, and the same cookie can
  be presented from several browsers in parallel.
- `session.clear()` inside `_set_pending_2fa` destroys `_csrf`, which is *why* `/2fa/verify` ended up with no
  CSRF token (see Q9).
- TTL is 600 s, not the 300 s Phase 4 specifies.

**Rank: high.**

## 7. Q7 — Does `/admin/login` use the same 2FA logic?

Same *helper*, different and weaker *outcome*. `admin_routes.py:169-174`:

```python
if profile.get("totp_secret"):
    auth._set_pending_2fa(profile, nxt, step="verify")
    flash("Your account has two-factor authentication enabled. Please verify your code.", "info")
    return redirect(url_for("auth.verify_2fa"))
auth.start_session(profile, admin=True)
```

- **H1 — admin 2FA is optional.** An admin with `totp_secret IS NULL` falls through to
  `start_session(profile, admin=True)` (`admin_routes.py:176`) with **password only**. There is no
  `MFA_ENROLL_REQUIRED` concept and no gate forcing admins to enrol. **Rank: high.**
- **H2 — when an admin *does* enable 2FA, admin login is broken.** The redirect lands in `auth.verify_2fa`,
  which on success calls `start_session(profile)` (`auth.py:693`) **without `admin=True`**, so `admin_at` is
  never set. `admin_state()` then returns `"reauth"` (`auth.py:206-208`) and `admin_required` redirects back to
  `/admin/login` (`auth.py:247`) — a loop. The `pending_2fa` dict carries no "admin" marker
  (`auth.py:552-559`), so the information needed to fix it is not even present. **Rank: high** (functional
  break; also the de-facto reason admins are likely to leave 2FA off).
- The `next` target is restricted to `/admin` paths (`admin_routes.py:141-143`) — **rank: low**, this part is fine.
- Admin logout (`admin_routes.py:188-200`) likewise only `session.clear()`s. **Rank: high** (same as Q1).

## 8. Q8 — Paths that skip 2FA, or disable it without re-authentication

| Path | Detail | Rank |
|---|---|---|
| Forged cookie | `uid` set directly in a cookie signed with the known default key (`app_clean.py:73`) — bypasses password *and* 2FA | **critical** |
| Admin without a factor | `admin_routes.py:176` — password-only admin session | **high** |
| `/2fa/disable` — no password | `auth.py:701-733` requires only a current TOTP code, no password, no re-auth window, and no notification to the user. An attacker on a shared/unlocked device with the authenticator app in reach strips the factor permanently. No step-up exists anywhere in the codebase. | **high** |
| `two_factor_pending` is a soft gate | `auth.py:421-424` redirects to `/2fa/setup`, but nothing stops the user simply navigating elsewhere; **no route requires login**, so the dashboard is reachable regardless. | **high** |
| TOTP libs absent ⇒ open door | `auth.py:602-605`, `:667-670`: if `totp_auth.is_available()` is False, `/2fa/setup` and `/2fa/verify` **redirect away** instead of failing closed. Since pyotp is in fact *not installed in `.venv`* (see §11), the practical behaviour today is "2FA page unavailable". For a user with `totp_secret` set, `/login` still redirects to `/2fa/verify` which bounces to `/login` — fail-closed by accident, not by design. For an admin the combination with H1 is worse. | **high** |
| Pre-MFA side effects | `verify_credentials` (`auth.py:286-298`) already does `UPDATE users SET last_login_at = now(), login_count = login_count + 1, is_online = TRUE` **before** any 2FA check, and commits. Password-only attackers get confirmation of valid credentials and pollute the audit trail. | **medium** |
| Enrolment trust-on-first-use | `/2fa/setup` POST pops the secret from the cookie (`auth.py:613`) and persists it on one valid code (`auth.py:627`); there is no `mfa_pending_enrollments` row, so a cookie-holder can enrol a factor they control. | **medium** |
| `/api/heartbeat` CSRF-exempt by design | `auth.py:751-758` documents this; only touches the caller's own timestamp. | **low** |

## 9. Q9 — CSRF protection on every POST route

`auth.csrf_token()` / `csrf_valid()` / `csrf_protect` — `auth.py:93-115`. Token in `session["_csrf"]`,
compared with `hmac.compare_digest` against `csrf_token` form field or `X-CSRF-Token` header.

### `auth.py`

| POST route | Line | Status | Evidence |
|---|---|---|---|
| `/login` | 390 | protected (manual) | `auth.py:392-395` `if not csrf_valid(): ... redirect` |
| `/signup` | 433 | protected (manual) | `auth.py:437-440` |
| `/logout` | 529 | protected | `@csrf_protect` `auth.py:530` |
| **`/2fa/setup`** | 600 | **UNPROTECTED** | `auth.py:600-602` — no decorator, no `csrf_valid()` in the POST branch (`:610-648`). Root cause: `_set_pending_2fa` calls `session.clear()` (`auth.py:551`), wiping `_csrf`. **Rank: high** — a cross-site POST can complete enrolment with an attacker-chosen code/secret pairing. |
| **`/2fa/verify`** | 665 | **UNPROTECTED** | `auth.py:665-667`; the test file even documents it: `tests/test_auth_2fa.py:259` "no CSRF protection on this route". **Rank: high** — enables cross-site login-CSRF / session fixation into the victim's browser. |
| `/2fa/disable` | 701 | protected | `@csrf_protect` `auth.py:702` |
| `/2fa/send-test` | 735 | protected | `@csrf_protect` `auth.py:736` |
| `/api/heartbeat` | 751 | **exempt, deliberately** | `auth.py:755-758` rationale. **Rank: low** |

### `admin_routes.py`

| POST route | Line | Status | Evidence |
|---|---|---|---|
| `/admin/login` | 137 | protected (manual) | `admin_routes.py:145-148` |
| `/admin/logout` | 188 | protected | `@auth.csrf_protect` `:189` |
| `/api/admin/users/<id>/toggle` | 561 | protected | `:563` |
| `/api/admin/users/<id>/delete` | 585 | protected | `:587` |
| `/api/admin/alerts/<kind>/<id>/toggle` | 611 | protected | `:613` |
| `/api/admin/alerts/<kind>/<id>/delete` | 624 | protected | `:626` |

All other `admin_bp` routes are GET-only. `flask_wtf` / `CSRFProtect` is **not used** (not installed, not
imported) — the hand-rolled helper is the whole mechanism; it is correct where applied. Outside the two audited
files, `app_clean.py` POSTs are almost all CSRF-free (only `:1883` uses `@auth.csrf_protect`) — out of scope
for Q9 but relevant to the blast radius.

## 10. Other files read

- `db.py` (468 lines) — **PostgreSQL only**, psycopg2 pool + a `sqlite3.Row`-compatible shim (`db.py:116-274`).
  `db.connect()` raises `DatabaseUnavailable` when `SUPABASE_DB_URL` is unset (`db.py:284`). There is **no
  SQLite path in `db.py`**: the SQLite store (`smart_weather.db`) is maintained separately and directly by
  `app_clean.py` (`:1095-1135`). Phase 1 tables must therefore be added to `supabase/schema.sql`, and the
  Phase 2 test "two concurrent verifications … use two threads and a real SQLite connection" will need its own
  SQLite harness (like `tests/fake_db.py`) because the app's `db` module cannot speak SQLite. **Conflict —
  flagged below.**
- `notifications.py` — Twilio SMS (`:86`), WhatsApp (`:122`), auto-fallback (`:246`), local gateway (`:204`),
  `_is_valid_phone` E.164 check (`:74`). **No OTP is ever sent over these channels today**, which is exactly
  the posture the threat model wants (SMS = notification channel only). `auth.py:735-749` only sends a test
  message. **Rank: low / positive.**
- `edge_case_hardening_v2.py` — cache/degradation/circuit-breaker for *weather data*
  (`FallbackCacheSystem:104`, `CircuitBreaker:353`, `GracefulDegradationManager:523`). **Nothing
  auth-related.** Worth reusing the `CircuitBreaker` shape for the "provider down" policy, but it is not in the
  auth path today.
- `tests/test_auth_2fa.py` — 36 tests. Pins current behaviour that Phase 2-5 will change: plain-text secret
  round-trip (`:316-318`), cookie-only `pending_2fa` dict shape (`:271-279`, `:340-348`), `/2fa/verify` with no
  CSRF token (`:259`, `:300`), `/2fa/disable` with code-only (no password) (`:493-500`), and
  `PROFILE_COLS` including `totp_secret` / `two_factor_pending` (`:37-40`).

---

## 11. Environment and dependencies

`.venv` Python: **3.11.9** (`d:\SWS\Smart_weather_system\.venv\Scripts\python.exe`).

| Package | In `requirements.txt` | Installed in `.venv` |
|---|---|---|
| `pyotp` | yes (`pyotp==2.10.0`) | **NO** |
| `qrcode` | yes (`qrcode==8.2`) | **NO** |
| `cryptography` (Fernet/MultiFernet) | not pinned | **yes, 50.0.2** |
| `argon2-cffi` | no | **NO** |
| `bcrypt` | no | **NO** |
| `py_webauthn` | no | **NO** |
| `flask-wtf` | no | **NO** |
| `psycopg2` | yes | **NO** |
| `Flask` | yes | yes, 2.3.3 |

So: Fernet is available out of the box; **pyotp, qrcode, a password hasher for recovery codes, and py_webauthn
must all be installed**, and the missing `psycopg2` means nothing can touch a real database in this venv.

### Test baseline (as requested)

- `python -m unittest discover -s tests -v` — **did not finish within 10 minutes**; I aborted it and did not
  get a full result. Partial results only, below. (The brief warned the suite is slow; discovery imports
  `test_live.py`, `test_gee_loaders.py`, `test_disease_detection_dl.py` and the DL/network modules, which is
  the likely cause.) **The project instruction "run discover between phases and keep it green" is not
  currently practical at this timeout; the implementer should scope it, e.g. `-p "test_auth*.py"` plus the
  security modules, and say so.**
- `python -m unittest tests.test_auth_2fa -v` → **Ran 36 tests, FAILED (failures=4, skipped=13)**, in 1.5 s.
  Baseline is **not green**. All 17 non-passing outcomes trace to `pyotp`/`qrcode` being absent:
  - skips: every `TestTOTPModule` test and each test guarded by `if not totp_auth.is_available(): skipTest`
  - failures: `Test2FASetup.test_setup_page_requires_pending_state` (`'/login' not found in '/dashboard'` —
    the `is_available()` guard at `auth.py:602-605` redirects to the dashboard instead),
    `Test2FAVerify.test_empty_code_shows_error` (302 ≠ 400, same cause),
    `TestSignupWith2FA.test_signup_with_valid_phone_and_2fa` (400 ≠ 302 — `auth.py:465` rejects the signup
    because pyotp is missing), `TestSignupWith2FA.test_signup_success_sets_two_factor_pending` (same cause).
- `python -m unittest tests.test_admin_security` → **Ran 43 tests, FAILED (errors=2)**, 5.3 s. Both errors are
  environmental, not logic: `test_connection_execute_wraps_rows_consistently` (`TypeError: tuple indices must
  be integers` — psycopg2 absent so the shim is not exercised) and
  `test_weak_secret_gets_random_key_in_debug_and_strong_key_is_kept` (`UnicodeEncodeError` on the `⚠️`
  emoji in `auth.py:83` under the cp1252 Windows console).

**Action for Phase 1, before anything else: `pip install -r requirements.txt` in `.venv` and re-measure the
baseline.** Until pyotp/qrcode/psycopg2 are installed, "keep the suite green" cannot be evaluated, and the
4 failures above will be mistaken for regressions. The emoji `print` at `auth.py:83` should also be made
console-safe or the test run forced to UTF-8 (`PYTHONIOENCODING=utf-8`).

---

## 12. Conflicts with the planned MFA design

Ordered by how much they will hurt.

1. **`SECRET_KEY` resolution must be rewired first.** Phase 1 requires "fail startup if … a non-default
   `SECRET_KEY` is missing when `DEBUG=False`". `auth.resolve_secret_key()` already implements exactly that
   (`auth.py:68-88`) but is dead code; `app_clean.py:73` bypasses it. **Breaking change to `app_clean.py`
   startup** — and any deployment relying on the default key will log everyone out on upgrade. Expected and
   correct, but it must be called out in the release note.
2. **`users.totp_secret` must become ciphertext — or stop being used.** `_PROFILE_SQL` (`auth.py:156`),
   `auth.py:629/687/709`, `admin_routes.py:170`, `app_clean.py:1116` and `tests/test_auth_2fa.py:37-40,316`
   all assume a readable base32 string. Moving the factor into `mfa_factors.secret_encrypted` means the column
   has to be kept (per "keep existing columns working") but reduced to a **migration source only**, with
   `_PROFILE_SQL` no longer selecting it. The existing tests pass plain secrets through `profile(...)` and will
   need extending (not weakening) to cover the new table.
3. **`db.py` is Postgres-only.** The Phase 2 concurrency test explicitly wants "a real SQLite connection".
   There is no SQLite adapter in `db.py`, and `app_clean.py`'s SQLite store is a separate, hand-rolled schema.
   **Decide now** whether the new `mfa_*` tables are Postgres-only (recommended: that is where auth lives) and
   the concurrency test uses a standalone `sqlite3` fixture, or whether `db.py` grows a SQLite backend.
   Otherwise the Phase 1 test "a secret is not readable in the database file" has no database file to inspect.
4. **No server-side session table exists**, so Phase 4's "logout invalidates the server-side session row" and
   "a rotated session ID is rejected" require a *new* `sessions` table that the task list does not mention
   (`mfa_login_challenges` covers only the pre-auth challenge). **Gap in the design — assumption: add a
   `mfa_sessions` / `sessions` table in Phase 4.**
5. **Login state machine vs. existing session keys.** `pending_2fa` / `_2fa_setup_secret` (`auth.py:552`,
   `:651`) disappear in favour of a challenge cookie. `tests/test_auth_2fa.py:271-279, 340-348, 365-376`
   assert on the `pending_2fa` dict directly and **will have to be rewritten** — that is a legitimate
   behaviour change, not a weakening, but it must be done as a rewrite, not a deletion.
6. **Admin flow must carry the admin flag through MFA.** `start_session(profile, admin=True)` vs the
   `admin_at`-less call at `auth.py:693` (finding H2) means the challenge row needs an "intent: admin" field,
   and `admin_state()`'s `"reauth"` branch (`auth.py:206`) needs a new `MFA_ENROLL_REQUIRED` sibling.
7. **`_RateLimiter` is in-memory and per-process** (`auth.py:120-144`) and is reset on success
   (`auth.py:412`). Phase 3's doubling lockout with 24-h memory cannot be built on it. Keep the class for
   cheap pre-DB throttling; make `mfa_attempts` authoritative. The existing `login_limiter` is poked directly
   by tests (`tests/test_auth_2fa.py:89`) and by `admin_routes.py:152,164,167`, so keep the attribute names.
8. **`client_ip()` returns a raw IP** (`auth.py:146`) with no `X-Forwarded-For` handling. `ip_hash` work needs
   a trusted-proxy decision first, or every NAT'd user collapses into one bucket (and behind a proxy,
   into *one* bucket globally).
9. **CSRF and the challenge cookie interact.** `_set_pending_2fa`'s `session.clear()` is why `/2fa/verify`
   has no token. The new flow must mint a CSRF token *for the challenge* so `/2fa/verify` and `/2fa/setup`
   can finally be protected — this is a prerequisite, not a nice-to-have.
10. **Dual user stores.** `app_clean.py:1095-1135` keeps a SQLite `users` table with its own
    `totp_secret`/`two_factor_pending`. If it is live anywhere, there are two sources of truth for MFA state.
    **Assumption: the SQLite copy is legacy/demo and the Postgres profile is authoritative; the SQLite
    columns should be left untouched and ignored, and that should be documented.**
11. **Dependencies.** `pyotp`, `qrcode`, `psycopg2` missing; `argon2-cffi` (or `bcrypt`) and `py_webauthn`
    not even in `requirements.txt`. `cryptography` 50.0.2 is present, so Fernet/MultiFernet needs no new pin.
12. **No conflict, but worth noting:** nothing in the repo currently sends an OTP over SMS/WhatsApp, so the
    "SMS is a notification channel only" policy needs no code removal — only a documented prohibition.

---

## 13. Recommendations (not implemented)

In the order that reduces risk fastest:

1. Call `auth.resolve_secret_key()` from `app_clean.py:73` and delete the hardcoded fallback. Highest
   risk-reduction per line changed; everything else is moot while the signing key is guessable.
2. Apply `@auth.login_required` to the user-facing routes that read or write per-user data
   (`/dashboard`, `/add_user`, `/farms/add`, `/fields/add`, the sensor and experiment POSTs).
3. Rate-limit and lock out `/2fa/verify`, `/2fa/setup` and `/2fa/disable` (Phase 3), and stop resetting the
   login counter until **after** MFA succeeds.
4. Add `mfa_used_totp_steps` + a `verify` that returns the matched step, and do the replay check in the same
   transaction as the insert (Phase 2).
5. Encrypt secrets at rest with MultiFernet and stop selecting the secret in `_PROFILE_SQL` (Phase 1).
6. Give `/2fa/verify` and `/2fa/setup` CSRF tokens tied to the server-side challenge (Phase 4/5).
7. Require password step-up for `/2fa/disable`, and notify the user on the notification channel when a factor
   is removed.
8. Fix the admin MFA path (carry the admin intent through the challenge) and make admin MFA mandatory with an
   `MFA_ENROLL_REQUIRED` state.
9. Issue recovery codes at enrolment, hashed with Argon2id (Phase 6).
10. Install `requirements.txt` into `.venv` and agree a fast test subset so "keep it green" is measurable.
