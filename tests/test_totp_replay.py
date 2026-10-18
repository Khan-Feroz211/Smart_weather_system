"""
Phase 2 tests for totp_auth.verify_totp -- replay protection and its guards.

Run from the project root:  python -m unittest tests.test_totp_replay -v

Hermetic: the replay store is a real temp sqlite3 file (tests/mfa_sqlite_store.py),
never PostgreSQL and never a mock -- a mock cannot prove that the insert is the
acceptance decision under concurrency.  The TOTP secret is generated locally per
test and is never printed or asserted on.
"""
import os
import sys
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

import totp_auth  # noqa: E402
from mfa_sqlite_store import SqliteMfaStore  # noqa: E402

USER_ID = str(uuid.uuid4())
FACTOR_ID = str(uuid.uuid4())
# Fixed instant so every candidate step is computed, not raced against the wall clock.
FIXED_NOW = datetime(2026, 3, 1, 12, 0, 15, tzinfo=timezone.utc)


class Base(unittest.TestCase):
    def setUp(self):
        if not totp_auth.is_available():
            self.skipTest("pyotp/qrcode not installed")
        import pyotp
        self.secret = totp_auth.generate_secret()     # throwaway; never printed
        self.totp = pyotp.TOTP(self.secret)
        self.store = SqliteMfaStore()
        self.addCleanup(self.store.close)

    def step_at(self, moment):
        return int(self.totp.timecode(moment))

    def code_for(self, step):
        return self.totp.generate_otp(step)

    def verify(self, code, factor_id=FACTOR_ID, user_id=USER_ID, now=FIXED_NOW):
        return totp_auth.verify_totp(
            self.secret, code,
            factor_id=factor_id,
            user_id=user_id,
            now=now,
            consume_step=self.store.try_consume_totp_step,
            log_event=self.store.log_event,
            recent_events=self.store.recent_events,
        )

    def event_types(self, user_id=USER_ID):
        return [r["event_type"] for r in self.store.inspect(
            "SELECT event_type FROM mfa_events WHERE user_id = ? "
            "ORDER BY created_at, rowid", (user_id,))]


class TestReplayRejection(Base):
    def test_same_code_twice_is_rejected_the_second_time(self):
        code = self.code_for(self.step_at(FIXED_NOW))
        first = self.verify(code)
        second = self.verify(code)
        self.assertTrue(first["ok"])
        self.assertEqual(first["reason"], "ok")
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "replay")
        self.assertIn("totp_replay_rejected", self.event_types())

    def test_only_one_row_per_claimed_step(self):
        step = self.step_at(FIXED_NOW)
        code = self.code_for(step)
        self.verify(code)
        self.verify(code)
        self.verify(code)
        rows = self.store.inspect(
            "SELECT * FROM mfa_used_totp_steps WHERE factor_id = ? AND time_step = ?",
            (FACTOR_ID, step))
        self.assertEqual(len(rows), 1)

    def test_previous_step_code_is_accepted_exactly_once(self):
        step = self.step_at(FIXED_NOW)
        code = self.code_for(step - 1)          # inside the +/-1 skew window
        self.assertTrue(self.verify(code)["ok"])
        self.assertEqual(self.verify(code)["reason"], "replay")

    def test_code_from_two_steps_ago_is_invalid(self):
        step = self.step_at(FIXED_NOW)
        result = self.verify(self.code_for(step - 2))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid")
        self.assertIn("totp_verify_failed", self.event_types())

    def test_result_never_reveals_the_matched_step(self):
        step = self.step_at(FIXED_NOW)
        result = self.verify(self.code_for(step))
        self.assertEqual(set(result), {"ok", "reason", "drift_notice"})
        self.assertNotIn(str(step), [str(v) for v in result.values()])


class TestConcurrency(Base):
    """Two real threads, one real sqlite file: exactly one of them may win."""

    ROUNDS = 20

    def _race(self, factor_id, code):
        barrier = threading.Barrier(2)
        results, lock = [], threading.Lock()

        def worker():
            barrier.wait()
            outcome = self.verify(code, factor_id=factor_id)
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(len(results), 2)
        return results

    def test_concurrent_verifications_cannot_both_win(self):
        step = self.step_at(FIXED_NOW)
        code = self.code_for(step)
        for round_no in range(self.ROUNDS):
            factor_id = f"{FACTOR_ID}-{round_no}"
            results = self._race(factor_id, code)
            reasons = sorted(r["reason"] for r in results)
            self.assertEqual(reasons, ["ok", "replay"], f"round {round_no}: {reasons}")
            self.assertEqual([r["ok"] for r in results].count(True), 1)
            rows = self.store.inspect(
                "SELECT * FROM mfa_used_totp_steps WHERE factor_id = ? AND time_step = ?",
                (factor_id, step))
            self.assertEqual(len(rows), 1, f"round {round_no}")


class TestMalformedInputDoesNoDatabaseWork(Base):
    def test_rejected_before_any_store_access(self):
        step = self.step_at(FIXED_NOW)
        good = self.code_for(step)
        bad = ["", "   ", "abcdef", "12345", "1234567", "12 456", "12-456",
               "\u0661\u0662\u0663\u0664\u0665\u0666",  # Arabic-Indic digits
               None, good + "0"]
        for candidate in bad:
            with self.subTest(length=len(candidate) if candidate else 0):
                result = self.verify(candidate)
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "invalid")
        self.assertEqual(self.store.calls, [])
        self.assertEqual(self.event_types(), [])

    def test_empty_secret_does_no_store_access(self):
        result = totp_auth.verify_totp(
            "", self.code_for(self.step_at(FIXED_NOW)),
            factor_id=FACTOR_ID, user_id=USER_ID, now=FIXED_NOW,
            consume_step=self.store.try_consume_totp_step,
            log_event=self.store.log_event,
            recent_events=self.store.recent_events)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid")
        self.assertEqual(self.store.calls, [])

    def test_canonical_code_guard(self):
        self.assertEqual(totp_auth.canonical_code(" 123456 "), "123456")
        for bad in ("12345", "1234567", "abcdef", "", None, "12345a"):
            self.assertIsNone(totp_auth.canonical_code(bad))


class TestAuditLogCarriesNoSecrets(Base):
    def test_failure_event_contains_neither_code_nor_secret(self):
        submitted = "000000"
        step = self.step_at(FIXED_NOW)
        if submitted == self.code_for(step):     # astronomically unlikely
            submitted = "000001"
        self.verify(submitted)
        rows = self.store.inspect("SELECT * FROM mfa_events")
        self.assertTrue(rows)
        for row in rows:
            blob = "|".join("" if v is None else str(v) for v in tuple(row))
            self.assertNotIn(self.secret, blob)
            self.assertNotIn(submitted, blob)
        self.assertEqual(self.event_types(), ["totp_verify_failed"])

    def test_replay_store_failure_fails_closed(self):
        def exploding_consume(factor_id, time_step, user_id=None):
            raise RuntimeError("replay store down")

        result = totp_auth.verify_totp(
            self.secret, self.code_for(self.step_at(FIXED_NOW)),
            factor_id=FACTOR_ID, user_id=USER_ID, now=FIXED_NOW,
            consume_step=exploding_consume,
            log_event=self.store.log_event,
            recent_events=self.store.recent_events)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "locked")
        self.assertIn("replay_store_unavailable", self.event_types())


class TestEnrolmentPath(Base):
    def test_factor_id_none_accepts_without_touching_the_replay_store(self):
        code = self.code_for(self.step_at(FIXED_NOW))
        result = self.verify(code, factor_id=None)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "ok")
        self.assertNotIn("try_consume_totp_step", self.store.calls)
        self.assertEqual(self.event_types(), ["totp_verify_ok_exact"])

    def test_enrolment_rejects_a_wrong_code(self):
        result = self.verify("000000", factor_id=None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid")


class TestClockDriftDerivation(Base):
    """Drift is derived from mfa_events; the window is never widened per user."""

    def _verify_skewed(self, index):
        moment = FIXED_NOW + timedelta(seconds=30 * index)
        code = self.code_for(self.step_at(moment) - 1)
        return self.verify(code, now=moment)

    def _verify_exact(self, index):
        moment = FIXED_NOW + timedelta(seconds=30 * index)
        return self.verify(self.code_for(self.step_at(moment)), now=moment)

    def test_five_consecutive_skewed_successes_raise_the_notice_once(self):
        for i in range(4):
            self.assertFalse(self._verify_skewed(i)["drift_notice"], f"call {i}")
        self.assertTrue(self._verify_skewed(4)["drift_notice"])
        self.assertEqual(self.event_types().count("clock_drift_suspected"), 1)
        # Already reported inside the inspected window -> not raised again.
        self.assertFalse(self._verify_skewed(5)["drift_notice"])
        self.assertEqual(self.event_types().count("clock_drift_suspected"), 1)

    def test_exact_successes_never_raise_the_notice(self):
        for i in range(6):
            self.assertFalse(self._verify_exact(i)["drift_notice"], f"call {i}")
        self.assertNotIn("clock_drift_suspected", self.event_types())
        self.assertEqual(self.event_types().count("totp_verify_ok_exact"), 6)

    def test_an_exact_success_breaks_the_run(self):
        for i in range(4):
            self._verify_skewed(i)
        self.assertFalse(self._verify_exact(4)["drift_notice"])
        self.assertFalse(self._verify_skewed(5)["drift_notice"])
        self.assertNotIn("clock_drift_suspected", self.event_types())


if __name__ == "__main__":
    unittest.main()
