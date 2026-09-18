"""Cuttlefish client — drives a Cuttlefish Android VM on a remote host via SSH + adb.

The Cuttlefish phone runs on ai-legion (or any Linux host). This adapter
SSHes to the host and invokes adb there, wrapping the output to match
the ADBDevice interface so existing phone tests work unchanged.

Environment variables:
    TOLLGATE_CF_HOST     SSH host running Cuttlefish (default: ai-legion)
    TOLLGATE_CF_SERIAL   adb serial on the remote host (default: 0.0.0.0:6520)
"""

import os
import re
import subprocess
import time
import logging

log = logging.getLogger("tollgate.cuttlefish")


class CuttlefishClient:
    """ADB-compatible client for a Cuttlefish Android VM on a remote host."""

    is_desktop = False
    is_container = False
    is_cuttlefish = True

    def __init__(self, host: str | None = None, serial: str | None = None):
        self.host = host or os.environ.get("TOLLGATE_CF_HOST", "ai-legion")
        self.serial = serial or os.environ.get("TOLLGATE_CF_SERIAL", "0.0.0.0:6520")
        self._ssh = ["ssh", "-o", "ControlPath=none",
                     "-o", "ClearAllForwardings=yes",
                     "-o", "BatchMode=yes",
                     "-o", "ConnectTimeout=5", self.host]
        self._adb = f"~/cf/bin/adb -s {self.serial}"

    def _exec(self, cmd: str, timeout: int = 30) -> str:
        """Run a command via SSH on the Cuttlefish host."""
        remote = f"{self._adb} shell '{cmd}'"
        try:
            r = subprocess.run(
                self._ssh + [remote],
                capture_output=True, text=True, timeout=timeout,
            )
            return r.stdout.strip()
        except (subprocess.TimeoutExpired, Exception) as e:
            log.warning("cuttlefish exec failed: %s", e)
            return ""

    def _exec_out(self, cmd: str, timeout: int = 30) -> bytes:
        """Run a command and return raw bytes (for screencap)."""
        remote = f"{self._adb} exec-out '{cmd}'"
        try:
            r = subprocess.run(
                self._ssh + [remote],
                capture_output=True, timeout=timeout,
            )
            return r.stdout
        except (subprocess.TimeoutExpired, Exception) as e:
            log.warning("cuttlefish exec-out failed: %s", e)
            return b""

    def _adb_cmd(self, cmd: str, timeout: int = 30) -> str:
        """Run a raw adb subcommand (not shell)."""
        remote = f"{self._adb} {cmd}"
        try:
            r = subprocess.run(
                self._ssh + [remote],
                capture_output=True, text=True, timeout=timeout,
            )
            return r.stdout.strip()
        except (subprocess.TimeoutExpired, Exception) as e:
            log.warning("cuttlefish adb cmd failed: %s", e)
            return ""

    # ── ADBDevice interface ─────────────────────────────────────────

    def wifi_mac(self) -> str:
        return self._exec("ip addr show wlan0 2>/dev/null | grep 'link/ether' | awk '{print $2}'")

    def wifi_ip(self) -> str:
        return self._exec("ip -f inet addr show wlan0 2>/dev/null | grep inet | awk '{print $2}' | cut -d/ -f1")

    def shell(self, cmd: str, timeout: int = 30) -> str:
        return self._exec(cmd, timeout)

    def screenshot(self, path: str) -> bool:
        data = self._exec_out("screencap -p", timeout=15)
        if data and len(data) > 1000:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            return True
        log.warning("cuttlefish screenshot empty")
        return False

    def screenshot_portal(self, path: str, report_dir: str = None) -> bool:
        self.screenshot(path)
        xml = self.ui_xml()
        keywords = ["tollgate", "captive.*portal", "portal_ready", "token_typing",
                     "countdown", "data-sm=", "usage.*dashboard", "authed",
                     "net4sats", "purchase.*internet"]
        pattern = "|".join(keywords)
        if re.search(pattern, xml, re.IGNORECASE):
            if report_dir:
                os.makedirs(report_dir, exist_ok=True)
                import shutil
                shutil.copy2(path, os.path.join(report_dir, os.path.basename(path)))
            return True
        return False

    def ui_xml(self) -> str:
        return self._exec(
            "uiautomator dump /sdcard/ui.xml 2>&1 && cat /sdcard/ui.xml", timeout=15)

    def tap(self, x: int, y: int):
        self._exec(f"input tap {x} {y}")

    def text(self, s: str):
        escaped = s.replace("'", "\\'").replace(" ", "%s")
        self._exec(f"input text '{escaped}'")

    def key(self, keycode: str):
        self._exec(f"input keyevent {keycode}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300):
        self._exec(f"input swipe {x1} {y1} {x2} {y2} {ms}")

    def start_activity(self, action: str = "", component: str = "", data: str = ""):
        cmd = "am start"
        if action:
            cmd += f" -a {action}"
        if component:
            cmd += f" -n {component}"
        if data:
            cmd += f" -d {data}"
        self._exec(cmd)

    def force_stop(self, package: str):
        self._exec(f"am force-stop {package}")

    def is_package_installed(self, package: str) -> bool:
        out = self._exec(f"pm list packages {package}")
        return package in out

    def has_internet(self, host: str = "1.1.1.1") -> bool:
        out = self._exec(f"ping -c 1 -W 3 {host} 2>/dev/null | grep '1 received'")
        return "1 received" in out

    def boot_completed(self) -> bool:
        out = self._exec("getprop sys.boot_completed")
        return out.strip() == "1"

    def screen_record_start(self, path: str = "/sdcard/film.mp4", bitrate: int = 6000000):
        """Start screen recording (non-blocking)."""
        remote = (f"nohup {self._adb} shell "
                  f"'screenrecord --bit-rate {bitrate} --time-limit 180 {path}' "
                  f">/dev/null 2>&1 &")
        subprocess.run(self._ssh + [remote], capture_output=True, timeout=10)

    def screen_record_stop(self, local_path: str):
        """Stop recording and pull the file."""
        self._exec("pkill -l INT screenrecord", timeout=5)
        time.sleep(2)
        return self._adb_pull("/sdcard/film.mp4", local_path)

    def _adb_pull(self, remote_path: str, local_path: str) -> bool:
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        remote = f"{self._adb} pull {remote_path} /tmp/cf_pull"
        subprocess.run(self._ssh + [remote], capture_output=True, timeout=30)
        # Then scp from remote
        scp = subprocess.run(
            ["scp", "-q", "-o", "ControlPath=none", "-o", "ClearAllForwardings=yes",
             "-o", "BatchMode=yes",
             f"{self.host}:/tmp/cf_pull", local_path],
            capture_output=True, timeout=30,
        )
        return os.path.exists(local_path)

    def disconnect_wifi(self):
        self._exec("cmd wifi set-wifi-enabled disabled")

    def connect_wifi(self, ssid: str, password: str = ""):
        self._exec("cmd wifi set-wifi-enabled enabled")
        time.sleep(3)
        if password:
            self._exec(f"cmd wifi connect-network {ssid} wpa2 {password}")
        else:
            self._exec(f"cmd wifi connect-network {ssid} open")

    def get_wifi_ssid(self) -> str:
        out = self._exec("cmd wifi status | grep -o 'SSID: [^,]*' | head -1")
        return out.replace("SSID: ", "").strip('"') if out else ""

    def dismiss_keyguard(self):
        self._exec("input keyevent KEYCODE_WAKEUP")
        self._exec("wm dismiss-keyguard")

    def open_url(self, url: str):
        self._exec(f"am start -a android.intent.action.VIEW -d '{url}'")
