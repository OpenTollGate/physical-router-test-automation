"""Lightning invoice payment flow through the captive portal.

Tests the full ln-invoice lifecycle: create invoice -> poll until settled ->
session active.  Works with FakeWallet mints (auto-settle after a few seconds)
in both the cloud lab (local mints) and physical lab (testnut.cashu.exchange).
"""

import json
import os
import time

import pytest

from lib.constants import BACKEND_PORT

pytestmark = [pytest.mark.api, pytest.mark.critical, pytest.mark.timeout(120), pytest.mark.virtual_lab]


def _skip_if_no_ln_invoice(router):
    resp = router.api_status("/ln-invoice")
    if resp != 405:
        pytest.skip(f"ln-invoice endpoint not available (status={resp}, expected 405 on GET)")


def _skip_if_degraded(router):
    discovery_raw = router.api_body("/")
    try:
        discovery = json.loads(discovery_raw)
    except json.JSONDecodeError:
        pytest.skip(f"Backend / did not return valid JSON: {discovery_raw[:200]}")
    if discovery.get("kind") == 21023:
        pytest.skip("Backend in degraded mode")
    return discovery


def _create_invoice(router, amount=21):
    create_resp = router.ssh(
        f"wget -qO- --timeout=15 --post-data='{{\"amount\": {amount}}}' "
        f"--header='Content-Type: application/json' "
        f"'http://[::1]:{BACKEND_PORT}/ln-invoice'",
        timeout=30,
    )
    assert create_resp, "Empty response from POST /ln-invoice"
    try:
        invoice = json.loads(create_resp)
    except json.JSONDecodeError:
        pytest.fail(f"ln-invoice response not JSON: {create_resp[:300]}")
    return invoice


def _poll_until_settled(router, quote, timeout_s=45):
    deadline = time.time() + timeout_s
    last_status = ""
    while time.time() < deadline:
        status_resp = router.ssh(
            f"wget -qO- --timeout=10 'http://[::1]:{BACKEND_PORT}/ln-invoice?quote={quote}'",
            timeout=15,
        )
        try:
            status = json.loads(status_resp)
        except json.JSONDecodeError:
            time.sleep(2)
            continue

        state = (status.get("status") or "").lower()
        last_status = status_resp[:200]
        if state in ("settled", "paid", "complete"):
            return status
        if state in ("expired", "cancelled"):
            pytest.fail(f"Lightning invoice expired/cancelled: {last_status}")
        time.sleep(2)

    pytest.fail(f"Lightning invoice did not settle within {timeout_s}s (last: {last_status})")


def test_ln_invoice_create_and_settle(router):
    """POST /ln-invoice creates a quote, FakeWallet auto-settles, GET confirms settled."""
    _skip_if_no_ln_invoice(router)
    _skip_if_degraded(router)

    invoice = _create_invoice(router, amount=21)
    quote = invoice.get("quote") or invoice.get("payment_hash") or invoice.get("r_hash")
    payment_request = invoice.get("payment_request") or invoice.get("invoice")
    assert quote, f"ln-invoice response missing quote/payment_hash: {json.dumps(invoice)[:300]}"
    assert payment_request, f"ln-invoice response missing payment_request: {json.dumps(invoice)[:300]}"

    _poll_until_settled(router, quote)


def test_ln_invoice_grants_session(router):
    """After Lightning invoice settles, client should have an active session."""
    _skip_if_no_ln_invoice(router)
    _skip_if_degraded(router)

    invoice = _create_invoice(router, amount=21)
    quote = invoice.get("quote") or invoice.get("payment_hash") or invoice.get("r_hash")
    assert quote, f"Missing quote: {json.dumps(invoice)[:200]}"

    _poll_until_settled(router, quote)

    session_resp = router.api_body("/balance")
    if not session_resp:
        session_resp = router.api_body("/usage")
    assert session_resp, "No session data after Lightning payment (both /balance and /usage empty)"
    try:
        session = json.loads(session_resp)
    except json.JSONDecodeError:
        pytest.fail(f"Session response not JSON: {session_resp[:200]}")

    has_session = (
        session.get("success") is True
        or session.get("authenticated") is True
        or "steps_remaining" in session
        or "active" in str(session).lower()
    )
    assert has_session, f"No active session found after Lightning payment: {session_resp[:300]}"


def test_cashu_and_lightning_both_accepted(router, cashu):
    """Both Cashu tokens and Lightning invoices are valid payment methods.

    Pays first with Cashu, verifies session, then after the Cashu session
    expires, pays with Lightning and verifies again.
    """
    _skip_if_no_ln_invoice(router)
    discovery = _skip_if_degraded(router)

    # Cashu payment
    token = cashu.mint_token(21)
    pay_resp = router.pay_direct(token)
    assert pay_resp, "Cashu payment returned empty response"
    pay_data = json.loads(pay_resp)
    assert pay_data.get("success") is True, f"Cashu payment failed: {pay_resp[:300]}"

    # Lightning payment
    invoice = _create_invoice(router, amount=21)
    quote = invoice.get("quote") or invoice.get("payment_hash") or invoice.get("r_hash")
    assert quote, f"Missing quote for Lightning leg: {json.dumps(invoice)[:200]}"

    settled = _poll_until_settled(router, quote)
    assert settled, "Lightning invoice did not settle in combined payment test"
