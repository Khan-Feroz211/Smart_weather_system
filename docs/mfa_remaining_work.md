# MFA Remaining Work

This checklist contains only work not yet implemented. Phase 0 audit, Phase 1 schema/encryption, Phase 2 replay protection, and Phase 3 rate limiting/lockout are complete.

## Phase 4 — Server-side login state machine
- [ ] Add a server-side session table and accessors; rotate and invalidate session IDs on login, MFA verification, step-up, and logout.
- [ ] Replace cookie-only `pending_2fa` with five-minute `mfa_login_challenges` and a signed HttpOnly/SameSite=Strict challenge cookie.
- [ ] Make challenges single-use on success and failure, with expiry and attempt limits.
- [ ] Carry admin intent through MFA and enforce `MFA_ENROLL_REQUIRED` for admins without a factor.
- [ ] Apply authentication guards to currently unprotected user data routes, including dashboard, farm/field, sensor, experiment, and export routes.
- [ ] Rewrite affected authentication tests for the server-side state machine without weakening assertions.

## Phase 5 — Enrollment and recovery-code issuance
- [ ] Store pending TOTP enrollment encrypted in `mfa_pending_enrollments`, never in the session cookie.
- [ ] Add ten 12-character recovery codes using Argon2id hashes only; show codes once with explicit confirmation.
- [ ] Create encrypted `mfa_factors` rows only after proof-of-possession verification.
- [ ] Add issuer configuration, enrollment expiry, restart invalidation, notifications, and audit events.
- [ ] Migrate existing plaintext `users.totp_secret` values into encrypted factor rows through a reversible, tested migration; retain the legacy column only during the cutover.

## Phase 6 — Recovery and lost-device reset
- [ ] Add single-use recovery-code login restricted to `MFA_REQUIRED`.
- [ ] Require fresh TOTP enrollment after recovery login and prompt regeneration below three remaining codes.
- [ ] Implement padded, account-enumeration-resistant lost-device requests.
- [ ] Implement 72-hour cooling-off, cancellation links, fresh confirmation links, factor/device removal, and forced enrollment.
- [ ] Implement two-person approval for admin resets and prohibit self-reset or single-admin reset of another admin.

## Phase 7 — Step-up authentication
- [ ] Add `require_recent_mfa(max_age_minutes=5)` backed by the server-side session.
- [ ] Apply it to disabling MFA, password/phone/email changes, recovery-code regeneration, alert deletion, data export, and every admin POST route.
- [ ] Add a re-verification page that does not replay the original request.
- [ ] Notify the old contact channel after phone/email changes.

## Phase 8 — Trusted devices
- [ ] Add farmer-only 14-day trusted-device tokens using SHA-256 hashes.
- [ ] Bind devices to coarse UA family and IP-prefix hash; log mismatches and require MFA.
- [ ] Enforce a five-device maximum with oldest-device eviction.
- [ ] Add profile listing/revocation with step-up; revoke all devices after password change or MFA disable.
- [ ] Ensure admin login never honors trusted-device cookies.

## Phase 9 — WebAuthn/passkeys
- [ ] Add a pinned, Python 3.11-compatible WebAuthn dependency after compatibility verification.
- [ ] Implement enrollment and login using `WEBAUTHN_RP_ID` and `WEBAUTHN_ORIGIN`.
- [ ] Validate origin and strictly increasing authenticator sign counts.
- [ ] Log cloned-authenticator suspicion and keep TOTP/recovery fallbacks.
- [ ] Warn on insecure production origins and add passkey-only login tests.

## Phase 10 — Notifications, audit, and anomalies
- [ ] Add asynchronous notification jobs using the existing WhatsApp → SMS → local gateway chain, with one retry and non-blocking failure handling.
- [ ] Notify for new devices, MFA changes, recovery-code use/regeneration, lost-device requests, and admin resets without including secrets or tokens.
- [ ] Add new-IP/UA-after-failed-MFA and excessive-recovery-use anomaly detection.
- [ ] Add read-only, step-up-protected `/admin/mfa-events` filtering by user, type, and date.
- [ ] Add retention cleanup for events older than `MFA_EVENT_RETENTION_DAYS`, with a summary event.

## Phase 11 — Offline and degraded mode
- [ ] Add MFA degradation status: `mfa_mode`, pending outbox count, and last clock check.
- [ ] Refuse enrollment when the server clock differs from the trusted reference by more than 30 seconds; keep verification offline-capable.
- [ ] Queue lost-device requests in `mfa_outbox` when offline without claiming delivery.
- [ ] Never complete resets from the queue without fresh confirmation.
- [ ] Permit matching trusted-device offline use for farmers only; admins must always use MFA.

## Phase 12 — Final verification and operations
- [ ] Run the complete scoped MFA suite and retain the known pre-existing `psycopg2` environment error separately.
- [ ] Run Bandit on `auth.py`, `totp_auth.py`, and `mfa_rate_limit.py`.
- [ ] Audit every POST route in `auth.py` and `admin_routes.py` for CSRF protection.
- [ ] Audit logs and exceptions for secrets, codes, tokens, passwords, and raw IPs.
- [ ] Verify production startup fails without `MFA_ENCRYPTION_KEY`, `MFA_IP_PEPPER`, and a strong `SECRET_KEY`.
- [ ] Execute the Postgres schema and accessors in staging; verify real replay and lockout expiry.
- [ ] Write `docs/mfa_runbook.md` covering key rotation, lost-device handling, admin resets, notification outages, and event review.

## Open decisions

- [ ] Decide trusted-proxy / `X-Forwarded-For` handling before production IP-based controls.
- [ ] Decide whether password failures with an unknown user should contribute to per-account lockout; current safe behavior counts them only per IP.
- [ ] Decide the server-side session table name and retention policy; the safest default is a new `mfa_sessions` table with short-lived/revocable records.
- [ ] Confirm crop/application deployment policy for admin enrollment and trusted devices before enabling production enforcement.
