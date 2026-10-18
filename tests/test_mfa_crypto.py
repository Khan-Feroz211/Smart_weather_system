"""
Tests for mfa_crypto: Fernet/MultiFernet secret encryption, key rotation and ip_hash.

Run from the project root:  python -m unittest tests.test_mfa_crypto -v

Hermetic: no network, no database, no dev credentials. Every environment
variable is patched with unittest.mock.patch.dict -- the real .env is never
read. Fixture keys/secrets are generated per test and are never printed.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from cryptography.fernet import Fernet, MultiFernet  # noqa: E402

import mfa_crypto  # noqa: E402

SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"  # throwaway base32-looking fixture


def env(**values):
    """patch.dict that also drops the MFA vars not explicitly provided."""
    base = {k: v for k, v in os.environ.items()
            if k not in (mfa_crypto.ENCRYPTION_KEY_ENV, mfa_crypto.IP_PEPPER_ENV)}
    base.update(values)
    return mock.patch.dict(os.environ, base, clear=True)


class CryptoBase(unittest.TestCase):
    def setUp(self):
        self.key_a = Fernet.generate_key().decode()
        self.key_b = Fernet.generate_key().decode()
        mfa_crypto.reset_cache()
        self.addCleanup(mfa_crypto.reset_cache)


class TestRoundTrip(CryptoBase):
    def test_round_trip(self):
        with env(MFA_ENCRYPTION_KEY=self.key_a):
            token = mfa_crypto.encrypt_secret(SECRET)
            self.assertEqual(mfa_crypto.decrypt_secret(token), SECRET)

    def test_ciphertext_is_not_the_plaintext(self):
        with env(MFA_ENCRYPTION_KEY=self.key_a):
            token = mfa_crypto.encrypt_secret(SECRET)
            self.assertNotIn(SECRET, token)
            self.assertNotIn(SECRET.encode(), token.encode())

    def test_ciphertext_differs_between_calls(self):
        with env(MFA_ENCRYPTION_KEY=self.key_a):
            self.assertNotEqual(mfa_crypto.encrypt_secret(SECRET),
                                mfa_crypto.encrypt_secret(SECRET))

    def test_empty_values_rejected(self):
        with env(MFA_ENCRYPTION_KEY=self.key_a):
            with self.assertRaises(mfa_crypto.MfaCryptoError):
                mfa_crypto.encrypt_secret("")
            with self.assertRaises(mfa_crypto.MfaCryptoError):
                mfa_crypto.decrypt_secret("")


class TestKeyErrors(CryptoBase):
    def test_missing_variable(self):
        with env():
            with self.assertRaises(mfa_crypto.MfaCryptoError) as ctx:
                mfa_crypto.encrypt_secret(SECRET)
            self.assertIn(mfa_crypto.ENCRYPTION_KEY_ENV, str(ctx.exception))

    def test_empty_variable(self):
        with env(MFA_ENCRYPTION_KEY="   "):
            with self.assertRaises(mfa_crypto.MfaCryptoError):
                mfa_crypto.encrypt_secret(SECRET)

    def test_comma_only_variable(self):
        with env(MFA_ENCRYPTION_KEY=" , , "):
            with self.assertRaises(mfa_crypto.MfaCryptoError):
                mfa_crypto.encrypt_secret(SECRET)

    def test_garbage_key_message_has_no_key_material(self):
        garbage = "not-a-valid-fernet-key-value"
        with env(MFA_ENCRYPTION_KEY=f"{self.key_a},{garbage}"):
            with self.assertRaises(mfa_crypto.MfaCryptoError) as ctx:
                mfa_crypto.encrypt_secret(SECRET)
            message = str(ctx.exception)
            self.assertNotIn(garbage, message)
            self.assertNotIn(self.key_a, message)
            self.assertIn("#2", message)

    def test_foreign_token_raises_without_leaking(self):
        foreign = Fernet(Fernet.generate_key()).encrypt(SECRET.encode()).decode()
        with env(MFA_ENCRYPTION_KEY=self.key_a):
            with self.assertRaises(mfa_crypto.MfaCryptoError) as ctx:
                mfa_crypto.decrypt_secret(foreign)
            message = str(ctx.exception)
            self.assertNotIn(foreign, message)
            self.assertNotIn(self.key_a, message)


class TestRotation(CryptoBase):
    def test_old_key_still_decrypts_when_listed_second(self):
        with env(MFA_ENCRYPTION_KEY=self.key_a):
            token = mfa_crypto.encrypt_secret(SECRET)
        mfa_crypto.reset_cache()
        with env(MFA_ENCRYPTION_KEY=f"{self.key_b},{self.key_a}"):
            self.assertEqual(mfa_crypto.decrypt_secret(token), SECRET)

    def test_new_key_alone_cannot_read_old_ciphertext(self):
        with env(MFA_ENCRYPTION_KEY=self.key_a):
            token = mfa_crypto.encrypt_secret(SECRET)
        mfa_crypto.reset_cache()
        with env(MFA_ENCRYPTION_KEY=self.key_b):
            with self.assertRaises(mfa_crypto.MfaCryptoError):
                mfa_crypto.decrypt_secret(token)

    def test_rotated_value_decrypts_under_new_key_alone(self):
        old_token = Fernet(self.key_a.encode()).encrypt(SECRET.encode())
        rotated = MultiFernet(
            [Fernet(self.key_b.encode()), Fernet(self.key_a.encode())]
        ).rotate(old_token).decode()
        mfa_crypto.reset_cache()
        with env(MFA_ENCRYPTION_KEY=self.key_b):
            self.assertEqual(mfa_crypto.decrypt_secret(rotated), SECRET)

    def test_decrypts_with_primary(self):
        with env(MFA_ENCRYPTION_KEY=self.key_a):
            token = mfa_crypto.encrypt_secret(SECRET)
        mfa_crypto.reset_cache()
        with env(MFA_ENCRYPTION_KEY=f"{self.key_b},{self.key_a}"):
            self.assertFalse(mfa_crypto.decrypts_with_primary(token))
            self.assertTrue(mfa_crypto.decrypts_with_primary(
                mfa_crypto.encrypt_secret(SECRET)))

    def test_cache_follows_the_key_string(self):
        with env(MFA_ENCRYPTION_KEY=self.key_a):
            first = mfa_crypto.get_multifernet()
            self.assertIs(first, mfa_crypto.get_multifernet())
        with env(MFA_ENCRYPTION_KEY=self.key_b):
            self.assertIsNot(first, mfa_crypto.get_multifernet())


class TestIpHash(CryptoBase):
    def test_stable_and_not_the_input(self):
        with env(MFA_IP_PEPPER="pepper-value-long-enough"):
            first = mfa_crypto.ip_hash("203.0.113.7")
            self.assertEqual(first, mfa_crypto.ip_hash("203.0.113.7"))
            self.assertNotIn("203.0.113.7", first)
            self.assertEqual(len(first), 64)

    def test_differs_per_pepper(self):
        with env(MFA_IP_PEPPER="pepper-one-value-long"):
            first = mfa_crypto.ip_hash("203.0.113.7")
        with env(MFA_IP_PEPPER="pepper-two-value-long"):
            self.assertNotEqual(first, mfa_crypto.ip_hash("203.0.113.7"))

    def test_requires_pepper(self):
        with env():
            with self.assertRaises(mfa_crypto.MfaCryptoError) as ctx:
                mfa_crypto.ip_hash("203.0.113.7")
            self.assertIn(mfa_crypto.IP_PEPPER_ENV, str(ctx.exception))
            self.assertNotIn("203.0.113.7", str(ctx.exception))

    def test_opaque_hash_handles_empty(self):
        with env(MFA_IP_PEPPER="pepper-value-long-enough"):
            self.assertIsNone(mfa_crypto.opaque_hash(""))
            self.assertEqual(len(mfa_crypto.opaque_hash("Chrome")), 64)

    def test_token_hash_is_not_the_token(self):
        token = "f" * 64
        digest = mfa_crypto.token_hash(token)
        self.assertNotEqual(digest, token)
        self.assertEqual(len(digest), 64)
        with self.assertRaises(mfa_crypto.MfaCryptoError):
            mfa_crypto.token_hash("")


if __name__ == "__main__":
    unittest.main()
