import json
import pytest


@pytest.mark.board_c
@pytest.mark.board_a
class TestApiEndpoints:
    def test_debug_endpoint(self, board_connected, http):
        data = http.get_json(f"{board_connected.api_url}/debug")
        assert data is not None, "/debug returned no JSON"
        assert data.get("portal_running") is True, f"portal_running={data.get('portal_running')}"
        assert data.get("start_services_called") is True
        assert data.get("sta_got_ip") is True
        assert data.get("ap_started") is True
        assert data.get("free_heap", 0) > 0

    def test_discovery_endpoint(self, board_connected, http):
        data = http.get_json(f"{board_connected.api_url}/")
        assert data is not None, "Discovery endpoint returned no JSON"
        assert data.get("kind") == 10021, f"Expected kind=10021, got {data.get('kind')}"
        tags = data.get("tags", [])
        price_tags = [t for t in tags if t[0] == "price_per_step"]
        assert len(price_tags) > 0, "No price_per_step tag found"
        assert price_tags[0][1] == "cashu"
        assert int(price_tags[0][2]) > 0

    def test_mints_endpoint(self, board_connected, http):
        data = http.get_json(f"{board_connected.api_url}/mints")
        assert data is not None, "/mints returned no JSON"
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        assert len(data) > 0, "No mints configured"
        for mint in data:
            assert "url" in mint, f"Mint missing 'url': {mint}"
            assert "reachable" in mint, f"Mint missing 'reachable': {mint}"

    def test_wallet_endpoint(self, board_connected, http):
        data = http.get_json(f"{board_connected.api_url}/wallet")
        assert data is not None, "/wallet returned no JSON"
        assert "balance" in data, f"Wallet missing 'balance': {data}"
        assert "proof_count" in data, f"Wallet missing 'proof_count': {data}"

    def test_whoami_endpoint(self, board_connected, http):
        body = http.get(f"{board_connected.api_url}/whoami")
        assert body is not None, "/whoami returned no response"
        assert "ip=" in body, f"Expected 'ip=...', got: {body}"
        assert "mac=" in body, f"Expected 'mac=...', got: {body}"

    def test_usage_endpoint_no_session(self, board_connected, http):
        http.get(f"{board_connected.portal_url}/reset_authentication")
        import time
        time.sleep(1)
        body = http.get(f"{board_connected.api_url}/usage")
        assert body is not None, "/usage returned no response"
        assert body.strip() == "-1/-1", f"Expected '-1/-1' before payment, got: {body}"

    def test_invalid_token_returns_error(self, board_connected, http):
        rc, body = http.post(f"{board_connected.api_url}/", "garbage_not_a_token")
        resp = json.loads(body) if body else {}
        assert resp.get("kind") == 21023, f"Expected error kind 21023, got: {body[:200]}"
        assert rc == 0

    def test_captive_detection_uris(self, board_connected, http):
        uris = [
            "/generate_204",
            "/hotspot-detect.html",
            "/canonical.html",
            "/success.txt",
        ]
        for uri in uris:
            status = http.get_status(f"{board_connected.portal_url}{uri}")
            assert status == 200, f"{uri} returned {status}, expected 200"

    def test_catchall_returns_portal(self, board_connected, http):
        body = None
        for attempt in range(3):
            body = http.get(f"{board_connected.portal_url}/some-random-page")
            if body and "TollGate" in body:
                break
            import time
            time.sleep(2)
        assert body is not None, "Catch-all returned no response after 3 attempts"
        assert "TollGate" in body, "Catch-all should return portal HTML (after redirect)"
