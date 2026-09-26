import hashlib
import hmac
import os
import time
import unittest

from backend.credits import CreditStore, credits_for_duration
from backend.webhooks import validate_x_signature


class TestBackendSecurity(unittest.TestCase):
    def test_hmac_signature_is_verified(self):
        secret = "test-secret"
        request_id = "request-123"
        data_id = "987654"
        timestamp = str(int(time.time()))
        manifest = f"id:{data_id};request-id:{request_id};ts:{timestamp};"
        signature = hmac.new(
            secret.encode(),
            manifest.encode(),
            hashlib.sha256,
        ).hexdigest()

        old = os.environ.get("MP_WEBHOOK_SECRET")
        os.environ["MP_WEBHOOK_SECRET"] = secret
        try:
            validate_x_signature(
                f"ts={timestamp},v1={signature}",
                request_id,
                data_id,
            )
        finally:
            if old is None:
                os.environ.pop("MP_WEBHOOK_SECRET", None)
            else:
                os.environ["MP_WEBHOOK_SECRET"] = old

    def test_credit_charge_and_idempotence(self):
        store = CreditStore()
        store._balances["user-test"] = 0
        applied, balance = store.apply_payment_once("payment-1", "user-test", 60)
        self.assertTrue(applied)
        self.assertEqual(balance, 60)
        applied_again, balance_again = store.apply_payment_once(
            "payment-1", "user-test", 60
        )
        self.assertFalse(applied_again)
        self.assertEqual(balance_again, 60)

    def test_duration_rounds_up_to_full_credit(self):
        self.assertEqual(credits_for_duration(1), 1)
        self.assertEqual(credits_for_duration(61), 2)


if __name__ == "__main__":
    unittest.main()
