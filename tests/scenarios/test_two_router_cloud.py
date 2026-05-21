"""
Cloud-native two-router degraded mode tests.

These tests run in the GCP cloud lab with two OpenWrt VMs connected
via a dedicated upstream bridge (tg-upstream-br). Alpha's eth1 gets
DHCP from Beta, providing internet through Beta's NAT.

Unlike tests/scenarios/test_two_router.py, these tests do NOT call
`tollgate upstream connect` (which requires WiFi hardware). Instead,
the cloud worker pre-configures Alpha's eth1 as WAN via UCI.

Requires TOLLGATE_SECONDARY_ROUTER_HOST to be set (done automatically
by the cloud worker when --two-router is used).
"""

import json
import os
import time

import pytest

from lib.router import Router

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.virtual_lab]


def _get_secondary_router(backend) -> Router | None:
    """Get the secondary router (Beta) from env vars."""
    host = os.environ.get("TOLLGATE_SECONDARY_ROUTER_HOST", "")
    if not host:
        return None
    password = os.environ.get(
        "TOLLGATE_SECONDARY_ROUTER_PASSWORD",
        os.environ.get("TOLLGATE_LUCI_PASSWORD", ""),
    )
    identity_file = os.environ.get("TOLLGATE_SECONDARY_ROUTER_SSH_KEY", "")
    port_str = os.environ.get("TOLLGATE_SECONDARY_ROUTER_PORT", "")
    return Router(
        host=host,
        phone_ip="",
        phone_mac="",
        domain="",
        identity_file=identity_file or None,
        port=int(port_str) if port_str else None,
        backend=backend,
    )


def _skip_if_no_secondary(router_b) -> Router:
    if router_b is None:
        pytest.skip("TOLLGATE_SECONDARY_ROUTER_HOST not set")
    return router_b


def _skip_if_not_virtual_lab():
    if os.environ.get("TOLLGATE_VIRTUAL_LAB") != "1":
        pytest.skip("Cloud two-router tests only run in virtual lab")


def _alpha_has_wan_ip(router) -> bool:
    """Check if Alpha's eth1 has a DHCP lease."""
    try:
        result = router.ssh("ip addr show eth1 2>/dev/null | grep 'inet '", timeout=10)
        return "10.99.98" in result
    except Exception:
        return False


def _alpha_can_reach_internet(router) -> bool:
    """Check if Alpha can ping through Beta to the internet."""
    try:
        result = router.ssh("ping -c 1 -W 5 9.9.9.9", timeout=15)
        return "100% packet loss" not in result
    except Exception:
        return False


def test_alpha_wan_connectivity_through_beta(router, backend):
    """Verify Alpha has internet through Beta (WAN on eth1)."""
    _skip_if_not_virtual_lab()
    router_b = _skip_if_no_secondary(_get_secondary_router(backend))

    assert _alpha_has_wan_ip(router), (
        "Alpha's eth1 does not have a DHCP lease in 10.99.98.0/24"
    )
    assert _alpha_can_reach_internet(router), (
        "Alpha cannot reach internet through Beta"
    )
    echo_result = router_b.ssh("echo OK", timeout=10)
    assert echo_result.strip() == "OK", (
        f"Beta SSH echo failed: {echo_result!r}"
    )


def test_block_mint_enters_degraded_mode(router, backend):
    """Verify degraded mode when mint is blocked on Alpha."""
    _skip_if_not_virtual_lab()
    _skip_if_no_secondary(_get_secondary_router(backend))

    mint_url = "https://testnut.cashu.exchange"
    try:
        router.block_mint(mint_url)
        router.ssh("service tollgate-wrt restart", timeout=20)
        time.sleep(10)

        status = router.get_tollgate_status()
        raw = json.dumps(status).lower()
        assert any(kw in raw for kw in ["degraded", "unreachable"]), (
            f"Expected degraded mode after blocking mint, got: {raw}"
        )
    finally:
        router.unblock_mint(mint_url)


def test_unblock_mint_recovers_from_degraded(router, backend):
    """Verify recovery when mint is unblocked after degraded mode."""
    _skip_if_not_virtual_lab()
    _skip_if_no_secondary(_get_secondary_router(backend))

    mint_url = "https://testnut.cashu.exchange"
    try:
        router.block_mint(mint_url)
        router.ssh("service tollgate-wrt restart", timeout=20)
        time.sleep(10)

        status = router.get_tollgate_status()
        raw = json.dumps(status).lower()
        assert any(kw in raw for kw in ["degraded", "unreachable"]), (
            f"Expected degraded mode, got: {raw}"
        )

        router.unblock_mint(mint_url)
        recovered = False
        for _ in range(30):
            time.sleep(2)
            status = router.get_tollgate_status()
            if status.get("success") is True:
                raw = json.dumps(status).lower()
                if "degraded" not in raw:
                    recovered = True
                    break

        assert recovered, (
            "Router did not recover from degraded mode after unblocking mint"
        )
    finally:
        router.unblock_mint(mint_url)


def test_both_routers_report_status(router, backend):
    """Verify both routers' TollGate status is queryable."""
    _skip_if_not_virtual_lab()
    router_b = _skip_if_no_secondary(_get_secondary_router(backend))

    alpha_status = router.get_tollgate_status()
    assert alpha_status.get("success") is True, (
        f"Alpha TollGate status not healthy: {alpha_status}"
    )

    beta_status = router_b.get_tollgate_status()
    assert beta_status.get("success") is True, (
        f"Beta TollGate status not healthy: {beta_status}"
    )
