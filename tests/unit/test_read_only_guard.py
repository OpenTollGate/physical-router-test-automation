"""--read-only fail-closed guards (incident 2026-10-01, fix F1).

An autouse fixture mutated a commissioned live router because --no-deploy
was not passed. --read-only arms the Router so every @_mutating method
raises RouterReadOnlyError BEFORE any command runs, while reads keep
working.
"""
import pytest

from lib.router import Router, RouterReadOnlyError

pytestmark = [pytest.mark.story("P8")]


def make_router():
    router = Router("10.99.99.1", "10.99.99.186", "aa:bb:cc:dd:ee:ff", "test.lan")
    ssh_calls = []
    router.ssh = lambda cmd, timeout=None: ssh_calls.append(cmd) or ""
    return router, ssh_calls


MUTATORS = [
    ("replace_mints", {"mint_urls": ["https://m.example"], "force": True}),
    ("ensure_test_mint", {}),
    ("enable_debug_portal", {}),
    ("disable_debug_portal", {}),
    ("restart_backend", {}),
    ("reset_state", {}),
    ("fix_nodogsplash_dhcp", {}),
    ("disable_ipv6_on_lan", {}),
    ("block_mint", {}),
    ("unblock_mint", {}),
    ("uci_commit", {"configs": ("firewall",)}),
    ("ensure_dhcp_lease", {}),
    ("ensure_nds_gateway_domain_supported", {}),
    ("write_remote_text", {"remote_path": "/tmp/x", "content": "x"}),
    ("write_remote_json", {"remote_path": "/tmp/x.json", "payload": {}}),
    ("ssh_stdin", {"cmd": "cat > /tmp/x", "content": "x"}),
    ("pay_direct", {"token": "cashuA..."}),
    ("pay_direct_mac", {"token": "cashuA..."}),
    ("pay_via_header", {"token": "cashuA..."}),
    ("apply_pricing", {}),
    ("restore_pricing", {}),
    ("clear_portal_log", {}),
    ("uci_set", {"key": "nodogsplash.@nodogsplash[0].gatewayport", "value": "2050"}),
    ("upstream_connect", {}),
    ("upstream_remove", {}),
]


@pytest.mark.parametrize("method,kwargs", MUTATORS)
def test_mutators_raise_in_read_only_mode(method, kwargs):
    router, ssh_calls = make_router()
    router.enable_read_only()
    with pytest.raises(RouterReadOnlyError, match=method):
        getattr(router, method)(**kwargs)
    assert not ssh_calls, f"{method} ran SSH commands before failing"


def test_reads_still_work_in_read_only_mode(monkeypatch):
    # api_status only routes through the (mocked) SSH read path when the
    # virtual-lab flag is set — without this, the test would really curl the
    # router address and stall on the network.
    monkeypatch.setenv("TOLLGATE_VIRTUAL_LAB", "1")
    router, ssh_calls = make_router()
    router.enable_read_only()
    router.ssh("uci -q get dhcp.lan.ra")
    router.api_status("/")  # read path: SSH-backed curl to the backend
    assert len(ssh_calls) == 2


def test_guard_names_the_incident():
    router, _ = make_router()
    router.enable_read_only()
    with pytest.raises(RouterReadOnlyError, match="2026-10-01"):
        router.replace_mints(["https://m.example"], force=True)


def test_default_mode_is_mutating_allowed():
    router, ssh_calls = make_router()
    router.ssh("echo hi")
    assert not router._read_only
    assert len(ssh_calls) == 1
