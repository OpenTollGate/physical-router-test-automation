"""Regression: a stale keyset for an unreachable mint must not degrade the portal.

Field incident: `mint.minibits.cash` was down with EOF while five other accepted
mints were healthy. The wallet DB still held an inactive keyset for minibits
(stored without public keys). On restart, `loadWalletMints()` tried to fetch
that keyset, got EOF, and — because `isNetworkError()` did not classify EOF —
aborted the whole wallet. The merchant stayed degraded and the captive portal
showed `no-reachable-mints`.

The fix (gonuts `isNetworkError`) skips the keyset and keeps its DB record and
proofs. This test reproduces the state on a real router without any external
mint:

1. two local fake mints: a healthy one and a "stale" one that advertises an
   inactive keyset (stored without public keys, so the next load must fetch it);
2. configure both as accepted mints, restart, and wait until the stale keyset
   is persisted in wallet.db;
3. switch the stale mint to EOF (accept then close, the shape of a crashed or
   restarting mint) and restart;
4. assert the backend is in full mode (kind 10021), the portal has no
   `no-reachable-mints`, and the stale keyset record is still in wallet.db.

Runs on the local QEMU lab or a physical router; the fake mints bind on the
test host and the router reaches them over the LAN.
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
def mints():
    healthy = FakeMintServer(mode="keysets")
    stale = FakeMintServer(mode="keysets", include_inactive=True)
    healthy.start()
    stale.start()
    yield healthy, stale
    healthy.stop()
    stale.stop()


@pytest.fixture
def seeded(router, mints):
    healthy, stale = mints
    host = _get_local_ip()
    healthy_url = f"http://{host}:{healthy.port}"
    stale_url = f"http://{host}:{stale.port}"

    router.ssh(f"cp /etc/tollgate/config.json {CONFIG_BACKUP}")
    if router.ssh(f"[ -f /etc/tollgate/wallet.db ] && echo yes").strip() == "yes":
        router.restart_backend()
        time.sleep(2)
        router.ssh(f"cp /etc/tollgate/wallet.db {WALLET_BACKUP}")

    cfg = json.loads(router.ssh("cat /etc/tollgate/config.json"))
    template = next(
        (m for m in cfg.get("accepted_mints", []) if isinstance(m, dict)),
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
    cfg["accepted_mints"] = [dict(template, url=healthy_url), dict(template, url=stale_url)]
    router.write_remote_json("/etc/tollgate/config.json", cfg, indent=None)

    try:
        yield healthy_url, stale_url
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


def test_stale_keyset_survives_unreachable_mint(router, mints, seeded):
    healthy, stale = mints

    # 1. Seed: the wallet stores the stale mint's inactive keyset (no keys).
    router.restart_backend()
    assert _wait_backend(router), "backend did not come up with both fake mints"

    seeded_ok = False
    for _ in range(30):
        if _keyset_present(router, INACTIVE_KEYSET_ID):
            seeded_ok = True
            break
        time.sleep(2)
    assert seeded_ok, "inactive keyset was not persisted in wallet.db (seed failed)"

    # 2. Mint becomes unreachable with EOF (process down / crash / restart).
    stale.set_mode("eof")
    router.restart_backend()
    assert _wait_backend(router), "backend did not come up with the stale mint"

    # 3. The portal must be in full mode, not degraded.
    body = router.api_body("/")
    assert "no-reachable-mints" not in body, body[:300]
    assert _kind(router) == 10021, f"expected kind 10021 (full), got {_kind(router)}: {body[:300]}"

    # 4. Fund safety: the keyset record is retained for when the mint returns.
    assert _keyset_present(router, INACTIVE_KEYSET_ID), "inactive keyset record was lost"
