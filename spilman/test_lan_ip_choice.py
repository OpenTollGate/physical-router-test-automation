#!/usr/bin/env python3
"""`--host` must be discoverable, and its failure mode must name itself.

The 2026-10-24 incident: `discover_lan_ip()` returns the DEFAULT-ROUTE address.
On this multi-homed bench the router is cabled to a different NIC's address, so
the artifact server was unreachable from the router and every deploy assertion
failed with an EMPTY router-side sha256sum -- a message that blames the deploy.

Two auto-decision attempts were made and both WITHDRAWN after measuring:

  * asking the router to fetch a URL: the fetch succeeded but its output landed
    after the console's read window and was captured by the next call, so it
    reported the working address as unreachable;
  * `ip neigh`: two of this host's NICs are on the same L2 segment and BOTH show
    the router REACHABLE, so it cannot distinguish them.

Hence: a hint, not a gate. What these tests pin is that a wrong `--host` produces
a message naming the address, and that the hint lists the real candidates instead
of pretending to know.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "spilman"))

import spilman_router_lane as L


def test_candidates_exclude_loopback_and_link_local():
    """Offering 127.x or 169.254.x would be offering addresses that cannot work."""
    ips = L.candidate_lan_ips()
    assert isinstance(ips, list)
    assert ips, "a bench host has at least one non-loopback IPv4"
    for ip in ips:
        assert not ip.startswith("127."), f"loopback offered: {ip}"
        assert not ip.startswith("169.254."), f"link-local offered: {ip}"


def test_candidates_include_the_default_route_address_first():
    """The common single-homed case should need no thought."""
    orig = L.discover_lan_ip
    L.discover_lan_ip = lambda: "10.1.2.3"
    try:
        ips = L.candidate_lan_ips()
    finally:
        L.discover_lan_ip = orig
    assert ips[0] == "10.1.2.3", f"default-route address should lead: {ips}"


def test_the_hint_names_a_way_out():
    """The hint must tell the operator what to do when the default is wrong.

    Without this, the failure mode is seven cascading deploy assertions; with it,
    the message says which knob to turn.
    """
    hint = L.bench_host_hint()
    assert "--host" in hint, f"hint must mention --host: {hint!r}"
    assert "checksum" in hint.lower(), (
        f"hint must name the symptom the operator will actually see: {hint!r}")


def test_the_hint_lists_real_candidates():
    """A hint that lists nothing is not a hint."""
    hint = L.bench_host_hint()
    reachable = [ip for ip in L.candidate_lan_ips() if ip in hint]
    assert reachable, f"hint should name at least one real address: {hint!r}"


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
