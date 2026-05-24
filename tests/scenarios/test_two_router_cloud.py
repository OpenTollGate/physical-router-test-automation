"""
Cloud-native two-router degraded mode tests.

These tests run in the GCP cloud lab with two OpenWrt VMs connected
via a dedicated upstream bridge (tg-upstream-br). Alpha's eth1 gets
DHCP from Beta (10.99.98.0/24), giving Alpha a link to its upstream
TollGate (Beta).

Topology:
    Alpha br-lan (10.99.99.1) <-> tg-poc-br <-> Host (10.99.99.2, NAT to internet)
    Beta  br-lan (10.99.99.11) <-> tg-poc-br <-> Host
    Alpha eth1 (10.99.98.x DHCP) <-> tg-upstream-br <-> Beta eth1 (10.99.98.1)

Unlike tests/scenarios/test_two_router.py, these tests do NOT call
`tollgate upstream connect` (which requires WiFi hardware). Instead,
the cloud worker pre-configures Alpha's eth1 as WAN via UCI.

Requires TOLLGATE_SECONDARY_ROUTER_HOST to be set (done automatically
by the cloud worker when --two-router is used).
"""

import json
import os
import time
from urllib.parse import urlparse

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


def _skip_if_no_degraded_support(router):
    """Skip if the deployed version does not support the status command / degraded mode."""
    resp = router.get_tollgate_status()
    if resp.get("success") is not True:
        pytest.skip(f"tollgate status command not available: {resp}")
    raw = json.dumps(resp).lower()
    if not any(kw in raw for kw in ["degraded", "reachable", "mint_health"]):
        pytest.skip("no degraded mode support detected in status output")


def _configured_mint_url(router) -> str:
    """Read the first configured mint URL from /etc/tollgate/config.json."""
    raw = router.ssh(
        "jq -r '.accepted_mints[0].url // .mints[0].url // .mint_url // empty' "
        "/etc/tollgate/config.json 2>/dev/null",
        timeout=10,
    ).strip()
    if not raw:
        pytest.skip("No configured mint URL found in /etc/tollgate/config.json")
    return raw


def _alpha_has_wan_ip(router) -> bool:
    """Check if Alpha's eth1 has a DHCP lease."""
    try:
        result = router.ssh("ip addr show eth1 2>/dev/null | grep 'inet '", timeout=10)
        return "10.99.98" in result
    except Exception:
        return False


def _alpha_can_reach_beta_upstream(router) -> bool:
    """Verify Alpha's eth1 link to Beta is live by pinging Beta's upstream IP."""
    try:
        result = router.ssh("ping -c 2 -W 3 10.99.98.1", timeout=10)
        return "100% packet loss" not in result
    except Exception:
        return False


def test_alpha_wan_link_to_beta(router, backend):
    """Verify Alpha has a working L3 link to Beta via the upstream bridge.

    This is the TollGate-style precondition: Alpha must be able to reach
    its upstream router (Beta) to discover and pay for internet. We don't
    require external internet here — that's what TollGate is for.
    """
    _skip_if_not_virtual_lab()
    router_b = _skip_if_no_secondary(_get_secondary_router(backend))

    assert _alpha_has_wan_ip(router), (
        "Alpha's eth1 does not have a DHCP lease in 10.99.98.0/24"
    )
    assert _alpha_can_reach_beta_upstream(router), (
        "Alpha cannot ping Beta upstream gateway (10.99.98.1)"
    )
    echo_result = router_b.ssh("echo OK", timeout=10)
    assert echo_result.strip() == "OK", (
        f"Beta SSH echo failed: {echo_result!r}"
    )


def test_block_mint_enters_degraded_mode(router, backend):
    """Verify degraded mode when the configured mint is blocked on Alpha."""
    _skip_if_not_virtual_lab()
    _skip_if_no_secondary(_get_secondary_router(backend))
    _skip_if_no_degraded_support(router)

    mint_url = _configured_mint_url(router)
    mint_host = urlparse(mint_url).hostname or mint_url

    try:
        router.block_mint(mint_url)
        router.ssh("service tollgate-wrt restart", timeout=20)
        time.sleep(10)

        status = router.get_tollgate_status()
        raw = json.dumps(status).lower()
        assert any(kw in raw for kw in ["degraded", "unreachable"]), (
            f"Expected degraded mode after blocking {mint_host}, got: {raw}"
        )
    finally:
        router.unblock_mint(mint_url)


def test_unblock_mint_recovers_from_degraded(router, backend):
    """Verify recovery when the configured mint is unblocked after degraded mode."""
    _skip_if_not_virtual_lab()
    _skip_if_no_secondary(_get_secondary_router(backend))
    _skip_if_no_degraded_support(router)

    mint_url = _configured_mint_url(router)
    mint_host = urlparse(mint_url).hostname or mint_url

    try:
        router.block_mint(mint_url)
        router.ssh("service tollgate-wrt restart", timeout=20)
        time.sleep(10)

        status = router.get_tollgate_status()
        raw = json.dumps(status).lower()
        assert any(kw in raw for kw in ["degraded", "unreachable"]), (
            f"Expected degraded mode for {mint_host}, got: {raw}"
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
            f"Router did not recover from degraded mode after unblocking {mint_host}"
        )
    finally:
        router.unblock_mint(mint_url)


def test_both_routers_report_status(router, backend):
    """Verify both routers' TollGate status is queryable."""
    _skip_if_not_virtual_lab()
    router_b = _skip_if_no_secondary(_get_secondary_router(backend))
    _skip_if_no_degraded_support(router)
    _skip_if_no_degraded_support(router_b)

    alpha_status = router.get_tollgate_status()
    assert alpha_status.get("success") is True, (
        f"Alpha TollGate status not healthy: {alpha_status}"
    )

    beta_status = router_b.get_tollgate_status()
    assert beta_status.get("success") is True, (
        f"Beta TollGate status not healthy: {beta_status}"
    )
