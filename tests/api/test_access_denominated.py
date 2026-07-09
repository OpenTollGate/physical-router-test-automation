"""Access-denominated token tests — PricePerStep=1."""
import json
import logging
import time

import pytest

log = logging.getLogger("tollgate.api.access_denominated")
pytestmark = [pytest.mark.api, pytest.mark.config, pytest.mark.slow]

SUCCESS_KINDS = (10021, 21000, 1022)


def _set_price_per_step(router, value):
    router.ssh(f"jq '.accepted_mints[0].price_per_step = {value}' /etc/tollgate/config.json > /tmp/cfg.tmp && mv /tmp/cfg.tmp /etc/tollgate/config.json")
    router.restart_backend()
    time.sleep(10)


def _reset_and_wait(router):
    router.reset_state()
    time.sleep(5)


def _extract_allotment(resp):
    for tag in resp.get("tags", []):
        if len(tag) >= 2 and tag[0] == "allotment":
            return int(tag[1])
    return 0


def test_price_per_step_1_face_value_equals_allotment(router, cashu):
    """5-unit token with price_per_step=1 grants 5 * step_size."""
    original = router.ssh("jq '.accepted_mints[0].price_per_step' /etc/tollgate/config.json").strip()
    try:
        _set_price_per_step(router, 1)
        _reset_and_wait(router)
        step_size = int(router.ssh("jq '.step_size' /etc/tollgate/config.json").strip())
        token = cashu.mint(5)
        assert token, "mint failed"
        resp = router.pay_direct(token)
        log.info("resp: %s", json.dumps(resp)[:300])
        assert resp.get("kind") in SUCCESS_KINDS, f"Payment rejected: {resp}"
        allotment = _extract_allotment(resp)
        expected = 5 * step_size
        log.info("step_size=%d expected=%d allotment=%d", step_size, expected, allotment)
        assert allotment == expected, f"Allotment {allotment} != {expected}"
    finally:
        _set_price_per_step(router, int(original))


def test_price_per_step_1_minimum_token(router, cashu):
    """1-unit token with price_per_step=1 grants step_size."""
    original = router.ssh("jq '.accepted_mints[0].price_per_step' /etc/tollgate/config.json").strip()
    try:
        _set_price_per_step(router, 1)
        _reset_and_wait(router)
        step_size = int(router.ssh("jq '.step_size' /etc/tollgate/config.json").strip())
        token = cashu.mint(1)
        assert token, "mint failed"
        resp = router.pay_direct(token)
        assert resp.get("kind") in SUCCESS_KINDS, f"Payment rejected: {resp}"
        allotment = _extract_allotment(resp)
        expected = 1 * step_size
        log.info("1-unit: step_size=%d expected=%d allotment=%d", step_size, expected, allotment)
        assert allotment == expected, f"Allotment {allotment} != {expected}"
    finally:
        _set_price_per_step(router, int(original))


def test_price_per_step_2_halves_allotment(router, cashu):
    """5-unit token with price_per_step=2 grants floor(5/2)=2 steps."""
    original = router.ssh("jq '.accepted_mints[0].price_per_step' /etc/tollgate/config.json").strip()
    try:
        _set_price_per_step(router, 2)
        _reset_and_wait(router)
        step_size = int(router.ssh("jq '.step_size' /etc/tollgate/config.json").strip())
        token = cashu.mint(5)
        resp = router.pay_direct(token)
        assert resp.get("kind") in SUCCESS_KINDS, f"Payment rejected: {resp}"
        allotment = _extract_allotment(resp)
        expected = (5 // 2) * step_size
        log.info("price=2: step_size=%d expected=%d allotment=%d", step_size, expected, allotment)
        assert allotment == expected, f"Allotment {allotment} != {expected}"
    finally:
        _set_price_per_step(router, int(original))
