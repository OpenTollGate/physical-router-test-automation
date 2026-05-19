"""Fake Cashu mint server for testing mint failure scenarios.

Uses Python stdlib http.server — no external dependencies.
Runs in a background thread, binds to 0.0.0.0 with a random available port
so that devices on the same LAN can reach it.
"""

import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler


class FakeMintHandler(BaseHTTPRequestHandler):
    """HTTP handler that simulates Cashu mint responses."""

    # Class-level config — set before starting server
    status_code = 502
    response_body = ""
    response_delay = 0

    def _respond(self):
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

    def __init__(self, status_code=502, response_body="", response_delay=0):
        self.status_code = status_code
        self.response_body = response_body
        self.response_delay = response_delay
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
