"""Fake Cashu mint server for testing mint failure scenarios.

Uses Python stdlib http.server — no external dependencies.
Runs in a background thread, binds to 0.0.0.0 with a random available port
so that devices on the same LAN can reach it.

Modes:
  status  - return status_code / response_body (default)
  eof     - accept the connection and close it without a response
  keysets - serve a static V1 keyset (optionally with one inactive keyset that
            is stored without public keys, forcing a fetch on the next load)

Each server owns its handler state, so several fake mints can run at once
(e.g. one healthy mint and one stale mint in the same test).
"""

import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler


# V1 keyset id (00-prefix, 8 bytes) derived from KEYSET_KEYS per NUT-02:
# "00" + sha256(concat(sorted compressed pubkeys))[:14].
KEYSET_ID = "00f79b1f58098df8"
INACTIVE_KEYSET_ID = "0087d9a379e504a0"
KEYSET_KEYS = {
    "1": "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
    "2": "02c6047f9441ed7d6d3045406e95c07cd85c778e4b8cef3ca7abac09b95c709ee5",
}
# A distinct, valid V1 keyset so /v1/keys/<inactive-id> derives to its own id.
INACTIVE_KEYSET_KEYS = {
    "1": "02f9308a019258c31049344f85f89d5229b531c845836f99b08601f113bce036f9",
    "2": "02e493dbf1c10d80f3581e4904930b1404cc6c13900ee0758474fa94abe8c4cd13",
}


def _make_handler(config):
    class _Handler(BaseHTTPRequestHandler):
        def _respond(self):
            if config["mode"] == "eof":
                try:
                    self.connection.close()
                except Exception:
                    pass
                return

            if config["mode"] == "keysets":
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
                    keysets = [
                        {"id": KEYSET_ID, "unit": "sat", "active": True, "input_fee_ppk": 0},
                    ]
                    if config["include_inactive"]:
                        keysets.append(
                            {"id": INACTIVE_KEYSET_ID, "unit": "sat", "active": False, "input_fee_ppk": 0}
                        )
                    body = {"keysets": keysets}
                elif self.path.startswith("/v1/keys/"):
                    # NUT-01 by-id lookup. Each keyset must derive to the id
                    # it is requested under, or gonuts refuses it.
                    wanted = self.path.rstrip("/").rsplit("/", 1)[-1]
                    if wanted == KEYSET_ID:
                        body = {
                            "keysets": [
                                {
                                    "id": KEYSET_ID,
                                    "unit": "sat",
                                    "active": True,
                                    "input_fee_ppk": 0,
                                    "keys": KEYSET_KEYS,
                                },
                            ]
                        }
                    elif wanted == INACTIVE_KEYSET_ID and config["include_inactive"]:
                        body = {
                            "keysets": [
                                {
                                    "id": INACTIVE_KEYSET_ID,
                                    "unit": "sat",
                                    "active": False,
                                    "input_fee_ppk": 0,
                                    "keys": INACTIVE_KEYSET_KEYS,
                                },
                            ]
                        }
                    else:
                        self.send_response(404)
                        self.end_headers()
                        return
                elif self.path.startswith("/v1/keys"):
                    # NUT-01: /v1/keys returns active keysets WITH keys. Do not
                    # include an inactive entry without a "keys" object here —
                    # gonuts' Keyset.UnmarshalJSON would try to unmarshal an
                    # empty RawMessage and fail with "unexpected end of JSON
                    # input". Inactive keysets belong to the /v1/keysets reply.
                    body = {
                        "keysets": [
                            {
                                "id": KEYSET_ID,
                                "unit": "sat",
                                "active": True,
                                "input_fee_ppk": 0,
                                "keys": KEYSET_KEYS,
                            },
                        ]
                    }
                else:
                    self.send_response(404)
                    self.end_headers()
                    return

                payload = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            if config["response_delay"] > 0:
                time.sleep(config["response_delay"])

            self.send_response(config["status_code"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if config["response_body"]:
                self.wfile.write(config["response_body"].encode())

        def do_GET(self):
            self._respond()

        def do_POST(self):
            self._respond()

        def log_message(self, format, *args):
            """Suppress default stderr logging."""
            pass

    return _Handler


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

    def __init__(
        self,
        status_code=502,
        response_body="",
        response_delay=0,
        mode="status",
        include_inactive=False,
    ):
        self.status_code = status_code
        self.response_body = response_body
        self.response_delay = response_delay
        self.mode = mode
        self.include_inactive = include_inactive
        self._server = None
        self._thread = None
        self._config = None

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
        self._config = {
            "status_code": self.status_code,
            "response_body": self.response_body,
            "response_delay": self.response_delay,
            "mode": self.mode,
            "include_inactive": self.include_inactive,
        }

        # Bind to 0.0.0.0 so devices on the LAN can reach the server
        self._server = HTTPServer(("0.0.0.0", 0), _make_handler(self._config))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self._thread = None

    def set_response(self, status_code=200, body="", delay=0):
        """Update the response configuration on the running server."""
        self.status_code = status_code
        self.response_body = body
        self.response_delay = delay
        if self._config is not None:
            self._config["status_code"] = status_code
            self._config["response_body"] = body
            self._config["response_delay"] = delay

    def set_mode(self, mode):
        """Switch the running server between status / eof / keysets."""
        self.mode = mode
        if self._config is not None:
            self._config["mode"] = mode
