"""Fake Cashu mint server for testing mint failure scenarios.

Uses Python stdlib http.server — no external dependencies.
Runs in a background thread, binds to 0.0.0.0 with a random available port
so that devices on the same LAN can reach it.
"""

import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler


KEYSET_ID = "0011223344556677"  # active, 16 hex chars (V1 keyset id)
INACTIVE_KEYSET_ID = "8899aabbccddeeff"  # inactive: stored without keys, fetched on load
KEYSET_KEYS = {
    "1": "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
    "2": "02c6047f9441ed7d6d3045406e95c07cd85c778e4b8cef3ca7abac09b95c709ee5",
}


class FakeMintHandler(BaseHTTPRequestHandler):
    """HTTP handler that simulates Cashu mint responses.

    Modes:
      status  - return status_code / response_body (default)
      eof     - accept the connection and close it without a response
      keysets - serve a valid-looking static keyset so the wallet registers it
    """

    # Class-level config — set before starting server
    status_code = 502
    response_body = ""
    response_delay = 0
    mode = "status"

    def _respond(self):
        if FakeMintHandler.mode == "eof":
            try:
                self.connection.close()
            except Exception:
                pass
            return

        if FakeMintHandler.mode == "keysets":
            import json as _json

            if self.path.startswith("/v1/info"):
                body = {
                    "name": "fake",
                    "version": "fake/0.0.1",
                    "nuts": {
                        "4": {"methods": [{"method": "bolt11", "unit": "sat"}], "disabled": False},
                        "5": {"methods": [{"method": "bolt11", "unit": "sat"}], "disabled": False},
                    },
                }
            elif self.path.startswith("/v1/keysets"):
                body = {"keysets": [
                    {"id": KEYSET_ID, "unit": "sat", "active": True, "input_fee_ppk": 0},
                    {"id": INACTIVE_KEYSET_ID, "unit": "sat", "active": False, "input_fee_ppk": 0},
                ]}
            elif self.path.startswith("/v1/keys"):
                body = {"keysets": [
                    {"id": KEYSET_ID, "unit": "sat", "active": True, "input_fee_ppk": 0, "keys": KEYSET_KEYS},
                    {"id": INACTIVE_KEYSET_ID, "unit": "sat", "active": False, "input_fee_ppk": 0},
                ]}
            else:
                self.send_response(404)
                self.end_headers()
                return

            payload = _json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.response_delay > 0:
            time.sleep(self.response_delay)

        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if self.response_body:
            self.wfile.write(self.response_body.encode())

    def do_GET(self):
        self._respond()

    def do_POST(self):
        self._respond()

    def log_message(self, format, *args):
        """Suppress default stderr logging."""
        pass


class FakeMintServer:
    """Manages a fake mint HTTP server for testing.

    Usage:
        server = FakeMintServer(status_code=502)
        server.start()
        # server.url is now "http://0.0.0.0:{port}"
        # ... configure router to use http://{lan_ip}:{port} as mint ...
        server.stop()

    Or as a pytest fixture:
        @pytest.fixture
        def fake_mint_502():
            server = FakeMintServer(status_code=502)
            server.start()
            yield server
            server.stop()
    """

    def __init__(self, status_code=502, response_body="", response_delay=0, mode="status"):
        self.status_code = status_code
        self.response_body = response_body
        self.response_delay = response_delay
        self.mode = mode
        self._server = None
        self._thread = None

    @property
    def url(self) -> str:
        if not self._server:
            raise RuntimeError("Server not started")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def port(self) -> int:
        if not self._server:
            raise RuntimeError("Server not started")
        return self._server.server_address[1]

    def start(self):
        FakeMintHandler.status_code = self.status_code
        FakeMintHandler.response_body = self.response_body
        FakeMintHandler.response_delay = self.response_delay
        FakeMintHandler.mode = self.mode

        # Bind to 0.0.0.0 so devices on the LAN can reach the server
        self._server = HTTPServer(("0.0.0.0", 0), FakeMintHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None
            self._thread = None

    def set_response(self, status_code=200, body="", delay=0):
        """Update the response configuration on the running server."""
        FakeMintHandler.status_code = status_code
        FakeMintHandler.response_body = body
        FakeMintHandler.response_delay = delay

    def set_mode(self, mode):
        """Switch the running server between status / eof / keysets."""
        self.mode = mode
        FakeMintHandler.mode = mode
