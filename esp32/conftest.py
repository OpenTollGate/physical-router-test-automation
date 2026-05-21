import os
import sys
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from boards import Config, BoardConfig


def pytest_addoption(parser):
    parser.addoption("--board", choices=["a", "b", "c"], help="Board to test (a, b, or c)")
    parser.addoption("--phase", default="", help="Phase description for lock metadata")


def pytest_configure(config):
    config.addinivalue_line("markers", "board_a: tests for Board A")
    config.addinivalue_line("markers", "board_b: tests for Board B")
    config.addinivalue_line("markers", "board_c: tests for Board C")
    config.addinivalue_line("markers", "requires_funding: tests that need a funded wallet")
    config.addinivalue_line("markers", "smoke: quick smoke tests (~30s)")


@pytest.fixture(scope="session")
def config():
    return Config.load()


@pytest.fixture(scope="session")
def board_id(request):
    bid = request.config.getoption("--board")
    if bid:
        return bid
    markers = {m.name for m in request.node.iter_markers()}
    for marker in ["board_a", "board_b", "board_c"]:
        if marker in markers:
            return marker.split("_")[1]
    return "c"


@pytest.fixture(scope="session")
def board_config(config, board_id):
    return config.get_board(board_id)


@pytest.fixture(scope="session")
def board_lock(request, config, board_config):
    lock_path = Path(config.lock_dir) / f"{board_config.lock_name}.lock"
    phase = request.config.getoption("--phase") or "pytest"

    if lock_path.exists():
        existing = lock_path.read_text()
        for line in existing.splitlines():
            if line.startswith("session:"):
                owner = line.split(":", 1)[1].strip().split("@")[0]
                current_user = os.environ.get("USER", "unknown")
                if owner != current_user:
                    pytest.exit(
                        f"\n  Board {board_config.id} locked by another session:\n"
                        f"  {existing}\n"
                        f"  Use --force-unlock to override."
                    )
                break

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    branch = "unknown"
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        pass

    lock_path.write_text(
        f"locked: true\n"
        f"board: {board_config.lock_name}\n"
        f"port: {board_config.port}\n"
        f"branch: {branch}\n"
        f"session: {os.environ.get('USER', 'unknown')}@{os.uname().nodename}\n"
        f"timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"phase: {phase}\n"
    )
    print(f"\n  Lock acquired: {board_config.lock_name} ({phase})")

    yield board_config

    lock_path.unlink(missing_ok=True)
    print(f"\n  Lock released: {board_config.lock_name}")


@pytest.fixture(scope="session")
def wifi(config):
    return WiFiHelper(config)


class WiFiHelper:
    def __init__(self, config: Config):
        self.config = config
        self.iface = config.wifi_iface
        self.sudo_pw = config.sudo_pw

    def _sudo(self, cmd: str, timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            f'echo "{self.sudo_pw}" | sudo -S {cmd}',
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def disconnect(self):
        self._sudo(f"nmcli device disconnect {self.iface}")
        time.sleep(2)

    def connect(self, ssid: str, timeout: int = 30) -> bool:
        self.disconnect()
        result = self._sudo(
            f"nmcli device wifi connect \"{ssid}\" ifname {self.iface}",
            timeout=timeout,
        )
        time.sleep(3)
        return "successfully activated" in result.stdout

    def connect_to_board(self, board: BoardConfig) -> bool:
        connected = self.connect(board.ssid)
        if connected:
            assert self._can_ping(board.ip), f"Board at {board.ip} not reachable after connect"
        return connected

    def connect_to_upstream(self) -> bool:
        return self.connect(self.config.upstream_ssid)

    def _can_ping(self, host: str) -> bool:
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "2", host],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return "100% packet loss" not in result.stdout and result.returncode == 0
        except Exception:
            return False

    def can_ping_internet(self, host: str = "1.1.1.1") -> bool:
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "3", "-I", self.iface, host],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return "100% packet loss" not in result.stdout
        except Exception:
            return False

    def scan_ssids(self) -> list[str]:
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "SSID", "dev", "wifi", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return [s for s in result.stdout.strip().split("\n") if s and s != "SSID"]
        except Exception:
            return []


@pytest.fixture(scope="session")
def http():
    return HttpHelper()


class HttpHelper:
    @staticmethod
    def get(url: str, timeout: int = 15) -> str | None:
        try:
            result = subprocess.run(
                ["curl", "-s", "-L", "--connect-timeout", str(timeout),
                 "--max-time", str(timeout + 10), url],
                capture_output=True,
                text=True,
                timeout=timeout + 15,
            )
            return result.stdout if result.returncode == 0 else None
        except Exception:
            return None

    @staticmethod
    def get_json(url: str, timeout: int = 15) -> dict | list | None:
        body = HttpHelper.get(url, timeout)
        if body:
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def get_status(url: str, timeout: int = 15) -> int | None:
        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "--connect-timeout", str(timeout),
                 "--max-time", str(timeout + 5), url],
                capture_output=True,
                text=True,
                timeout=timeout + 10,
            )
            code = result.stdout.strip()
            return int(code) if code.isdigit() else None
        except Exception:
            return None

    @staticmethod
    def post(url: str, data: str, timeout: int = 15) -> tuple[int, str]:
        try:
            result = subprocess.run(
                ["curl", "-s", "--connect-timeout", str(timeout),
                 "--max-time", str(timeout + 5),
                 "-X", "POST", "-d", data, url],
                capture_output=True,
                text=True,
                timeout=timeout + 10,
            )
            return result.returncode, result.stdout
        except Exception:
            return -1, ""


@pytest.fixture(scope="session")
def board_connected(board_config, board_lock, wifi):
    wifi.connect_to_board(board_config)
    return board_config


@pytest.fixture(scope="session")
def funded_board(board_config, board_lock, wifi, http):
    wifi.connect_to_board(board_config)
    time.sleep(3)

    token = _create_cashu_token(wifi.config.mint_url, wifi.config.fund_amount)

    rc, body = http.post(f"{board_config.api_url}/", token)
    assert rc == 0, f"POST to board failed: rc={rc}"
    resp = json.loads(body)
    assert resp.get("kind") == 1022, f"Payment failed: {body}"

    print(f"\n  Wallet funded: {resp.get('allotment', '?')}ms allotted")
    return board_config


def _create_cashu_token(mint_url: str, amount: int) -> str:
    try:
        result = subprocess.run(
            ["cashu", "-h", mint_url, "send", "--legacy", str(amount)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in result.stdout.splitlines():
            if line.startswith("cashuA"):
                return line.strip()
        raise RuntimeError(f"No token in cashu output: {result.stdout}\n{result.stderr}")
    except FileNotFoundError:
        raise RuntimeError("cashu CLI not found. Install: pip install cashu")
