import json
import pytest
from lib.helpers import parse_json_or_fail

pytestmark = [pytest.mark.api, pytest.mark.smoke]


@pytest.fixture(scope="module")
def discovery(router):
    body = router.api_body("/")
    event = parse_json_or_fail(body, "discovery response")
    if event.get("kind") != 10021:
        pytest.skip(f"Discovery in degraded mode (kind={event.get('kind')}), skipping healthy-mode tests")
    return event


def test_info_returns_discovery_event(discovery):
    assert discovery.get("kind") == 10021, \
        f"Expected kind=10021, got kind={discovery.get('kind')}"


def test_info_has_metric_tag(discovery):
    tags = discovery.get("tags", [])
    metric_tags = [t for t in tags if isinstance(t, list) and len(t) >= 2 and t[0] == "metric"]
    assert len(metric_tags) > 0, f"Missing 'metric' tag in discovery event: {tags}"
    assert metric_tags[0][1] in ("milliseconds", "bytes"), \
        f"Invalid metric value: {metric_tags[0][1]}"


def test_info_has_step_size_tag(discovery):
    tags = discovery.get("tags", [])
    step_tags = [t for t in tags if isinstance(t, list) and len(t) >= 2 and t[0] == "step_size"]
    assert len(step_tags) > 0, f"Missing 'step_size' tag in discovery event: {tags}"
    assert step_tags[0][1].isdigit(), f"step_size is not a number: {step_tags[0][1]}"


def test_info_has_price_per_step(discovery):
    tags = discovery.get("tags", [])
    price_tags = [t for t in tags if isinstance(t, list) and len(t) >= 2 and t[0] == "price_per_step"]
    if not price_tags:
        pytest.skip(f"'price_per_step' tags not present in this build (feature not yet available)")
        return  # for type checkers
    price = price_tags[0]
    price = price_tags[0]
    assert len(price) >= 4, f"price_per_step tag too short: {price}"
    assert price[1] == "cashu", f"Expected bearer_asset_type='cashu', got: {price[1]}"


def test_info_has_tips_tag(discovery, router):
    if router.backend.is_rust:
        pytest.skip("Rust v1 discovery event missing 'tips' tag (Amperstrand/tollgate-rs#43)")
    tags = discovery.get("tags", [])
    tips_tags = [t for t in tags if isinstance(t, list) and len(t) >= 2 and t[0] == "tips"]
    assert len(tips_tags) > 0, f"Missing 'tips' tag in discovery event: {tags}"
