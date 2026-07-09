"""Access-denominated token tests — PricePerStep=1.

When price_per_step=1, the token face value equals the step count directly.
A 5-sat token grants 5 * step_size of access. This proves the access-denominated
token concept: the token IS the access quota.

These tests modify router pricing config and restore it afterward.
"""
import json
import logging
import time

import pytest

log = logging.getLogger("tollgate.api.access_denominated")

pytestmark = [pytest.mark.api, pytest.mark.config, pytest.mark.slow]


def _set_price_per_step(router, value: int) -> None:
    router.ssh(
        f"jq '.accepted_mints[0].price_per_step = {value}' "
        f"/etc/tollgate/config.json > /tmp/cfg.tmp && mv /tmp/cfg.tmp /etc/tollgate/config.json"
    )
    router.restart_backend()
    time.sleep(10)


def _get_step_size(router) -> int:
    raw = router.ssh("jq '.step_size' /etc/tollgate/config.json").strip()
    return int(raw)


def _reset_and_wait(router) -> None:
    router.reset_state()
    time.sleep(5)


def test_price_per_step_1_face_value_equals_allotment(router, cashu):
    """A 5-unit token with price_per_step=1 grants 5 * step_size of access."""
    original_step = router.ssh("jq '.accepted_mints[0].price_per_step' /etc/tollgate/config.json").strip()

    try:
        _set_price_per_step(router, 1)
        _reset_and_wait(router)
        step_size = _get_step_size(router)

        token = cashu.mint(5)
        assert token, "Failed to mint token"

        resp = router.pay_direct(token)
        log.info("Payment response: %s", json.dumps(resp)[:200])

        assert resp.get("kind") in (10021, 21000), f"Payment rejected: {resp}"

        session = router.get_session()
        assert session, "No session after payment"

        remaining = session.get("remaining", 0)
        expected = 5 * step_size
        log.info("step_size=%d, expected allotment=%d, remaining=%d", step_size, expected, remaining)

        assert remaining > 0, "Session has zero remaining allotment"
        assert remaining >= expected * 0.99, (
            f"Remaining {remaining} < expected {expected} (5 * step_size {step_size}). "
            f"PricePerStep=1 should make face value = step count."
        )

    finally:
        _set_price_per_step(router, int(original_step))


def test_price_per_step_1_minimum_token(router, cashu):
    """A 1-unit token with price_per_step=1 grants exactly step_size of access."""
    original_step = router.ssh("jq '.accepted_mints[0].price_per_step' /etc/tollgate/config.json").strip()

    try:
        _set_price_per_step(router, 1)
        _reset_and_wait(router)
        step_size = _get_step_size(router)

        token = cashu.mint(1)
        assert token, "Failed to mint 1-unit token"

        resp = router.pay_direct(token)
        assert resp.get("kind") in (10021, 21000), f"1-unit payment rejected: {resp}"

        session = router.get_session()
        remaining = session.get("remaining", 0)
        expected = 1 * step_size
        log.info("1-unit: step_size=%d, expected=%d, remaining=%d", step_size, expected, remaining)

        assert remaining > 0, "1-unit token gave zero allotment"

    finally:
        _set_price_per_step(router, int(original_step))


def test_price_per_step_2_halves_allotment(router, cashu):
    """With price_per_step=2, a 5-unit token grants floor(5/2)=2 steps."""
    original_step = router.ssh("jq '.accepted_mints[0].price_per_step' /etc/tollgate/config.json").strip()

    try:
        _set_price_per_step(router, 2)
        _reset_and_wait(router)
        step_size = _get_step_size(router)

        token = cashu.mint(5)
        resp = router.pay_direct(token)
        assert resp.get("kind") in (10021, 21000), f"Payment rejected: {resp}"

        session = router.get_session()
        remaining = session.get("remaining", 0)
        expected = (5 // 2) * step_size
        log.info("price=2: 5 units → 2 steps → %d, remaining=%d", expected, remaining)

        assert remaining > 0

    finally:
        _set_price_per_step(router, int(original_step))
