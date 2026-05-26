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
class TestResetAuth:
    def test_reset_clears_state(self, board_connected, http):
        http.get(f"{board_connected.portal_url}/reset_authentication")
        time.sleep(1)
        body = http.get(f"{board_connected.api_url}/usage")
        assert body is not None
        assert "-1/-1" in body, f"Expected -1/-1: {body}"

    def test_no_internet_before_payment(self, board_connected, http, wifi):
        http.get(f"{board_connected.portal_url}/reset_authentication")
        time.sleep(1)
        assert not wifi.can_ping_internet()

    def test_payment_accepted(self, board_connected, http, wifi, config):
        http.get(f"{board_connected.portal_url}/reset_authentication")
        time.sleep(1)
        token = _mint_token(config.mint_url, 21)
        assert token is not None, "Token generation failed (mint may be down)"
        rc, body = http.post(f"{board_connected.api_url}/", token)
        resp = json.loads(body) if body else {}
        assert resp.get("kind") == 1022, f"Expected kind=1022: {body[:200]}"

    def test_internet_allowed_after_payment(self, board_connected, http, wifi, config):
        token = _mint_token(config.mint_url, 21)
        if not token:
            pytest.skip("Token generation failed")
        http.post(f"{board_connected.api_url}/", token)
        time.sleep(1)
        assert wifi.can_ping_internet()

    def test_reset_kills_session(self, board_connected, http, wifi):
        http.get(f"{board_connected.portal_url}/reset_authentication")
        time.sleep(1)
        body = http.get(f"{board_connected.api_url}/usage")
        assert body and "-1/-1" in body
        assert not wifi.can_ping_internet()

    def test_second_payment(self, board_connected, http, wifi, config):
        token = _mint_token(config.mint_url, 21)
        if not token:
            pytest.skip("Token generation failed")
        rc, body = http.post(f"{board_connected.api_url}/", token)
        resp = json.loads(body) if body else {}
        assert resp.get("kind") == 1022
        time.sleep(1)
        assert wifi.can_ping_internet()
        http.get(f"{board_connected.portal_url}/reset_authentication")
