"""Feed postinst private-subnet recheck (module #926, mirrored in the rc5 feed).

The TollGate postinst derives the private (br-private) subnet, then starts
dnsmasq. On a router whose UPSTREAM hands out a subnet that collides with the
derived one — most commonly the classic ``192.168.2.0/24`` LAN behind a
``192.168.2.x`` upstream, or a DHCP lease that arrives AFTER the derivation —
the private bridge ends up ON the upstream subnet. The box then loses its
default route / DNS: the wired LAN spur and the uplink disagree about where
``192.168.2.0/24`` lives.

#926 added ``recheck_private_subnet`` (called from the postinst) which moves a
late-colliding private subnet off the upstream. The rc5 feed recipe mirrors
that call. This module pins the behaviour on a live router: force a collision,
re-run the setup recheck, and assert the private subnet moved and DNS survived.

Destructive: it rewrites ``network.private`` and bounces the network. Run on a
bench router (``make`` bench lane), never on a commissioned box.
"""
from __future__ import annotations

import ipaddress
import re

import pytest

pytestmark = [
    pytest.mark.story("O5"),
    pytest.mark.api,
    pytest.mark.hardware,
    pytest.mark.destructive,
    pytest.mark.upgrade,
    pytest.mark.timeout(600),
]

SETUP_SCRIPT = "/etc/uci-defaults/99-tollgate-setup"


def _upstream_cidr(router) -> str | None:
    """CIDR of the router's default-route (upstream) interface."""
    dev = router.ssh(
        "ip route show default 2>/dev/null | awk '{print $5}' | head -1", timeout=10
    ).strip()
    if not dev:
        return None
    cidr = router.ssh(
        f"ip -4 -o addr show dev {dev} 2>/dev/null | awk '{{print $4}}' | head -1",
        timeout=10,
    ).strip()
    return cidr or None


def _private_addr(router) -> str | None:
    return (
        router.uci_get("network.private.ipaddr").strip()
        or router.ssh(
            "ip -4 -o addr show dev br-private 2>/dev/null | awk '{print $4}' | head -1",
            timeout=10,
        ).strip()
        or None
    )


def _overlaps(a: str, b: str) -> bool:
    try:
        na = ipaddress.ip_interface(a)
        nb = ipaddress.ip_network(b, strict=False)
    except ValueError:
        return False
    return na.ip in nb


def _setup_script_present(router) -> bool:
    out = router.ssh(f"test -f {SETUP_SCRIPT} && echo yes || echo no", timeout=10).strip()
    return out.endswith("yes")


def _rerun_recheck(router) -> None:
    """Re-run the setup script so its private-subnet recheck executes.

    #926 wires ``recheck_private_subnet`` into the same script the postinst
    runs; re-running it is the supported way to re-derive without a full
    reinstall. The script is idempotent.
    """
    router.ssh(f"sh {SETUP_SCRIPT} >/tmp/recheck.log 2>&1 || true", timeout=180)


@pytest.fixture
def forced_private_collision(router):
    """Force network.private.ipaddr onto the upstream subnet, then restore."""
    upstream = _upstream_cidr(router)
    if not upstream:
        pytest.skip("no upstream (default-route) address — cannot force a collision")
    original = router.uci_get("network.private.ipaddr") or None
    net = ipaddress.ip_network(upstream, strict=False)
    colliding = str(next(net.hosts()))  # e.g. upstream 192.168.2.0/24 -> 192.168.2.1
    router.uci_set("network.private.ipaddr", colliding)
    router.uci_set("network.private.netmask", "255.255.255.0")
    router.uci_commit("network")
    router.ssh("/sbin/ifup private 2>/dev/null || true", timeout=30)
    try:
        yield upstream
    finally:
        if original:
            router.uci_set("network.private.ipaddr", original)
            router.uci_commit("network")
            router.ssh("/sbin/ifup private 2>/dev/null || true", timeout=30)


def test_recheck_moves_a_colliding_private_subnet(router, forced_private_collision):
    """After the recheck, br-private must not sit on the upstream subnet (#926)."""
    if not _setup_script_present(router):
        pytest.skip(f"{SETUP_SCRIPT} not present (postinst already cleaned up) — "
                    "cannot drive the recheck on this artifact")

    upstream = forced_private_collision
    before = _private_addr(router)
    assert before and _overlaps(before, upstream), (
        f"seed failed: private {before!r} does not overlap upstream {upstream!r}"
    )

    _rerun_recheck(router)

    after = _private_addr(router)
    assert after, "br-private has no address after the recheck"
    assert not _overlaps(after, upstream), (
        f"private subnet {after!r} still overlaps upstream {upstream!r} after the "
        f"recheck — #926 did not move the colliding private subnet"
    )


def test_dns_survives_the_private_subnet_move(router, forced_private_collision):
    """DNS must still resolve after a colliding private subnet is moved.

    The whole point of the recheck is that the box keeps a working resolver;
    assert dnsmasq is up and answers after the move.
    """
    if not _setup_script_present(router):
        pytest.skip(f"{SETUP_SCRIPT} not present — cannot drive the recheck")

    _rerun_recheck(router)

    assert "down" not in router.ssh(
        "pgrep -f '[d]nsmasq' >/dev/null && echo up || echo down", timeout=10
    ), "dnsmasq is down after the private-subnet recheck"

    out = router.ssh("nslookup github.com 127.0.0.1 2>&1 | grep -c 'Address'", timeout=20)
    assert out.strip() != "0", "127.0.0.1 DNS stopped answering after the recheck"

    private = _private_addr(router)
    assert private, "no br-private address to check DNS on"
    ip = private.split("/")[0]
    assert "Address" in router.ssh(f"nslookup github.com {ip} 2>&1 || true", timeout=20), (
        f"dnsmasq on the moved private address {ip} does not answer"
    )
