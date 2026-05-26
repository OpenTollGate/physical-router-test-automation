import json

import pytest


@pytest.mark.board_a
@pytest.mark.board_c
class TestMarket:
    def test_market_returns_json(self, board_connected, http):
        data = http.get_json(f"{board_connected.api_url}/market")
        assert data is not None, "/market returned no JSON"
        assert isinstance(data.get("count"), int)
        assert isinstance(data.get("entries"), list)

    def test_market_entry_structure(self, board_connected, http):
        data = http.get_json(f"{board_connected.api_url}/market")
        assert data is not None
        entries = data.get("entries", [])
        if not entries:
            pytest.skip("No market entries (scan may not have run yet)")
        e = entries[0]
        assert isinstance(e.get("bssid"), str)
        assert isinstance(e.get("ssid"), str)
        assert isinstance(e.get("rssi"), (int, float))
        assert isinstance(e.get("price_per_step"), (int, float))
        assert isinstance(e.get("step_size"), (int, float))
        assert isinstance(e.get("metric"), str)

    def test_market_empty_when_no_discovery(self, board_connected, http):
        data = http.get_json(f"{board_connected.api_url}/market")
        assert data is not None
        if data.get("count", 0) == 0:
            assert len(data["entries"]) == 0
        else:
            pytest.skip(f"{data['count']} nearby TollGate(s) discovered")
