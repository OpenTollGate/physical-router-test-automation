"""Train convergence-storm and era-invariant tests (P2/P3 of the drift round).

Companion to test_train_topology_scenarios.py (P1). Same venue-safety
discipline: topology staging stays in uci staging (uci get sees staged
values; nothing is committed), renderers run against staged state, and a
finally-block re-renders from the committed (real) state and reloads.

- P2 renderer convergence storm: cycle the gatewayinterface through stock
  and renamed values; the defines fragment must track exactly, re-render
  byte-identically (idempotence), and the venue must come back healthy.
  Zone-rename convergence (#788/#755): the deployed resolver must follow
  a staged zone rename into tollgate_in.src — the stock name is never
  written when the router's own config says otherwise.
- P3 era invariants: fw4 and the NDS iptables-nft chains must coexist
  after a reload (the same-priority cross-family bridge is load-bearing
  and unspecified by nft — if an update drops one side, this trips), and
  the fw4-reload-restarts-NDS behavior is asserted AS DOCUMENTED (the
  tripwire flips when a persistent-state fix lands — see the comment on
  the test).
"""

import re

import pytest

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.virtual_lab]

from tests.api.test_train_topology_scenarios import (  # noqa: E402
    TRAIN_MARKER,
    SETUP,
    _train_present,
    _train_skip,
)

INTERFACES_RENDERER = "/usr/local/bin/tollgate-nft-interfaces-render"
SELFCHECK = "/usr/local/bin/tollgate-topology-selfcheck"
DEFS = "/etc/nftables.d/00-tollgate-defs.nft"


def _defines_portal(router):
    out = router.ssh(f"grep 'define tg_portal_if' {DEFS} 2>/dev/null")
    m = re.search(r'define tg_portal_if = "([^"]+)"', out)
    return m.group(1) if m else None


def _backend_reachable():
    import subprocess

    r = subprocess.run(
        ["curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}",
         "http://10.99.99.1:2121/"],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# P2 — convergence storm
# ---------------------------------------------------------------------------


def test_renderer_convergence_storm(router):
    """gatewayinterface cycles → the defines track exactly, renders are
    idempotent, and the stock state is fully restorable."""
    if not _train_present(router):
        _train_skip()
    if router.ssh(f"test -x {INTERFACES_RENDERER} || echo no").strip() == "no":
        pytest.skip(f"{INTERFACES_RENDERER} not deployed (pre-#784 package)")

    try:
        for cycle, value in enumerate(["br-lan", "br-portal", "br-lan", "br-portal", "br-lan"]):
            router.ssh(
                f"uci set nodogsplash.@nodogsplash[0].gatewayinterface='{value}'; "
                f"{INTERFACES_RENDERER} >/dev/null 2>&1"
            )
            got = _defines_portal(router)
            assert got == value, (
                f"cycle {cycle}: defines must track the gatewayinterface exactly "
                f"(want {value}, got {got})"
            )
            h1 = router.ssh(f"md5sum {DEFS} 2>/dev/null").split()[0]
            router.ssh(f"{INTERFACES_RENDERER} >/dev/null 2>&1")
            h2 = router.ssh(f"md5sum {DEFS} 2>/dev/null").split()[0]
            assert h1 == h2, (
                f"cycle {cycle}: re-render must be byte-identical (idempotence)"
            )
    finally:
        # converge back to the committed (real) topology and prove the
        # venue survived: renderers re-own their fragments from stock state.
        router.ssh(
            "uci revert nodogsplash 2>/dev/null; "
            f"{INTERFACES_RENDERER} >/dev/null 2>&1; "
            f"{TRAIN_MARKER} >/dev/null 2>&1; "
            "fw4 reload >/dev/null 2>&1; true"
        )
    code = _backend_reachable()
    assert code not in ("", "000"), (
        f"venue backend :2121 must survive the storm (got http={code})"
    )


def test_zone_rename_convergence(router):
    """A staged zone rename must flow into tollgate_in.src via the deployed
    resolver — the stock 'lan' is never written when the router's own config
    names another zone (#755/#788: an upgrade must not revert a rename)."""
    if not _train_present(router):
        _train_skip()
    has_resolver = router.ssh(f"grep -c resolve_captive_zone {SETUP} 2>/dev/null").strip() not in ("", "0")
    if not has_resolver:
        pytest.skip(f"{SETUP} predates the #755 resolver")

    try:
        # Stage: rename the zone that owns the (committed) lan network; the
        # resolver walks gatewayinterface → network → zone and must return
        # the renamed zone. Staging only — nothing is applied.
        router.ssh(
            "z=$(uci show firewall | sed -n \"s/^firewall\\.\\(@zone\\[[0-9]*\\]\\)\\.network='lan'/firewall.\\1/p\" | head -n1); "
            "[ -n \"$z\" ] && uci set $z.name='portal-renamed' || echo NO-LAN-ZONE"
        )
        out = router.ssh(
            f"awk '/^# -- driver/{{exit}} {{print}}' {SETUP} > /tmp/train-lib2.sh && "
            ". /tmp/train-lib2.sh; LOGFILE=/tmp/train-scenario2.log; : > $LOGFILE; "
            "resolved=$(resolve_captive_zone); "
            "setup_tollgate_firewall_rules >/dev/null 2>&1; "
            "echo resolved=$resolved; "
            "echo src=$(uci -q get firewall.tollgate_in.src 2>/dev/null)"
        )
        facts = dict(l.split("=", 1) for l in out.strip().splitlines() if "=" in l)
        if facts.get("resolved") == "portal-renamed":
            assert facts.get("src") == "portal-renamed", (
                f"tollgate_in.src must follow the resolved zone, not the stock "
                f"name (#755/#788): {facts}"
            )
        else:
            pytest.skip(f"resolver did not pick up the staged rename on this topology: {facts}")
    finally:
        router.ssh("uci revert firewall 2>/dev/null; rm -f /tmp/train-lib2.sh; true")


# ---------------------------------------------------------------------------
# P3 — era invariants
# ---------------------------------------------------------------------------


def test_fw4_nds_coexistence_invariant(router):
    """After a reload, BOTH sides of the enforcement bridge must exist:
    inet fw4 (with nds_enforce_forward) AND the NDS iptables-nft tables
    (ip filter/mangle/nat). The bridge relies on same-priority cross-family
    ordering that nft does not specify — if an fw4 or NDS update drops or
    renames one side, enforcement silently dies and this trips."""
    if not _train_present(router):
        _train_skip()
    router.ssh("fw4 reload >/dev/null 2>&1; /etc/init.d/nodogsplash restart >/dev/null 2>&1; true")
    tables = router.ssh("nft list tables 2>/dev/null")
    assert "table inet fw4" in tables, "inet fw4 missing after reload — fw4 broken"
    assert "table ip filter" in tables, (
        "NDS iptables-nft table missing — NDS chains dead (coexistence broken)"
    )
    chain = router.ssh("nft list chain inet fw4 nds_enforce_forward 2>/dev/null")
    assert "hook forward" in chain and "reject" in chain, (
        "nds_enforce_forward must keep its hook and terminal reject"
    )


def test_fw4_reload_restarts_nds_documented(router):
    """DOCUMENTED-BEHAVIOR TRIPWIRE: fw4 reload procd-restarts nodogsplash
    (bench-verified 25.12; measured on 24.10 too), wiping runtime trust/auth
    state. This asserts the CURRENT behavior so that any change (an fw4
    update, an NDS change, or a deliberate persistent-state fix) is a
    noticable event. WHEN THE FIX LANDS: flip this assert — the test is the
    reminder, per AGENTS.md's reload-trigger disclosure duty."""
    if not _train_present(router):
        _train_skip()
    pid_before = router.ssh("pidof nodogsplash 2>/dev/null").strip()
    if not pid_before:
        pytest.skip("nodogsplash not running")
    clients_before = router.ssh("ndsctl clients 2>/dev/null | grep -c ':' || true").strip()
    router.ssh("fw4 reload >/dev/null 2>&1")
    pid_after = router.ssh("pidof nodogsplash 2>/dev/null").strip()
    clients_after = router.ssh("ndsctl clients 2>/dev/null | grep -c ':' || true").strip()
    # Report the state-wipe measurement either way — it is the number the
    # disclosure duty asks reload-trigger changes to state.
    print(
        f"\nnds state across fw4 reload: pid {pid_before} -> {pid_after}, "
        f"clients {clients_before} -> {clients_after}"
    )
    assert pid_after and pid_after != pid_before, (
        "fw4 reload no longer restarts nodogsplash — behavior CHANGED. If a "
        "persistent-state fix landed, flip this assert (and update AGENTS.md); "
        "if not, an fw4/procd update changed the restart contract — investigate."
    )
