"""
Phase 3 tests -- MFA rate limiting and lockout.

Hermetic: every test drives a real sqlite store (tests/mfa_sqlite_store.py), no
network, no Postgres, no credentials.  `mfa_rate_limit._sleep` is patched to a
recorder throughout, so the per-IP delay is asserted without the suite ever
actually waiting -- the whole module runs in well under a second.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import mfa_rate_limit as rl

try:
    from tests.mfa_sqlite_store import SqliteMfaStore
except ImportError:  # pragma: no cover
    from mfa_sqlite_store import SqliteMfaStore

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"
SHARED_IP_HASH = "a" * 64


class RateLimitBase(unittest.TestCase):
    def setUp(self):
        self.store = SqliteMfaStore()
        self.addCleanup(self.store.close)
        self.now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        self.slept = []
        patcher = mock.patch.object(rl, "_sleep", self.slept.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- helpers -----------------------------------------------------------
    def hooks(self):
        return dict(recent_attempts=self.store.recent_attempts,
                    recent_events=self.store.recent_events)

    def stamped_record(self, at):
        """record_attempt bound to the injected clock (db.py has no created_at)."""
        def record(attempt_type, success, user_id=None, ip_hash=None):
            self.store.record_attempt(attempt_type, success, user_id, ip_hash,
                                      created_at=at)
        return record

    def stamped_log(self, at):
        def log(event_type, actor=None, user_id=None, ip_hash=None,
                user_agent_hash=None):
            self.store.log_event(event_type, actor, user_id, ip_hash,
                                 user_agent_hash, created_at=at)
        return log

    def succeed(self, user_id=None, ip_hash=None, at=None, attempt_type="totp"):
        rl.record_success(attempt_type, user_id=user_id, ip_hash=ip_hash,
                          record_attempt=self.stamped_record(at or self.now))

    def fail(self, user_id=None, ip_hash=None, at=None, attempt_type="totp"):
        moment = at or self.now
        return rl.record_failure(attempt_type, user_id=user_id, ip_hash=ip_hash,
                                 now=moment,
                                 record_attempt=self.stamped_record(moment),
                                 recent_attempts=self.store.recent_attempts,
                                 log_event=self.stamped_log(moment),
                                 recent_events=self.store.recent_events)

    def fail_n(self, n, user_id=None, ip_hash=None, at=None, attempt_type="totp"):
        decision = None
        for _ in range(n):
            decision = self.fail(user_id=user_id, ip_hash=ip_hash, at=at,
                                 attempt_type=attempt_type)
        return decision

    def check(self, user_id=None, ip_hash=None, at=None):
        return rl.check(user_id=user_id, ip_hash=ip_hash, now=at or self.now,
                        **self.hooks())


class TestAccountLockout(RateLimitBase):
    def test_under_threshold_is_not_locked(self):
        self.fail_n(rl.MAX_ATTEMPTS - 1, user_id=USER_A)
        self.assertFalse(self.check(user_id=USER_A).locked)

    def test_lockout_engages_at_threshold(self):
        decision = self.fail_n(rl.MAX_ATTEMPTS, user_id=USER_A)
        self.assertTrue(decision.locked)
        self.assertGreater(decision.retry_after_seconds, 0)
        self.assertTrue(self.check(user_id=USER_A).locked)

    def test_lockout_expires_once_the_window_passes(self):
        self.fail_n(rl.MAX_ATTEMPTS, user_id=USER_A)
        self.assertTrue(self.check(user_id=USER_A).locked)
        later = self.now + timedelta(minutes=rl.WINDOW_MINUTES + rl.LOCKOUT_MINUTES + 1)
        self.assertFalse(self.check(user_id=USER_A, at=later).locked)

    def test_success_resets_the_counter(self):
        self.fail_n(rl.MAX_ATTEMPTS - 1, user_id=USER_A)
        self.succeed(user_id=USER_A)
        self.fail_n(2, user_id=USER_A)
        self.assertFalse(self.check(user_id=USER_A).locked)

    def test_failed_password_attempts_count_toward_the_same_limit(self):
        self.fail_n(3, user_id=USER_A, attempt_type="password")
        decision = self.fail_n(2, user_id=USER_A, attempt_type="totp")
        self.assertTrue(decision.locked)

    def test_lockout_event_is_logged_once_at_the_threshold(self):
        self.fail_n(rl.MAX_ATTEMPTS, user_id=USER_A)
        rows = self.store.inspect(
            "SELECT count(*) FROM mfa_events WHERE event_type = ?", (rl.LOCKOUT_EVENT,))
        self.assertEqual(rows[0][0], 1)

    def test_no_user_id_means_no_account_lock(self):
        self.fail_n(rl.MAX_ATTEMPTS + 3, ip_hash=SHARED_IP_HASH)
        self.assertFalse(self.check(ip_hash=SHARED_IP_HASH).locked)


class TestLockoutDoubling(RateLimitBase):
    def test_duration_doubles_per_prior_lockout(self):
        self.assertEqual(rl._lockout_minutes(0), rl.LOCKOUT_MINUTES)
        self.assertEqual(rl._lockout_minutes(1), rl.LOCKOUT_MINUTES * 2)
        self.assertEqual(rl._lockout_minutes(2), rl.LOCKOUT_MINUTES * 4)

    def test_duration_is_capped(self):
        self.assertEqual(rl._lockout_minutes(99), rl.LOCKOUT_MAX_MINUTES)
        self.assertLessEqual(rl._lockout_minutes(50), rl.LOCKOUT_MAX_MINUTES)

    def test_second_lockout_within_24h_lasts_longer(self):
        self.fail_n(rl.MAX_ATTEMPTS, user_id=USER_A)
        first = self.check(user_id=USER_A).retry_after_seconds

        # Far enough ahead that the first batch has left the 15-minute window,
        # but still inside the 24-hour doubling memory.
        later = self.now + timedelta(minutes=rl.WINDOW_MINUTES + rl.LOCKOUT_MINUTES + 5)
        self.fail_n(rl.MAX_ATTEMPTS, user_id=USER_A, at=later)
        second = self.check(user_id=USER_A, at=later).retry_after_seconds

        self.assertTrue(second > first)
        self.assertLessEqual(second, rl.LOCKOUT_MAX_MINUTES * 60 + 1)

    def test_prior_lockouts_outside_24h_do_not_count(self):
        self.store.log_event(rl.LOCKOUT_EVENT, actor="mfa_rate_limit", user_id=USER_A,
                             created_at=self.now - timedelta(hours=30))
        self.assertEqual(
            rl._prior_lockouts_24h(USER_A, self.now,
                                   recent_events=self.store.recent_events), 0)

    def test_doubling_survives_a_chatty_event_log(self):
        """Every verification writes an mfa_events row.

        The lockout rows that drive the doubling must therefore be filtered in
        SQL, not looked for inside the newest EVENT_ROW_LIMIT rows of every kind.
        """
        self.store.log_event(rl.LOCKOUT_EVENT, actor="mfa_rate_limit", user_id=USER_A,
                             created_at=self.now - timedelta(minutes=30))
        for _ in range(rl.EVENT_ROW_LIMIT + 20):
            self.store.log_event("totp_verify_ok_exact", actor="totp_auth",
                                 user_id=USER_A, created_at=self.now)
        self.assertEqual(
            rl._prior_lockouts_24h(USER_A, self.now,
                                   recent_events=self.store.recent_events), 1)

    def test_a_longer_lockout_is_enforced_after_the_attempt_window_passes(self):
        """A lockout must outlive the window that produced it.

        The threshold is a question about MFA_WINDOW_MINUTES, but the lockout
        itself can last up to MFA_LOCKOUT_MAX_MINUTES.  Evaluating the expiry
        against the window alone ended every lockout after WINDOW_MINUTES, which
        left the whole doubling ladder computed but never applied.
        """
        self.fail_n(rl.MAX_ATTEMPTS, user_id=USER_A)                     # lockout 1
        second = self.now + timedelta(
            minutes=rl.WINDOW_MINUTES + rl.LOCKOUT_MINUTES + 5)
        self.fail_n(rl.MAX_ATTEMPTS, user_id=USER_A, at=second)          # lockout 2
        advertised = self.check(user_id=USER_A, at=second).retry_after_seconds
        self.assertGreater(advertised, rl.WINDOW_MINUTES * 60)

        # Past the attempt window, still well inside the doubled lockout.
        later = self.check(user_id=USER_A,
                           at=second + timedelta(minutes=rl.WINDOW_MINUTES + 1))
        self.assertTrue(later.locked)
        self.assertGreater(later.retry_after_seconds, 0)
        self.assertLess(later.retry_after_seconds, advertised)

        # ...and it does end when the wait it advertised is over.
        self.assertFalse(
            self.check(user_id=USER_A,
                       at=second + timedelta(seconds=advertised + 1)).locked)


class TestSharedIpCarrierNat(RateLimitBase):
    def test_locked_account_does_not_block_another_account_on_the_same_ip(self):
        self.fail_n(rl.MAX_ATTEMPTS, user_id=USER_A, ip_hash=SHARED_IP_HASH)
        self.assertTrue(self.check(user_id=USER_A, ip_hash=SHARED_IP_HASH).locked)
        # The farmer next door behind the same carrier NAT must still get in.
        self.assertFalse(self.check(user_id=USER_B, ip_hash=SHARED_IP_HASH).locked)

    def test_ip_is_never_hard_blocked(self):
        # There is deliberately no per-IP cap to compare against, so pile up far
        # more failures than any per-account threshold and assert the address is
        # still not locked -- only delayed.
        self.fail_n(rl.IP_SLOWDOWN_AFTER * 3, user_id=USER_B, ip_hash=SHARED_IP_HASH)
        self.assertFalse(self.check(ip_hash=SHARED_IP_HASH).locked)


class TestIpSlowdown(RateLimitBase):
    def test_no_delay_below_the_threshold(self):
        self.fail_n(rl.IP_SLOWDOWN_AFTER - 1, ip_hash=SHARED_IP_HASH)
        self.slept.clear()
        self.assertFalse(self.check(ip_hash=SHARED_IP_HASH).delay_applied)
        self.assertEqual(self.slept, [])

    def test_delay_applies_after_the_threshold(self):
        self.fail_n(rl.IP_SLOWDOWN_AFTER, ip_hash=SHARED_IP_HASH)
        self.slept.clear()
        decision = self.check(ip_hash=SHARED_IP_HASH)
        self.assertTrue(decision.delay_applied)
        self.assertEqual(self.slept, [rl.IP_SLOWDOWN_SECONDS])
        self.assertFalse(decision.locked)

    def seed_ip_failures(self, n, ip_hash=SHARED_IP_HASH):
        """Put `n` failed rows on one ip_hash without going through the module."""
        for _ in range(n):
            self.store.record_attempt("totp", False, None, ip_hash,
                                      created_at=self.now)

    def test_one_request_pays_the_delay_once(self):
        """The 2FA routes gate on check() then record the failure.

        Both calls used to run the per-IP slowdown, so a single rejected code
        slept 2 x IP_SLOWDOWN_SECONDS.  record_failure(apply_delay=False) is how
        the routes avoid paying twice.
        """
        self.seed_ip_failures(rl.IP_SLOWDOWN_AFTER)
        self.slept.clear()
        self.check(ip_hash=SHARED_IP_HASH)                      # the route's gate
        rl.record_failure("totp", user_id=USER_A, ip_hash=SHARED_IP_HASH,
                          now=self.now, apply_delay=False,
                          record_attempt=self.stamped_record(self.now),
                          recent_attempts=self.store.recent_attempts,
                          log_event=self.stamped_log(self.now),
                          recent_events=self.store.recent_events)
        self.assertEqual(self.slept, [rl.IP_SLOWDOWN_SECONDS])

    def test_record_failure_still_delays_on_its_own_by_default(self):
        """/login records a failed password without gating on check() first."""
        self.seed_ip_failures(rl.IP_SLOWDOWN_AFTER)
        self.slept.clear()
        self.fail(user_id=USER_A, ip_hash=SHARED_IP_HASH, attempt_type="password")
        self.assertEqual(self.slept, [rl.IP_SLOWDOWN_SECONDS])


class TestEnvConfiguration(RateLimitBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(rl.refresh_config)   # restore the import-time values

    def test_defaults_match_the_phase3_brief(self):
        self.assertEqual(
            (rl.MAX_ATTEMPTS, rl.LOCKOUT_MINUTES, rl.IP_SLOWDOWN_AFTER,
             rl.LOCKOUT_MAX_MINUTES), (5, 15, 10, 240))

    def test_env_overrides_are_honoured(self):
        with mock.patch.dict(os.environ, {"MFA_MAX_ATTEMPTS": "3",
                                          "MFA_LOCKOUT_MINUTES": "7",
                                          "MFA_IP_SLOWDOWN_AFTER": "2",
                                          "MFA_LOCKOUT_MAX_MINUTES": "20"}):
            rl.refresh_config()
            self.assertEqual(rl.MAX_ATTEMPTS, 3)
            self.assertEqual(rl.LOCKOUT_MINUTES, 7)
            self.assertEqual(rl.IP_SLOWDOWN_AFTER, 2)
            self.assertEqual(rl._lockout_minutes(0), 7)
            self.assertEqual(rl._lockout_minutes(9), 20)   # capped by the override
            # The lower threshold is actually used, not just stored.
            decision = self.fail_n(3, user_id=USER_A)
            self.assertTrue(decision.locked)

    def test_garbage_and_zero_values_fall_back_to_the_defaults(self):
        with mock.patch.dict(os.environ, {"MFA_MAX_ATTEMPTS": "not-a-number",
                                          "MFA_LOCKOUT_MINUTES": "0"}):
            rl.refresh_config()
            self.assertEqual(rl.MAX_ATTEMPTS, 5)
            self.assertEqual(rl.LOCKOUT_MINUTES, 15)


class TestDegradedAndHygiene(RateLimitBase):
    def test_message_is_generic(self):
        text = rl.GENERIC_LOCKOUT_MESSAGE.lower()
        for hint in ("account", "2fa", "two-factor", "exist", "password", "email",
                     "locked for", "minutes"):
            self.assertNotIn(hint, text)

    def test_hash_ip_returns_none_without_a_pepper(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            mfa_crypto_reset = getattr(__import__("mfa_crypto"), "reset_cache", None)
            if mfa_crypto_reset:
                mfa_crypto_reset()
            self.assertIsNone(rl.hash_ip("203.0.113.7"))

    def test_hash_ip_of_nothing_is_none(self):
        self.assertIsNone(rl.hash_ip(""))
        self.assertIsNone(rl.hash_ip(None))

    def test_db_failure_degrades_open_rather_than_erroring(self):
        def boom(*a, **k):
            raise RuntimeError("db down")
        decision = rl.check(user_id=USER_A, ip_hash=SHARED_IP_HASH, now=self.now,
                            recent_attempts=boom, recent_events=boom)
        self.assertFalse(decision.locked)

    def test_database_unavailable_degrades_open(self):
        import db as real_db

        def unavailable(*a, **k):
            raise real_db.DatabaseUnavailable("not configured")
        decision = rl.check(user_id=USER_A, now=self.now,
                            recent_attempts=unavailable,
                            recent_events=unavailable)
        self.assertFalse(decision.locked)

    def test_recording_never_raises_when_the_store_is_broken(self):
        def boom(*a, **k):
            raise RuntimeError("db down")
        # Must not propagate: a broken audit store cannot break the login path.
        rl.record_success("totp", user_id=USER_A, record_attempt=boom)
        decision = rl.record_failure("totp", user_id=USER_A, now=self.now,
                                     record_attempt=boom, recent_attempts=boom,
                                     log_event=boom, recent_events=boom)
        self.assertFalse(decision.locked)

    def test_no_raw_ip_is_stored(self):
        raw = "203.0.113.7"
        self.fail(user_id=USER_A, ip_hash=SHARED_IP_HASH)
        rows = self.store.inspect("SELECT ip_hash FROM mfa_attempts")
        self.assertTrue(rows)
        for row in rows:
            self.assertNotEqual(row[0], raw)


if __name__ == "__main__":
    unittest.main()
