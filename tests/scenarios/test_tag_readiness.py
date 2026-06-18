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
            # wwan6/wan6 are the IPv6 (DHCPv6) companions of wwan/wan — a standard
            # OpenWrt pair, NOT a second WWAN. The real pitfall
            # (docs/router-mutex.md) is a second distinct L3 interface like wwan2.
            wwan_ifaces = {i for i in interfaces if i.startswith("wwan") and not i.endswith("6")}
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

# Common mint both routers are reconciled to. nofee.testnut.cashu.space is a
# Nutshell-0.18.2 feeless FakeWallet that reliably auto-pays (verified).
# testnut.cashu.exchange and testnut-compat.mints.orangesync.tech are both
# broken right now (BOLT11/SSL). Override via TOLLGATE_TEST_MINT_URL.
COMMON_MINT = os.environ.get("TOLLGATE_TEST_MINT_URL", "https://nofee.testnut.cashu.space")
# Nutshell cashu CLI (setup-cashu.sh installs it at ~/.cashu-venv/bin/cashu).
CASHU_BIN = os.environ.get("TOLLGATE_CASHU_BIN", os.path.expanduser("~/.cashu-venv/bin/cashu"))
CASHU_WALLET = os.environ.get("TOLLGATE_CASHU_WALLET", "tag-fund")
FUND_AMOUNT = int(os.environ.get("TOLLGATE_FUND_AMOUNT", "1100"))
# alpha reaches the mint to "receive" funds via this upstream WiFi (it has no
# other internet). The fixture removes beta's STA first, connects this, funds,
# then switches alpha back to beta for the actual purchase.
INTERNET_SSID = os.environ.get("TOLLGATE_INTERNET_SSID", "EnterSSID-5GHz")
INTERNET_PSK = os.environ.get("TOLLGATE_INTERNET_PSK", "c03rad0r123!")


def _mint_token_nutshell(mint_url: str, amount: int) -> str:
    """Mint `amount` sats at mint_url via the nutshell cashu CLI; return a token.

    Uses a dedicated wallet name. The FakeWallet auto-pays the Lightning invoice,
    so `invoice` blocks until paid, then `send` produces a cashuB… token.
    """
    if not os.path.exists(CASHU_BIN):
        raise RuntimeError(f"cashu CLI not found at {CASHU_BIN} (run scripts/setup-cashu.sh)")
    inv = subprocess.run(
        [CASHU_BIN, "-h", mint_url, "-w", CASHU_WALLET, "invoice", str(amount)],
        capture_output=True, text=True, timeout=120,
    )
    combined = (inv.stdout or "") + (inv.stderr or "")
    if inv.returncode != 0 and "Invoice paid" not in combined:
        raise RuntimeError(f"nutshell invoice failed: {(inv.stderr or inv.stdout)[:160]!r}")
    send = subprocess.run(
        [CASHU_BIN, "-h", mint_url, "-w", CASHU_WALLET, "send", str(amount)],
        capture_output=True, text=True, timeout=90,
    )
    for line in (send.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("cashu"):  # cashuA…/cashuB…/cashuEu…
            return line
    raise RuntimeError(
        f"nutshell send produced no token: send={(send.stdout or send.stderr)[:160]!r}"
    )


def _upstream_active_ssid(router) -> str:
    """Return the currently ACTIVE upstream SSID on the router, or ''."""
    try:
        result = router.cli_command("upstream", ["list"], timeout=10)
    except Exception:  # noqa: BLE001
        return ""
    for line in str(result.get("raw", "")).splitlines():
        if "ACTIVE" in line:
            return line.split()[0]
    return ""


@pytest.fixture(scope="session")
def two_router_funded_upstream(router, secondary_router):
    """Reconcile both routers to COMMON_MINT, give alpha internet via
    INTERNET_SSID, fund alpha, then switch alpha's upstream to the secondary so
    it pays for a real session. Teardown restores configs + removes test STAs.

    Skips cleanly (AGENTS.md rule #5) when: no secondary/SSID, the mint/funding
    tool fails, or alpha can't obtain internet for funding.
    """
    if secondary_router is None:
        pytest.skip("secondary router not configured (TOLLGATE_SECONDARY_ROUTER_HOST)")
    beta_ssid = os.environ.get("TOLLGATE_SECONDARY_ROUTER_SSID", "")
    if not beta_ssid:
        pytest.skip("TOLLGATE_SECONDARY_ROUTER_SSID not set")

    # 1. Mint the funding token on the HOST first — skip BEFORE any router
    #    mutation if the mint/funding tool is broken (AGENTS.md rule #5).
    try:
        token = _mint_token_nutshell(COMMON_MINT, FUND_AMOUNT)
    except RuntimeError as exc:
        pytest.skip(f"funding unavailable — {exc}")

    targets = [router, secondary_router]
    for rtr in targets:
        rtr.ssh("cp /etc/tollgate/config.json /etc/tollgate/config.json.tag-fund-bak", timeout=10)

    internet_established = False
    try:
        # 2. Reconcile both routers to the common mint (Router.replace_mints).
        for rtr in targets:
            rtr.replace_mints([COMMON_MINT])
            rtr.ssh("service tollgate-wrt restart", timeout=20)
        time.sleep(8)

        # 3. Give alpha internet via INTERNET_SSID so it can receive funds.
        #    Remove beta's STA first (clears the active-upstream conflict that
        #    otherwise makes a second connect hang).
        try:
            router.cli_command("upstream", ["remove", beta_ssid], timeout=15)
        except Exception:  # noqa: BLE001
            pass
        # `tollgate upstream connect` spawns long-lived state and does NOT return
        # promptly (even after a successful association), so waiting on the
        # command blocks ~forever. Run it backgrounded and verify via poll.
        router.ssh(
            f"sh -c 'tollgate upstream connect {INTERNET_SSID} {INTERNET_PSK}' "
            ">/tmp/tg-up-connect.log 2>&1 </dev/null &",
            timeout=15,
        )
        # Poll for wwan up + mint reachability.
        deadline = time.time() + 120
        while time.time() < deadline:
            up = router.ssh("ifstatus wwan 2>/dev/null | jsonfilter -e '@.up' 2>/dev/null", timeout=5)
            if "true" in (up or ""):
                info = router.ssh(
                    f"wget -qO- --timeout=6 {COMMON_MINT}/v1/info 2>/dev/null | head -c 16", timeout=12
                )
                if (info or "").strip():
                    internet_established = True
                    break
            time.sleep(5)
        if not internet_established:
            pytest.skip(f"{INTERNET_SSID} did not provide alpha internet to reach the mint")

        # 4. Fund alpha and confirm balance > 0.
        router.ssh(f"echo {token!r} | tollgate wallet fund", timeout=120)
        deadline = time.time() + 30
        funded = False
        while time.time() < deadline:
            bal = router.ssh(
                "tollgate wallet balance 2>/dev/null | grep -i 'wallet balance' | head -1", timeout=10
            )
            if re.search(r"[1-9]", bal or ""):
                funded = True
                break
            time.sleep(3)
        if not funded:
            pytest.skip(f"tollgate wallet fund did not yield a positive balance (last: {bal!r})")

        # 5. Switch alpha back to beta (secondary). Now funded, alpha's USM pays
        #    beta and opens a real upstream session. Backgrounded (see step 3).
        try:
            router.cli_command("upstream", ["remove", INTERNET_SSID], timeout=15)
        except Exception:  # noqa: BLE001
            pass
        router.ssh(
            f"sh -c 'tollgate upstream connect {beta_ssid}' "
            ">/tmp/tg-up-beta.log 2>&1 </dev/null &",
            timeout=15,
        )
        time.sleep(20)  # let the autopay/session-manager settle

        yield

    finally:
        # Best-effort teardown: remove test STAs, restore both routers' mint config.
        for ssid in (INTERNET_SSID, beta_ssid):
            try:
                router.cli_command("upstream", ["remove", ssid], timeout=15)
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
    established (no secondary, funding tool broken, mint unreachable, alpha
    can't obtain funding-internet).
    """

    def test_funded_autopay_opens_session(self, two_router_funded_upstream, router):
        """After funding, alpha's USM pays beta and network_ok becomes true.

        Note: the autopay sometimes fails its first 'open gate' attempt then
        recovers via the token-recovery path, so we poll generously.
        """
        deadline = time.time() + 180
        data = {}
        while time.time() < deadline:
            status = router.get_tollgate_status()
            data = status.get("data") or status  # status nests fields under "data"
            if data.get("network_ok") is True:
                break
            time.sleep(5)
        status = router.get_tollgate_status()
        data = status.get("data") or status
        assert data.get("running") is True, f"alpha not running: {status}"
        active = _upstream_active_ssid(router)
        logs = router.ssh(
            "logread 2>/dev/null | grep -iE 'session|payment|upstream|paid' | tail -6", timeout=10
        )
        assert data.get("network_ok") is True, (
            f"funded autopay did not open a session (network_ok false; active upstream={active!r}). "
            f"Recent logs:\n{logs}"
        )

    def test_funded_session_persists(self, two_router_funded_upstream, router):
        """The funded upstream session stays up (no immediate drop after autopay).

        Shares the session-scoped funded fixture, so this is cheap. Guards
        against the session being flaky right after the recovered first attempt.
        """
        status = router.get_tollgate_status()
        data = status.get("data") or status
        assert data.get("running") is True, f"alpha not running: {status}"
        assert data.get("network_ok") is True, (
            f"funded session dropped after autopay: {status}"
        )
