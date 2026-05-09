# TIP-02: Cashu Payments — Edge-Case Token Handling

import pytest
from lib.helpers import is_session_event, require_client_identity

pytestmark = [pytest.mark.api, pytest.mark.extended]


def test_empty_token_rejected(router, cashu):
    empty_token = ""
    resp = router.pay_via_header(empty_token)

    # Should not crash — response should be empty or non-success
    assert not resp or '"success":true' not in resp, \
        f"Empty token was accepted (unexpected success)"


def test_garbage_token_rejected(router, cashu):
    garbage_token = "not-a-token"
    resp = router.pay_via_header(garbage_token)

    # Should not crash — response should not indicate success
    assert '"success":true' not in resp, \
        f"Garbage token was ACCEPTED (expected rejection)"


def test_malformed_cashu_prefix(router, cashu):
    malformed_token = "cashuA" + "!" * 10  # Not valid base64
    resp = router.pay_via_header(malformed_token)

    # Should not crash — response should not indicate success
    assert '"success":true' not in resp, \
        f"Malformed token was ACCEPTED (expected rejection)"


def test_duplicate_token_immediate_reuse(router, cashu):
    require_client_identity(router)

    # Mint token
    token = cashu.mint(1)

    # First payment should succeed
    resp1 = router.pay_via_header(token)
    assert is_session_event(resp1), \
        f"First payment failed: {str(resp1)[:200]}"

    # Second payment with same token should fail (double-spend protection)
    resp2 = router.pay_via_header(token)
    assert '"success":true' not in resp2, \
        f"Duplicate token was ACCEPTED (expected double-spend rejection)"
