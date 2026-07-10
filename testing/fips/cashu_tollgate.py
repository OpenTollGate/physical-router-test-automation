#!/usr/bin/env python3
"""Cashu payment → FIPS forwarding policy bridge.

Creates a Cashu Lightning invoice for mesh transit. When the invoice is
paid, calls FIPS `set_peer_policy` to enable Full forwarding for the
specified peer. After the paid duration expires, reverts to LocalOnly.

Designed for the testnut mint (FakeWallet — all invoices auto-PAID),
but works with any Cashu NUT-05 compliant mint.

Usage (standalone):
    python3 cashu_tollgate.py --mint https://testnut.cashu.exchange \\
        --fips-ip 66.92.204.236 --peer-npub npub1... --amount 21 --duration 60

Usage (as module in run_test.py):
    from cashu_tollgate import CashuTollgate
    gate = CashuTollgate(mint_url, fips_ip)
    quote = gate.create_invoice(21)
    gate.wait_for_payment(quote["quote"])
    gate.enable_transit(peer_npub, duration_secs=60)
    # ... run transit tests ...
    gate.disable_transit(peer_npub)  # or wait for auto-expiry
"""
import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10",
    "-o", "LogLevel=ERROR",
]


class CashuTollgate:
    """Bridge between Cashu payments and FIPS forwarding policy."""

    def __init__(
        self,
        mint_url: str,
        fips_ip: str,
        fips_user: str = "debian",
        unit: str = "sat",
    ):
        self.mint_url = mint_url.rstrip("/")
        self.fips_ip = fips_ip
        self.fips_user = fips_user
        self.unit = unit
        self._timers: dict[str, threading.Timer] = {}

    # ── Cashu Mint API ────────────────────────────────────────────

    def _api(self, method: str, path: str, data: dict | None = None) -> dict:
        """Make a Cashu mint API call."""
        url = f"{self.mint_url}{path}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        if body:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            raise RuntimeError(f"Mint API {method} {path} failed: {e.code} {error_body}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Mint API {method} {path} unreachable: {e.reason}")

    def create_invoice(self, amount_sats: int) -> dict:
        """Create a Cashu melt quote (Lightning invoice).

        Returns dict with: quote, request (bolt11), state, amount, unit, expiry.
        On testnut (FakeWallet), the quote is immediately PAID.
        """
        result = self._api("POST", "/v1/mint/quote/bolt11", {
            "unit": self.unit,
            "amount": amount_sats,
        })
        return result

    def check_payment(self, quote_id: str) -> bool:
        """Check if a melt quote has been paid. Returns True if PAID."""
        result = self._api("GET", f"/v1/mint/quote/bolt11/{quote_id}")
        return result.get("state") == "PAID"

    def wait_for_payment(self, quote_id: str, timeout: int = 300, poll_interval: int = 2) -> bool:
        """Poll until payment is received or timeout. Returns True if paid."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.check_payment(quote_id):
                return True
            time.sleep(poll_interval)
        return False

    # ── FIPS Control Socket ───────────────────────────────────────

    def _fipsctl(self, command_json: str) -> dict:
        """Send a raw command to the FIPS control socket via SSH + nc."""
        cmd = f"echo '{command_json}' | sudo nc -U /run/fips/control.sock 2>/dev/null"
        r = subprocess.run(
            ["ssh"] + SSH_OPTS + [f"{self.fips_user}@{self.fips_ip}", cmd],
            capture_output=True, text=True, timeout=15,
        )
        try:
            return json.loads(r.stdout.strip())
        except (json.JSONDecodeError, ValueError):
            return {"raw": r.stdout.strip(), "error": r.stderr.strip()}

    def set_peer_policy(self, peer_npub: str, policy: str) -> bool:
        """Set a peer's forwarding policy via FIPS control socket.

        Args:
            peer_npub: The peer's npub (bech32).
            policy: "full" or "local_only".

        Returns True if the policy was successfully set.
        """
        cmd = json.dumps({
            "command": "set_peer_policy",
            "params": {"npub": peer_npub, "policy": policy},
        })
        result = self._fipsctl(cmd)
        ok = result.get("status") == "ok"
        if ok:
            print(f"  FIPS policy set: {peer_npub[:20]}... → {policy}")
        else:
            print(f"  FIPS policy FAILED: {result}")
        return ok

    def enable_transit(self, peer_npub: str, duration_secs: int = 3600) -> bool:
        """Enable Full forwarding for a peer, auto-revert after duration.

        Args:
            peer_npub: The peer's npub.
            duration_secs: How long to allow transit (default 1 hour).

        Returns True if policy was set successfully.
        """
        ok = self.set_peer_policy(peer_npub, "full")
        if not ok:
            return False

        # Schedule auto-revert
        timer = threading.Timer(
            duration_secs,
            self._auto_revert,
            args=[peer_npub],
        )
        timer.daemon = True
        timer.start()
        self._timers[peer_npub] = timer
        print(f"  Transit enabled for {duration_secs}s (auto-revert scheduled)")
        return True

    def disable_transit(self, peer_npub: str) -> bool:
        """Revert a peer's forwarding policy to LocalOnly immediately.

        Cancels any pending auto-revert timer.
        """
        timer = self._timers.pop(peer_npub, None)
        if timer:
            timer.cancel()
        return self.set_peer_policy(peer_npub, "local_only")

    def _auto_revert(self, peer_npub: str):
        """Timer callback: revert policy to LocalOnly."""
        print(f"  [TIMER] Auto-reverting {peer_npub[:20]}... → local_only")
        self.set_peer_policy(peer_npub, "local_only")
        self._timers.pop(peer_npub, None)

    # ── Full Payment Flow ─────────────────────────────────────────

    def pay_and_enable(
        self,
        peer_npub: str,
        amount_sats: int = 21,
        duration_secs: int = 3600,
    ) -> dict | None:
        """Full TollGate flow: create invoice, wait for payment, enable transit.

        Returns a dict with quote details and timing, or None on failure.
        """
        t0 = time.time()

        # 1. Create invoice
        print(f"  Creating invoice: {amount_sats} sats for {duration_secs}s transit...")
        quote = self.create_invoice(amount_sats)
        quote_id = quote["quote"]
        print(f"  Quote: {quote_id}")
        print(f"  Invoice: {quote.get('request', 'N/A')[:60]}...")

        # 2. Wait for payment (FakeWallet auto-pays on testnut)
        print(f"  Waiting for payment...")
        paid = self.wait_for_payment(quote_id, timeout=30, poll_interval=1)
        t_paid = time.time()

        if not paid:
            print(f"  Payment TIMEOUT")
            return None

        print(f"  Payment confirmed ({t_paid - t0:.1f}s)")

        # 3. Enable transit
        ok = self.enable_transit(peer_npub, duration_secs)
        if not ok:
            print(f"  Failed to enable transit")
            return None

        t_enabled = time.time()
        print(f"  Transit enabled ({t_enabled - t_paid:.1f}s after payment)")

        return {
            "quote_id": quote_id,
            "amount_sats": amount_sats,
            "duration_secs": duration_secs,
            "payment_time": round(t_paid - t0, 2),
            "enable_time": round(t_enabled - t_paid, 2),
            "total_time": round(t_enabled - t0, 2),
            "peer_npub": peer_npub,
            "transit_active": True,
            "expires_at": time.time() + duration_secs,
        }


# ── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Cashu → FIPS TollGate payment bridge")
    parser.add_argument("--mint", default="https://testnut.cashu.exchange", help="Cashu mint URL")
    parser.add_argument("--fips-ip", required=True, help="FIPS transit node IP")
    parser.add_argument("--fips-user", default="debian")
    parser.add_argument("--peer-npub", required=True, help="Peer npub to enable transit for")
    parser.add_argument("--amount", type=int, default=21, help="Amount in sats")
    parser.add_argument("--duration", type=int, default=60, help="Transit duration in seconds")
    args = parser.parse_args()

    gate = CashuTollgate(args.mint, args.fips_ip, args.fips_user)

    print(f"TollGate Payment Bridge")
    print(f"  Mint: {args.mint}")
    print(f"  FIPS: {args.fips_ip}")
    print(f"  Peer: {args.peer_npub[:20]}...")
    print(f"  Price: {args.amount} sats for {args.duration}s")
    print()

    result = gate.pay_and_enable(args.peer_npub, args.amount, args.duration)
    if result:
        print(f"\nTransit active! Waiting {args.duration}s for expiry...")
        print(f"  (Press Ctrl+C to revert early)")
        try:
            time.sleep(args.duration + 5)
        except KeyboardInterrupt:
            gate.disable_transit(args.peer_npub)
            print("Transit reverted.")
    else:
        print("Payment flow failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
