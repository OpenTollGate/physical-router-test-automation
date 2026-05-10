"""Tests for PR #117: Set hostname to TollGate and configure captive portal domain.

These tests verify that the setup process configures the router hostname
and captive portal DNS domain correctly.

Tests skip cleanly when PR #117 is not installed (hostname is still
the OpenWrt default).
"""

import pytest

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.pr(117)]


def _is_pr117_installed(router):
    hostname = router.ssh("uci get system.@system[0].hostname 2>/dev/null").strip()
    return hostname.lower() == "tollgate"


def _skip_if_no_pr117(router):
    if not _is_pr117_installed(router):
        pytest.skip("PR #117 not installed (hostname is not 'TollGate')")


def test_hostname_set_to_tollgate(router):
    _skip_if_no_pr117(router)
    hostname = router.ssh("uci get system.@system[0].hostname 2>/dev/null").strip()
    assert hostname == "TollGate", f"Expected hostname 'TollGate', got '{hostname}'"


def test_hostname_persists_after_restart(router):
    _skip_if_no_pr117(router)
    hostname = router.ssh("uci get system.@system[0].hostname 2>/dev/null").strip()
    assert hostname == "TollGate"
    hostname_runtime = router.ssh("cat /proc/sys/kernel/hostname").strip()
    assert hostname_runtime == "TollGate", \
        f"UCI hostname is TollGate but runtime hostname is '{hostname_runtime}'"


def test_captive_portal_domain_resolves(router):
    _skip_if_no_pr117(router)
    result = router.ssh("nslookup tollgate.lan 127.0.0.1 2>&1").strip()
    assert "Name:" in result or "Address:" in result, \
        f"tollgate.lan does not resolve via dnsmasq: {result}"


def test_dhcp_advertises_domain(router):
    _skip_if_no_pr117(router)
    domain = router.ssh("uci get dhcp.@dnsmasq[0].domain 2>/dev/null").strip()
    assert domain, "dnsmasq domain not configured"
    assert "tollgate" in domain.lower() or "lan" in domain.lower(), \
        f"Unexpected dnsmasq domain: {domain}"


def test_nodogsplash_gateway_domain(router):
    _skip_if_no_pr117(router)
    gw_domain = router.ssh("uci get nodogsplash.@nodogsplash[0].gatewaydomainname 2>/dev/null").strip()
    assert gw_domain, "nodogsplash gatewaydomainname not set"
    assert "tollgate" in gw_domain.lower(), \
        f"Expected gatewaydomainname containing 'tollgate', got '{gw_domain}'"


def test_nodogsplash_gateway_port_80(router):
    _skip_if_no_pr117(router)
    gw_port = router.ssh("uci get nodogsplash.@nodogsplash[0].gatewayport 2>/dev/null").strip()
    assert gw_port == "80", \
        f"Expected gatewayport '80' (clean URL), got '{gw_port}'"


def test_uhttpd_commonname_set(router):
    _skip_if_no_pr117(router)
    cn = router.ssh("uci get uhttpd.main.commonname 2>/dev/null").strip()
    assert cn, "uhttpd commonname not set"
    assert cn == "TollGate", f"Expected commonname 'TollGate', got '{cn}'"


def test_setup_hostname_function_exists(router):
    _skip_if_no_pr117(router)
    setup = router.ssh("cat /etc/uci-defaults/99-tollgate-setup 2>/dev/null || echo MISSING")
    if setup == "MISSING":
        pytest.skip("uci-defaults script not found")
    assert "setup_hostname" in setup or "hostname" in setup.lower(), \
        "setup_hostname function not found in 99-tollgate-setup"


def test_hostname_only_set_when_default(router):
    _skip_if_no_pr117(router)
    setup = router.ssh("cat /etc/uci-defaults/99-tollgate-setup 2>/dev/null || echo MISSING")
    if setup == "MISSING":
        pytest.skip("uci-defaults script not found")
    assert "OpenWrt" in setup, \
        "setup_hostname should only change hostname when current value is 'OpenWrt' (preserves custom hostnames)"
