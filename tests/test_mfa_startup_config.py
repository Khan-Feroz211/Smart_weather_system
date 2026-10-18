"""
Tests for mfa_crypto.validate_mfa_config (the startup guard).

Run from the project root:  python -m unittest tests.test_mfa_startup_config -v

Hermetic: every variable is patched with unittest.mock.patch.dict; the real
.env is never read. Fixture values are generated per test and never printed.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from cryptography.fernet import Fernet  # noqa: E402

import mfa_crypto  # noqa: E402

PEPPER = "test-pepper-value-not-a-real-secret"
STRONG_SECRET_KEY = "a1b2" * 16  # 64 chars, not weak, not a real key


def env(**values):
    """Replace the environment entirely, so nothing leaks in from the shell."""
    return mock.patch.dict(os.environ, values, clear=True)


class ConfigBase(unittest.TestCase):
    def setUp(self):
        self.key = Fernet.generate_key().decode()
        mfa_crypto.reset_cache()
        self.addCleanup(mfa_crypto.reset_cache)

    def good_env(self, **overrides):
        values = {
            "DEBUG": "False",
            mfa_crypto.ENCRYPTION_KEY_ENV: self.key,
            mfa_crypto.IP_PEPPER_ENV: PEPPER,
            mfa_crypto.SECRET_KEY_ENV: STRONG_SECRET_KEY,
        }
        values.update(overrides)
        return env(**values)


class TestStrictMode(ConfigBase):
    def test_clean_config_returns_empty_list(self):
        with self.good_env():
            self.assertEqual(mfa_crypto.validate_mfa_config(), [])

    def test_missing_encryption_key_fails_startup(self):
        values = {
            "DEBUG": "False",
            mfa_crypto.IP_PEPPER_ENV: PEPPER,
            mfa_crypto.SECRET_KEY_ENV: STRONG_SECRET_KEY,
        }
        with env(**values):
            with self.assertRaises(mfa_crypto.MfaConfigError) as ctx:
                mfa_crypto.validate_mfa_config()
            self.assertIn(mfa_crypto.ENCRYPTION_KEY_ENV, str(ctx.exception))

    def test_missing_pepper_fails_startup(self):
        with self.good_env():
            os.environ.pop(mfa_crypto.IP_PEPPER_ENV)
            with self.assertRaises(mfa_crypto.MfaConfigError) as ctx:
                mfa_crypto.validate_mfa_config()
            self.assertIn(mfa_crypto.IP_PEPPER_ENV, str(ctx.exception))

    def test_short_pepper_fails_startup(self):
        with self.good_env(**{mfa_crypto.IP_PEPPER_ENV: "short"}):
            with self.assertRaises(mfa_crypto.MfaConfigError):
                mfa_crypto.validate_mfa_config()

    def test_malformed_encryption_key_fails_startup(self):
        with self.good_env(**{mfa_crypto.ENCRYPTION_KEY_ENV: "definitely-not-a-fernet-key"}):
            with self.assertRaises(mfa_crypto.MfaConfigError) as ctx:
                mfa_crypto.validate_mfa_config()
            self.assertIn(mfa_crypto.ENCRYPTION_KEY_ENV, str(ctx.exception))

    def test_weak_default_secret_key_rejected(self):
        with self.good_env(**{mfa_crypto.SECRET_KEY_ENV: "smart_weather_ai_2024"}):
            with self.assertRaises(mfa_crypto.MfaConfigError) as ctx:
                mfa_crypto.validate_mfa_config()
            self.assertIn(mfa_crypto.SECRET_KEY_ENV, str(ctx.exception))

    def test_short_secret_key_rejected(self):
        with self.good_env(**{mfa_crypto.SECRET_KEY_ENV: "abcdef"}):
            with self.assertRaises(mfa_crypto.MfaConfigError):
                mfa_crypto.validate_mfa_config()

    def test_missing_secret_key_rejected(self):
        with self.good_env():
            os.environ.pop(mfa_crypto.SECRET_KEY_ENV)
            with self.assertRaises(mfa_crypto.MfaConfigError):
                mfa_crypto.validate_mfa_config()

    def test_message_names_variables_but_no_values(self):
        with env(DEBUG="False", **{mfa_crypto.ENCRYPTION_KEY_ENV: "bad-key-fixture-value",
                                   mfa_crypto.IP_PEPPER_ENV: PEPPER,
                                   mfa_crypto.SECRET_KEY_ENV: STRONG_SECRET_KEY}):
            with self.assertRaises(mfa_crypto.MfaConfigError) as ctx:
                mfa_crypto.validate_mfa_config()
            message = str(ctx.exception)
            self.assertIn(mfa_crypto.ENCRYPTION_KEY_ENV, message)
            self.assertNotIn("bad-key-fixture-value", message)
            self.assertNotIn(PEPPER, message)
            self.assertNotIn(STRONG_SECRET_KEY, message)


class TestDebugMode(ConfigBase):
    def test_debug_does_not_raise_and_reports_problems(self):
        with env(DEBUG="True"):
            with self.assertLogs("mfa_crypto", level="WARNING") as logs:
                problems = mfa_crypto.validate_mfa_config()
            self.assertTrue(problems)
            self.assertTrue(any(mfa_crypto.ENCRYPTION_KEY_ENV in line for line in logs.output))

    def test_strict_defaults_to_not_debug(self):
        with env(DEBUG="False"):
            with self.assertRaises(mfa_crypto.MfaConfigError):
                mfa_crypto.validate_mfa_config()

    def test_explicit_strict_false_overrides_debug_off(self):
        with env(DEBUG="False"):
            with self.assertLogs("mfa_crypto", level="WARNING"):
                self.assertTrue(mfa_crypto.validate_mfa_config(strict=False))

    def test_explicit_strict_true_overrides_debug_on(self):
        with env(DEBUG="True"):
            with self.assertRaises(mfa_crypto.MfaConfigError):
                mfa_crypto.validate_mfa_config(strict=True)

    def test_clean_config_in_debug_returns_empty_list(self):
        with self.good_env(DEBUG="True"):
            self.assertEqual(mfa_crypto.validate_mfa_config(), [])


if __name__ == "__main__":
    unittest.main()
