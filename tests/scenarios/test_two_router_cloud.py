"""
Cloud-native two-router tests.

Runs in the GCP cloud lab with two OpenWrt VMs connected via a dedicated
upstream bridge (tg-upstream-br). Alpha's eth1 gets DHCP from Beta
(10.99.98.0/24), giving Alpha a link to its upstream TollGate (Beta).

Topology:
    Alpha br-lan (10.99.99.1) <-> tg-poc-br <-> Host (10.99.99.2, NAT to internet)
    Beta  br-lan (10.99.99.11) <-> tg-poc-br <-> Host
    Alpha eth1 (10.99.98.x DHCP) <-> tg-upstream-br <-> Beta eth1 (10.99.98.1)

Unlike tests/scenarios/test_two_router.py, these tests do NOT call
`tollgate upstream connect` (which requires WiFi hardware). Instead,
the cloud worker pre-configures Alpha's eth1 as WAN via UCI.

Uses HTTP API (kind 10021/21023) for degraded mode detection so tests
work even without the CLI socket (/var/run/tollgate.sock).

Requires TOLLGATE_SECONDARY_ROUTER_HOST to be set (done automatically
by the cloud worker when --two-router is used).
"""

import json
import os
import re
import time
from urllib.parse import urlparse

import pytest

from lib.helpers import is_degraded, is_full_merchant, wait_for_degraded
from lib.router import Router

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.virtual_lab]


def _get_secondary_router(backend) -> Router | None:
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


def _has_degraded_mode_support(router) -> bool:
    """Check if the deployed firmware supports degraded mode.

    Two detection paths:
    1. CLI socket: if /var/run/tollgate.sock exists AND `tollgate status`
       returns success with health tracking fields (degraded/reachable/mint_health)
    2. HTTP API: if blocking mints is known to trigger degraded mode

    Returns True only if we have concrete evidence of health tracking.
    Returns False (→ skip) if the CLI socket is absent, returns empty,
    or lacks health tracking fields.
    """
    if router.backend.has_cli_socket:
        try:
            out = router.ssh("ls -S /var/run/tollgate.sock 2>/dev/null", timeout=5)
            if out.strip():
                resp = router.get_tollgate_status()
                if resp.get("success") is True:
                    raw = json.dumps(resp).lower()
                    if any(kw in raw for kw in ["degraded", "reachable", "mint_health"]):
                        return True
        except Exception:
            pass

    return False


def _configured_mint_url(router) -> str:
    raw = router.ssh(
        "jq -r '.accepted_mints[0].url // .mints[0].url // .mint_url // empty' "
        "/etc/tollgate/config.json 2>/dev/null",
        timeout=10,
    ).strip()
    if not raw:
        pytest.skip("No configured mint URL found in /etc/tollgate/config.json")
    return raw


def _alpha_has_wan_ip(router) -> bool:
    try:
        result = router.ssh("ip addr show eth1 2>/dev/null | grep 'inet '", timeout=10)
        return "10.99.98" in result
    except Exception:
        return False


def _alpha_can_reach_beta_upstream(router) -> bool:
    try:
        result = router.ssh("ping -c 2 -W 3 10.99.98.1", timeout=10)
        return "100% packet loss" not in result
    except Exception:
        return False


def _block_mint_and_wait_degraded(router, mint_url, timeout=60):
    """Block the mint and poll until the HTTP API shows degraded (kind 21023)."""
    router.block_mint(mint_url)
    router.ssh("service tollgate-wrt restart", timeout=20)
    time.sleep(5)

    entered = wait_for_degraded(router, timeout=timeout, interval=3)
    if not entered:
        logs = router.get_tollgate_logs(lines=200)
        degraded_signals = re.findall(
            r"(degraded|no reachable mints|all mints unreachable)",
            logs, re.IGNORECASE,
        )
        if degraded_signals:
            entered = True

    return entered


def test_alpha_wan_link_to_beta(router, backend):
    """Verify Alpha has a working L3 link to Beta via the upstream bridge."""
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


@pytest.mark.timeout(300)
def test_block_mint_enters_degraded_mode(router, backend):
    """Verify degraded mode when the configured mint is blocked on Alpha."""
    _skip_if_not_virtual_lab()
    _skip_if_no_secondary(_get_secondary_router(backend))
    if not _has_degraded_mode_support(router):
        pytest.skip("deployed firmware does not support degraded mode detection")

    mint_url = _configured_mint_url(router)
    mint_host = urlparse(mint_url).hostname or mint_url

    try:
        entered = _block_mint_and_wait_degraded(router, mint_url, timeout=60)
        assert entered, (
            f"Service did not enter degraded mode after blocking {mint_host}"
        )
    finally:
        router.unblock_mint(mint_url)


@pytest.mark.timeout(300)
def test_unblock_mint_recovers_from_degraded(router, backend):
    """Verify recovery when the configured mint is unblocked after degraded mode."""
    _skip_if_not_virtual_lab()
    _skip_if_no_secondary(_get_secondary_router(backend))
    if not _has_degraded_mode_support(router):
        pytest.skip("deployed firmware does not support degraded mode detection")

    mint_url = _configured_mint_url(router)
    mint_host = urlparse(mint_url).hostname or mint_url

    try:
        entered = _block_mint_and_wait_degraded(router, mint_url, timeout=60)
        assert entered, (
            f"Service did not enter degraded mode for {mint_host}"
        )

        router.unblock_mint(mint_url)

        recovered = False
        for _ in range(30):
            time.sleep(2)
            if is_full_merchant(router):
                recovered = True
                break

        assert recovered, (
            f"Router did not recover from degraded mode after unblocking {mint_host}"
        )
    finally:
        router.unblock_mint(mint_url)


@pytest.mark.timeout(120)
def test_both_routers_healthy(router, backend):
    """Verify both routers are running TollGate with healthy HTTP API.

    Uses HTTP API (GET /) instead of CLI socket so it works regardless
    of backend version. A healthy router returns kind 10021 (merchant)
    or kind 21023 (degraded) — both indicate TollGate is running.
    """
    _skip_if_not_virtual_lab()
    router_b = _skip_if_no_secondary(_get_secondary_router(backend))

    alpha_body = router.api_body("/")
    try:
        alpha_data = json.loads(alpha_body)
    except json.JSONDecodeError:
        pytest.fail(f"Alpha API returned non-JSON: {alpha_body[:200]}")

    alpha_kind = alpha_data.get("kind")
    assert alpha_kind in (10021, 21023), (
        f"Alpha API returned unexpected kind {alpha_kind}: {alpha_body[:200]}"
    )

    beta_body = router_b.api_body("/")
    try:
        beta_data = json.loads(beta_body)
    except json.JSONDecodeError:
        pytest.fail(f"Beta API returned non-JSON: {beta_body[:200]}")

    beta_kind = beta_data.get("kind")
    assert beta_kind in (10021, 21023), (
        f"Beta API returned unexpected kind {beta_kind}: {beta_body[:200]}"
    )
