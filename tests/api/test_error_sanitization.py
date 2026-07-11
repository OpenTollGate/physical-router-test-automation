"""Test that API error responses don't leak internal details (audit S3)."""

import json
import pytest
from lib.helpers import skip_if_no_cli_socket


@pytest.mark.api
@pytest.mark.critical
def test_balance_invalid_mac_sanitized(router):
    """GET /balance with invalid MAC returns generic error, not internal details."""
    skip_if_no_cli_socket(router)
    resp = router.api_status("/balance?mac=invalid-mac-format")
    # Should get an error response
    if resp.status_code != 200:
        pytest.skip(f"/balance returned {resp.status_code}, expected 200 with error body")
    body = resp.json()
    error_msg = body.get("error", "")
    # Must NOT contain internal Go error patterns
    internal_patterns = ["runtime", "goroutine", ".go:", "panic:", "nil pointer",
                        "connection refused", "dial tcp", "no such file"]
    for pattern in internal_patterns:
        assert pattern not in error_msg.lower(), \
            f"Error response leaks internal detail '{pattern}': {error_msg}"
    # Should contain a generic message (after S3 fix)
    # Before fix: would contain raw err.Error() (e.g., "exit status 1: ...")
    # After fix: "failed to retrieve usage data" or similar generic message


@pytest.mark.api
@pytest.mark.critical
def test_ln_invoice_error_sanitized(router):
    """POST /ln-invoice with bad mint returns generic error, not internal details."""
    import requests
    try:
        resp = requests.post(
            f"http://[::1]:2121/ln-invoice",
            json={"amount": 1, "mint_url": "http://nonexistent.invalid"},
            timeout=10
        )
    except Exception:
        pytest.skip("ln-invoice endpoint not reachable")
    
    if resp.status_code == 405:
        pytest.skip("ln-invoice not available (GET returns 405)")
    
    body = resp.json()
    error_msg = body.get("error", "")
    internal_patterns = ["runtime", "goroutine", ".go:", "panic:", "nil pointer",
                        "dial tcp", "connection refused", "no such host"]
    for pattern in internal_patterns:
        assert pattern not in error_msg.lower(), \
            f"LN invoice error leaks internal detail '{pattern}': {error_msg}"
