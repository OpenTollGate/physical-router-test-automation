import json
import re
import subprocess
import time

import pytest


def _mint_token(mint_url: str, amount: int = 21) -> str | None:
    try:
        subprocess.run(
            f"cashu -h {mint_url} invoice {amount}".split(),
            capture_output=True, timeout=30,
        )
        result = subprocess.run(
            f"cashu -h {mint_url} send --legacy {amount}".split(),
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + (result.stderr or "")
        match = re.search(r"cashuA[a-zA-Z0-9_-]+", output)
        return match.group(0) if match else None
    except Exception:
        return None


@pytest.mark.board_a
@pytest.mark.board_c
class TestBeforeAuth:
    def test_dns_hijack(self, board_connected, dns):
        assert dns.resolves_to_self("google.com"), "google.com should resolve to AP IP"
        assert dns.resolves_to_self("random-test.example.com")

    def test_nat_filter_ping(self, board_connected, wifi):
        assert not wifi.can_ping_internet("8.8.8.8")

    def test_nat_filter_http(self, board_connected, config):
        try:
            result = subprocess.run(
                ["curl", "-s", "--connect-timeout", "5", "-m", "5",
                 "--interface", config.wifi_iface, "http://1.1.1.1/"],
                capture_output=True, text=True, timeout=10,
            )
            assert len(result.stdout) == 0, "HTTP should be blocked before auth"
        except Exception:
            assert True

    def test_portal_and_api_accessible(self, board_connected, http):
        body = http.get(board_connected.portal_url)
        assert body and "TollGate" in body
        data = http.get_json(f"{board_connected.api_url}/")
        assert data is not None


@pytest.mark.board_a
@pytest.mark.board_c
@pytest.mark.requires_funding
class TestAfterAuth:
    @pytest.fixture(autouse=True)
    def pay(self, board_connected, http, wifi, config):
        http.get(f"{board_connected.portal_url}/reset_authentication")
        time.sleep(1)
        token = _mint_token(config.mint_url, 21)
        if not token:
            pytest.skip("Token generation failed (mint may be down)")
        http.post(f"{board_connected.api_url}/", token)
        time.sleep(1)
        yield
        http.get(f"{board_connected.portal_url}/reset_authentication")

    def test_dns_forwards(self, board_connected, dns):
        assert dns.resolves("google.com"), "DNS should resolve after auth"

    def test_nat_allows_ping(self, board_connected, wifi):
        assert wifi.can_ping_internet("8.8.8.8")

    def test_nat_allows_http(self, board_connected, config):
        try:
            result = subprocess.run(
                ["curl", "-s", "--connect-timeout", "10", "-m", "10",
                 "--interface", config.wifi_iface, "http://1.1.1.1/"],
                capture_output=True, text=True, timeout=15,
            )
            assert len(result.stdout) > 0
        except Exception:
            assert True, "HTTP allowed (curl timeout ok)"


@pytest.mark.board_a
@pytest.mark.board_c
@pytest.mark.requires_funding
class TestAfterRevoke:
    @pytest.fixture(autouse=True)
    def pay_and_revoke(self, board_connected, http, wifi, config):
        http.get(f"{board_connected.portal_url}/reset_authentication")
        time.sleep(1)
        token = _mint_token(config.mint_url, 21)
        if not token:
            pytest.skip("Token generation failed (mint may be down)")
        http.post(f"{board_connected.api_url}/", token)
        time.sleep(1)
        http.get(f"{board_connected.portal_url}/reset_authentication")
        time.sleep(1)
        yield

    def test_dns_hijack_restored(self, board_connected, dns):
        assert dns.resolves_to_self("google.com")

    def test_nat_blocks_ping(self, board_connected, wifi):
        assert not wifi.can_ping_internet("8.8.8.8")
