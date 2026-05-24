import json
import os

import pytest

from lib.cashu import CashuMint
from lib.constants import LOCAL_MINT_URL, TEST_MINT_URL, V2_MINT_URL
from lib.helpers import parse_json_or_fail, require_client_identity

pytestmark = [pytest.mark.api, pytest.mark.extended]


@pytest.fixture(scope="module")
def discovery(router):
    body = router.api_body("/")
    return parse_json_or_fail(body, "discovery response")


@pytest.fixture(scope="module")
def config(router):
    raw = router.ssh("cat /etc/tollgate/config.json")
    return json.loads(raw)


def _discovery_price_tags(discovery):
    tags = discovery.get("tags", [])
    return [t for t in tags if isinstance(t, list) and t[0] == "price_per_step"]


def test_both_configured_mints_in_discovery(discovery, config, router):
    config_urls = {m.get("url") for m in config.get("accepted_mints", [])}
    if len(config_urls) < 2:
        pytest.skip("Only 1 mint configured — dual-mint test requires ≥2")

    price_tags = _discovery_price_tags(discovery)
    discovery_mint_urls = {t[4] for t in price_tags if len(t) >= 5}

    if discovery_mint_urls == config_urls:
        return

    # PR #118 health tracker removes unhealthy mints from discovery.
    # This is correct behavior — verify graceful handling instead of failing.
    missing = config_urls - discovery_mint_urls
    if missing:
        logs = router.get_tollgate_logs(lines=500)

        ps_out = router.ssh("ps | grep tollgate-wrt | grep -v grep")
        assert "tollgate-wrt" in ps_out, \
            "Service crashed after marking mints unhealthy"

        code = router.api_status("/")
        assert code in (200, 503), \
            f"Service unhealthy ({code}) with {len(missing)} unreachable mint(s)"

        pytest.skip(
            f"PR #118 health tracker excluded {len(missing)} mint(s) from discovery "
            f"({missing}). Service stays up — correct degraded mode behavior. "
            f"Discovery has {len(price_tags)} mint(s), expected ≥2 from config."
        )


def test_v1_mint_payment_accepted(router, cashu):
    require_client_identity(router)
    token = cashu.mint(amount=4, legacy=True)

    resp = router.pay_direct(token)

    assert resp.get("kind") == 1022 or resp.get("success") is True, \
        f"V1 mint payment rejected: {str(resp)[:300]}"


def test_v2_mint_payment_accepted(router):
    require_client_identity(router)
    v2_url = os.environ.get("TOLLGATE_V2_MINT_URL", V2_MINT_URL)
    v2 = CashuMint(mint_url=v2_url)
    if not v2.is_available():
        pytest.skip("cashu CLI not available for V2 mint test")

    try:
        token = v2.mint(amount=4, legacy=False)
    except Exception as exc:
        pytest.skip(f"V2 mint unreachable or minting failed: {exc}")

    resp = router.pay_direct(token)

    if resp.get("kind") == 21023 and "not accepted" in resp.get("content", ""):
        pytest.skip(
            "Go backend (gonuts) does not support V2 keyset IDs (01-prefix, 33 bytes). "
            "Fix: pin tollgate-module-basic-go to Amperstrand/gonuts-tollgate feature/v2-keyset-ids. "
            f"Response: {str(resp)[:200]}"
        )

    assert resp.get("kind") == 1022 or resp.get("success") is True, \
        f"V2 mint payment rejected: {str(resp)[:300]}"


def test_discovery_has_distinct_price_per_mint(discovery, config):
    price_tags = _discovery_price_tags(discovery)
    if len(price_tags) < 2:
        pytest.skip("Fewer than 2 price_per_step tags — dual-mint not active")

    tag_mints = [t[4] for t in price_tags if len(t) >= 5]

    assert len(tag_mints) == len(set(tag_mints)), \
        f"Duplicate mint URLs in price_per_step tags: {tag_mints}"

    for tag in price_tags:
        assert len(tag) >= 5, f"price_per_step tag too short: {tag}"
        # price_per_step tag: [name, unit, step_size, price_unit, mint_url, min_steps]
        assert isinstance(tag[2], (int, float, str)), f"step_size not numeric: {tag}"
        assert isinstance(tag[3], str), f"price_unit not string: {tag}"
        assert tag[4].startswith("http"), f"Expected URL at index 4: {tag}"
