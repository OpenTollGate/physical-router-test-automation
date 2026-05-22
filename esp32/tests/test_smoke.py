import pytest

pytestmark = pytest.mark.smoke


@pytest.mark.board_c
@pytest.mark.board_a
class TestSmoke:
    def test_ssid_visible(self, board_config, wifi):
        ssids = wifi.scan_ssids()
        assert board_config.ssid in ssids, (
            f"SSID '{board_config.ssid}' not found in scan: {ssids[:10]}"
        )

    def test_portal_html_loads(self, board_connected, http):
        body = http.get(board_connected.portal_url)
        assert body is not None, "Portal returned no response"
        assert "TollGate" in body, "Portal HTML missing 'TollGate'"

    def test_grant_access(self, board_connected, http):
        body = http.get(f"{board_connected.portal_url}/grant_access")
        assert body is not None, "grant_access returned no response"
        assert "granted" in body, f"Expected 'granted', got: {body[:200]}"

    def test_internet_after_grant(self, board_connected, wifi):
        import time
        time.sleep(2)
        assert wifi.can_ping_internet("1.1.1.1"), "Internet not reachable after grant"

    def test_reset_auth(self, board_connected, http):
        body = http.get(f"{board_connected.portal_url}/reset_authentication")
        assert body is not None, "reset_authentication returned no response"
        assert "reset" in body, f"Expected 'reset', got: {body[:200]}"

    def test_internet_blocked_after_reset(self, board_connected, wifi):
        import time
        time.sleep(2)
        assert not wifi.can_ping_internet("1.1.1.1"), "Internet still reachable after reset"
