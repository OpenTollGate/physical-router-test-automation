"""Mock router backend for local testing without a physical OpenWrt router.

When TOLLGATE_MOCK=1 is set, the test framework uses MockRouter instead of
the real Router class. MockRouter starts a local HTTP server that responds
to all backend API endpoints, and overrides ssh() to return canned responses
for common OpenWrt commands.

Usage::

    TOLLGATE_MOCK=1 TOLLGATE_BACKEND=rust python3 -m pytest tests/api/ -v

This allows running ~50+ API tests locally without any router hardware.
Tests that require real hardware behavior (WiFi, NDS captive portal, iptables,
physical device access) should be explicitly skipped via the mock router's
ssh() return values, which naturally cause feature-detection skips.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

from lib.router import Router

log = logging.getLogger("tollgate.mock")

# ---------------------------------------------------------------------------
# Canned mock data — realistic responses for a healthy tollgate backend
# ---------------------------------------------------------------------------

# A realistic Nostr kind:10021 advertisement event (what GET / returns)
MOCK_ADVERTISEMENT = {
    "kind": 10021,
    "pubkey": "a" * 64,
    "content": "",
    "tags": [
        ["metric", "milliseconds"],
        ["step_size", "5000"],
        ["price_per_step", "cashu", "1", "sat", "https://testnut.cashu.exchange"],
        ["tips", "Pay with Cashu tokens for internet access"],
    ],
}

# A realistic balance response for an active session
MOCK_BALANCE = {
    "remaining": 20000000,
    "allotment": 20000000,
    "session_active": True,
}

# A realistic usage response (X/Y format per the parity tests)
MOCK_USAGE = "10000000/20000000"

# A realistic whoami response
MOCK_WHOAMI = "mac=00:11:22:33:44:55"

# A realistic session event for a successful payment (kind 1022)
MOCK_PAYMENT_SUCCESS = {
    "kind": 1022,
    "pubkey": "a" * 64,
    "content": "session_active",
    "tags": [
        ["allotment", "20000000"],
        ["remaining", "20000000"],
        ["mac", "00:11:22:33:44:55"],
    ],
}

# Error response for invalid token payment (kind 21023)
MOCK_PAYMENT_INVALID = {
    "kind": 21023,
    "pubkey": "a" * 64,
    "content": "error: invalid token",
    "tags": [["code", "invalid-token"]],
}

# Mock config.json content
MOCK_CONFIG = {
    "config_version": "v0.0.7",
    "log_level": "info",
    "metric": "milliseconds",
    "step_size": 5000,
    "margin": 0.1,
    "accepted_mints": [
        {
            "url": "https://testnut.cashu.exchange",
            "min_balance": 0,
            "balance_tolerance_percent": 0,
            "payout_interval_seconds": 60,
            "min_payout_amount": 0,
            "price_per_step": 1,
            "price_unit": "sat",
            "purchase_min_steps": 0,
        }
    ],
    "profit_share": [{"factor": 1.0, "identity": "owner"}],
}

MOCK_CONFIG_JSON = json.dumps(MOCK_CONFIG, indent=2)

# Mock identities.json (contains keypair)
MOCK_IDENTITIES = {
    "pubkey": "a" * 64,
    "privkey": "b" * 64,
}
MOCK_IDENTITIES_JSON = json.dumps(MOCK_IDENTITIES, indent=2)

# Mock CLI version response
MOCK_VERSION_MSG = "\n".join([
    "version: v1.2.3-mock",
    "commit: abc1234",
    "build_time: 2026-07-01T00:00:00Z",
    "go_version: go1.22.0",
    "rust_version: rustc 1.80.0",
    "openwrt: OpenWrt 23.05.3",
])

# Mock tollgate status (CLI socket JSON)
MOCK_CLI_STATUS = {
    "success": True,
    "message": json.dumps({
        "mode": "full_merchant",
        "mints": [{"url": "https://testnut.cashu.exchange", "status": "reachable"}],
        "degraded": False,
    }),
}

# Mock wallet info
MOCK_WALLET_INFO = {
    "success": True,
    "message": json.dumps({
        "balance": 100,
        "unit": "sat",
    }),
}

MOCK_WALLET_BALANCE = {
    "success": True,
    "message": json.dumps({"balance": 100, "unit": "sat"}),
}

MOCK_CLI_VERSION = {
    "success": True,
    "message": MOCK_VERSION_MSG,
}

# Mock backend log lines (for degraded mode / health tracker tests)
MOCK_LOGS = "\n".join([
    "2026-07-01T00:00:01Z tollgate-wrt[1234]: Starting tollgate-wrt v1.2.3-mock",
    "2026-07-01T00:00:02Z tollgate-wrt[1234]: MintHealthTracker: RunInitialProbe started",
    "2026-07-01T00:00:03Z tollgate-wrt[1234]: Mint health: testnut.cashu.exchange reachable",
    "2026-07-01T00:00:04Z tollgate-wrt[1234]: Merchant started successfully",
    "2026-07-01T00:00:05Z tollgate-wrt[1234]: HTTP server listening on :2121",
])

# Mock process list showing tollgate-wrt running
MOCK_PS = " 1234 root      1200 S    /usr/bin/tollgate-wrt"

# Mock DHCP leases
MOCK_DHCP_LEASES = f"{int(time.time())} 00:11:22:33:44:55 10.0.0.100 * 01:00:11:22:33:44:55"

# Mock ndsctl clients output
MOCK_NDS_CLIENTS = "\n".join([
    "--- Client 00:11:22:33:44:55 ---",
    "ip=10.0.0.100",
    "mac=00:11:22:33:44:55",
    "state=Authenticated",
    "download=0  uploaded=0",
])


# ---------------------------------------------------------------------------
# Mock HTTP Server — serves backend API endpoints locally
# ---------------------------------------------------------------------------


class MockBackendHandler(BaseHTTPRequestHandler):
    """HTTP request handler that mimics the TollGate backend API."""

    def log_message(self, format, *args):
        # Suppress default logging (too noisy)
        pass

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, separators=(",", ":")) if isinstance(data, (dict, list)) else str(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def _send_text(self, text: str, status: int = 200, content_type: str = "text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(text)))
        self.end_headers()
        self.wfile.write(text.encode())

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/" or path == "/pay":
            self._send_json(MOCK_ADVERTISEMENT)
        elif path == "/balance":
            self._send_json(MOCK_BALANCE)
        elif path == "/usage":
            self._send_text(MOCK_USAGE)
        elif path == "/whoami":
            self._send_text(MOCK_WHOAMI)
        elif path == "/health":
            self._send_json({"status": "ok"})
        elif path == "/info":
            self._send_json({"version": "v1.2.3-mock", "backend": "mock"})
        elif path == "/ln-invoice":
            self._send_json({"error": "ln-invoice not supported in mock mode"}, status=400)
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8", errors="replace") if content_length else ""

        if path == "/" or path == "/pay":
            # Check if the body looks like a valid Cashu token (starts with "cashu")
            if body.strip().lower().startswith("cashu") or body.strip().startswith("{") and "token" in body.lower():
                self._send_json(MOCK_PAYMENT_SUCCESS)
            else:
                self._send_json(MOCK_PAYMENT_INVALID)
        elif path == "/ln-invoice":
            self._send_json({"error": "ln-invoice not supported in mock mode"}, status=400)
        elif path == "/connect":
            self._send_json({"success": True})
        else:
            self._send_json({"error": "not found"}, status=404)


class MockBackendServer:
    """A local HTTP server that mimics the TollGate backend API."""

    def __init__(self, port: int = 2121, host: str = "127.0.0.1"):
        self.port = port
        self.host = host
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        if self._server is not None:
            return  # already running
        try:
            self._server = HTTPServer((self.host, self.port), MockBackendHandler)
        except OSError as exc:
            # Port already in use — try a random port
            log.warning("Port %d in use (%s), using random port", self.port, exc)
            self._server = HTTPServer((self.host, 0), MockBackendHandler)
            self.port = self._server.server_address[1]
            log.info("Mock backend started on port %d", self.port)
            os.environ["TOLLGATE_MOCK_PORT"] = str(self.port)
            return

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        log.info("Mock backend started on %s:%d", self.host, self.port)
        os.environ["TOLLGATE_MOCK_PORT"] = str(self.port)

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        log.info("Mock backend stopped")

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


# Singleton server instance
_mock_server: MockBackendServer | None = None


def get_mock_server(port: int = 2121) -> MockBackendServer:
    global _mock_server
    if _mock_server is None:
        _mock_server = MockBackendServer(port=port)
        _mock_server.start()
    return _mock_server


def stop_mock_server():
    global _mock_server
    if _mock_server is not None:
        _mock_server.stop()
        _mock_server = None


# ---------------------------------------------------------------------------
# MockRouter — extends Router, overrides all I/O methods
# ---------------------------------------------------------------------------


class MockRouter(Router):
    """A Router subclass that returns canned responses for all operations.

    No SSH connection is made. No hardware is required. API calls hit a
    local HTTP server that returns realistic TollGate backend responses.
    """

    def __init__(self, host: str = "127.0.0.1", phone_ip: str = "10.0.0.100",
                 phone_mac: str = "00:11:22:33:44:55", domain: str = "",
                 backend=None, **kwargs):
        # Don't call super().__init__ fully — we don't need SSH control master
        # But we do need to set the attributes tests access
        self.host = host
        self.phone_ip = phone_ip
        self.phone_mac = phone_mac
        self.domain = domain or host
        self.identity_file = None
        self.jump_host = None
        self.port = None
        from lib.backend import BackendConfig
        self.backend = backend or BackendConfig()
        self._ssh_pw = None
        self._control_dir = "/tmp/tollgate-mock-ssh"
        self._control_path = "/tmp/tollgate-mock-ssh/control"
        self._nds_portal_port = 2050

        # Start the mock HTTP backend server
        self._mock_server = get_mock_server()
        self._mock_port = self._mock_server.port

    def close(self):
        """No SSH connections to close in mock mode."""
        pass

    @property
    def mock_base_url(self) -> str:
        return f"http://127.0.0.1:{self._mock_port}"

    def backend_url(self, path: str = "/") -> str:
        return f"{self.mock_base_url}{path}"

    # --- SSH mock: return canned responses based on command patterns ---

    def ssh(self, cmd: str, timeout: int = 30) -> str:
        """Return canned responses for common OpenWrt SSH commands.

        For unknown commands, returns a plausible default that causes
        feature-detection skips rather than test failures.
        """
        result = self._mock_ssh(cmd)
        if result is not None:
            return result
        # Default: empty string (triggers skip conditions in tests)
        return ""

    def _mock_ssh(self, cmd: str) -> str | None:
        """Pattern-match SSH commands and return canned responses."""
        cmd_lower = cmd.lower().strip()

        # --- Backend API calls via wget/curl (must be checked BEFORE echo/cat) ---
        if ("wget" in cmd_lower or "curl" in cmd_lower) and ("127.0.0.1" in cmd_lower or "[::1]" in cmd_lower or "localhost" in cmd_lower):
            return self._mock_curl_via_ssh(cmd)

        # --- Config file reads ---
        if "cat /etc/tollgate/config.json" in cmd_lower:
            return MOCK_CONFIG_JSON

        if "cat /etc/tollgate/identities.json" in cmd_lower:
            return MOCK_IDENTITIES_JSON

        if "cat /etc/tollgate/quotes.json" in cmd_lower:
            return "{}"

        if "cat /etc/tollgate/sessions.json" in cmd_lower:
            return "{}"

        # --- File permission checks ---
        if "stat -c" in cmd_lower and "config.json" in cmd_lower:
            return "600"
        if "stat -c" in cmd_lower and "identities.json" in cmd_lower:
            return "600"
        if "stat -c" in cmd_lower:
            return "644"

        # --- File existence checks ---
        if "ls" in cmd_lower and "/etc/tollgate/config.json" in cmd_lower:
            return "/etc/tollgate/config.json"
        if "ls -S /var/run/tollgate.sock" in cmd_lower or "test -S /var/run/tollgate.sock" in cmd_lower:
            return "READY"  # CLI socket exists in mock mode
        if "ls /etc/tollgate/sessions.json" in cmd_lower:
            return "/etc/tollgate/sessions.json"
        if "ls /var/run/tollgate.sock" in cmd_lower:
            return "/var/run/tollgate.sock"
        if "ls -la" in cmd_lower and "/www/net4sats/" in cmd_lower:
            return "-rw-r--r--    1 root     root          1024 Jul  1 00:00 /www/net4sats/balance.html"
        if "ls" in cmd_lower and "nodogsplash/htdocs" in cmd_lower and "assets" in cmd_lower:
            return "/etc/nodogsplash/htdocs/assets/splash-a1b2c3.js"
        if "ls" in cmd_lower and "/etc/nodogsplash/htdocs/" in cmd_lower:
            return "splash.html\nassets\nfavicon.ico"
        if "cat" in cmd_lower and "nodogsplash/htdocs/splash.html" in cmd_lower:
            return "<!DOCTYPE html>\n<html><head><title>TollGate Portal</title></head><body>TollGate Captive Portal</body></html>"
        if "cat" in cmd_lower and "nodogsplash/htdocs/portal.html" in cmd_lower:
            return "<!DOCTYPE html>\n<html><head><title>TollGate Portal</title></head><body>Cashu Payment Portal</body></html>"
        if "cat" in cmd_lower and "nodogsplash/htdocs/manifest.json" in cmd_lower:
            return json.dumps({"name": "TollGate Portal", "short_name": "TollGate"})

        # --- find commands (file scanning) ---
        if cmd_lower.startswith("find /etc/tollgate/"):
            return ""  # No world-readable files

        # --- Process checks ---
        if "ps" in cmd_lower and "tollgate" in cmd_lower:
            return MOCK_PS
        if "pidof nodogsplash" in cmd_lower:
            return ""  # NDS not running in mock mode

        # --- Log reads ---
        if "logread" in cmd_lower and "tollgate" in cmd_lower:
            return MOCK_LOGS

        # --- ndsctl commands ---
        if "ndsctl clients" in cmd_lower:
            return MOCK_NDS_CLIENTS
        if "ndsctl status" in cmd_lower:
            return ""  # NDS not running in mock mode
        if "ndsctl auth" in cmd_lower:
            return ""
        if "ndsctl deauth" in cmd_lower:
            return ""

        # --- DHCP leases ---
        if "cat /tmp/dhcp.leases" in cmd_lower:
            return MOCK_DHCP_LEASES

        # --- netstat ---
        if "netstat" in cmd_lower:
            if ":2121" in cmd_lower:
                return f"tcp        0      0 0.0.0.0:{self._mock_port}            0.0.0.0:*               LISTEN"
            if ":80 " in cmd_lower:
                return "tcp        0      0 0.0.0.0:80                    0.0.0.0:*               LISTEN"
            if ":8080" in cmd_lower:
                return "tcp        0      0 0.0.0.0:8080                   0.0.0.0:*               LISTEN"
            return ""

        # --- iptables (return empty for clean state) ---
        if cmd_lower.startswith("iptables"):
            return ""

        # --- UCI commands ---
        if "uci -q get nodogsplash" in cmd_lower and "gatewayport" in cmd_lower:
            return "2050"
        if "uci" in cmd_lower and "get" in cmd_lower:
            if "network.lan.ipaddr" in cmd_lower:
                return "10.0.0.1"
            if "system" in cmd_lower and "hostname" in cmd_lower:
                return "TollGate"
            if "uhttpd" in cmd_lower:
                return "uhttpd.main.listen_http='0.0.0.0:80'"
            return ""

        # --- nslookup ---
        if "nslookup" in cmd_lower:
            return f"Server: 127.0.0.1\nAddress: 127.0.0.1\n\nName: testnut.cashu.exchange\nAddress: 1.2.3.4"

        # --- wget/curl on localhost (for internal API calls via SSH) ---
        if ("wget" in cmd_lower or "curl" in cmd_lower) and ("127.0.0.1" in cmd_lower or "[::1]" in cmd_lower):
            return self._mock_curl_via_ssh(cmd)

        # --- which ---
        if cmd_lower.startswith("which"):
            return "/usr/bin/curl"

        # --- jq ---
        if "jq" in cmd_lower:
            if "price_per_step" in cmd_lower:
                return "1"
            if "step_size" in cmd_lower:
                return "5000"
            return "null"

        # --- hostname ---
        if "hostname" in cmd_lower or "/proc/sys/kernel/hostname" in cmd_lower:
            return "TollGate"

        # --- strings (binary inspection) ---
        if "strings" in cmd_lower:
            return "1"  # Non-zero count for feature detection

        # --- tollgate CLI commands ---
        if "tollgate version" in cmd_lower or "tollgate --json version" in cmd_lower:
            return json.dumps(MOCK_CLI_VERSION)
        if "tollgate status" in cmd_lower or "tollgate --json status" in cmd_lower:
            return json.dumps(MOCK_CLI_STATUS)
        if "tollgate" in cmd_lower and "ssl" in cmd_lower:
            return "SSL not configured"
        if "tollgate" in cmd_lower and "wallet" in cmd_lower:
            return json.dumps(MOCK_WALLET_INFO)

        # --- service restart ---
        if "service tollgate" in cmd_lower or "/etc/init.d/tollgate" in cmd_lower:
            return ""

        # --- iw/iwinfo (WiFi) ---
        if "iw dev" in cmd_lower or "iwinfo" in cmd_lower or "iw list" in cmd_lower:
            return "phy#0\n\tInterface phy0-ap0\n\t\tifindex 10\n\t\tssid TollGate"

        # --- brctl ---
        if "brctl show" in cmd_lower:
            return "bridge name\tbridge id\t\tSTP enabled\tinterfaces\nbr-lan\t\t7fff.001122334455\tno\t\teth0\n\t\t\t\t\t\twlan0"

        # --- echo/cat on portal files ---
        if "cat /tmp/tollgate-portal.log" in cmd_lower:
            return ""
        if "echo" in cmd_lower and "dhcp.leases" not in cmd_lower:
            return ""

        # --- sed (config modifications) ---
        if cmd_lower.startswith("sed"):
            return ""

        # --- grep ---
        if "grep" in cmd_lower:
            if "tollgate.sock" in cmd_lower or "gatewaydomainname" in cmd_lower:
                return ""

        # --- RPC ACL ---
        if "cat /usr/share/rpcd" in cmd_lower:
            return json.dumps({"access": {"tollgate": {"read": True, "write": True}}})

        # --- UCI defaults ---
        if "cat /etc/uci-defaults/99-tollgate-setup" in cmd_lower:
            return "#!/bin/sh\nexit 0"

        return None  # Let ssh() return "" for unhandled commands

    def _mock_curl_via_ssh(self, cmd: str) -> str:
        """Handle wget/curl commands that hit the local backend or router ports."""
        import subprocess as sp

        url_match = re.search(r"https?://[^\s'\"]+", cmd)
        if not url_match:
            return ""
        url = url_match.group(0)

        try:
            mock_url = re.sub(
                r"https?://\[?::1\]?:(\d+)",
                f"http://127.0.0.1:{self._mock_port}",
                url,
            )
            mock_url = re.sub(
                r"https?://127\.0\.0\.1:(\d+)",
                f"http://127.0.0.1:{self._mock_port}",
                mock_url,
            )

            is_post = ("--post-data" in cmd or "-d " in cmd or "-d@" in cmd
                       or "--post-file" in cmd or "wget -O- --post" in cmd)
            post_data = None
            post_match = re.search(r"--post-data='([^']*)'", cmd)
            if not post_match:
                post_match = re.search(r'-d\s+["\']?([^\'"\s]+)', cmd)
            if post_match:
                post_data = post_match.group(1)

            wants_headers = "-D -" in cmd or "-i" in cmd
            wants_status_only = "-o /dev/null" in cmd and "-w" in cmd

            args = ["curl", "-s", "--max-time", "5"]
            if wants_headers:
                args.append("-D")
                args.append("-")
            if wants_status_only:
                args.extend(["-o", "/dev/null", "-w", "%{http_code}"])
            args.append(mock_url)
            if is_post:
                args.extend(["-X", "POST"])
                if post_data:
                    args.extend(["-d", post_data])

            r = sp.run(args, capture_output=True, text=True, timeout=10)
            return r.stdout.strip()
        except Exception:
            return ""

    def ssh_stdin(self, cmd: str, data: str, timeout: int = 15):
        """Mock ssh_stdin — returns a fake subprocess result."""
        import subprocess
        result = subprocess.run(
            ["echo", "mock"],
            capture_output=True, text=True, timeout=timeout,
        )
        return result

    def ssh_bool(self, cmd: str, timeout: int = 30) -> bool:
        """Mock boolean SSH check."""
        out = self.ssh(f"( {cmd} ) >/dev/null 2>&1 && echo YES || echo NO", timeout=timeout)
        return out.strip() == "YES"

    # --- API methods — make real HTTP calls to the mock server ---

    def _use_ssh_for_api(self) -> bool:
        return False  # In mock mode, make direct HTTP calls

    def api_status(self, path: str) -> int:
        import subprocess as sp
        url = self.backend_url(path)
        try:
            r = sp.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url],
                capture_output=True, text=True, timeout=10,
            )
            code = r.stdout.strip()
            return int(code) if code.isdigit() else 0
        except Exception:
            return 0

    def api_body(self, path: str) -> str:
        import subprocess as sp
        url = self.backend_url(path)
        try:
            r = sp.run(
                ["curl", "-s", url],
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip()
        except Exception:
            return ""

    def pay_direct(self, token: str, ip: str | None = None) -> dict:
        import subprocess as sp
        url = self.backend_url("/")
        try:
            r = sp.run(
                ["curl", "-s", "--max-time", "10", "-d", "@-",
                 "-H", "Content-Type: text/plain", url],
                input=token, capture_output=True, text=True, timeout=15,
            )
            resp = r.stdout.strip()
            try:
                return json.loads(resp)
            except json.JSONDecodeError:
                return {"raw": resp}
        except Exception:
            return {"error": "mock pay_direct failed"}

    def backend_curl_xff(self, path: str, ip: str | None = None, method: str | None = None,
                         headers: dict | None = None, data: str | None = None) -> str:
        import subprocess as sp
        url = path if path.startswith("http") else self.backend_url(path)
        try:
            r = sp.run(
                ["curl", "-s", "--max-time", "10", url],
                capture_output=True, text=True, timeout=15,
            )
            return r.stdout.strip()
        except Exception:
            return ""

    def get_session(self, ip: str | None = None) -> dict:
        resp = self.api_body("/balance")
        try:
            return json.loads(resp)
        except json.JSONDecodeError:
            return {"raw": resp}

    def get_remaining_seconds(self, ip: str | None = None) -> int:
        return 20000

    def get_nds_state(self, mac: str | None = None) -> str:
        return "Authenticated"

    def wait_for_auth(self, timeout: int = 30, mac: str | None = None) -> bool:
        return True

    def reset_state(self, mac: str | None = None, adb=None):
        pass

    def restart_backend(self, timeout: int = 30):
        pass

    def _wait_for_backend(self, timeout: int = 15):
        pass

    def wait_for_cli_socket(self, timeout: int = 30, interval: int = 1) -> bool:
        return True

    def ensure_test_mint(self):
        pass

    def replace_mints(self, mint_urls: list[str] | None = None):
        pass

    def ensure_dhcp_lease(self, ip: str | None = None, mac: str | None = None) -> None:
        pass

    def fix_nodogsplash_dhcp(self):
        pass

    def disable_ipv6_on_lan(self):
        pass

    def apply_pricing(self, step_size: int | None = None, metric: str = "milliseconds"):
        pass

    def restore_pricing(self):
        pass

    def enable_debug_portal(self):
        pass

    def disable_debug_portal(self):
        pass

    def clear_portal_log(self):
        pass

    def get_portal_log(self) -> str:
        return ""

    def get_tollgate_version(self) -> dict:
        return MOCK_CLI_VERSION

    def get_wallet_info(self) -> dict:
        return MOCK_WALLET_INFO

    def get_wallet_balance(self) -> dict:
        return MOCK_WALLET_BALANCE

    def get_tollgate_status(self) -> dict:
        return MOCK_CLI_STATUS

    def get_tollgate_logs(self, filter_expr: str = "tollgate", lines: int = 200) -> str:
        return MOCK_LOGS

    def cli_command(self, command: str, args: list[str] | None = None, timeout: int = 10) -> dict:
        if command == "version":
            return MOCK_CLI_VERSION
        if command == "status":
            return MOCK_CLI_STATUS
        if command == "wallet":
            if args and "balance" in args:
                return MOCK_WALLET_BALANCE
            return MOCK_WALLET_INFO
        return {"success": True, "message": "mock"}

    def collect_logs(self, results_dir: str, adb=None, bundle: str | None = None):
        pass

    def scp_to(self, local_path: str, remote_path: str, timeout: int = 120):
        pass

    def write_remote_text(self, remote_path: str, content: str, timeout: int = 15):
        pass

    def write_remote_json(self, remote_path: str, payload, indent: int = 2, timeout: int = 15):
        pass

    def resolve_phone_client(self, adb) -> tuple:
        return self.phone_mac, self.phone_ip

    def get_nds_portal_port(self) -> int:
        return 2050

    def get_nds_gateway_domain(self) -> str:
        return ""

    @property
    def gateway_ip(self) -> str:
        return self.domain or self.host

    def file_mode(self, path: str) -> str:
        return "-rw-------" if "config.json" in path or "identities.json" in path else "-rw-r--r--"

    def file_octal_mode(self, path: str) -> str:
        return "600" if "config.json" in path or "identities.json" in path else "644"

    def uci_get(self, path: str) -> str:
        if "gatewayport" in path:
            return "2050"
        if "network.lan.ipaddr" in path:
            return "10.0.0.1"
        if "hostname" in path:
            return "TollGate"
        return ""

    def uci_set(self, path: str, value: str) -> None:
        pass

    def uci_commit(self, *configs: str) -> None:
        pass

    def block_mint(self, mint_url: str | None = None) -> None:
        pass

    def unblock_mint(self, mint_url: str | None = None) -> None:
        pass

    def get_hosts_entries(self) -> list[str]:
        return ["127.0.0.1 localhost"]

    def upstream_connect(self, ssid: str, password: str | None = None) -> dict:
        return {"success": True}

    def upstream_remove(self, ssid: str) -> dict:
        return {"success": True}

    def upstream_list(self) -> dict:
        return {"success": True, "message": "[]"}

    def get_client_ip_from_nds(self, mac: str | None = None) -> str:
        return self.phone_ip

    def wait_for_session_expiry(self, mac: str | None = None, poll_interval: float = 1, max_wait: int = 120) -> int:
        return 0

    def router_fetch(self, url: str, method: str = "GET", data: str | None = None, timeout: int = 10) -> str:
        if ":2050" in url or "cgi-bin" in url or ":8080" in url or ":8090" in url:
            return ""
        return self.api_body(url.split(":", 2)[-1] if ":" in url else "/")

    def router_fetch_status(self, url: str, timeout: int = 10) -> str:
        if ":2050" in url or "cgi-bin" in url or ":8080" in url or ":8090" in url:
            return "000"
        return "200"

    def cgi_url(self, endpoint: str) -> str:
        return f"http://127.0.0.1:2050/cgi-bin/{endpoint}"

    def ensure_nds_gateway_domain_supported(self):
        pass
