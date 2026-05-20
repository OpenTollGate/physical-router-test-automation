import json
import time
import pytest


@pytest.mark.board_c
@pytest.mark.board_a
class TestMultiMint:
    def test_discovery_has_pricing(self, board_connected, http):
        data = http.get_json(f"{board_connected.api_url}/")
        assert data is not None
        assert data["kind"] == 10021
        tags = data.get("tags", [])
        price_tags = [t for t in tags if t[0] == "price_per_step"]
        assert len(price_tags) >= 1
        pt = price_tags[0]
        assert pt[1] == "cashu"
        assert int(pt[2]) > 0

    def test_mint_listed(self, board_connected, http):
        data = http.get_json(f"{board_connected.api_url}/mints")
        assert data is not None
        assert isinstance(data, list)
        assert any("testnut" in m["url"] or "orangesync" in m["url"] for m in data)

    def test_mint_health_after_probe(self, board_connected, http):
        print("  Waiting 10s for mint health probes...")
        time.sleep(10)
        data = http.get_json(f"{board_connected.api_url}/mints")
        assert data is not None
        for mint in data:
            assert "reachable" in mint

    def test_wallet_balance_type(self, board_connected, http):
        data = http.get_json(f"{board_connected.api_url}/wallet")
        assert data is not None
        assert isinstance(data["balance"], (int, float))
        assert isinstance(data["proof_count"], int)
        assert isinstance(data.get("proofs", []), list)
