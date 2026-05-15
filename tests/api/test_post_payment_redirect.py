"""Tests for post-payment redirect (feat/post-payment-redirect).

API-tier tests: pure SSH/config checks verifying the welcome page
redirect mechanism. No phone required.

The redirect works via the captive portal site, not NDS redirecturl
(NDS redirecturl only fires through the authdir HTTP endpoint, which
TollGate never hits since it uses ndsctl auth externally).

Flow: payment → React app success → redirect to /balance.html →
      redirect to /welcome.html (3 approaches).

Tests verify:
- welcome.html exists in the captive portal site
- welcome.html contains the target redirect URL
- welcome.html contains all 3 approaches (auto-redirect, tap link, PWA)
- balance.html redirects to welcome.html
- Setup script does NOT set redirecturl (removed as non-functional)
- welcome.html is served by NDS (HTTP 200 from a LAN client)

Tests use feature detection: they check if welcome.html exists
and skip cleanly when the feature is absent.
"""

import os
import subprocess

import pytest

log = __import__("logging").getLogger("tollgate.post_payment_redirect")

pytestmark = [pytest.mark.api, pytest.mark.extended]

DEFAULT_REDIRECT_URL = "https://wallet.cashu.me/welcome"
CAPTIVE_PORTAL_DIR = "/etc/tollgate/tollgate-captive-portal-site"


def _skip_if_no_welcome_page(router):
    exists = router.ssh(f"test -f {CAPTIVE_PORTAL_DIR}/welcome.html && echo YES || echo NO").strip()
    if exists != "YES":
        pytest.skip("Post-payment redirect not configured (no welcome.html)")


# --- Welcome page checks ---


def test_welcome_html_exists(router):
    _skip_if_no_welcome_page(router)


def test_welcome_html_contains_target_url(router):
    _skip_if_no_welcome_page(router)
    content = router.ssh(f"cat {CAPTIVE_PORTAL_DIR}/welcome.html 2>/dev/null")
    assert DEFAULT_REDIRECT_URL in content, \
        f"welcome.html should contain target URL '{DEFAULT_REDIRECT_URL}'"


def test_welcome_html_has_auto_redirect(router):
    _skip_if_no_welcome_page(router)
    content = router.ssh(f"cat {CAPTIVE_PORTAL_DIR}/welcome.html 2>/dev/null")
    assert "window.location" in content, \
        "welcome.html should have auto-redirect via window.location"
    assert "countdown" in content.lower(), \
        "welcome.html should have a countdown for auto-redirect"


def test_welcome_html_has_intent_url(router):
    _skip_if_no_welcome_page(router)
    content = router.ssh(f"cat {CAPTIVE_PORTAL_DIR}/welcome.html 2>/dev/null")
    assert "intent://" in content, \
        "welcome.html should have intent URL for Android Chrome escape"


def test_welcome_html_has_clickable_link(router):
    _skip_if_no_welcome_page(router)
    content = router.ssh(f"cat {CAPTIVE_PORTAL_DIR}/welcome.html 2>/dev/null")
    assert 'href="https://wallet.cashu.me/welcome"' in content, \
        "welcome.html should have clickable link to target URL"


def test_welcome_html_has_pwa_section(router):
    _skip_if_no_welcome_page(router)
    content = router.ssh(f"cat {CAPTIVE_PORTAL_DIR}/welcome.html 2>/dev/null")
    assert "PWA" in content, \
        "welcome.html should have PWA install section"


# --- Balance page redirect ---


def test_balance_html_redirects_to_welcome(router):
    _skip_if_no_welcome_page(router)
    content = router.ssh(f"cat {CAPTIVE_PORTAL_DIR}/balance.html 2>/dev/null")
    assert "welcome.html" in content, \
        "balance.html should redirect to welcome.html"


# --- Setup script should NOT have redirecturl ---


def test_setup_script_no_redirecturl(router):
    setup = router.ssh(
        "cat /etc/uci-defaults/99-tollgate-setup 2>/dev/null || echo MISSING"
    )
    if setup == "MISSING":
        pytest.skip("uci-defaults script already consumed")
    assert "redirecturl" not in setup, \
        "Setup script should NOT set redirecturl (non-functional with ndsctl auth)"


# --- NDS webroot symlink ---


def test_nds_webroot_links_to_captive_portal(router):
    target = router.ssh("readlink /etc/nodogsplash/htdocs 2>/dev/null").strip()
    assert "tollgate-captive-portal-site" in target, \
        f"NDS webroot should symlink to captive portal site, got: {target}"


def _client_ssh(*args, timeout=10):
    """Run a command on the client VM via SSH jump host."""
    jump_host = os.environ.get("TOLLGATE_SSH_JUMP_HOST", "")
    password = os.environ.get("TOLLGATE_SSH_PASSWORD",
                              os.environ.get("TOLLGATE_LUCI_PASSWORD", "tollgate"))
    client_ip = os.environ.get("TOLLGATE_CLIENT_IP", "192.168.1.100")

    ssh_cmd = ["sshpass", "-p", password, "ssh",
               "-o", "StrictHostKeyChecking=no",
               "-o", "UserKnownHostsFile=/dev/null",
               "-o", "LogLevel=ERROR"]
    if jump_host:
        ssh_cmd += ["-J", jump_host]
    ssh_cmd.append(f"root@{client_ip}")
    ssh_cmd.extend(args)

    return subprocess.run(
        ssh_cmd,
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def test_welcome_html_served_by_nds(router):
    _skip_if_no_welcome_page(router)
    gateway = router.ssh("uci -q get nodogsplash.@nodogsplash[0].gatewayaddress 2>/dev/null || echo 192.168.1.1").strip()
    port = router.ssh("uci -q get nodogsplash.@nodogsplash[0].gatewayport 2>/dev/null || echo 2050").strip()
    url = f"http://{gateway}:{port}/welcome.html"

    # NDS returns 500 for localhost requests; test from a LAN client instead.
    if os.environ.get("TOLLGATE_SSH_JUMP_HOST"):
        result = _client_ssh("curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url)
        code = result.stdout.strip()
    else:
        code = router.ssh(f"curl -s -o /dev/null -w '%{{http_code}}' {url} 2>/dev/null").strip()

    assert code in ("200", "302"), \
        f"welcome.html should be served by NDS (got HTTP {code})"
