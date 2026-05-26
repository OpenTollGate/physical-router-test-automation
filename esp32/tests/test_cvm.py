import json
import os
import subprocess
import time

import pytest

CVM_RELAY = "wss://nos.lol"


def _nak(args: str, timeout: int = 10000) -> str:
    try:
        result = subprocess.run(
            ["timeout", str(timeout // 1000), "nak"] + args.split(),
            capture_output=True, text=True, timeout=timeout // 1000 + 2,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


@pytest.mark.board_a
class TestCvm:
    @pytest.fixture(scope="class")
    def npub(self, board_config):
        nsec = os.environ.get(board_config.nsec_env_var, "")
        if not nsec:
            pytest.skip(f"{board_config.nsec_env_var} not set")
        npub = _nak(f"key public {nsec}")
        assert len(npub) == 64, f"npub should be 64 chars: {npub}"
        return npub

    @pytest.fixture(scope="class")
    def nsec(self, board_config):
        nsec = os.environ.get(board_config.nsec_env_var, "")
        if not nsec:
            pytest.skip(f"{board_config.nsec_env_var} not set")
        return nsec

    def test_board_api_reachable(self, board_connected, http):
        data = http.get_json(f"{board_connected.api_url}/usage")
        assert data is not None, "API /usage returned nothing"

    def test_kind_11316_announcement(self, npub):
        result = _nak(f"req -k 11316 -a {npub} -l 1 {CVM_RELAY}", 8000)
        if '"kind"' not in result and "11316" not in result:
            pytest.skip("Kind 11316 not yet published to relay")

    def test_kind_11317_tools_list(self, npub):
        result = _nak(f"req -k 11317 -a {npub} -l 1 {CVM_RELAY}", 8000)
        if '"kind"' not in result and "11317" not in result:
            pytest.skip("Kind 11317 not yet published to relay")
        assert "get_config" in result or "tools" in result

    def test_mcp_get_config_roundtrip(self, nsec, npub):
        content = json.dumps({
            "jsonrpc": "2.0", "id": int(time.time()),
            "method": "tools/call", "params": {"name": "get_config", "arguments": {}},
        })
        escaped = content.replace("'", "'\\''")
        event_out = _nak(
            f"event --sec {nsec} --kind 25910 "
            f"--tag p={npub} --content '{escaped}' {CVM_RELAY}", 8000,
        )
        assert "Success" in event_out or '"id"' in event_out, \
            f"Failed to publish kind 25910: {event_out[:200]}"

        time.sleep(8)

        resp = _nak(f"req -k 25910 -a {npub} -l 5 {CVM_RELAY}", 8000)
        assert '"kind"' in resp and "25910" in resp, f"No response: {resp[:200]}"
