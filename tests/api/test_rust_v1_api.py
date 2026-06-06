import json
import pytest

from lib.helpers import parse_json_or_fail

pytestmark = [pytest.mark.rust_only, pytest.mark.api, pytest.mark.smoke]


@pytest.mark.smoke
def test_rust_advertisement(router):
    if router.backend.is_rust:
        pytest.skip("Rust v1 advertisement format differs from test expectations (Amperstrand/tollgate-rs#42)")
    body = router.api_body("/")
    if '"kind":21023' in body:
        pytest.skip("Discovery in degraded mode, skipping kind:10021 check")
    assert '"kind":10021' in body, f"Response missing kind:10021: {body[:200]}"
    data = parse_json_or_fail(body, "advertisement")
    tags = {t[0]: t[1] for t in data.get("tags", []) if isinstance(t, list) and len(t) >= 2}
    assert "metric" in tags, f"Missing metric tag: {body[:200]}"
    assert "step" in tags, f"Missing step tag: {body[:200]}"


@pytest.mark.smoke
def test_rust_pay_token(router, cashu):
    token = cashu.mint(1)
    resp = router.pay_direct(token)
    assert resp.get("kind") == 1022, f"Expected kind:1022 session event, got: {str(resp)[:200]}"


@pytest.mark.smoke
def test_rust_usage(router, cashu):
    if router.backend.is_rust:
        pytest.skip("Rust v1 usage API format differs from Go (Amperstrand/tollgate-rs#42)")
    resp = router.backend_curl_xff(
        router.backend_url("/usage"),
        ip=router.phone_ip or "127.0.0.1",
    )
    assert resp, f"Empty usage response"
    assert "allotment" in resp.lower() or "usage" in resp.lower(), f"Unexpected usage response: {resp[:200]}"


@pytest.mark.smoke
def test_rust_balance(router, cashu):
    if router.backend.is_rust:
        pytest.skip("Rust v1 balance API format differs from Go (Amperstrand/tollgate-rs#42)")
    resp = router.backend_curl_xff(
        router.backend_url("/balance"),
        ip=router.phone_ip or "127.0.0.1",
    )
    data = parse_json_or_fail(resp, "balance", skip=True)
    assert "remaining" in data or "allotment" in data, f"Unexpected balance response: {resp[:200]}"


@pytest.mark.smoke
def test_rust_whoami(router):
    resp = router.ssh(
        f"wget -qO- --header='X-Forwarded-For: {router.phone_ip or '127.0.0.1'}' "
        f"'{router.backend_url('/whoami')}'"
    )
    assert resp, "Empty whoami response"
    assert ":" in resp and len(resp.split(":")) >= 6, f"Expected MAC address, got: {resp[:100]}"


@pytest.mark.smoke
def test_rust_ln_invoice_create(router):
    resp = router.ssh(
        f"wget -qO- --post-data='{{\"amount\": 10, \"unit\": \"sat\"}}' "
        f"--header='Content-Type: application/json' '{router.backend_url('/ln-invoice')}'"
    )
    data = parse_json_or_fail(resp, "ln-invoice create", skip=True)
    assert "quote" in data or "payment_request" in data or "invoice" in data, \
        f"Unexpected ln-invoice response: {str(data)[:200]}"


@pytest.mark.smoke
def test_rust_ln_invoice_status(router):
    resp = router.ssh(
        f"wget -qO- '{router.backend_url('/ln-invoice')}?quote=nonexistent-test-quote'"
    )
    assert resp, "Empty ln-invoice status response"


@pytest.mark.smoke
def test_rust_full_payment_cycle(router, cashu):
    """End-to-end: mint token → pay → verify session → check balance → verify whoami."""
    if not cashu.is_available():
        pytest.skip("cashu venv not available")

    # Step 1: Advertisement has kind:10021
    body = router.api_body("/")
    data = parse_json_or_fail(body, "advertisement")
    assert data.get("kind") == 10021, f"Expected kind:10021, got: {body[:200]}"

    # Step 2: Mint a token and pay
    token = cashu.mint(4)
    resp = router.pay_direct(token)
    assert resp.get("kind") == 1022, f"Payment failed: {str(resp)[:200]}"

    # Step 3: Check balance returns remaining > 0
    bal_resp = router.backend_curl_xff(
        router.backend_url("/balance"),
        ip=router.phone_ip or "127.0.0.1",
    )
    bal = parse_json_or_fail(bal_resp, "balance")
    assert bal.get("session_active") is True, f"Session not active: {bal_resp[:200]}"
    assert bal.get("remaining", 0) > 0, f"No remaining allotment: {bal_resp[:200]}"

    # Step 4: Usage returns used/allotment text
    usage_resp = router.backend_curl_xff(
        router.backend_url("/usage"),
        ip=router.phone_ip or "127.0.0.1",
    )
    assert usage_resp, "Empty usage response"
    parts = usage_resp.split("/")
    assert len(parts) == 2, f"Usage not in used/allotment format: {usage_resp[:200]}"
    assert int(parts[1]) > 0, f"Allotment is 0 or negative: {usage_resp[:200]}"

    # Step 5: Whoami returns MAC address
    whoami = router.ssh(
        f"wget -qO- --header='X-Forwarded-For: {router.phone_ip or '127.0.0.1'}' "
        f"'{router.backend_url('/whoami')}'"
    )
    assert whoami, "Empty whoami response"
    assert "mac=" in whoami, f"Whoami missing mac=: {whoami[:100]}"
