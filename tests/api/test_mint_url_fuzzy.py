"""Mint URL fuzzy matching tests for PR #252.

PR #252 (merged) changes calculateAllotment() from exact string equality
(==) to MintURLMatches(), which tolerates trailing slashes, case
differences, and path normalization.

These tests verify end-to-end: modify the router's accepted_mints URL
to differ from the token's embedded URL, then pay — should succeed.
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
    mints = cfg.get("accepted_mints", [])
    return mints[0].get("url", "") if mints else ""


def _set_config_mint_url_safe(router, new_url):
    router.ssh(
        f"jq '.accepted_mints[0].url = \"{new_url}\"' /etc/tollgate/config.json"
        f" > /tmp/cfg.json && mv /tmp/cfg.json /etc/tollgate/config.json",
        timeout=10,
    )
    router.restart_backend(timeout=45)


def _pay_via_requests(router, token):
    backend_ip = os.environ.get("TOLLGATE_SSH_HOST", "10.99.99.1")
    url = f"http://{backend_ip}:{BACKEND_PORT}/"
    try:
        resp = requests.post(url, data=token, headers={"Content-Type": "text/plain"}, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        return {"raw": resp.text[:300], "status": resp.status_code}
    except Exception as e:
        return {"raw": str(e)[:200]}


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
        time.sleep(5)
        resp = _pay_via_requests(router, token)
        if is_mac_lookup_failure(resp):
            pytest.skip("No client on TollGate AP")
        assert is_session_event(resp), (
            f"Payment with trailing-slash URL mismatch failed (fuzzy match should handle it): {str(resp)[:200]}"
        )
    finally:
        _set_config_mint_url_safe(router, original_url)


def test_payment_with_case_mismatch(router, cashu):
    """Token has lowercase host, config has uppercase — payment succeeds."""
    require_client_identity(router)
    original_url = _get_config_mint_url(router)
    if not original_url:
        pytest.skip("Cannot read configured mint URL")

    token = cashu.mint(3)

    parts = original_url.split("://")
    if len(parts) != 2:
        pytest.skip("Cannot parse mint URL scheme")

    host_port_path = parts[1]
    slash_idx = host_port_path.find("/")
    if slash_idx == -1:
        upper_url = parts[0] + "://" + host_port_path.upper() + "/"
    else:
        upper_url = parts[0] + "://" + host_port_path[:slash_idx].upper() + host_port_path[slash_idx:]

    if upper_url == original_url:
        pytest.skip("URL has no case-variable characters")

    try:
        _set_config_mint_url_safe(router, upper_url)
        time.sleep(5)
        resp = _pay_via_requests(router, token)
        if is_mac_lookup_failure(resp):
            pytest.skip("No client on TollGate AP")
        assert is_session_event(resp), (
            f"Payment with case-mismatch URL failed (fuzzy match should handle it): {str(resp)[:200]}"
        )
    finally:
        _set_config_mint_url_safe(router, original_url)
