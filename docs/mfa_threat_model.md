# MFA / 2FA Threat Model — Smart Weather System

Scope: authentication and second-factor handling in `auth.py`, `totp_auth.py`, `admin_routes.py`,
`supabase/schema.sql`. Deployment context: Flask + Supabase/PostgreSQL, farmers in rural Pakistan on
carrier-NAT mobile networks and shared devices, intermittent connectivity.

Audit findings referenced as **F1-F9** map to the questions in
[`.agents/tasks/mfa-phase0-audit.md`](../.agents/tasks/mfa-phase0-audit.md) (F1 = routes/session, F2 = secret
storage, F3 = replay, F4 = rate limiting, F5 = recovery codes, F6 = pending state, F7 = admin login,
F8 = bypass paths, F9 = CSRF).

---

## 1. Assets

| Asset | Where it lives today | Why an attacker wants it | Current exposure |
|---|---|---|---|
| Farmer accounts | `public.users` | farm data, advisory manipulation, pivot to admin | password-only for most users |
| Admin accounts | `public.users` + `public.admin_users` | read/modify every user, deactivate, delete | 2FA optional (F7); broken when enabled |
| TOTP secrets | `users.totp_secret`, **plain text** (`supabase/schema.sql:52`) | permanent second-factor forgery | critical (F2); also placed in the session cookie during enrolment (`auth.py:651`) |
| Recovery codes | **do not exist** (F5) | one-shot account takeover | n/a — and the absence forces unaudited manual resets |
| Sessions | signed Flask cookie only, no server-side row (F1) | full impersonation; survives logout | high |
| Phone numbers | `users.phone_number` (E.164) | SIM-swap targeting, social engineering, PII | readable wherever the profile row is |
| Notification channels | Twilio SMS / WhatsApp / local gateway (`notifications.py`) | phishing with a trusted sender, alert suppression | no OTP is sent over these today — keep it that way |
| Audit trail | `admin_audit_log`; no MFA event log yet | hide the intrusion | gap |

## 2. Adversaries, attack paths, controls

### A. Credential stuffing
**Path.** Reused password POSTed to `/login`. `auth.login_limiter` (`auth.py:143`, 8 per 300 s, keyed
`ip|email`) is in-memory and per-process, so a multi-worker deploy multiplies the budget and a restart clears
it (F4). On success the counter is reset (`auth.py:412`) and `/2fa/verify` is then unlimited (F4).
**Control.** DB-backed per-account attempt counting and lockout (`mfa_attempts`, Phase 3) that spans
processes; failed password attempts count toward the same account limit; the counter is reset **only after MFA
succeeds**, not after the password step. Generic error text on every failure.

### B. Phished password
**Path.** Password harvested, attacker reaches `/2fa/verify`. For a farmer with no factor there is no second
step at all; for an admin with `totp_secret IS NULL` the attacker gets a full admin session on the password
alone (`admin_routes.py:176`, F7/F8).
**Control.** Admin MFA mandatory with an `MFA_ENROLL_REQUIRED` state that blocks every `/admin` route until a
factor is enrolled. WebAuthn/passkeys as the phishing-resistant option (origin-bound, so a proxy phishing page
cannot relay it). TOTP remains phishable in real time — accept that and compensate with lockout, step-up and
MFA event alerting.

### C. SIM swap
**Path.** Attacker ports the number in `users.phone_number` and expects an OTP. Today nothing in
`notifications.py` sends an OTP, so this path is *already* closed by accident.
**Control.** Make it a rule, not an accident: **SMS/WhatsApp are notification channels only and must never
carry an authentication code or a reset link.** A SIM swap then yields only visibility into alerts, and a
phone-number change itself requires step-up (§3).

### D. Stolen session cookie (XSS, shared browser, malware, proxy log)
**Path.** `sw_session` is `HttpOnly` + `SameSite=Lax`, `Secure` outside debug (`auth.py:800-808`), but there
is **no server-side session row**, so `logout` (`auth.py:529`) cannot revoke a copied cookie, and the default
lifetime is 7 days (F1). Worse, `app_clean.py:73` falls back to a publicly known `SECRET_KEY`
(`'smart_weather_ai_2024'`, listed in `auth._KNOWN_WEAK_SECRETS`) while `auth.resolve_secret_key()` is never
called — a cookie containing `uid` can simply be **forged**, skipping password and MFA entirely (F8).
**Control.** Call `resolve_secret_key()` and fail startup when `DEBUG=False`; server-side session rows with
rotation on login, MFA verification and step-up; logout deletes the row; `SameSite=Strict` on the pre-auth
challenge cookie; short admin absolute/idle limits (already present, `auth.py:198-227`).

### E. Insider with database read access (or a leaked backup)
**Path.** `SELECT id, totp_secret FROM users` yields a working authenticator for every enrolled user, forever
(F2). The secret is also pulled into `g._user` on **every request** by `_PROFILE_SQL` (`auth.py:156`),
widening it to logs and templates.
**Control.** Fernet/MultiFernet encryption with keys from `MFA_ENCRYPTION_KEY` held outside the database;
`mfa_factors.secret_encrypted` only; stop selecting the secret in the per-request profile query; key rotation
via `scripts/rotate_mfa_key.py`. Recovery codes hashed with Argon2id, never recoverable. `mfa_events` records
`ip_hash`/`user_agent_hash` only — no raw IPs, no codes.

### F. Stolen recovery codes
**Path.** None exist today (F5), so the real-world "recovery" is an operator editing the database — unlogged
and unverified.
**Control.** 10 single-use codes, Argon2id-hashed, shown once at enrolment, consumed atomically, counted
against the same lockout budget as TOTP, and every use written to `mfa_events` **and** pushed to the user's
notification channel. Regenerating codes requires step-up.

### G. Attacker on a shared device
**Path.** Device left unlocked with the authenticator app reachable. `/2fa/disable` (`auth.py:701-733`)
removes the factor on a TOTP code alone — no password, no notification (F8). A TOTP code also stays valid for
~90 s with no replay protection, so a glanced code is reusable (F3). `/2fa/verify` and `/2fa/setup` POST have
no CSRF token (F9), so a malicious page in the same browser can drive the flow.
**Control.** Replay protection via `mfa_used_totp_steps` keyed `(factor_id, time_step)`, inserted in the same
transaction as the accept; password step-up for disabling 2FA; CSRF tokens bound to the server-side challenge;
trusted-device tokens that are revocable and listed on the profile page.

---

## 3. Recommended policy defaults

| Area | Default | Reason |
|---|---|---|
| Admin MFA | **Required.** No `/admin` route reachable until a factor is enrolled (`MFA_ENROLL_REQUIRED`). | Admin compromise is total: read/deactivate/delete every user. Fixes F7/F8. |
| Farmer MFA | **Optional, strongly encouraged.** Prompt at signup and on each login until dismissed; never block access. | A hard requirement on shared feature phones in rural areas locks out legitimate users and pushes them off the platform. |
| Allowed factors | TOTP (RFC 6238, 6 digits, 30 s), WebAuthn/passkeys, recovery codes. | TOTP works offline; passkeys are phishing-resistant; codes are the break-glass. |
| SMS / WhatsApp | **Notification channel only. Never a primary or fallback factor, never a reset link.** | SIM swap (§2C). Already true in `notifications.py`; this makes it binding. |
| TOTP window | `valid_window=1` (±1 step) **plus** server-side used-step tracking. | Tolerates clock drift without granting a 90 s replay window (F3). |
| Clock drift | After 5 consecutive logins matching only at ±1 step, log `clock_drift_suspected` and show a profile notice. Never widen the window per user. | Per-user widening is a silent, permanent weakening. |
| Trusted device | **30 days**, max 5 per account, bound to `ua_family` + `ip_prefix_hash`, revocable individually and "revoke all" on password change, factor change or recovery-code use. Never available to admins. | Cuts MFA friction on a farmer's own phone; the admin exception keeps privileged access always-MFA. [DECISION NEEDED] confirm 30 days and the 5-device cap. |
| Per-account lockout | 5 failed TOTP/recovery/password attempts in 15 min → lock the factor 15 min. Second lockout inside 24 h doubles, capped at 4 h. Success resets. | 6 digits = 10^6; 5 tries per 15 min keeps brute force far below any useful rate while a mistyped code is forgiven. |
| Per-IP control | **No hard block.** 30 failures in 15 min from one `ip_hash` → 2 s delay after the 10th failure. | **Carrier NAT is the norm in rural Pakistan**: one `ip_hash` can be thousands of real farmers, so an IP block is a self-inflicted outage. The per-account counter is the primary control; the IP delay only raises the cost of spray attacks. |
| IP handling | Store only `ip_hash = HMAC-SHA256(MFA_IP_PEPPER, ip)`. Never a raw IP in `mfa_events` or `mfa_attempts`. | Minimises PII and keeps the audit log safe to export. Needs a trusted-proxy decision first — `client_ip()` (`auth.py:146`) reads `remote_addr` with no `X-Forwarded-For` handling, so behind a proxy every user hashes to one value. [DECISION NEEDED] trusted proxy / header policy. |
| Error messages | Generic for every auth failure. Never reveal whether the account exists, has MFA, or is locked. | Enumeration. |
| Config flags | `MFA_MAX_ATTEMPTS=5`, `MFA_LOCKOUT_MINUTES=15`, `MFA_IP_SLOWDOWN_AFTER=10`, `MFA_LOCKOUT_MAX_MINUTES=240` | Tunable without redeploy. |
| Challenge TTL | 5 min, server-side (`mfa_login_challenges`), single-use, consumed on success **and** failure. Challenge id in a signed HttpOnly `SameSite=Strict` cookie. | Replaces the 10-min cookie-only `pending_2fa` dict (F6). |
| Session lifetime | Farmer 7 days (current); **admin 8 h absolute / 60 min idle** (current, `auth.py:48-49`). Server-side rows, rotated on login / MFA / step-up; logout deletes the row. | Fixes the unrevocable-cookie gap in F1. |
| Step-up re-auth | Password **+** a fresh factor assertion, valid 5 min, required for: disabling 2FA or removing a factor; changing phone, email or password; regenerating recovery codes; exporting data; **every destructive admin action** (`/api/admin/users/*/toggle`, `*/delete`, `/api/admin/alerts/*/delete`). | Closes F8: today `/2fa/disable` needs only a TOTP code. [DECISION NEEDED] 5-minute step-up freshness window. |
| Notify on security events | Factor added/removed, recovery code used, reset requested/completed, new trusted device, password change → user's notification channel **and** `mfa_events`. | Makes a silent takeover loud. The channel is informational only, so a SIM swap cannot abuse it. |

### Recovery paths

| Path | Rule |
|---|---|
| Recovery codes | 10 single-use Argon2id-hashed codes, issued at enrolment, shown once. Use counts toward the lockout budget and revokes all trusted devices. |
| Lost device (farmer) | Self-service request → **72-hour cooling-off** before the reset is eligible. Immediate notification to every known channel with a one-click cancel token. Queued in `mfa_outbox` so the request survives an offline period. Tokens stored hashed (`mfa_reset_requests.cancel_token_hash` / `confirm_token_hash`). |
| Lost device (admin) | No self-service. **Two-person approval**: a second active admin approves, logged in `mfa_events` with both actor ids. The 72-hour window still applies. [DECISION NEEDED] fallback when only one admin account exists — recommended: a documented, audited break-glass procedure executed against the database by the project owner, not a code path. |
| Manual DB edit | Prohibited as a routine recovery route. Every reset must leave an `mfa_events` row. |

### Degraded operation

| Condition | Admin | Farmer |
|---|---|---|
| Database unreachable | **Fail closed** — no login, no session. `db.connect()` already raises `DatabaseUnavailable` (`db.py:284`) and `admin_required` returns 503 (`auth.py:242`). | Fail closed for login; cached public weather pages stay readable (existing `edge_case_hardening_v2.py` behaviour). |
| Notification channel down (Twilio / local gateway) | **Fail closed** for anything that depends on a notification (reset, two-person approval). Login itself is unaffected — no OTP is ever sent. | Login unaffected. Security notifications are queued in `mfa_outbox` and delivered when the channel returns; the user sees a banner that alerting is delayed. |
| `pyotp` / `qrcode` missing | **Fail closed.** Today `auth.py:602-605,667-670` redirects away instead, which combined with optional admin MFA is a bypass (F8). | Fail closed for users who already have a factor; users without one are unaffected. |
| `MFA_ENCRYPTION_KEY` / `MFA_IP_PEPPER` / strong `SECRET_KEY` missing | **Refuse to start** when `DEBUG=False`. | same |
| No network on the user's device | **TOTP must work fully offline** — it is a local time-based computation. Nothing in the verification path may require an outbound call. This is the main reason TOTP, not SMS or a push provider, is the default factor. | same |

---

## 4. Decisions needing the project owner's sign-off

- [DECISION NEEDED] Trusted-device duration (proposed 30 days) and the per-account device cap (proposed 5).
- [DECISION NEEDED] Trusted-proxy / `X-Forwarded-For` policy, which determines whether `ip_hash` is meaningful at all.
- [DECISION NEEDED] Step-up freshness window (proposed 5 minutes).
- [DECISION NEEDED] Admin reset when only one admin account exists — documented break-glass vs. a coded single-admin override.
- [DECISION NEEDED] Confirm farmer MFA stays optional indefinitely, or set a date after which it becomes required for accounts that own farm data.
- [DECISION NEEDED] 72-hour cooling-off period: confirm the duration and whether a farmer can shorten it by presenting a recovery code.
- [DECISION NEEDED] Fate of `users.totp_secret` after migration — keep as a read-only migration source, or drop the column once every secret is re-encrypted into `mfa_factors`.
- [DECISION NEEDED] Whether the legacy SQLite store in `app_clean.py:1095-1135` (which has its own `totp_secret` / `two_factor_pending` columns) is live anywhere. If it is, there are two sources of truth for MFA state and that must be resolved before Phase 1.
- [DECISION NEEDED] Whether the new `mfa_*` tables are PostgreSQL-only. `db.py` has no SQLite backend, so the Phase 2 concurrency test needs a standalone `sqlite3` fixture either way.
- [DECISION NEEDED] Acceptance that forcing a strong `SECRET_KEY` invalidates every existing session on upgrade (all users signed out once).
- [DECISION NEEDED] WebAuthn scope for the first release — attestation policy, whether a passkey alone may replace TOTP for admins, and the RP ID / origin list.
- [DECISION NEEDED] Which test command counts as the CI gate: full `unittest discover` currently exceeds 10 minutes without completing, so a scoped auth/security subset is proposed instead.
