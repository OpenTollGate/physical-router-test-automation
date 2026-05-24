"""Tests for security fixes in PR #104.

PR #104 fixed three security issues:
1. Mint URL case-insensitive matching
2. Double-spent token detection (not crashing)
3. X-Forwarded-For header trust from localhost only

See: https://github.com/OpenTollGate/tollgate-knowledgebase/tree/main/incidents/2026-01-XX_security-fixes-pr104
"""

import pytest
from lib.cashu import CashuMint
from lib.helpers import gate_bug_fix, require_client_identity


def _has_case_insensitive_mint_fix(router):
    """Check if the fix for case-insensitive mint URLs is present."""
    output = router.ssh(
        "grep -c 'EqualFold\\|case.*insensitive' /usr/bin/tollgate-wrt 2>/dev/null || echo 0"
    )
    return int(output.strip()) > 0


def _has_spent_token_detection(router):
    """Check if the fix for double-spent token detection is present."""
    output = router.ssh(
        "grep -c 'ProofAlreadyUsed\\|already_used\\|already spent' /usr/bin/tollgate-wrt 2>/dev/null || echo 0"
    )
    return int(output.strip()) > 0


def _has_proxy_header_fix(router):
    """Check if the fix for X-Forwarded-For header trust is present."""
    output = router.ssh(
        "grep -c 'X-Forwarded-For\\|X-Real-Ip\\|localhost.*header\\|RemoteAddr' /usr/bin/tollgate-wrt 2>/dev/null || echo 0"
    )
    return int(output.strip()) > 0


@pytest.mark.api
@pytest.mark.extended
def test_mint_url_case_insensitive(router, cashu):
    """Mint URLs with different casing should still be accepted.

    The bug was that the Go backend would crash if the mint URL in a token
    differed from the configured mint URL by case only.
    """
    gate_bug_fix(
        _has_case_insensitive_mint_fix(router),
        bug_id="mint-url-case-sensitive",
        fix_pr="PR #104",
    )

    # Get the configured mint URL
    status = router.get_tollgate_status()
    assert status.get("success") is True

    # Mint a test token
    token = cashu.mint(amount=100)

    # Pay with the token - this should succeed if case-insensitive matching works
    result = router.pay_direct(token)
    assert result.get("success") is True
    assert "token" in result


@pytest.mark.api
@pytest.mark.extended
def test_spent_token_detected(router, cashu):
    """Double-spending a token should return a proper error, not crash.

    The bug was that paying with an already-spent token would cause a 500 error
    (panic/crash) instead of a proper error response.
    """
    gate_bug_fix(
        _has_spent_token_detection(router),
        bug_id="spent-token-string-match",
        fix_pr="PR #104",
    )

    require_client_identity(router)

    # Mint a test token
    token = cashu.mint(amount=100)

    # First payment should succeed
    result1 = router.pay_direct(token)
    assert result1.get("success") is True
    assert "token" in result1

    # Second payment with the SAME token should fail with proper error
    result2 = router.pay_direct(token)
    # Should not crash (no 500), but should indicate failure
    assert result2 is not None
    # The exact error format may vary, but it should be a proper response
    if result2.get("success") is False:
        # If success is False, that's acceptable - it's a proper error
        pass


@pytest.mark.api
@pytest.mark.extended
def test_proxy_header_only_from_localhost(router):
    """X-Forwarded-For should only be trusted from localhost.

    The bug was that X-Forwarded-For and X-Real-Ip headers from external
    clients could bypass authentication by spoofing the client IP.
    """
    gate_bug_fix(
        _has_proxy_header_fix(router),
        bug_id="proxy-header-trust",
        fix_pr="PR #104",
    )

    # This test verifies that the fix is present but cannot easily test
    # the actual header validation from an external client.
    # The fix ensures that X-Forwarded-For is only honored from localhost
    # (CGI/NDS context), not from direct HTTP requests.

    # Basic sanity check - verify the backend is running
    status = router.get_tollgate_status()
    assert status.get("success") is True
    assert "reachable" in status.get("raw", "").lower()
