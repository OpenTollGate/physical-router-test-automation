"""Mint URL fuzzy matching tests for PR #252.

PR #252 changes calculateAllotment() from exact string equality (==) to
MintURLMatches(), which tolerates trailing slashes, case differences,
and path normalization. These tests verify payments succeed when the
token's mint URL doesn't exactly match the configured URL.
"""

import json
import os

import pytest
import requests

from lib.constants import BACKEND_PORT
from lib.helpers import is_session_event, is_mac_lookup_failure, require_client_identity

pytestmark = [pytest.mark.api, pytest.mark.extended]


def _get_config_mint_url(router):
    raw = router.ssh("cat /etc/tollgate/config.json")
    cfg = json.loads(raw)
    return cfg.get("accepted_mints", [{}])[0].get("url", "")


def _set_config_mint_url_safe(router, new_url):
    router.ssh(
        f"jq '.accepted_mints[0].url = \"{new_url}\"' /etc/tollgate/config.json"
        f" > /tmp/cfg.json && mv /tmp/cfg.json /etc/tollgate/config.json",
        timeout=10,
    )
    router.restart_backend(timeout=45)
    deadline = time.time() + 60
    while time.time() < deadline:
        if router.api_status("/") == 200:
            time.sleep(3)
            return
        time.sleep(2)
    pytest.fail("Backend did not become healthy after config change")


def test_payment_with_trailing_slash_mismatch(router, cashu):
    """Token minted without trailing slash, config has trailing slash — payment succeeds."""
    require_client_identity(router)
    original_url = _get_config_mint_url(router)
    if not original_url:
        pytest.skip("Cannot read configured mint URL")

    token = cashu.mint(3)

    slashed_url = original_url.rstrip("/") + "/"
    if slashed_url == original_url:
        slashed_url = original_url + "/"

    try:
        _set_config_mint_url_safe(router, slashed_url)
        resp = router.pay_direct(token)
        if is_mac_lookup_failure(resp):
            pytest.skip("No client on TollGate AP")
        assert is_session_event(resp), (
            f"Payment with trailing-slash URL mismatch failed (fuzzy match should handle it): {str(resp)[:200]}"
        )
    finally:
        _set_config_mint_url_safe(router, original_url)


def test_payment_with_case_mismatch(router, cashu):
    """Token minted with lowercase host, config has uppercase — payment succeeds."""
    require_client_identity(router)
    original_url = _get_config_mint_url(router)
    if not original_url:
        pytest.skip("Cannot read configured mint URL")

    token = cashu.mint(3)

    upper_url = original_url.replace("://", "://").replace("10.", "10.")
    parts = original_url.split("://")
    if len(parts) == 2:
        upper_url = parts[0] + "://" + parts[1].upper().replace("10.99.99.2", "10.99.99.2")

    if upper_url == original_url:
        pytest.skip("Cannot create case-variant URL from current config")

    try:
        _set_config_mint_url_safe(router, upper_url)
        resp = router.pay_direct(token)
        if is_mac_lookup_failure(resp):
            pytest.skip("No client on TollGate AP")
        assert is_session_event(resp), (
            f"Payment with case-mismatch URL failed (fuzzy match should handle it): {str(resp)[:200]}"
        )
    finally:
        _set_config_mint_url_safe(router, original_url)
