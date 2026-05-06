import json
import pytest
from lib.helpers import parse_json_or_fail

pytestmark = [pytest.mark.api, pytest.mark.extended]


def test_session_response_structure(router):
    resp = router.api_body("/balance")
    data = parse_json_or_fail(resp, "balance response")
    assert isinstance(data, dict), f"Balance response is not a dict: {type(data)}"


def test_session_has_remaining_or_error(router):
    resp = router.api_body("/balance")
    data = parse_json_or_fail(resp, "balance response")
    assert "remaining" in data or "allotment" in data or "error" in data or "raw" in data or data.get("kind") == 10021, \
        f"Balance response missing expected fields: {resp[:200]}"


def test_session_with_client_ip(router):
    resp = router.backend_curl_xff(router.backend_url("/balance"), router.phone_ip)
    data = parse_json_or_fail(resp, "balance response with X-Forwarded-For")
    assert isinstance(data, dict), f"Session response is not a dict: {type(data)}"
