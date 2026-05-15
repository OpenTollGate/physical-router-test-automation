import re

import pytest

pytestmark = [pytest.mark.api, pytest.mark.smoke]


def test_root_endpoint(router):
    code = router.api_status("/")
    assert code == 200, f"Expected 200, got {code}"
    body = router.api_body("/")
    if '"kind":21023' in body:
        pytest.skip("Discovery in degraded mode, skipping kind:10021 check")
    assert '"kind":10021' in body, f"Response missing kind:10021: {body[:200]}"


def test_pay_endpoint(router):
    code = router.api_status("/pay")
    body = router.api_body("/pay")
    assert code in (200, 402), f"Expected 200 or 402, got {code}"
    if code == 402:
        assert '"payment_request"' in body, "402 but missing payment_request"
        assert '"qr_image"' in body, "402 but missing qr_image"
    else:
        assert '"kind"' in body, f"200 but missing kind field: {body[:200]}"


def test_whoami_endpoint(router):
    code = router.api_status("/whoami")
    body = router.api_body("/whoami")
    assert code in (200, 500), f"Expected 200 or 500, got {code}"
    if code == 200:
        assert body, "200 but empty body"
        first_line = body.strip().split("\n")[0]
        match = re.match(r'^(\w+)=(.*)$', first_line)
        assert match, f"Response not in type=value format: {body[:100]}"
        id_type, id_value = match.group(1), match.group(2).strip()
        assert id_type == "mac", f"Expected type 'mac', got '{id_type}'"
        if id_value:
            assert re.match(r'^([0-9a-f]{2}:){5}[0-9a-f]{2}$', id_value, re.IGNORECASE), \
                f"Invalid MAC format: {id_value}"
    else:
        assert "error" in body.lower() or not body, \
            f"500 without error message: {body[:200]}"


def test_balance_endpoint(router):
    code = router.api_status("/balance")
    body = router.api_body("/balance")
    assert code == 200, f"Expected 200, got {code}"
    assert '"remaining"' in body or '"allotment"' in body or '"kind":10021' in body, \
        f"Balance response missing expected fields: {body[:200]}"
