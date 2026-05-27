import time

import pytest

from tests.unit.vpn.conftest_vpn import FakeSubscription


class TestSubscriptionLifecycle:
    def test_pending_to_active(self):
        s = FakeSubscription(state="pending_payment", amount_sats=1000, duration_days=30)
        s.activate()
        assert s.state == "active"
        assert s.paid_at is not None
        assert s.expires_at is not None
        assert s.expires_at > time.time()

    def test_active_to_expired(self):
        s = FakeSubscription(state="active", amount_sats=1000, duration_days=30)
        s.expire()
        assert s.state == "expired"

    def test_renewal_resets_to_pending(self):
        s = FakeSubscription(state="active", amount_sats=1000, duration_days=30)
        s.renew(2000, 30)
        assert s.state == "pending_payment"
        assert s.amount_sats == 2000
        assert s.renewed_count == 1

    def test_double_renewal(self):
        s = FakeSubscription(state="active", amount_sats=1000, duration_days=30)
        s.renew(1000, 30)
        s.activate()
        s.renew(1000, 30)
        assert s.renewed_count == 2

    def test_cannot_renew_expired(self):
        s = FakeSubscription(state="expired", amount_sats=1000, duration_days=30)
        with pytest.raises(ValueError, match="active"):
            s.renew(1000, 30)


class TestWorkerExpiryLogic:
    def test_expired_subscription_detected(self):
        s = FakeSubscription(state="active", amount_sats=1000, duration_days=30)
        s.expires_at = time.time() - 3600
        assert s.is_expired()

    def test_active_subscription_not_expired(self):
        s = FakeSubscription(state="active", amount_sats=1000, duration_days=30)
        s.expires_at = time.time() + 86400 * 30
        assert not s.is_expired()

    def test_pending_subscription_not_expired(self):
        s = FakeSubscription(state="pending_payment", amount_sats=1000, duration_days=30)
        assert not s.is_expired()

    def test_worker_expires_multiple(self):
        subs = [
            FakeSubscription(state="active", amount_sats=1000, duration_days=30),
            FakeSubscription(state="active", amount_sats=2000, duration_days=30),
            FakeSubscription(state="active", amount_sats=3000, duration_days=30),
        ]
        subs[0].expires_at = time.time() - 100
        subs[1].expires_at = time.time() + 99999
        subs[2].expires_at = time.time() - 200
        expired = [s for s in subs if s.is_expired()]
        assert len(expired) == 2
