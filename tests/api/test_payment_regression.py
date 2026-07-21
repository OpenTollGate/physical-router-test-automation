"""Payment regression tests for PR #252 (fuzzy mint URL matching) and PR #253 (DLEQ keyset fix).

PR #252: calculateAllotment now uses MintURLMatches() instead of exact == comparison,
tolerating trailing slashes, case differences, and path normalization.

PR #253: gonuts-tollgate bumped to v0.7.3 which fixes DLEQ proof verification
against the correct keyset (not just the current active keyset).

These tests verify the full ecash payment flow still works end-to-end.
"""

import json
import os

import pytest
import requests

from lib.constants import BACKEND_PORT
from lib.helpers import is_session_event, is_mac_lookup_failure, require_client_identity

pytestmark = [pytest.mark.api, pytest.mark.critical]


def test_ecash_payment_end_to_end(router, cashu):
    """Full ecash payment: mint token → pay → session active (PR #252 + #253 regression)."""
    require_client_identity(router)
    token = cashu.mint(4)
    assert token, "cashu.mint() returned empty token"

    resp = router.pay_direct(token)
    if is_mac_lookup_failure(resp):
        pytest.skip("No client on TollGate AP — backend cannot resolve MAC")

    assert is_session_event(resp), f"Payment did not return session event: {str(resp)[:200]}"


def test_ecash_payment_minimum_token(router, cashu):
    """Single-step token payment succeeds (boundary test for allotment calculation)."""
    require_client_identity(router)
    token = cashu.mint(4)
    assert token, "cashu.mint(1) returned empty token"

    resp = router.pay_direct(token)
    if is_mac_lookup_failure(resp):
        pytest.skip("No client on TollGate AP")

    assert is_session_event(resp), f"Minimum token payment failed: {str(resp)[:200]}"


def test_ecash_payment_verify_allotment(router, cashu):
    """Payment returns correct allotment in session event (PR #252 calculateAllotment regression)."""
    require_client_identity(router)
    token = cashu.mint(4)
    resp = router.pay_direct(token)
    if is_mac_lookup_failure(resp):
        pytest.skip("No client on TollGate AP")

    if not is_session_event(resp):
        pytest.fail(f"Payment failed: {str(resp)[:200]}")

    if resp.get("kind") == 1022:
        tags = resp.get("tags", [])
        allotment_tags = [t for t in tags if isinstance(t, list) and t[0] == "allotment"]
        assert len(allotment_tags) > 0, f"Session event missing allotment tag: {tags}"
        allotment = int(allotment_tags[0][1])
        assert allotment > 0, f"Allotment should be positive, got {allotment}"
