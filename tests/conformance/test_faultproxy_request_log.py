"""Tests for faultproxy request-level logging (#29).

Proves that two identical swap requests produce distinguishable log trails
(different correlation ids, different body hashes when backends differ,
sightings traceable to specific requests) — the audit trail needed to
adjudicate backend swaps when the no-output-reuse invariant fires.
"""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.conformance.faultproxy import ProxyState, make_handler


class _FakeUpstream:
    """A minimal mint stand-in that records every request it receives."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def handler(self) -> type[BaseHTTPRequestHandler]:
        upstream = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                with upstream.lock:
                    upstream.requests.append({
                        "path": self.path,
                        "body": body,
                        "x_request_id": self.headers.get("X-Request-Id", ""),
                    })
                resp = json.dumps({"sig": "ok", "outputs": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

            def log_message(self, fmt, *args):
                pass

        return H


@pytest.fixture()
def proxy_and_upstream():
    upstream = _FakeUpstream()
    up_server = HTTPServer(("127.0.0.1", 0), upstream.handler())
    up_thread = threading.Thread(target=up_server.serve_forever, daemon=True)
    up_thread.start()

    # Reset the module-level STATE so tests don't bleed into each other
    import tests.conformance.faultproxy as fp
    fp.STATE = ProxyState()

    proxy_server = HTTPServer(("127.0.0.1", 0), make_handler(f"http://127.0.0.1:{up_server.server_port}"))
    proxy_thread = threading.Thread(target=proxy_server.serve_forever, daemon=True)
    proxy_thread.start()

    proxy_url = f"http://127.0.0.1:{proxy_server.server_port}"

    yield proxy_url, upstream

    proxy_server.shutdown()
    up_server.shutdown()


def _post(proxy_url: str, path: str, body: dict, headers: dict | None = None) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        proxy_url + path, data=data, method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def _get(proxy_url: str, path: str) -> dict:
    with urllib.request.urlopen(proxy_url + path, timeout=5) as resp:
        return json.loads(resp.read())


def test_two_identical_requests_produce_distinct_log_entries(proxy_and_upstream):
    """Two identical wire requests → two request-log entries with different
    correlation ids and different client ports — the trail that proves
    'genuinely two wire requests' vs 'proxy double-count'."""
    proxy_url, _ = proxy_and_upstream
    body = {"outputs": [{"B_": "abc123blindedmsg1", "amount": 4}]}

    r1 = _post(proxy_url, "/v1/swap", body)
    r2 = _post(proxy_url, "/v1/swap", body)

    log = _get(proxy_url, "/__fault/request-log")
    assert log["total"] == 2, f"expected 2 entries, got {log['total']}"
    entries = log["entries"]

    # Different correlation ids — each wire request gets its own audit trail
    assert entries[0]["corr_id"] != entries[1]["corr_id"], "identical requests must have distinct corr_ids"

    # Same body hash (the requests ARE identical on the wire)
    assert entries[0]["body_sha256"] == entries[1]["body_sha256"]

    # Different client ports — proves two separate TCP connections
    assert entries[0]["client_port"] != entries[1]["client_port"], \
        "identical requests from different connections must have different client ports"


def test_two_different_backends_distinguishable_by_body_hash(proxy_and_upstream):
    """Two requests with the same B_ but different surrounding JSON (as when
    rust and go serialize the swap differently) produce different body hashes
    — the trail that identifies WHICH backend sent each request."""
    proxy_url, _ = proxy_and_upstream

    # Simulate rust-style serialization
    rust_body = {
        "outputs": [{"B_": "same_blinded_message", "amount": 4}],
        "inputs": [{"_": "rust_proof_format"}],
    }
    # Simulate go-style serialization (same B_, different surrounding structure)
    go_body = {
        "outputs": [{"B_": "same_blinded_message", "amount": 4, "id": "keyset-01"}],
        "inputs": [],
    }

    _post(proxy_url, "/v1/swap", rust_body)
    _post(proxy_url, "/v1/swap", go_body)

    log = _get(proxy_url, "/__fault/request-log")
    entries = log["entries"]
    assert log["total"] == 2

    # Different body hashes → the requests are distinguishable even though
    # they carry the same blinded message
    assert entries[0]["body_sha256"] != entries[1]["body_sha256"], \
        "different backend serializations must produce different body hashes"

    # The observations trace each sighting to a specific request via corr_id
    obs = _get(proxy_url, "/__fault/observations")
    reused = obs["reused"]
    assert len(reused) == 1, f"same B_ twice → one reused digest, got {len(reused)}"

    digest = reused[0]
    sightings = obs["blinded_messages"][digest]
    assert len(sightings) == 2
    assert sightings[0]["corr_id"] != sightings[1]["corr_id"], \
        "each sighting must carry its own corr_id"
    assert sightings[0]["body_sha256"] != sightings[1]["body_sha256"], \
        "different backends → different body hashes in sightings"


def test_request_log_carries_x_request_id(proxy_and_upstream):
    """The X-Request-Id header (if the backend sends one) is captured in the
    log entry — the third distinguishing dimension after corr_id and body hash."""
    proxy_url, _ = proxy_and_upstream
    body = {"outputs": [{"B_": "xreq_test_msg", "amount": 2}]}

    _post(proxy_url, "/v1/swap", body, headers={"X-Request-Id": "backend-rust-001"})
    _post(proxy_url, "/v1/swap", body, headers={"X-Request-Id": "backend-go-002"})

    log = _get(proxy_url, "/__fault/request-log")
    entries = log["entries"]
    assert entries[0]["x_request_id"] == "backend-rust-001"
    assert entries[1]["x_request_id"] == "backend-go-002"


def test_state_exposes_request_log_count(proxy_and_upstream):
    """The /__fault/state endpoint includes the request-log entry count —
    a quick cross-check that the log matches the request counter."""
    proxy_url, _ = proxy_and_upstream
    body = {"outputs": [{"B_": "count_check", "amount": 1}]}

    _post(proxy_url, "/v1/swap", body)
    _post(proxy_url, "/v1/swap", body)

    state = _get(proxy_url, "/__fault/state")
    assert state["requests"] == 2
    assert state["request_log_entries"] == 2


def test_control_requests_not_in_log(proxy_and_upstream):
    """Control-plane requests (/__fault/*) are not logged — they're observer
    infrastructure, not backend traffic."""
    proxy_url, _ = proxy_and_upstream

    _get(proxy_url, "/__fault/state")
    _get(proxy_url, "/__fault/observations")

    log = _get(proxy_url, "/__fault/request-log")
    assert log["total"] == 0
