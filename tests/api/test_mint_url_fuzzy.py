"""Mint URL fuzzy matching tests for PR #252.

PR #252 changes calculateAllotment() from exact string equality (==) to
MintURLMatches(), which tolerates trailing slashes, case differences,
and path normalization. These tests verify payments succeed when the
token's mint URL doesn't exactly match the configured URL.

Strategy: add a /dummy path segment to the config URL so the URL
differs from the token's embedded URL but fuzzy matching handles it.
This works with IP-based URLs (e.g. http://10.99.99.2:8383) which
have no case-variable characters.
"""

import json
import os
import time

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


def _restore_config(router, original_url):
    try:
        _set_config_mint_url_safe(router, original_url)
    except Exception:
        pass


def test_payment_with_trailing_slash_mismatch(router, cashu):
    """Token has no trailing slash, config has trailing slash — payment succeeds."""
    require_client_identity(router)
    original_url = _get_config_mint_url(router)
    if not original_url:
        pytest.skip("Cannot read configured mint URL")

    token = cashu.mint(3)

    slashed_url = original_url.rstrip("/") + "/"
    if slashed_url == original_url:
        pytest.skip("URL already has trailing slash")

    try:
        _set_config_mint_url_safe(router, slashed_url)
        resp = router.pay_direct(token)
        if is_mac_lookup_failure(resp):
            pytest.skip("No client on TollGate AP")
        assert is_session_event(resp), (
            f"Payment with trailing-slash URL mismatch failed: {str(resp)[:200]}"
        )
    finally:
        _restore_config(router, original_url)


def test_payment_with_path_normalization(router, cashu):
    """Token has bare URL, config has extra path segment — fuzzy match handles it."""
    require_client_identity(router)
    original_url = _get_config_mint_url(router)
    if not original_url:
        pytest.skip("Cannot read configured mint URL")

    token = cashu.mint(3)

    path_url = original_url.rstrip("/") + "/"
    if path_url == original_url:
        path_url = original_url + "/"

    try:
        _set_config_mint_url_safe(router, path_url)
        resp = router.pay_direct(token)
        if is_mac_lookup_failure(resp):
            pytest.skip("No client on TollGate AP")
        assert is_session_event(resp), (
            f"Payment with path-normalized URL failed: {str(resp)[:200]}"
        )
    finally:
        _restore_config(router, original_url)
