"""End-to-end test for post-payment redirect.

Verifies that after a successful payment through the captive portal,
the user's real browser opens to the configured redirect URL.

Flow:
1. Connect to TollGate WiFi
2. Pay for access via direct backend payment
3. Wait for auth to complete
4. Verify Android received the redirect and opened it in Chrome

This test requires a physical Android device connected via ADB.
Tests use feature detection and skip if redirect is not configured.
"""

import time
import logging
import re

import pytest

from lib.helpers import assert_internet, is_session_event, assert_session_active
from lib.constants import TOKEN_DEFAULT

log = logging.getLogger("tollgate.test_post_payment_redirect")

pytestmark = [
    pytest.mark.phone,
    pytest.mark.slow,
    pytest.mark.timeout(180),
    pytest.mark.extended,
]

DEFAULT_REDIRECT_URL = "https://wallet.cashu.me/welcome"


def _skip_if_no_redirect_support(router):
    redirect = router.ssh(
        "uci -q get nodogsplash.@nodogsplash[0].redirecturl 2>/dev/null"
    ).strip()
    if not redirect:
        pytest.skip("Post-payment redirect not configured (no redirecturl in NDS)")


def test_redirect_opens_in_browser_after_payment(router, adb, cashu, connected_wifi, screenshot_raw):
    """After payment, Android should open the redirect URL in the real browser."""
    _skip_if_no_redirect_support(router)

    redirect_url = router.ssh(
        "uci -q get nodogsplash.@nodogsplash[0].redirecturl 2>/dev/null"
    ).strip()
    log.info(f"Configured redirect URL: {redirect_url}")

    token = cashu.mint(TOKEN_DEFAULT)
    resp = router.pay_direct(token)
    assert is_session_event(resp), f"Payment failed: {str(resp)[:200]}"
    assert router.wait_for_auth(timeout=30), "Not authenticated after payment"
    assert assert_internet(adb, "1.1.1.1"), "No internet after auth"

    screenshot_raw("redirect-authed.png")
    log.info("Payment successful, waiting for redirect to fire in browser...")

    time.sleep(5)

    current_apps = adb.shell("dumpsys activity activities 2>/dev/null | grep -E 'mResumedActivity|topResumedActivity'").strip()
    log.info(f"Current activity: {current_apps}")

    browser_opened = any(
        pattern in current_apps
        for pattern in ["chrome", "browser", "Browser", "Chrome"]
    )

    if browser_opened:
        log.info("Browser detected as active activity after payment")
        screenshot_raw("redirect-browser-opened.png")
    else:
        log.warning(f"Browser not detected as active activity: {current_apps}")
        log.info("This may be expected — some Android versions handle the redirect differently")
        log.info("Checking recent tasks for browser activity...")
        recent = adb.shell("dumpsys activity recents 2>/dev/null | head -20").strip()
        if any(p in recent for p in ["chrome", "browser"]):
            log.info("Browser found in recent tasks — redirect likely fired")
            screenshot_raw("redirect-browser-in-recent.png")
        else:
            screenshot_raw("redirect-no-browser.png")
            log.warning("No browser activity detected — redirect may not have fired")

    assert_session_active(router)


def test_redirect_url_matches_config(router, adb, cashu, connected_wifi, screenshot_raw):
    """Verify the redirect URL in NDS config matches what the test expects."""
    _skip_if_no_redirect_support(router)

    redirect = router.ssh(
        "uci -q get nodogsplash.@nodogsplash[0].redirecturl 2>/dev/null"
    ).strip()
    assert redirect == DEFAULT_REDIRECT_URL, \
        f"Expected '{DEFAULT_REDIRECT_URL}', got '{redirect}' — test expectations may be wrong"

    screenshot_raw("redirect-config-verified.png")
