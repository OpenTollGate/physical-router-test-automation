import json
import pytest
from lib.helpers import parse_json_or_fail

pytestmark = [pytest.mark.api, pytest.mark.smoke]


@pytest.fixture(scope="module")
def discovery(router):
    body = router.api_body("/")
    return parse_json_or_fail(body, "discovery response")


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
    assert len(price_tags) > 0, f"Missing 'price_per_step' tags in discovery event: {tags}"
    price = price_tags[0]
    assert len(price) >= 4, f"price_per_step tag too short: {price}"
    assert price[1] == "cashu", f"Expected bearer_asset_type='cashu', got: {price[1]}"


def test_info_has_tips_tag(discovery):
    tags = discovery.get("tags", [])
    tips_tags = [t for t in tags if isinstance(t, list) and len(t) >= 2 and t[0] == "tips"]
    assert len(tips_tags) > 0, f"Missing 'tips' tag in discovery event: {tags}"
