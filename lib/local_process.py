"""Local process orchestrator for dry testing.

Starts a mock Cashu mint + Go backend on localhost, yielding a TestTarget
that tests interact with. No SHC, no VMs, no real Lightning.

Usage:
    from lib.local_process import LocalProcessTarget

    target = LocalProcessTarget()
    target.start()
    # ... run tests against target.backend_url ...
    target.stop()

Or as a pytest fixture (in conftest.py):
    @pytest.fixture(scope="session")
    def test_target():
        t = LocalProcessTarget()
        t.start()
        yield t.test_target
        t.stop()
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PRTA_ROOT = REPO_ROOT
BACKEND_SRC = REPO_ROOT.parent / "tollgate-module-basic-go" / "src"

MINT_PORT = 3338
BACKEND_PORT = 2121
MOCK_MAC = "1a:2b:3c:4d:5e:6f"


def detect_loopback() -> str:
    """Return a working loopback address ('127.0.0.1' or '[::1]').

    Tries IPv4 first. Falls back to IPv6 if IPv4 loopback is broken.
    See docs/known-issues.md#ipv4-loopback for details.
    """
    for addr in ["127.0.0.1", "[::1]"]:
        host = addr.strip("[]")
        try:
            s = socket.socket(
                socket.AF_INET if ":" not in host else socket.AF_INET6,
                socket.SOCK_STREAM,
            )
            s.settimeout(1)
            s.connect((host, 1))
        except ConnectionRefusedError:
            return addr  # Connection refused = loopback works, just no server on port 1
        except (socket.timeout, OSError):
            continue
        finally:
            s.close()
    return "127.0.0.1"  # Default; will fail clearly if IPv4 is broken


LOOPBACK = detect_loopback()
MINT_HOST = LOOPBACK


@dataclass
class TestTarget:
    backend_url: str
    mint_url: str
    frontend_url: str
    mac_address: str
    _orchestrator: LocalProcessTarget | None = None

    def exec(self, cmd: str) -> str:
        if self._orchestrator:
            return self._orchestrator.exec(cmd)
        raise NotImplementedError

    def logs(self) -> str:
        if self._orchestrator:
            return self._orchestrator.backend_logs()
        raise NotImplementedError

    def create_token(self, amount: int = 1) -> str:
        import urllib.request
        url = f"http://{MINT_HOST}:{MINT_PORT}/test/create-token?amount={amount}"
        r = urllib.request.urlopen(url, timeout=5)
        return json.loads(r.read())["token"]


class LocalProcessTarget:
    """Manages mock mint + Go backend as local processes."""

    def __init__(
        self,
        backend_binary: str = "/tmp/tollgate-test",
        mint_port: int = MINT_PORT,
        backend_port: int = BACKEND_PORT,
    ):
        self.backend_binary = backend_binary
        self.mint_port = mint_port
        self.backend_port = backend_port
        self.mint_proc: subprocess.Popen | None = None
        self.backend_proc: subprocess.Popen | None = None
        self.config_dir: str | None = None
        self.stub_dir: str | None = None
        self._old_path: str | None = None

    def start(self) -> TestTarget:
        self._build_backend()
        self._write_ndsctl_stub()
        self._write_config()
        self._start_mint()
        self._start_backend()
        self._wait_for_health()
        return self.test_target

    @property
    def test_target(self) -> TestTarget:
        return TestTarget(
            backend_url=f"http://{MINT_HOST}:{self.backend_port}",
            mint_url=f"http://{MINT_HOST}:{self.mint_port}",
            frontend_url="",
            mac_address=MOCK_MAC,
            _orchestrator=self,
        )

    def stop(self):
        for proc in [self.backend_proc, self.mint_proc]:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if self._old_path is not None:
            os.environ["PATH"] = self._old_path
        if self.config_dir and os.path.exists(self.config_dir):
            import shutil
            shutil.rmtree(self.config_dir, ignore_errors=True)

    def exec(self, cmd: str) -> str:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return r.stdout

    def backend_logs(self) -> str:
        if self.backend_proc:
            return self.backend_proc.stdout.read() if self.backend_proc.stdout else ""
        return ""

    def _build_backend(self):
        if os.path.exists(self.backend_binary):
            return
        subprocess.run(
            ["go", "build", "-o", self.backend_binary, "."],
            cwd=str(BACKEND_SRC),
            check=True,
            timeout=120,
        )

    def _write_ndsctl_stub(self):
        self.stub_dir = tempfile.mkdtemp(prefix="tollgate-stubs-")
        stub = Path(self.stub_dir) / "ndsctl"
        stub.write_text(f"""#!/bin/bash
if [ "$1" = "auth" ]; then echo "Auth ok"; exit 0; fi
if [ "$1" = "deauth" ]; then echo "Deauth ok"; exit 0; fi
if [ "$1" = "json" ]; then echo '{{"id":1,"state":"authenticated","downloaded":1048576,"uploaded":524288}}'; exit 0; fi
echo "OK"; exit 0
""")
        stub.chmod(0o755)
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.stub_dir}:{self._old_path}"

    def _write_config(self):
        self.config_dir = tempfile.mkdtemp(prefix="tollgate-test-config-")
        config = {
            "config_version": "1",
            "log_level": "info",
            "accepted_mints": [{
                "url": f"http://{MINT_HOST}:{self.mint_port}",
                "min_balance": 0,
                "balance_tolerance_percent": 0,
                "payout_interval_seconds": 999999,
                "min_payout_amount": 999999,
                "price_per_step": 1,
                "price_unit": "sat",
                "purchase_min_steps": 1,
            }],
            "step_size": 22020096,
            "margin": 0.1,
            "metric": "bytes",
            "show_setup": False,
            "reseller_mode": False,
            "redirect_url": "",
            "auth_delay_seconds": 0,
            "upstream_detector": {"enabled": False},
            "upstream_session_manager": {"enabled": False},
            "upstream_wifi": {"enabled": False},
        }
        (Path(self.config_dir) / "config.json").write_text(json.dumps(config, indent=2))

        dhcp_leases = Path("/tmp/dhcp.leases")
        dhcp_leases.write_text(
            f"1700000000 {MOCK_MAC} ::1 test-client *\n"
        )

    def _start_mint(self):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self.mint_proc = subprocess.Popen(
            [sys.executable, str(PRTA_ROOT / "lib" / "mock_mint.py"),
             "--port", str(self.mint_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PRTA_ROOT),
            env=env,
        )

    def _start_backend(self):
        env = os.environ.copy()
        env["TOLLGATE_TEST_CONFIG_DIR"] = self.config_dir
        if self.stub_dir:
            env["PATH"] = f"{self.stub_dir}:{env.get('PATH', '')}"
        self.backend_proc = subprocess.Popen(
            [self.backend_binary],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )

    def _wait_for_health(self, timeout: int = 30):
        import urllib.request
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = urllib.request.urlopen(
                    f"http://{MINT_HOST}:{self.mint_port}/v1/info", timeout=2
                )
                if r.status == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise TimeoutError(f"Mock mint not ready after {timeout}s")

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = urllib.request.urlopen(
                    f"http://{MINT_HOST}:{self.backend_port}/", timeout=2
                )
                data = json.loads(r.read())
                if data.get("kind") == 10021:
                    return
            except Exception:
                pass
            time.sleep(1)
        else:
            raise TimeoutError(f"Backend not ready after {timeout}s")
