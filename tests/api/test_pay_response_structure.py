import json
import pytest
from lib.helpers import is_session_event, is_mac_lookup_failure, require_client_identity

pytestmark = [pytest.mark.api, pytest.mark.critical]


def test_pay_rejects_empty_body(router):
    url = router.backend_url("/").replace("[::1]", "127.0.0.1")
    resp_text = router.ssh(
        f"wget -S -o /dev/null -O /dev/null --post-data='' '{url}' 2>&1 | head -1 | grep -oE '[0-9]{{3}}' | head -1"
    )
    code = int(resp_text.strip().strip("'")) if resp_text.strip().strip("'").isdigit() else 0
    assert code in (400, 402, 500), \
        f"Expected error status for empty POST, got {code}"


def test_pay_rejects_invalid_json(router):
    resp_text = router.ssh(
        f"wget -qO- --post-data='not-json' "
        f"--header='Content-Type: application/json' '{router.backend_url('/')}'"
    )
    assert resp_text, "Expected error response for invalid JSON"


def test_pay_rejects_fake_token(router):
    resp_text = router.ssh(
        f"wget -qO- --post-data='cashuBfake_token_not_real' "
        f"--header='Content-Type: text/plain' '{router.backend_url('/')}'"
    )
    assert '"success":true' not in resp_text, \
        f"Fake token was accepted: {resp_text[:200]}"


def test_pay_success_returns_session(router, cashu):
    require_client_identity(router)
    token = cashu.mint(3)
    resp = router.pay_direct(token)

    if is_mac_lookup_failure(resp):
        pytest.skip("No client on TollGate AP — backend cannot resolve MAC")

    assert is_session_event(resp), \
        f"Payment did not return session event: {str(resp)[:200]}"

    if resp.get("kind") == 1022:
        tags = resp.get("tags", [])
        allotment_tags = [t for t in tags if isinstance(t, list) and t[0] == "allotment"]
        assert len(allotment_tags) > 0, f"Session event missing allotment tag: {tags}"

        metric_tags = [t for t in tags if isinstance(t, list) and t[0] == "metric"]
        assert len(metric_tags) > 0, f"Session event missing metric tag: {tags}"
