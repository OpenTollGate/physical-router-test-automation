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
@pytest.mark.requires_funding
class TestSessionExpiry:
    def test_session_expires_and_renews(self, board_connected, http, wifi, config):
        http.get(f"{board_connected.portal_url}/reset_authentication")
        time.sleep(1)
        assert not wifi.can_ping_internet()

        token = _mint_token(config.mint_url, 21)
        assert token is not None, "Token generation failed"
        rc, body = http.post(f"{board_connected.api_url}/", token)
        resp = json.loads(body) if body else {}
        assert resp.get("kind") == 1022
        time.sleep(1)

        usage_body = http.get(f"{board_connected.api_url}/usage")
        assert usage_body and "-1/-1" not in usage_body
        assert wifi.can_ping_internet()

        for remaining in range(65, 0, -5):
            time.sleep(min(5, remaining))

        usage_body = http.get(f"{board_connected.api_url}/usage")
        assert usage_body and "-1/-1" in usage_body
        assert not wifi.can_ping_internet()

        token2 = _mint_token(config.mint_url, 21)
        if token2:
            rc, body = http.post(f"{board_connected.api_url}/", token2)
            resp = json.loads(body) if body else {}
            assert resp.get("kind") == 1022
            time.sleep(1)
            assert wifi.can_ping_internet()

        http.get(f"{board_connected.portal_url}/reset_authentication")
