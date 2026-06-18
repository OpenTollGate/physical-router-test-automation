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


# ---------------------------------------------------------------------------
# Funded two-router setup (fixture-managed — do NOT hand-configure this).
# See AGENTS.md "Golden Rules": mint reconciliation, wallet funding, and
# upstream association belong in this fixture, with teardown that restores.
# ---------------------------------------------------------------------------

import json  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

# Common mint both routers will be reconciled to. testnut.cashu.exchange is the
# only currently-reachable test mint; testnut-compat.mints.orangesync.tech is the
# harness default but is intermittently down. Override via TOLLGATE_TEST_MINT_URL.
COMMON_MINT = os.environ.get("TOLLGATE_TEST_MINT_URL", "https://testnut.cashu.exchange")
# Funding tool: build with `cd scripts/mint-token && go build -o mint-token .`
MINT_TOKEN_BIN = os.environ.get(
    "MINT_TOKEN_BIN",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "mint-token", "mint-token"),
)
FUND_AMOUNT = int(os.environ.get("TOLLGATE_FUND_AMOUNT", "1013"))


def _mint_token(mint_url: str, amount: int) -> str:
    """Run scripts/mint-token to mint `amount` sats; return the token string."""
    cp = subprocess.run(
        [MINT_TOKEN_BIN, mint_url, str(amount)],
        capture_output=True, text=True, timeout=90,
    )
    if cp.returncode != 0:
        raise RuntimeError(f"mint-token failed ({cp.stderr.strip() or cp.stdout.strip()[:160]})")
    try:
        return json.loads(cp.stdout)["token"]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"mint-token produced no token: {exc}; out={cp.stdout[:160]!r}")


@pytest.fixture(scope="session")
def two_router_funded_upstream(router, secondary_router):
    """Reconcile both routers to COMMON_MINT, fund the primary, associate its
    upstream WiFi to the secondary. Teardown restores configs + disconnects.

    Skips (cleanly) when a prerequisite is unmet: no secondary, no SSID, or the
    funding tool errors (e.g. test mint unreachable). Per AGENTS.md rule #5 we
    never paper over a funding failure with manual SSH.
    """
    if secondary_router is None:
        pytest.skip("secondary router not configured (TOLLGATE_SECONDARY_ROUTER_HOST)")
    beta_ssid = os.environ.get("TOLLGATE_SECONDARY_ROUTER_SSID", "")
    if not beta_ssid:
        pytest.skip("TOLLGATE_SECONDARY_ROUTER_SSID not set")
    if not os.path.exists(MINT_TOKEN_BIN):
        pytest.skip(f"funding tool not built: {MINT_TOKEN_BIN} (cd scripts/mint-token && go build)")

    # Mint the funding token on the HOST first — if the mint/tool is broken,
    # skip BEFORE mutating any router state (AGENTS.md rule #5).
    try:
        token = _mint_token(COMMON_MINT, FUND_AMOUNT)
    except RuntimeError as exc:
        pytest.skip(f"funding unavailable — {exc}")

    targets = [router, secondary_router]
    for rtr in targets:
        rtr.ssh("cp /etc/tollgate/config.json /etc/tollgate/config.json.tag-fund-bak", timeout=10)

    connected = False
    try:
        # 1. Reconcile both routers to the common mint (Router.replace_mints).
        for rtr in targets:
            rtr.replace_mints([COMMON_MINT])
            rtr.ssh("service tollgate-wrt restart", timeout=20)
        time.sleep(8)

        # 2. Associate primary upstream to secondary (pre-auth on beta's network;
        #    beta's captive portal whitelists its accepted mint so alpha can fund).
        try:
            router.cli_command("upstream", ["connect", beta_ssid], timeout=30)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"upstream connect to {beta_ssid} failed: {exc}")
        time.sleep(25)
        connected = True

        # 3. Fund the primary.
        router.ssh(f"echo {token!r} | tollgate wallet fund", timeout=90)
        time.sleep(15)  # let the upstream session manager attempt autopay

        yield

    finally:
        if connected:
            try:
                router.cli_command("upstream", ["disconnect", beta_ssid], timeout=15)
            except Exception:  # noqa: BLE001
                pass
        for rtr in targets:
            try:
                rtr.ssh(
                    "mv /etc/tollgate/config.json.tag-fund-bak /etc/tollgate/config.json "
                    "&& service tollgate-wrt restart",
                    timeout=20,
                )
            except Exception:  # noqa: BLE001
                pass


class TestTwoRouterFunded:
    """Funded two-router e2e: alpha pays beta and gets an active upstream session.

    All tests here skip cleanly if the funded-upstream fixture cannot be
    established (no secondary, funding tool broken, mint unreachable).
    """

    def test_funded_autopay_opens_session(self, two_router_funded_upstream, router):
        """After funding, alpha's USM pays beta and network_ok becomes true."""
        # Give the autopay a brief window, then inspect status + logs.
        deadline = time.time() + 60
        while time.time() < deadline:
            status = router.get_tollgate_status()
            if str(status).lower().find("true") >= 0 and status.get("network_ok") is True:
                break
            time.sleep(5)
        status = router.get_tollgate_status()
        assert status.get("running") is True, f"alpha not running: {status}"
        logs = router.ssh("logread 2>/dev/null | grep -iE 'session|payment|upstream' | tail -5", timeout=10)
        # network_ok true => autopay succeeded; if false, surface the log for diagnosis.
        assert status.get("network_ok") is True, (
            f"autopay did not open a session (network_ok false). Recent logs:\n{logs}"
        )
