#!/usr/bin/env python3
"""The bench-host address must be chosen by the ROUTER's opinion, not the host's.

Regression for the 2026-10-24 failure where `discover_lan_ip()` returned the
default-route address, the router could not reach it, and all seven deploy
assertions failed with an EMPTY router-side sha256sum. That symptom blames the
deploy; the defect was the address.

These tests need no router: the "router" is a fake serializer whose answers we
control, which is exactly the point -- the lane must believe the probe, not a
local heuristic.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "spilman"))

import spilman_router_lane as L


class FakeRouter:
    """Stands in for the serial console. Records what it was asked."""

    def __init__(self, answers):
        self.answers = answers          # list of (substring, reply)
        self.asked = []

    def probe(self, expr, settle=0.0):
        self.asked.append(expr)
        for needle, reply in self.answers:
            if needle in expr:
                return reply
        return None


def _run_choose(monkeypatch_marked, router, explicit=None):
    """Call choose_reachable_lan_ip with `marked` redirected to the fake."""
    orig = L.marked
    L.marked = lambda ser, expr, settle=2.5: router.probe(expr, settle)
    try:
        return L.choose_reachable_lan_ip(router, 18998, explicit)
    finally:
        L.marked = orig


def test_the_router_probe_decides_not_the_default_route(monkeypatch=None):
    """The address the default route would pick is unreachable; another is not.

    This IS the incident: 192.168.2.6 is what `discover_lan_ip()` returned and
    the router answered nothing; 192.168.2.3 is reachable. The lane must land on
    192.168.2.3 by asking the router about each candidate in turn.
    """
    router = FakeRouter([
        ("which curl wget", "curl"),
        ("192.168.2.6", "000"),          # the default-route address: dead
        ("192.168.2.3", "200"),          # the wire the router is actually on
    ])
    orig = L.candidate_lan_ips
    L.candidate_lan_ips = lambda: ["192.168.2.6", "192.168.2.3"]
    try:
        got = _run_choose(None, router)
    finally:
        L.candidate_lan_ips = orig
    assert got == "192.168.2.3", f"chose {got!r}; the router proved .2.3 works"
    # It must have asked about both, in order: enumerating is the mechanism.
    assert any("192.168.2.6" in e for e in router.asked)
    assert any("192.168.2.3" in e for e in router.asked)


def test_an_explicit_host_is_still_proved(monkeypatch=None):
    """--host is honoured, but an unreachable one must fail LOUDLY.

    Honouring blindly is what produced seven cascading failures instead of one
    message naming the address.
    """
    router = FakeRouter([
        ("which curl wget", "curl"),
        ("192.168.2.6", "000"),
    ])
    orig = L.candidate_lan_ips
    L.candidate_lan_ips = lambda: ["192.168.2.3"]
    try:
        got = _run_choose(None, router, explicit="192.168.2.6")
    finally:
        L.candidate_lan_ips = orig
    assert got == "", "an unreachable explicit --host must not be used"
    # And it must not silently fall back to a different address behind the
    # operator's back: that would hide the mistake.
    assert not any("192.168.2.3" in e for e in router.asked), \
        "must not secretly substitute another address for an explicit --host"


def test_wget_only_routers_are_supported():
    """Some images have wget but no curl; the probe must adapt, not fail."""
    router = FakeRouter([
        ("which curl wget", "/usr/bin/wget"),
        ("192.168.2.9", "200"),
    ])
    orig = L.candidate_lan_ips
    L.candidate_lan_ips = lambda: ["192.168.2.9"]
    try:
        got = _run_choose(None, router)
    finally:
        L.candidate_lan_ips = orig
    assert got == "192.168.2.9"
    assert any("wget" in e for e in router.asked), "must fall back to wget"


def test_no_fetcher_means_no_claim_of_reachability():
    """A router with neither curl nor wget cannot prove anything -- say so."""
    router = FakeRouter([("which curl wget", "")])
    orig = L.candidate_lan_ips
    L.candidate_lan_ips = lambda: ["192.168.2.9"]
    try:
        got = _run_choose(None, router)
    finally:
        L.candidate_lan_ips = orig
    assert got == "", "reachability must not be asserted without a probe"


def test_candidates_exclude_loopback_and_link_local():
    """Enumerating every address must not offer ones that cannot be the answer."""
    ips = L.candidate_lan_ips()
    assert isinstance(ips, list)
    for ip in ips:
        assert not ip.startswith("127."), f"loopback offered: {ip}"
        assert not ip.startswith("169.254."), f"link-local offered: {ip}"
    assert ips, "at least one non-loopback IPv4 address should exist on a bench host"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{'FAILED' if failures else 'OK'}: {failures} failure(s)")
    sys.exit(1 if failures else 0)
