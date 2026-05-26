import json
import subprocess
import time

import pytest


@pytest.mark.board_a
@pytest.mark.board_c
class TestLocalRelay:
    def test_websocket_connect(self, board_connected, relay):
        ws = relay.connect()
        assert ws.connected, f"Failed to connect to {relay.ws_url}"
        ws.close()

    def test_req_returns_eose(self, board_connected, relay):
        ws = relay.connect()
        ws.send(json.dumps(["REQ", "sub1", {"limit": 1}]))
        msgs = relay.collect_messages(ws, count=2, timeout_s=5.0)
        ws.close()
        has_eose = any(isinstance(m, list) and len(m) > 0 and m[0] == "EOSE" for m in msgs)
        assert has_eose, f"REQ should return EOSE, got: {msgs}"

    def test_publish_event(self, board_connected, relay):
        import hashlib
        ws = relay.connect()
        event = {
            "id": hashlib.sha256(b"pytest-test").hexdigest(),
            "pubkey": "a" * 64,
            "created_at": int(time.time()),
            "kind": 1,
            "content": "hello from pytest test_local_relay",
            "tags": [],
            "sig": "b" * 128,
        }
        ws.send(json.dumps(["EVENT", event]))
        msgs = relay.collect_messages(ws, count=2, timeout_s=5.0)
        ws.close()
        has_ok = any(isinstance(m, list) and len(m) > 0 and m[0] == "OK" for m in msgs)
        has_notice = any(isinstance(m, list) and len(m) > 0 and m[0] == "NOTICE" for m in msgs)
        assert has_ok or has_notice, f"Publish should return OK or NOTICE, got: {msgs}"

    def test_close_subscription(self, board_connected, relay):
        ws = relay.connect()
        ws.send(json.dumps(["REQ", "sub3", {"limit": 10}]))
        time.sleep(0.3)
        ws.send(json.dumps(["CLOSE", "sub3"]))
        time.sleep(0.3)
        ws.close()

    def test_multiple_connections(self, board_connected, relay):
        connections = []
        try:
            for _ in range(2):
                connections.append(relay.connect())
            assert len(connections) == 2
        finally:
            for ws in connections:
                ws.close()
