"""ESP32 board management: lock, flash, SPIFFS config, reset, HTTP API access.

Provides the ESP32Board class used by pytest fixtures to provision boards
with proper mutex locking, firmware flashing, and SPIFFS configuration.
"""

import json
import logging
import os
import platform
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("esp32_board")

_STALE_THRESHOLD = timedelta(hours=2)

_DEFAULT_LOCK_DIR = "/home/c03rad0r/physical-router-test-automation/locks"
_DEFAULT_SPIFFS_OFFSET = 0x410000
_DEFAULT_SPIFFS_SIZE = 0xF0000
_DEFAULT_BAUD = 460800
_DEFAULT_WIFI_IFACE = "wlp59s0"


def _session_id():
    return f"{os.getenv('USER', 'unknown')}@{platform.node()}"


class BoardLockError(RuntimeError):
    pass


class ESP32Board:
    """ESP32 board: advisory lock, firmware flash, SPIFFS config, reset, HTTP.

    Usage::

        board = ESP32Board("a", "/dev/ttyACM0", nsec="...", ssid="...", ip="10.x.x.1",
                           worktree="/home/c03rad0r/esp32-tollgate")
        board.acquire_lock(phase="pytest")
        try:
            board.flash()
            board.write_config(wifi_ssid="...", wifi_password="...", mint_url="...")
            board.wait_for_boot(timeout=30)
            code, body = board.api_get("/usage")
        finally:
            board.release_lock()

    Context manager::

        with ESP32Board(...) as board:
            board.flash()
            ...

    Lock files are written to LOCK_DIR/board-{id}.lock, same format as the
    Makefile's _acquire_lock macro, so pytest and make are mutually exclusive.
    """

    def __init__(
        self,
        board_id: str,
        port: str,
        nsec: str,
        ssid: str,
        ip: str,
        worktree: str,
        lock_dir: str = _DEFAULT_LOCK_DIR,
        idf_path: str | None = None,
        baud: int = _DEFAULT_BAUD,
        wifi_iface: str = _DEFAULT_WIFI_IFACE,
    ):
        self.board_id = board_id
        self.port = port
        self.nsec = nsec
        self.ssid = ssid
        self.ip = ip
        self.worktree = worktree
        self.lock_dir = lock_dir
        self.idf_path = idf_path or os.environ.get("IDF_PATH", os.path.expanduser("~/esp/esp-idf"))
        self.baud = baud
        self.wifi_iface = wifi_iface
        self._lock_held = False

    @property
    def lock_path(self) -> str:
        return os.path.join(self.lock_dir, f"board-{self.board_id}.lock")

    @property
    def spiffsgen(self) -> str:
        return os.path.join(self.idf_path, "components", "spiffs", "spiffsgen.py")

    # ── Lock ───────────────────────────────────────────────────────────

    def acquire_lock(self, phase: str = "pytest") -> None:
        if self._lock_held:
            return
        existing = self._read_lock()
        if existing is not None:
            locked = existing.get("locked", "false").lower() == "true"
            if locked:
                ts_str = existing.get("timestamp", "")
                stale = False
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) - ts > _STALE_THRESHOLD:
                        stale = True
                except (ValueError, TypeError):
                    stale = True
                if stale:
                    log.warning("Overwriting stale lock on board %s (held by %s since %s)",
                                self.board_id, existing.get("session", "?"), ts_str)
                else:
                    raise BoardLockError(
                        f"Board {self.board_id} locked by {existing.get('session', '?')} "
                        f"since {ts_str} (phase: {existing.get('phase', '?')}). "
                        f"Use force_release() or wait."
                    )
        content = (
            f"locked: true\n"
            f"board: board-{self.board_id}\n"
            f"branch: pytest\n"
            f"worktree: {self.worktree}\n"
            f"session: {_session_id()}\n"
            f"timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"phase: {phase}\n"
        )
        self._atomic_write(self.lock_path, content)
        self._lock_held = True
        log.info("Acquired lock on board %s (phase=%s)", self.board_id, phase)

    def release_lock(self) -> None:
        if not self._lock_held:
            return
        try:
            os.remove(self.lock_path)
            log.info("Released lock on board %s", self.board_id)
        except FileNotFoundError:
            pass
        self._lock_held = False

    def force_release(self) -> None:
        try:
            os.remove(self.lock_path)
            log.warning("Force-released lock on board %s", self.board_id)
        except FileNotFoundError:
            pass
        self._lock_held = False

    def is_locked(self) -> bool:
        data = self._read_lock()
        return data is not None and data.get("locked", "false").lower() == "true"

    def _read_lock(self) -> dict | None:
        try:
            with open(self.lock_path) as f:
                text = f.read().strip()
        except FileNotFoundError:
            return None
        data = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            key, _, val = line.partition(":")
            data[key.strip()] = val.strip()
        return data

    @staticmethod
    def _atomic_write(path: str, content: str) -> None:
        parent = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".board-lock-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    # ── Firmware ───────────────────────────────────────────────────────

    def build(self) -> subprocess.CompletedProcess:
        log.info("Building firmware (worktree=%s)", self.worktree)
        return subprocess.run(
            ["bash", "-c", f". {self.idf_path}/export.sh && idf.py build"],
            cwd=self.worktree,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def flash(self, baud: int | None = None) -> subprocess.CompletedProcess:
        baud = baud or self.baud
        log.info("Flashing firmware to %s (port=%s, baud=%d)", self.board_id, self.port, baud)
        return subprocess.run(
            ["bash", "-c",
             f". {self.idf_path}/export.sh && idf.py -p {self.port} -b {baud} flash"],
            cwd=self.worktree,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )

    # ── SPIFFS Config ──────────────────────────────────────────────────

    def write_config(
        self,
        wifi_ssid: str = "",
        wifi_password: str = "",
        mint_url: str = "https://testnut-nutshell.mints.orangesync.tech",
        price_per_step: int = 21,
        step_size_ms: int = 60000,
        nostr_geohash: str = "u281w0dfz",
        nostr_relays: list[str] | None = None,
        nostr_publish_interval_s: int = 21600,
        nostr_seed_relays: list[str] | None = None,
        nostr_sync_interval_s: int = 1800,
        nostr_fallback_sync_interval_s: int = 21600,
        cvm_enabled: bool = True,
        extra: dict | None = None,
    ) -> None:
        if nostr_relays is None:
            nostr_relays = ["wss://relay.damus.io", "wss://nos.lol"]
        if nostr_seed_relays is None:
            nostr_seed_relays = [
                "wss://relay.orangesync.tech",
                "wss://relay.damus.io",
                "wss://nos.lol",
                "wss://relay.nostr.band",
                "wss://relay.anzenkodo.workers.dev",
                "wss://nostr.koning-degraaf.nl",
                "wss://knostr.neutrine.com",
                "wss://nostr.einundzwanzig.space",
            ]
        networks = []
        if wifi_ssid:
            networks.append({"ssid": wifi_ssid, "password": wifi_password})
        config = {
            "nsec": self.nsec,
            "wifi_networks": networks,
            "ap_password": "",
            "mint_url": mint_url,
            "price_per_step": price_per_step,
            "step_size_ms": step_size_ms,
            "client_enabled": False,
            "nostr_geohash": nostr_geohash,
            "nostr_relays": nostr_relays,
            "nostr_publish_interval_s": nostr_publish_interval_s,
            "nostr_seed_relays": nostr_seed_relays,
            "nostr_sync_interval_s": nostr_sync_interval_s,
            "nostr_fallback_sync_interval_s": nostr_fallback_sync_interval_s,
            "cvm_enabled": cvm_enabled,
        }
        if extra:
            config.update(extra)
        self._write_spiffs(config)

    def write_config_ap_only(self, **kwargs) -> None:
        kwargs.setdefault("wifi_ssid", "")
        kwargs.setdefault("wifi_password", "")
        self.write_config(wifi_ssid="", wifi_password="", **kwargs)

    def _write_spiffs(self, config: dict) -> None:
        tmpdir = tempfile.mkdtemp(prefix=f"esp32-spiffs-{self.board_id}-")
        try:
            config_path = os.path.join(tmpdir, "config.json")
            with open(config_path, "w") as f:
                json.dump(config, f)

            spiffs_bin = os.path.join(tmpdir, "spiffs.bin")
            log.info("Generating SPIFFS image for board %s", self.board_id)
            subprocess.run(
                [
                    "python3", self.spiffsgen,
                    "--page-size", "256",
                    "--obj-name-len", "32",
                    "--use-magic", "--use-magic-len",
                    hex(_DEFAULT_SPIFFS_SIZE),
                    tmpdir,
                    spiffs_bin,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )

            log.info("Writing SPIFFS to board %s at offset 0x%x", self.board_id, _DEFAULT_SPIFFS_OFFSET)
            subprocess.run(
                [
                    "python3", "-m", "esptool",
                    "--port", self.port,
                    "--baud", str(self.baud),
                    "write_flash",
                    hex(_DEFAULT_SPIFFS_OFFSET),
                    spiffs_bin,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.reset()
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ── Reset ──────────────────────────────────────────────────────────

    def reset(self) -> subprocess.CompletedProcess:
        log.info("Resetting board %s", self.board_id)
        return subprocess.run(
            ["python3", "-m", "esptool", "--port", self.port, "run"],
            capture_output=True,
            text=True,
            timeout=10,
        )

    # ── Wait ───────────────────────────────────────────────────────────

    def wait_for_boot(self, timeout: int = 30) -> None:
        log.info("Waiting for board %s to boot (timeout=%ds)", self.board_id, timeout)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                code, _ = self.api_get("/usage")
                if code == 200:
                    log.info("Board %s booted successfully", self.board_id)
                    return
            except Exception:
                pass
            time.sleep(1)
        raise TimeoutError(f"Board {self.board_id} did not boot within {timeout}s")

    def wait_for_ap(self, timeout: int = 30) -> None:
        log.info("Waiting for board %s AP (timeout=%ds)", self.board_id, timeout)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "2", self.ip],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                log.info("Board %s AP reachable", self.board_id)
                return
            time.sleep(1)
        raise TimeoutError(f"Board {self.board_id} AP not reachable within {timeout}s")

    # ── HTTP API (port 2121) ───────────────────────────────────────────

    def api_get(self, path: str = "/") -> tuple[int, str]:
        return self._curl(f"http://{self.ip}:2121{path}")

    def api_post(self, path: str, data: str = "", content_type: str | None = None) -> tuple[int, str]:
        cmd = ["curl", "-s", "-w", "\\n%{http_code}", "--connect-timeout", "10", "-X", "POST"]
        if data:
            cmd += ["-d", data]
        if content_type:
            cmd += ["-H", f"Content-Type: {content_type}"]
        cmd.append(f"http://{self.ip}:2121{path}")
        return self._run_curl(cmd)

    def api_post_raw(self, path: str, data: str, content_type: str = "application/cashu") -> tuple[int, str]:
        cmd = [
            "curl", "-s", "-w", "\\n%{http_code}",
            "--connect-timeout", "20",
            "-X", "POST",
            "--data-binary", data,
            "-H", f"Content-Type: {content_type}",
            f"http://{self.ip}:2121{path}",
        ]
        return self._run_curl(cmd)

    # ── Captive Portal (port 80) ───────────────────────────────────────

    def http_get(self, path: str = "/") -> tuple[int, str]:
        return self._curl(f"http://{self.ip}{path}")

    def http_post(self, path: str, data: str = "") -> tuple[int, str]:
        cmd = ["curl", "-s", "-w", "\\n%{http_code}", "--connect-timeout", "10", "-X", "POST"]
        if data:
            cmd += ["-d", data]
        cmd.append(f"http://{self.ip}{path}")
        return self._run_curl(cmd)

    # ── DNS ────────────────────────────────────────────────────────────

    def dns_resolves_to_self(self, domain: str) -> bool:
        try:
            result = subprocess.run(
                ["dig", "+short", "+timeout=5", domain, f"@{self.ip}"],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip() == self.ip
        except Exception:
            return False

    def dns_resolves(self, domain: str, timeout: int = 5) -> bool:
        try:
            result = subprocess.run(
                ["dig", "+short", f"+timeout={timeout}", "+tries=1", domain],
                capture_output=True, text=True, timeout=timeout + 5,
            )
            out = result.stdout.strip()
            return len(out) > 0 and "NXDOMAIN" not in out
        except Exception:
            return False

    # ── Connectivity ───────────────────────────────────────────────────

    def can_ping(self, host: str = "8.8.8.8") -> bool:
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "3", "-I", self.wifi_iface, host],
                capture_output=True, text=True, timeout=10,
            )
            return "100% packet loss" not in result.stdout
        except Exception:
            return False

    def can_http(self, url: str = "http://1.1.1.1/") -> bool:
        try:
            result = subprocess.run(
                ["curl", "-s", "--connect-timeout", "5", "-m", "5",
                 "--interface", self.wifi_iface, url],
                capture_output=True, text=True, timeout=10,
            )
            return len(result.stdout) > 0
        except Exception:
            return False

    # ── WiFi ───────────────────────────────────────────────────────────

    def connect_wifi(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["nmcli", "device", "wifi", "connect", self.ssid, "ifname", self.wifi_iface],
            capture_output=True, text=True, timeout=30,
        )

    def disconnect_wifi(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["nmcli", "device", "disconnect", self.wifi_iface],
            capture_output=True, text=True, timeout=10,
        )

    # ── NIP-11 ─────────────────────────────────────────────────────────

    def nip11(self) -> tuple[int, dict | None]:
        cmd = [
            "curl", "-s", "-w", "\\n%{http_code}",
            "--connect-timeout", "5",
            "-H", "Accept: application/nostr+json",
            f"http://{self.ip}:4869/",
        ]
        code, body = self._run_curl(cmd)
        if code != 200 or not body:
            return code, None
        try:
            return code, json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return code, None

    # ── Context manager ────────────────────────────────────────────────

    def __enter__(self) -> "ESP32Board":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._lock_held:
            self.release_lock()
        return None

    # ── Internals ──────────────────────────────────────────────────────

    def _curl(self, url: str, connect_timeout: int = 10) -> tuple[int, str]:
        cmd = [
            "curl", "-s", "-w", "\\n%{http_code}",
            "--connect-timeout", str(connect_timeout),
            url,
        ]
        return self._run_curl(cmd)

    @staticmethod
    def _run_curl(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            output = result.stdout
            lines = output.rsplit("\n", 1)
            if len(lines) == 2 and lines[1].strip().isdigit():
                return int(lines[1].strip()), lines[0]
            return 0, output
        except subprocess.TimeoutExpired:
            return 0, ""
        except Exception:
            return 0, ""
