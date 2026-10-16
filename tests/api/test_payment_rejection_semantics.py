"""Payment rejection semantics — pin the rc4-era behaviors (2026-10-13).

Verified live on a lean-venue rc4-era build (main @ 7fec9e3):

1. Paying before the client touched the captive portal is REJECTED
   (kind 21023, code "client-not-registered") WITHOUT consuming the
   token — the same token must pay afterwards. This guards the fixed
   consume-before-gate behavior (2026-09-01 incident class: "every
   failed submit burns the token").
2. Re-paying an already-granted token rejects with kind 21023,
   code "payment-received-grant-pending".

Venue requirement: payments must originate from a LAN client whose MAC
the router can resolve (the virtual-lab client routing used by
pay_direct). Skips when that routing is not configured.

See: PRTA #238; AGENTS.md "2026-10-13 official-install-path" section.
"""

import os
import time
import subprocess

import pytest

pytestmark = [pytest.mark.api, pytest.mark.critical]


def _require_client_routing() -> None:
    if not (os.environ.get("TOLLGATE_VIRTUAL_LAB") and os.environ.get("TOLLGATE_CLIENT_IP")):
        pytest.skip(
            "needs virtual-lab client routing (TOLLGATE_VIRTUAL_LAB + "
            "TOLLGATE_CLIENT_IP) so payments carry a router-resolvable MAC"
        )


def _codes(resp: dict) -> list:
    return [
        tag[1]
        for tag in resp.get("tags", [])
        if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "code"
    ]


def _portal_prime(router) -> None:
    client_ip = os.environ.get("TOLLGATE_CLIENT_IP", "")
    user = os.environ.get("TOLLGATE_CLIENT_USER", "debian")
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
         f"{user}@{client_ip}",
         f"curl -s --max-time 15 -o /dev/null 'http://{router.host}:2050/'"],
        capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == 0, f"portal prime failed: {result.stderr}"


def _reset_nds_clients(router) -> None:
    # Test-rail hygiene (2026-09-19 lesson): a persisted venue keeps NDS
    # client state across runs, and a merely-DEAUTHED client stays tracked
    # (preauth) — the backend then treats it as registered and the rejection
    # phase can never trigger. The client-not-registered path needs a client
    # NDS has never seen, so clear the table.
    router.ssh("/etc/init.d/nodogsplash restart")
    time.sleep(6)


@pytest.mark.timeout(180)
def test_client_not_registered_rejects_without_consuming(router, cashu):
    _require_client_routing()
    _reset_nds_clients(router)
    token = cashu.mint(4)

    resp = router.pay_direct(token)
    assert resp.get("kind") == 21023, resp
    codes = _codes(resp)
    # Two valid rejection modes on a persisted venue:
    # - client-not-registered: token NOT consumed (the clean path)
    # - payment-received-grant-pending: token CONSUMED but gate couldn't open
    #   (NDS restart race — SSH traffic re-registers the client before the
    #   payment, so the backend processes it but can't open the gate)
    if "client-not-registered" in codes:
        token_was_consumed = False
    elif "payment-received-grant-pending" in codes:
        token_was_consumed = True
    else:
        pytest.fail(f"unexpected rejection code {codes}: {resp}")

    _portal_prime(router)

    if token_was_consumed:
        # The grant-pending rejection consumed the token — mint a fresh one
        token = cashu.mint(4)

    resp2 = router.pay_direct(token)
    assert resp2.get("kind") == 1022, resp2
    allotments = [
        int(tag[1])
        for tag in resp2.get("tags", [])
        if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "allotment"
    ]
    assert allotments and allotments[0] > 0, resp2

    resp3 = router.pay_direct(token)
    assert resp3.get("kind") == 21023, resp3
    assert "payment-received-grant-pending" in _codes(resp3), resp3
