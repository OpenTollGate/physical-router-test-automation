import time


class FakeSubscription:
    def __init__(self, state="pending_payment", amount_sats=1000, duration_days=30):
        self.id = 1
        self.state = state
        self.amount_sats = amount_sats
        self.duration_days = duration_days
        self.expires_at = None
        self.paid_at = None
        self.renewed_count = 0
        self.mint_quote_id = None
        self.bolt11_invoice = None

    def activate(self):
        if self.state != "pending_payment":
            raise ValueError(f"Cannot activate from state {self.state}")
        self.state = "active"
        self.paid_at = time.time()
        self.expires_at = time.time() + (self.duration_days * 86400)

    def expire(self):
        self.state = "expired"

    def renew(self, amount_sats, duration_days):
        if self.state != "active":
            raise ValueError(f"Can only renew active subscriptions, got {self.state}")
        self.state = "pending_payment"
        self.amount_sats = amount_sats
        self.duration_days = duration_days
        self.renewed_count += 1

    def is_expired(self):
        if self.state != "active":
            return False
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at
