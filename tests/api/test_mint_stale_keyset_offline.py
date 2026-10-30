"""Regression: a stale keyset for an unreachable mint must not degrade the portal.

Field incident: `mint.minibits.cash` was down with EOF while five other accepted
mints were healthy. The wallet DB still held an inactive keyset for minibits
(stored without public keys). On restart, `loadWalletMints()` tried to fetch
that keyset, got EOF, and — because `isNetworkError()` did not classify EOF —
aborted the whole wallet. The merchant stayed degraded and the captive portal
showed `no-reachable-mints`.

The fix (gonuts `isNetworkError`) skips the keyset and keeps its DB record and
proofs. This test reproduces the state on a real router:

1. add a local fake mint (one active keyset + one inactive keyset without keys);
2. restart so the wallet stores both keysets;
3. switch the fake mint to EOF and restart;
4. assert the backend is in full mode (kind 10021), the portal is up, and the
   inactive keyset record is still in wallet.db.

Requires a healthy local mint (TOLLGATE_LOCAL_MINT_URL) that the router can
reach, plus a test host the router can reach (TOLLGATE_VIRTUAL_HOST in the
virtual lab). Skips cleanly otherwise.
"""

import json
import os
import socket
import time

import pytest

from lib.fake_mint import FakeMintServer, INACTIVE_KEYSET_ID

pytestmark = [
    pytest.mark.api,
    pytest.mark.extended,
    pytest.mark.timeout(420),
]

CONFIG_BACKUP = "/etc/tollgate/config.json.stale-mint-test-backup"
WALLET_BACKUP = "/etc/tollgate/wallet.db.stale-mint-test-backup"


def _get_local_ip():
    if os.environ.get("TOLLGATE_VIRTUAL_LAB"):
        return os.environ.get("TOLLGATE_VIRTUAL_HOST", "10.99.99.2")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _wait_backend(router, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if router.api_status("/") == 200:
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def _kind(router):
    try:
        return json.loads(router.api_body("/")).get("kind")
    except Exception:
        return None


def _keyset_present(router, keyset_id):
    try:
        out = router.ssh(f"grep -ac {keyset_id} /etc/tollgate/wallet.db 2>/dev/null || true").strip()
        return out not in ("", "0")
    except Exception:
        return False


@pytest.fixture
def stale_mint():
    server = FakeMintServer(mode="keysets")
    server.start()
    yield server
    server.stop()


@pytest.fixture
def seeded(router, stale_mint):
    healthy = os.environ.get("TOLLGATE_LOCAL_MINT_URL")
    if not healthy:
        pytest.skip("TOLLGATE_LOCAL_MINT_URL not set — need a healthy local mint")

    stale_url = f"http://{_get_local_ip()}:{stale_mint.port}"

    router.ssh(f"cp /etc/tollgate/config.json {CONFIG_BACKUP}")
    if router.ssh(f"[ -f /etc/tollgate/wallet.db ] && echo yes").strip() == "yes":
        router.restart_backend()
        time.sleep(2)
        router.ssh(f"cp /etc/tollgate/wallet.db {WALLET_BACKUP}")

    cfg = json.loads(router.ssh("cat /etc/tollgate/config.json"))
    template = next(
        (m for m in cfg.get("accepted_mints", []) if m.get("url") == healthy),
        {
            "min_balance": 64,
            "balance_tolerance_percent": 10,
            "payout_interval_seconds": 60,
            "min_payout_amount": 128,
            "price_per_step": 1,
            "price_unit": "sat",
            "purchase_min_steps": 1,
        },
    )
    cfg["accepted_mints"] = [dict(template, url=healthy), dict(template, url=stale_url)]
    router.write_remote_json("/etc/tollgate/config.json", cfg, indent=None)

    try:
        yield stale_url
    finally:
        router.ssh(f"cat {CONFIG_BACKUP} > /etc/tollgate/config.json")
        router.ssh(f"rm -f {CONFIG_BACKUP}")
        if router.ssh(f"[ -f {WALLET_BACKUP} ] && echo yes").strip() == "yes":
            router.restart_backend()
            time.sleep(2)
            router.ssh(f"cat {WALLET_BACKUP} > /etc/tollgate/wallet.db")
            router.ssh(f"rm -f {WALLET_BACKUP}")
        router.restart_backend()
        time.sleep(5)


def test_stale_keyset_survives_unreachable_mint(router, stale_mint, seeded):
    # 1. Seed: the wallet registers both keysets while the mint is healthy.
    router.restart_backend()
    assert _wait_backend(router), "backend did not come up with the keysets mint"

    seeded_ok = False
    for _ in range(30):
        if _keyset_present(router, INACTIVE_KEYSET_ID):
            seeded_ok = True
            break
        time.sleep(2)
    assert seeded_ok, "inactive keyset was not persisted in wallet.db (seed failed)"

    # 2. Mint becomes unreachable with EOF (process down / crash / restart).
    stale_mint.set_mode("eof")
    router.restart_backend()
    assert _wait_backend(router), "backend did not come up with the stale mint"

    # 3. The portal must be in full mode, not degraded.
    body = router.api_body("/")
    assert "no-reachable-mints" not in body, body[:300]
    assert _kind(router) == 10021, f"expected kind 10021 (full), got {_kind(router)}: {body[:300]}"

    # 4. Fund safety: the keyset record is retained for when the mint returns.
    assert _keyset_present(router, INACTIVE_KEYSET_ID), "inactive keyset record was lost"
