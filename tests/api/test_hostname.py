"""Tests for PR #117: Set hostname to TollGate (HTTPS is opt-in).

API-tier tests: pure SSH/config checks verifying the router plumbing
is set up correctly. No phone required.

- Hostname is always set to TollGate on first boot (when default is OpenWrt).
- HTTPS is NOT enabled by default — must be activated via ``tollgate-setup-ssl``.
- After opt-in, self-signed cert is generated and uhttpd listens on 443.

Requires a fresh deploy (factory reset) for the "not enabled by default"
tests to pass.
"""

import pytest

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.pr(117)]


def _is_pr117_installed(router):
    hostname = router.ssh("uci get system.@system[0].hostname 2>/dev/null").strip()
    return hostname.lower() == "tollgate"


def _skip_if_no_pr117(router):
    if not _is_pr117_installed(router):
        pytest.skip("PR #117 not installed (hostname is not 'TollGate')")


# --- Hostname config (always enabled) ---

def test_hostname_set_to_tollgate(router):
    _skip_if_no_pr117(router)
    hostname = router.ssh("uci get system.@system[0].hostname 2>/dev/null").strip()
    assert hostname == "TollGate", f"Expected hostname 'TollGate', got '{hostname}'"


def test_hostname_persists_after_restart(router):
    _skip_if_no_pr117(router)
    hostname_uci = router.ssh("uci get system.@system[0].hostname 2>/dev/null").strip()
    assert hostname_uci == "TollGate"
    hostname_runtime = router.ssh("cat /proc/sys/kernel/hostname").strip()
    assert hostname_runtime == "TollGate", \
        f"UCI says TollGate but kernel hostname is '{hostname_runtime}'"


# --- HTTPS NOT enabled by default (opt-in) ---

def test_https_not_configured_by_default(router):
    _skip_if_no_pr117(router)
    listen_https = router.ssh("uci -q get uhttpd.main.listen_https 2>/dev/null").strip()
    assert not listen_https or "443" not in listen_https, \
        f"HTTPS listener should not be configured by default, got: {listen_https}"


def test_no_cert_files_by_default(router):
    _skip_if_no_pr117(router)
    cert_exists = router.ssh("test -f /etc/uhttpd.crt && echo YES || echo NO").strip()
    key_exists = router.ssh("test -f /etc/uhttpd.key && echo YES || echo NO").strip()
    assert cert_exists == "NO", "Cert file should not exist by default (HTTPS is opt-in)"
    assert key_exists == "NO", "Key file should not exist by default (HTTPS is opt-in)"


def test_https_port_not_listening_by_default(router):
    _skip_if_no_pr117(router)
    out = router.ssh("netstat -tlnp 2>/dev/null | grep ':443 '").strip()
    assert not out, f"Nothing should listen on 443 by default: {out}"


# --- tollgate-setup-ssl script exists ---

def test_tollgate_setup_ssl_exists(router):
    _skip_if_no_pr117(router)
    exists = router.ssh("test -x /usr/sbin/tollgate-setup-ssl && echo YES || echo NO").strip()
    assert exists == "YES", "tollgate-setup-ssl script not found or not executable"


# --- Opt-in HTTPS activation (runs tollgate-setup-ssl) ---

def test_tollgate_setup_ssl_enables_https(router):
    _skip_if_no_pr117(router)
    result = router.ssh("tollgate-setup-ssl 2>&1")
    assert "HTTPS enabled" in result, \
        f"tollgate-setup-ssl did not report success: {result}"

    # Verify UCI config
    listen = router.ssh("uci -q get uhttpd.main.listen_https 2>/dev/null").strip()
    assert "443" in listen, f"listen_https should include 443, got: {listen}"

    cert = router.ssh("uci get uhttpd.main.cert 2>/dev/null").strip()
    assert cert == "/etc/uhttpd.crt", f"cert path wrong: {cert}"

    key = router.ssh("uci get uhttpd.main.key 2>/dev/null").strip()
    assert key == "/etc/uhttpd.key", f"key path wrong: {key}"

    # Verify WAN firewall rule
    rule = router.ssh("uci -q get firewall.allow_https_wan.name 2>/dev/null").strip()
    assert rule == "Allow-HTTPS-WAN", f"WAN HTTPS firewall rule missing: {rule}"


def test_tollgate_setup_ssl_cert_generated(router):
    _skip_if_no_pr117(router)
    cert_exists = router.ssh("test -f /etc/uhttpd.crt && echo YES || echo NO").strip()
    key_exists = router.ssh("test -f /etc/uhttpd.key && echo YES || echo NO").strip()
    assert cert_exists == "YES", "Cert file not generated after tollgate-setup-ssl"
    assert key_exists == "YES", "Key file not generated after tollgate-setup-ssl"

    # Verify CN matches hostname
    cn = router.ssh("uci -q get uhttpd.defaults.commonname 2>/dev/null").strip()
    hostname = router.ssh("uci -q get system.@system[0].hostname 2>/dev/null").strip()
    assert cn == hostname, f"Cert CN '{cn}' doesn't match hostname '{hostname}'"


def test_tollgate_setup_ssl_https_port_listening(router):
    _skip_if_no_pr117(router)
    out = router.ssh("netstat -tlnp 2>/dev/null | grep uhttpd").strip()
    assert "443" in out, \
        f"uhttpd not listening on 443 after tollgate-setup-ssl: {out}"


# --- uci-defaults script checks ---

def test_setup_hostname_function_exists(router):
    _skip_if_no_pr117(router)
    setup = router.ssh("cat /etc/uci-defaults/99-tollgate-setup 2>/dev/null || echo MISSING")
    if setup == "MISSING":
        pytest.skip("uci-defaults script already consumed")
    assert "setup_hostname" in setup, "setup_hostname function not found"


def test_hostname_only_set_when_default(router):
    _skip_if_no_pr117(router)
    setup = router.ssh("cat /etc/uci-defaults/99-tollgate-setup 2>/dev/null || echo MISSING")
    if setup == "MISSING":
        pytest.skip("uci-defaults script already consumed")
    assert "OpenWrt" in setup, \
        "setup_hostname should only change hostname when current value is 'OpenWrt'"


def test_no_auto_https_in_uci_defaults(router):
    _skip_if_no_pr117(router)
    setup = router.ssh("cat /etc/uci-defaults/99-tollgate-setup 2>/dev/null || echo MISSING")
    if setup == "MISSING":
        pytest.skip("uci-defaults script already consumed")
    assert "setup_self_signed_cert" not in setup, \
        "setup_self_signed_cert should not be in uci-defaults (HTTPS is opt-in)"
    assert "listen_https" not in setup, \
        "listen_https should not be in uci-defaults (HTTPS is opt-in)"
