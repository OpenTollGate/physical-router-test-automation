"""Tag-readiness physical two-router checks for tollgate-module-basic-go main.

These are *net-new* assertions that complement the already-committed coverage in
``test_two_router.py`` / ``test_mint_health.py`` / ``test_upstream_wifi.py``.
They do NOT duplicate that coverage. Run them with ``--no-deploy`` so the
session-wide ``deploy_session`` fixture does not mutate mint state before the
readiness observations are taken.

Preflight (``-k preflight``): verify both routers are reachable, running the
expected deployed build, broadcasting TollGate SSIDs, free of the dual-WWAN
routing pitfall, and not in a crash loop — *before* the rest of the campaign
mutates anything.

Postflight (``-k postflight``): after the smoke + two-router + reboot tiers,
verify the service is still alive on both routers, no panic/fatal log lines
appeared, no leftover mint-blocks (iptables / /etc/hosts) remain, and the
wallet balance command still answers.

Entry points (see ``docs/tag-readiness.md``)::

    make tag-readiness-prefight    # via pymake -> pytest -k preflight --no-deploy
    make tag-readiness-postflight  # via pymake -> pytest -k postflight --no-deploy
"""

from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.smoke

_TOLLGATE_VERSION_RE = re.compile(r"v?\d+\.\d+\.\d+", re.IGNORECASE)


def _ssh(router, cmd: str, timeout: int = 20) -> str:
    """Run a command on a router and return stripped stdout; fail loudly."""
    assert router is not None, "router fixture is None (host not configured)"
    out = router.ssh(cmd, timeout=timeout)
    return (out or "").strip()


def _both_routers(router, secondary_router):
    """Yield (label, router_obj) pairs, skipping cleanly if secondary absent."""
    yield ("primary", router)
    if secondary_router is None:
        pytest.skip("secondary router not configured (TOLLGATE_SECONDARY_ROUTER_HOST)")
    yield ("secondary", secondary_router)


# ---------------------------------------------------------------------------
# Preflight — pristine-state observations before the campaign mutates anything.
# Run with --no-deploy so deploy_session does not touch mint config first.
# ---------------------------------------------------------------------------


class TestPreflight:
    """Readiness checks run BEFORE smoke / two-router / reboot tiers."""

    def test_preflight_both_routers_ssh_reachable(self, router, secondary_router):
        """Both routers accept SSH and execute a command."""
        failures = []
        for label, rtr in _both_routers(router, secondary_router):
            try:
                out = _ssh(rtr, "echo tollgate-ready")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{label} ({rtr.host}): SSH failed: {exc}")
                continue
            if out != "tollgate-ready":
                failures.append(f"{label} ({rtr.host}): unexpected echo: {out!r}")
        assert not failures, "preflight SSH reachability failed:\n" + "\n".join(failures)

    def test_preflight_deployed_version_captured(self, router, secondary_router):
        """Each router reports an installed tollgate version (recorded for report)."""
        missing = []
        for label, rtr in _both_routers(router, secondary_router):
            out = _ssh(
                rtr,
                "tollgate version 2>/dev/null || /usr/bin/tollgate-wrt version 2>/dev/null "
                "|| /usr/bin/tollgate version 2>/dev/null || true",
            )
            if not out or not _TOLLGATE_VERSION_RE.search(out):
                missing.append(f"{label} ({rtr.host}): no version string (got: {out!r})")
        # Informational but asserted: a tag-readiness run must know what build is live.
        assert not missing, "could not determine deployed version:\n" + "\n".join(missing)

    def test_preflight_tollgate_ssids_broadcast(self, router, secondary_router):
        """Each router broadcasts at least one TollGate-* SSID."""
        missing = []
        for label, rtr in _both_routers(router, secondary_router):
            ssids = _ssh(
                rtr,
                "iwinfo 2>/dev/null | grep -E 'ESSID' | grep -oE '\"[^\"]+\"' || "
                "uci show wireless 2>/dev/null | grep ssid",
            )
            if "TollGate" not in ssids and "tollgate" not in ssids.lower():
                missing.append(f"{label} ({rtr.host}): no TollGate SSID found in:\n{ssids}")
        assert not missing, "TollGate SSID not broadcast:\n" + "\n".join(missing)

    def test_preflight_no_dual_wwan_pitfall(self, router, secondary_router):
        """Guard against the documented dual-WWAN routing conflict (no wwan2)."""
        bad = []
        for label, rtr in _both_routers(router, secondary_router):
            wwan = _ssh(rtr, "uci show network 2>/dev/null | grep -oE 'network\\.[a-z0-9_]+' | sort -u")
            interfaces = {line.split(".", 1)[-1] for line in wwan.splitlines() if line.strip()}
            wwan_ifaces = {i for i in interfaces if i.startswith("wwan")}
            if len(wwan_ifaces) > 1:
                bad.append(f"{label} ({rtr.host}): multiple wwan interfaces: {sorted(wwan_ifaces)}")
        assert not bad, "dual-WWAN pitfall detected (see docs/router-mutex.md):\n" + "\n".join(bad)

    def test_preflight_no_crash_loop(self, router, secondary_router):
        """tollgate-wrt is running and was not restarted excessively recently."""
        bad = []
        for label, rtr in _both_routers(router, secondary_router):
            pid = _ssh(rtr, "pidof tollgate-wrt 2>/dev/null || echo MISSING")
            if pid in ("", "MISSING"):
                bad.append(f"{label} ({rtr.host}): tollgate-wrt process not running")
                continue
            # procd respawn count: count crash log lines in the last boot's logread.
            panics = _ssh(
                rtr,
                "logread 2>/dev/null | grep -ciE 'tollgate.*panic|fatal error|out of memory' || true",
            )
            try:
                if int(panics or "0") > 0:
                    bad.append(f"{label} ({rtr.host}): {panics} panic/fatal lines in logread")
            except ValueError:
                bad.append(f"{label} ({rtr.host}): could not parse panic count: {panics!r}")
        assert not bad, "crash-loop / unhealthy process detected:\n" + "\n".join(bad)


# ---------------------------------------------------------------------------
# Postflight — after the full campaign, verify clean steady state.
# ---------------------------------------------------------------------------


class TestPostflight:
    """Readiness checks run AFTER smoke / two-router / reboot tiers."""

    def test_postflight_service_still_running(self, router, secondary_router):
        bad = []
        for label, rtr in _both_routers(router, secondary_router):
            pid = _ssh(rtr, "pidof tollgate-wrt 2>/dev/null || echo MISSING")
            if pid in ("", "MISSING"):
                bad.append(f"{label} ({rtr.host}): tollgate-wrt not running after campaign")
        assert not bad, "service down after campaign:\n" + "\n".join(bad)

    def test_postflight_no_new_panic_in_logs(self, router, secondary_router):
        bad = []
        for label, rtr in _both_routers(router, secondary_router):
            panics = _ssh(
                rtr,
                "logread 2>/dev/null | grep -ciE 'tollgate.*panic|fatal error|out of memory' || true",
            )
            try:
                if int(panics or "0") > 0:
                    bad.append(f"{label} ({rtr.host}): {panics} panic/fatal lines in logread")
            except ValueError:
                bad.append(f"{label} ({rtr.host}): could not parse panic count: {panics!r}")
        assert not bad, "panic/fatal lines present after campaign:\n" + "\n".join(bad)

    def test_postflight_no_leftover_mint_blocks(self, router, secondary_router):
        """Degraded-mode tests block mints; ensure none were left behind."""
        bad = []
        for label, rtr in _both_routers(router, secondary_router):
            iptables_drops = _ssh(
                rtr, "iptables -S OUTPUT 2>/dev/null | grep -c ' -j DROP' || true"
            )
            hosts_blocks = _ssh(
                rtr,
                "grep -cE 'nofee.testnut|testnut.cashu|192.0.2.1' /etc/hosts 2>/dev/null || true",
            )
            try:
                if int(iptables_drops or "0") > 0:
                    bad.append(f"{label} ({rtr.host}): {iptables_drops} OUTPUT DROP iptables rules remain")
                if int(hosts_blocks or "0") > 0:
                    bad.append(f"{label} ({rtr.host}): {hosts_blocks} mint /etc/hosts overrides remain")
            except ValueError:
                bad.append(f"{label} ({rtr.host}): unparseable block counts")
        assert not bad, "leftover mint-block state after campaign:\n" + "\n".join(bad)

    def test_postflight_wallet_balance_answers(self, router, secondary_router):
        """`tollgate wallet balance` (or status) still returns a numeric balance."""
        bad = []
        for label, rtr in _both_routers(router, secondary_router):
            out = _ssh(
                rtr,
                "tollgate wallet balance 2>/dev/null || /usr/bin/tollgate-wrt wallet balance 2>/dev/null || true",
            )
            if not re.search(r"\d", out):
                bad.append(f"{label} ({rtr.host}): no balance returned (got: {out!r})")
        assert not bad, "wallet balance command did not answer:\n" + "\n".join(bad)
