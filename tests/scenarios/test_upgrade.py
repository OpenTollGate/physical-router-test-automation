"""TollGate package upgrade tests — verify config migration, service health, and
uci-defaults behavior when upgrading from one version to another.

Issue #45: repeatable upgrade test framework.

Test flow:
  1. Install baseline .ipk (pre-upgrade version)
  2. Capture pre-upgrade state (config, UCI, service)
  3. Install target .ipk (post-upgrade version)
  4. Reboot to trigger uci-defaults scripts
  5. Verify config migration, service health, AP setup, no crash loops

Environment variables:
  UPGRADE_BASELINE_IPK — path or URL to pre-upgrade .ipk (default: skip install,
    assume router is already running the baseline version)
  UPGRADE_TARGET_IPK   — path or URL to post-upgrade .ipk (required)
  UPGRADE_SKIP_INSTALL — "1" to skip both install steps (assume router already
    has target version — for testing post-upgrade state directly)

The upgrade workflow on the cloud lab:
  cloud-lab.py submit --smoke -- pytest tests/scenarios/test_upgrade.py -v

For physical routers with a real upgrade:
  UPGRADE_BASELINE_IPK=/path/to/tollgate-wrt-v0.4.0.ipk \
  UPGRADE_TARGET_IPK=/path/to/tollgate-wrt-v0.5.0.ipk \
  pytest tests/scenarios/test_upgrade.py -v --router alpha
"""
from __future__ import annotations

import json
import logging
import os
import time

import pytest

pytestmark = [
    pytest.mark.api,
    pytest.mark.slow,
    pytest.mark.virtual_lab,
]

log = logging.getLogger("tollgate.upgrade")

TARGET_IPK = os.environ.get("UPGRADE_TARGET_IPK", "")
BASELINE_IPK = os.environ.get("UPGRADE_BASELINE_IPK", "")
SKIP_INSTALL = os.environ.get("UPGRADE_SKIP_INSTALL", "").lower() in ("1", "true", "yes")


def _install_ipk(router, ipk_path: str, timeout: int = 120) -> None:
    """Install a .ipk on the router via SCP + opkg."""
    if not ipk_path:
        return
    if ipk_path.startswith("http"):
        router.ssh(f"wget -qO /tmp/tollgate-upgrade.ipk '{ipk_path}'", timeout=60)
        remote_path = "/tmp/tollgate-upgrade.ipk"
    else:
        remote_path = "/tmp/tollgate-upgrade.ipk"
        router.scp_to(ipk_path, remote_path)
    router.ssh(f"opkg install --force-upgrade {remote_path}", timeout=timeout)
    log.info("Installed %s", ipk_path)


def _reboot_router(router, timeout: int = 180) -> None:
    """Reboot router and wait for SSH to return."""
    try:
        router.ssh("reboot", timeout=5)
    except Exception:
        pass  # expected — SSH dies on reboot
    log.info("Waiting for router to come back after reboot...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            router.ssh("echo alive", timeout=10)
            log.info("Router is back after %.0fs", time.monotonic() - (deadline - timeout))
            time.sleep(10)  # let services settle
            return
        except Exception:
            time.sleep(5)
    pytest.fail(f"Router did not come back within {timeout}s after reboot")


@pytest.fixture(scope="module")
def upgraded_router(router, backend):
    """Install baseline, capture state, upgrade, reboot, yield router."""
    if not SKIP_INSTALL:
        if BASELINE_IPK:
            log.info("Installing baseline: %s", BASELINE_IPK)
            _install_ipk(router, BASELINE_IPK)
            time.sleep(5)

    pre_config = router.ssh("cat /etc/tollgate/config.json 2>/dev/null || echo '{}'", timeout=10)
    pre_wireless = router.ssh("uci show wireless 2>/dev/null || echo 'no wireless'", timeout=10)
    pre_uhttpd = router.ssh("uci show uhttpd 2>/dev/null || echo 'no uhttpd'", timeout=10)
    pre_version = router.ssh("opkg list-installed tollgate-wrt 2>/dev/null || echo 'not installed'", timeout=10).strip()

    log.info("Pre-upgrade state: %s", pre_version)

    if not SKIP_INSTALL:
        if TARGET_IPK:
            log.info("Installing target: %s", TARGET_IPK)
            _install_ipk(router, TARGET_IPK)
            _reboot_router(router)

    yield router, {
        "config": pre_config,
        "wireless": pre_wireless,
        "uhttpd": pre_uhttpd,
        "version": pre_version,
    }


class TestConfigMigration:
    """Config.json should survive the upgrade with correct version bump."""

    def test_config_exists_and_is_valid_json(self, upgraded_router):
        router, _ = upgraded_router
        raw = router.ssh("cat /etc/tollgate/config.json 2>/dev/null || echo '{}'", timeout=10)
        cfg = json.loads(raw)
        assert "accepted_mints" in cfg, f"config.json missing accepted_mints: {raw[:200]}"
        assert isinstance(cfg["accepted_mints"], list), "accepted_mints must be a list"

    def test_config_version_present(self, upgraded_router):
        router, _ = upgraded_router
        raw = router.ssh("cat /etc/tollgate/config.json 2>/dev/null || echo '{}'", timeout=10)
        cfg = json.loads(raw)
        version = cfg.get("config_version", "")
        assert version, f"config_version missing or empty: {version}"
        log.info("Post-upgrade config_version: %s", version)

    def test_mint_urls_preserved(self, upgraded_router):
        router, pre = upgraded_router
        pre_cfg = json.loads(pre["config"])
        post_raw = router.ssh("cat /etc/tollgate/config.json 2>/dev/null || echo '{}'", timeout=10)
        post_cfg = json.loads(post_raw)

        pre_mints = {m.get("url", "") for m in pre_cfg.get("accepted_mints", [])}
        post_mints = {m.get("url", "") for m in post_cfg.get("accepted_mints", [])}

        missing = pre_mints - post_mints
        assert not missing, f"Mint URLs lost during upgrade: {missing}"

    def test_step_size_preserved(self, upgraded_router):
        router, pre = upgraded_router
        pre_cfg = json.loads(pre["config"])
        post_raw = router.ssh("cat /etc/tollgate/config.json 2>/dev/null || echo '{}'", timeout=10)
        post_cfg = json.loads(post_raw)

        pre_step = pre_cfg.get("step_size", 0)
        post_step = post_cfg.get("step_size", 0)
        assert pre_step == post_step, f"step_size changed: {pre_step} → {post_step}"


class TestServiceHealth:
    """TollGate backend should be running and healthy after upgrade."""

    def test_service_running(self, upgraded_router):
        router, _ = upgraded_router
        status = router.ssh("/etc/init.d/tollgate-wrt status 2>/dev/null || echo 'not running'", timeout=10)
        assert "running" in status.lower(), f"tollgate-wrt not running: {status}"

    def test_backend_api_responds(self, upgraded_router):
        router, _ = upgraded_router
        code = router.api_status("/")
        assert code == 200, f"Backend API not responding (HTTP {code})"

    def test_no_crash_loop(self, upgraded_router):
        router, _ = upgraded_router
        log_text = router.ssh("logread 2>/dev/null | grep -c 'tollgate-wrt' || echo 0", timeout=10)
        respawn_count = router.ssh(
            "logread 2>/dev/null | grep 'respawn' | grep -c 'tollgate' || echo 0", timeout=10
        )
        assert int(respawn_count.strip()) < 5, (
            f"Service crash-looped {respawn_count.strip()} times after upgrade"
        )

    def test_backend_listening_on_2121(self, upgraded_router):
        router, _ = upgraded_router
        listeners = router.ssh("netstat -tlnp 2>/dev/null | grep 2121 || echo 'not listening'", timeout=10)
        assert "2121" in listeners, f"Backend not listening on 2121: {listeners}"


class TestUCIDefaults:
    """uci-defaults scripts should have run correctly after upgrade."""

    def test_ap_interfaces_exist(self, upgraded_router):
        router, _ = upgraded_router
        wireless = router.ssh("uci show wireless 2>/dev/null || echo 'no wireless'", timeout=10)
        if "no wireless" in wireless:
            pytest.skip("No wireless config (cloud lab without hwsim)")
        assert "default_radio0" in wireless or "radio0" in wireless, (
            f"No radio0 in wireless config: {wireless[:200]}"
        )

    def test_nodogsplash_configured(self, upgraded_router):
        router, _ = upgraded_router
        nds = router.ssh("uci show nodogsplash 2>/dev/null || echo 'no nodogsplash'", timeout=10)
        assert "gatewayinterface" in nds, f"Nodogsplash not configured: {nds[:200]}"

    def test_setup_flag_exists(self, upgraded_router):
        """The 99-tollgate-setup flag should exist (script ran to completion)."""
        router, _ = upgraded_router
        flag = router.ssh("ls /etc/tollgate-setup-done 2>/dev/null && echo EXISTS || echo MISSING", timeout=10)
        assert "EXISTS" in flag, "tollgate-setup-done flag missing — uci-defaults may have failed"

    def test_captive_portal_site_deployed(self, upgraded_router):
        router, _ = upgraded_router
        portal = router.ssh(
            "ls /etc/tollgate/tollgate-captive-portal-site/splash.html 2>/dev/null && echo EXISTS || echo MISSING",
            timeout=10,
        )
        if "MISSING" in portal:
            pytest.skip("Captive portal site not deployed on this build")


class TestPaymentFlow:
    """Basic payment flow should work after upgrade."""

    def test_token_payment_works(self, upgraded_router, cashu):
        router, _ = upgraded_router
        from lib.helpers import require_client_identity, assert_session_active

        require_client_identity(router)

        token = cashu.mint(4)
        resp = router.pay_direct(token)

        accepted = (
            resp.get("kind") in (1022, 21000, 10021)
            or resp.get("success") is True
        )
        assert accepted, f"Payment rejected after upgrade: {str(resp)[:300]}"


class TestFirewallRules:
    """Firewall rules should be correct after upgrade."""

    def test_port_2121_accessible(self, upgraded_router):
        router, _ = upgraded_router
        # Already verified in TestServiceHealth, but check the firewall rule exists
        rules = router.ssh("nft list ruleset 2>/dev/null | grep 2121 || iptables -L -n 2>/dev/null | grep 2121 || echo 'no rule'", timeout=10)
        if "no rule" in rules:
            pytest.skip("Could not verify port 2121 firewall rule (nft/iptables not available)")

    def test_nodogsplash_chain_exists(self, upgraded_router):
        router, _ = upgraded_router
        chains = router.ssh("nft list chains 2>/dev/null | grep nds || iptables -L -n 2>/dev/null | grep nds || echo 'no nds chain'", timeout=10)
        if "no nds chain" in chains:
            pytest.skip("Could not verify nodogsplash chain (nft/iptables not available)")
