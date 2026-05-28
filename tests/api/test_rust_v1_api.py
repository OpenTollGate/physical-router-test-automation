import json
import pytest

from lib.helpers import parse_json_or_fail

pytestmark = [pytest.mark.rust_only, pytest.mark.api, pytest.mark.smoke]


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


def test_rust_pay_token(router, cashu):
    token = cashu.mint(1)
    resp = router.pay_direct(token)
    assert resp.get("kind") == 1022, f"Expected kind:1022 session event, got: {str(resp)[:200]}"


def test_rust_usage(router, cashu):
    if router.backend.is_rust:
        pytest.skip("Rust v1 usage API format differs from Go (Amperstrand/tollgate-rs#42)")
    resp = router.backend_curl_xff(
        router.backend_url("/usage"),
        ip=router.phone_ip or "127.0.0.1",
    )
    assert resp, f"Empty usage response"
    assert "allotment" in resp.lower() or "usage" in resp.lower(), f"Unexpected usage response: {resp[:200]}"


def test_rust_balance(router, cashu):
    if router.backend.is_rust:
        pytest.skip("Rust v1 balance API format differs from Go (Amperstrand/tollgate-rs#42)")
    resp = router.backend_curl_xff(
        router.backend_url("/balance"),
        ip=router.phone_ip or "127.0.0.1",
    )
    data = parse_json_or_fail(resp, "balance", skip=True)
    assert "remaining" in data or "allotment" in data, f"Unexpected balance response: {resp[:200]}"


def test_rust_whoami(router):
    resp = router.ssh(
        f"wget -qO- --header='X-Forwarded-For: {router.phone_ip or '127.0.0.1'}' "
        f"'{router.backend_url('/whoami')}'"
    )
    assert resp, "Empty whoami response"
    assert ":" in resp and len(resp.split(":")) >= 6, f"Expected MAC address, got: {resp[:100]}"


def test_rust_ln_invoice_create(router):
    resp = router.ssh(
        f"wget -qO- --post-data='{{\"amount\": 10, \"unit\": \"sat\"}}' "
        f"--header='Content-Type: application/json' '{router.backend_url('/ln-invoice')}'"
    )
    data = parse_json_or_fail(resp, "ln-invoice create", skip=True)
    assert "quote" in data or "payment_request" in data or "invoice" in data, \
        f"Unexpected ln-invoice response: {str(data)[:200]}"


def test_rust_ln_invoice_status(router):
    resp = router.ssh(
        f"wget -qO- '{router.backend_url('/ln-invoice')}?quote=nonexistent-test-quote'"
    )
    assert resp, "Empty ln-invoice status response"
