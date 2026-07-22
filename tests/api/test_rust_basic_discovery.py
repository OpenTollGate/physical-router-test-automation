import pytest
import requests

pytestmark = [pytest.mark.rust_basic_only, pytest.mark.api, pytest.mark.smoke]


def test_discovery_returns_kind_10021(rust_basic_server):
    """S1: GET / returns 200 with a valid Nostr kind 10021 advertisement event."""
    resp = requests.get(f"{rust_basic_server['http_url']}/", timeout=5)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    data = resp.json()
    assert data["kind"] == 10021, f"Expected kind 10021, got {data.get('kind')}"
    assert len(data.get("pubkey", "")) == 64, "pubkey must be 64 hex chars"
    assert len(data.get("sig", "")) == 128, "sig must be 128 hex chars"
    tag_names = {t[0] for t in data.get("tags", []) if isinstance(t, list) and t}
    assert "metric" in tag_names, f"Missing metric tag: {data.get('tags')}"
    assert "step_size" in tag_names, "Missing step_size tag"
    assert "price_per_step" in tag_names, "Missing price_per_step tag"
    assert "tips" in tag_names, "Missing tips tag"


def test_discovery_cors_header(rust_basic_server):
    """CORS: GET / sets Access-Control-Allow-Origin: *."""
    resp = requests.get(f"{rust_basic_server['http_url']}/", timeout=5)
    assert resp.status_code == 200
    aco = resp.headers.get("access-control-allow-origin", "")
    assert aco == "*", f"Expected CORS *, got {aco!r}"
