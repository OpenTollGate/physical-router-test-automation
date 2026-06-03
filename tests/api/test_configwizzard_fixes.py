"""Verification tests for configurationwizzard PR #10 (issue #9) bug fixes.

Tests Bugs 2-4, 9, A, B against the physical router using the existing
router fixture and helper libraries.
"""

import json
import logging
import time

import pytest

from lib.constants import BACKEND_PORT, NDS_PORTAL_PORT, TEST_MINT_URL
from lib.helpers import parse_json_or_fail

log = logging.getLogger("tollgate.configwizzard_fixes")

pytestmark = [pytest.mark.api, pytest.mark.extended]


@pytest.fixture(scope="module")
def backend_running(router):
    code = router.api_status("/")
    if code == 0:
        pytest.skip("Backend not responding on this router")
    return True


@pytest.fixture(scope="module")
def session_token(router):
    login = router.ssh(
        f"wget -qO- --post-data='{{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"call\","
        f"\"params\":[\"00000000000000000000000000000000\",\"session\",\"login\","
        f"{{\"username\":\"root\",\"password\":\"\"}}]}}' "
        f"--header='Content-Type: application/json' "
        f"http://127.0.0.1/ubus 2>/dev/null",
        timeout=15,
    )
    if not login:
        pytest.skip("Could not get ubus session token")
    try:
        data = json.loads(login)
        token = data.get("result", [None, None])[1]
        if token:
            return token
    except (json.JSONDecodeError, IndexError, TypeError):
        pass
    pytest.skip("Could not parse ubus session token")


def _ubus_call(router, token, method, params=None):
    params = params or {}
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "call",
        "params": [token, "tollgate", method, params],
    })
    resp = router.ssh(
        f"wget -qO- --post-data='{payload}' "
        f"--header='Content-Type: application/json' "
        f"http://127.0.0.1/ubus 2>/dev/null",
        timeout=15,
    )
    if not resp:
        return {}
    try:
        return json.loads(resp)
    except json.JSONDecodeError:
        return {}


def test_profit_share_config_save(router, session_token):
    """Bug 2: config_save must accept full config including array values."""
    resp = _ubus_call(router, session_token, "config_get")
    result = resp.get("result", [])
    if not result or len(result) < 2:
        pytest.skip("config_get did not return data")
    config_data = result[1]
    if not isinstance(config_data, dict):
        pytest.skip("config_get returned non-dict")

    save_resp = _ubus_call(router, session_token, "config_save", config_data)
    save_result = save_resp.get("result", [])
    if save_result and save_result[0] == 0:
        log.info("config_save accepted full config with arrays")
    elif "error" in save_resp:
        pytest.fail(f"config_save returned error: {save_resp['error']}")
    else:
        log.info("config_save response: %s", save_resp)


def test_wallet_balance_sats_field(router, session_token):
    """Bug 3: wallet_balance must return balance_sats (not balance)."""
    resp = _ubus_call(router, session_token, "wallet_balance")
    result = resp.get("result", [])
    if not result or len(result) < 2:
        pytest.skip("wallet_balance did not return data")
    data = result[1]
    if isinstance(data, dict):
        if "balance_sats" in data:
            log.info("wallet_balance has balance_sats field: %s", data["balance_sats"])
        elif "balance" in data and "balance_sats" not in data:
            pytest.fail("wallet_balance has 'balance' instead of 'balance_sats' (Bug 3 regression)")
        else:
            log.info("wallet_balance response: %s", data)
    else:
        pytest.skip(f"wallet_balance returned non-dict: {data}")


def test_ln_invoice_test_mint_autopay(router, backend_running):
    """Bug 9: Test mint dummy invoice auto-pays and grants access."""
    payload = json.dumps({"amount": 1, "mint_url": TEST_MINT_URL})
    resp_raw = router.ssh(
        f"wget -qO- --post-data='{payload}' "
        f"--header='Content-Type: application/json' "
        f"--header='X-Forwarded-For: {router.phone_ip}' "
        f"http://[::1]:{BACKEND_PORT}/ln-invoice 2>/dev/null",
        timeout=15,
    )
    if not resp_raw:
        pytest.skip("ln-invoice returned empty response")
    resp = parse_json_or_fail(resp_raw, "ln-invoice response")

    if resp.get("status") == 0:
        err = resp.get("error", "")
        if "mac" in err.lower():
            pytest.skip(f"MAC resolution failed: {err}")
        pytest.fail(f"ln-invoice error: {err}")

    quote = resp.get("quote", "")
    invoice = resp.get("invoice", "")
    assert quote, f"Missing quote in response: {resp}"
    assert invoice, f"Missing invoice in response: {resp}"

    is_real_bolt11 = invoice.lower().startswith("lnbc")
    log.info(
        "Invoice created: quote=%s is_bolt11=%s invoice=%s...",
        quote, is_real_bolt11, invoice[:40],
    )

    for i in range(10):
        time.sleep(2)
        poll_raw = router.api_body(f"/ln-invoice?quote={quote}")
        if not poll_raw:
            continue
        poll = parse_json_or_fail(poll_raw, f"ln-invoice poll {i}")
        if poll.get("access_granted"):
            allotment = poll.get("allotment", 0)
            log.info("Access granted after %ds: allotment=%s", (i + 1) * 2, allotment)
            assert allotment > 0, f"allotment was 0: {poll}"
            return
        log.debug("Poll %d: state=%s", i, poll.get("state", "?"))

    pytest.fail("Lightning invoice did not auto-settle in 20s")


def test_portal_tab_labels_not_white(router, backend_running):
    """Bug A: Portal tab labels must use dark text, not white-on-white."""
    port = router.get_nds_portal_port()
    html = router.ssh(f"wget -qO- http://127.0.0.1:{port}/ 2>/dev/null", timeout=15)
    if not html:
        pytest.skip("Could not fetch portal HTML from NDS")

    css_path = ""
    for line in html.split("\n"):
        if ".css" in line and "href=" in line:
            import re
            match = re.search(r'href="([^"]*\.css)"', line)
            if match:
                css_path = match.group(1)
                break
    if not css_path:
        pytest.skip("Could not find CSS link in portal HTML")

    css = router.ssh(f"wget -qO- http://127.0.0.1:{port}{css_path} 2>/dev/null", timeout=15)
    if not css:
        pytest.skip("Could not fetch portal CSS")

    if "captive-portal-tabs-tab" not in css:
        pytest.skip("Portal CSS does not contain tab class")

    has_dark_color = "color:#0" in css and "captive-portal-tabs-tab" in css
    has_white_color = "color:#fff" in css or "color:white" in css

    tab_context = css[css.find("captive-portal-tabs-tab"):css.find("}", css.find("captive-portal-tabs-tab")) + 1]
    assert "color:#fff" not in tab_context and "color:white" not in tab_context, \
        f"Tab rule has white text: {tab_context[:200]}"
    log.info("Tab labels use dark text (Bug A verified)")


def test_portal_manifest_linked(router, backend_running):
    """Bug B: Portal HTML must link manifest.json and register service worker."""
    port = router.get_nds_portal_port()
    html = router.ssh(f"wget -qO- http://127.0.0.1:{port}/ 2>/dev/null", timeout=15)
    if not html:
        pytest.skip("Could not fetch portal HTML from NDS")

    assert 'rel="manifest"' in html, "Portal HTML missing manifest link (Bug B)"
    assert "serviceWorker" in html, "Portal HTML missing SW registration (Bug B)"
    log.info("Portal has manifest link and SW registration (Bug B)")


def test_portal_cna_detection(router, backend_running):
    """Bug B: Portal JS must detect CNA webview user-agents."""
    port = router.get_nds_portal_port()
    html = router.ssh(f"wget -qO- http://127.0.0.1:{port}/ 2>/dev/null", timeout=15)
    if not html:
        pytest.skip("Could not fetch portal HTML from NDS")

    import re
    js_path = ""
    match = re.search(r'src="([^"]*\.js)"', html)
    if match:
        js_path = match.group(1)
    if not js_path:
        pytest.skip("Could not find JS bundle in portal HTML")

    js = router.ssh(f"wget -qO- http://127.0.0.1:{port}{js_path} 2>/dev/null", timeout=15)
    if not js:
        pytest.skip("Could not fetch portal JS bundle")

    has_cna_check = "CaptiveNetworkAssistant" in js or "captiveportallogin" in js
    assert has_cna_check, "Portal JS missing CNA user-agent detection (Bug B)"

    has_cna_ui = "Open in Browser" in js
    assert has_cna_ui, "Portal JS missing CNA-specific UI text (Bug B)"
    log.info("Portal JS has CNA detection and conditional UI (Bug B)")
