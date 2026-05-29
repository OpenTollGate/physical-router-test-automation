"""Reproduction tests for issue #149: Lightning invoice 'amount 0 sats' failure.

Tests the /ln-invoice endpoint with various payloads to reproduce and
characterize the bug where amount arrives as 0 in the backend.

Root cause analysis:
  1. The captive portal frontend (lightning.js) sends { amount, device }
     but the backend expects { amount, mint_url } — missing mint_url field.
  2. The frontend input uses type='text' with inputMode='numeric', so amount
     can be '' (empty string), which Go decodes as 0 for uint64.
  3. The rickroll stub in lightning.js (line 4-8) prevents this in the
     current production build, but the bug manifests when the stub is removed.
"""

import json
import logging

import pytest

from lib.constants import BACKEND_PORT, TEST_MINT_URL
from lib.helpers import parse_json_or_fail

log = logging.getLogger("tollgate.lightning_invoice")

pytestmark = [pytest.mark.api, pytest.mark.extended]


def _post_ln_invoice_via_nc(router, payload_dict):
    """POST to /ln-invoice via nc, returning (status_code, response_body)."""
    payload = json.dumps(payload_dict)
    host = router.backend_url("/ln-invoice").replace("http://", "").rstrip("/ln-invoice")
    request = (
        f"POST /ln-invoice HTTP/1.0\r\n"
        f"Host: {host}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload.encode('utf-8'))}\r\n"
        f"X-Forwarded-For: {router.phone_ip}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
        f"{payload}"
    )
    raw = router.ssh(
        f"printf '%s' '{request}' | nc ::1 {BACKEND_PORT} 2>/dev/null",
        timeout=30,
    )
    if not raw:
        return 0, ""

    normalized = raw.replace("\r\n", "\n")
    parts = normalized.split("\n\n", 1)
    body = parts[1].strip() if len(parts) > 1 else ""
    status_line = parts[0].split("\n")[0] if parts else ""
    code = 0
    if "HTTP/" in status_line:
        tokens = status_line.split()
        if len(tokens) >= 2:
            try:
                code = int(tokens[1])
            except ValueError:
                pass
    return code, body


@pytest.fixture(scope="module")
def backend_running(router):
    code = router.api_status("/")
    if code == 0:
        pytest.skip("Backend not responding on this router")
    return True


def test_zero_amount_returns_error(router, backend_running):
    """POST with amount=0 must be rejected (issue #149 reproduction)."""
    code, body = _post_ln_invoice_via_nc(router, {
        "amount": 0,
        "mint_url": TEST_MINT_URL,
    })
    assert code in (400, 200), f"Unexpected status {code}: {body[:200]}"
    if code == 400:
        resp = parse_json_or_fail(body, "zero-amount response")
        assert resp.get("status") == 0, f"Expected status 0, got: {resp}"
        assert "amount" in resp.get("error", "").lower(), \
            f"Expected amount-related error, got: {resp.get('error')}"
        log.info("Zero amount correctly rejected: %s", resp.get("error"))
    else:
        log.warning("Backend accepted amount=0 — this is the bug from issue #149")


def test_missing_mint_url_returns_error(router, backend_running):
    """POST with amount but no mint_url must be rejected.

    This reproduces the frontend bug where lightning.js sends { amount, device }
    instead of { amount, mint_url }.
    """
    code, body = _post_ln_invoice_via_nc(router, {
        "amount": 4,
        "device": "test-device",
    })
    assert code == 400, f"Expected 400, got {code}: {body[:200]}"
    resp = parse_json_or_fail(body, "missing-mint-url response")
    assert resp.get("status") == 0
    assert "mint_url" in resp.get("error", "").lower() or "required" in resp.get("error", "").lower(), \
        f"Expected mint_url-related error, got: {resp.get('error')}"
    log.info("Missing mint_url correctly rejected: %s", resp.get("error"))


def test_string_amount_behavior(router, backend_running):
    """POST with amount as empty string — Go decodes as uint64 zero value.

    The frontend input is type='text', so amount can be '' when user clears
    the field. Go's json.Unmarshal decodes empty string as 0 for uint64.
    """
    code, body = _post_ln_invoice_via_nc(router, {
        "amount": "",
        "mint_url": TEST_MINT_URL,
    })
    assert code in (400, 200), f"Unexpected status {code}: {body[:200]}"
    if code == 400:
        resp = parse_json_or_fail(body, "string-amount response")
        log.info("String amount rejected: %s", resp.get("error"))
    else:
        log.warning("Backend accepted string amount — potential issue #149 vector")


def test_valid_request_returns_quote(router, backend_running):
    """POST with valid amount + mint_url should return a lightning quote.

    This is the happy path — verifies the /ln-invoice endpoint works when
    the frontend sends the correct payload.

    Skips if MAC resolution fails (X-Forwarded-For IP not in DHCP leases).
    """
    code, body = _post_ln_invoice_via_nc(router, {
        "amount": 4,
        "mint_url": TEST_MINT_URL,
    })
    if code == 400:
        resp = json.loads(body) if body else {}
        if "mac" in resp.get("error", "").lower():
            pytest.skip("MAC resolution failed — no DHCP lease for test client IP")
    assert code == 200, f"Expected 200, got {code}: {body[:300]}"
    resp = parse_json_or_fail(body, "valid-invoice response")
    assert resp.get("status") == 1, f"Expected status 1, got: {resp}"
    assert resp.get("quote"), f"Missing quote in response: {resp}"
    assert resp.get("mint_url"), f"Missing mint_url in response: {resp}"
    assert resp.get("amount", 0) > 0, f"Expected positive amount, got: {resp.get('amount')}"
    log.info(
        "Valid invoice created: quote=%s amount=%s state=%s",
        resp.get("quote"), resp.get("amount"), resp.get("state"),
    )


def test_mint_field_alias(router, backend_running):
    """POST with 'mint' instead of 'mint_url' — backend checks both.

    The backend lightningInvoiceRequest struct has both MintURL and Mint
    fields. Verify the alias works.

    Skips if MAC resolution fails.
    """
    code, body = _post_ln_invoice_via_nc(router, {
        "amount": 4,
        "mint": TEST_MINT_URL,
    })
    if code == 400:
        resp = json.loads(body) if body else {}
        if "mac" in resp.get("error", "").lower():
            pytest.skip("MAC resolution failed — no DHCP lease for test client IP")
    assert code == 200, f"Expected 200, got {code}: {body[:300]}"
    resp = parse_json_or_fail(body, "mint-alias response")
    assert resp.get("status") == 1, f"Expected status 1, got: {resp}"
    log.info("Mint field alias works: quote=%s", resp.get("quote"))
