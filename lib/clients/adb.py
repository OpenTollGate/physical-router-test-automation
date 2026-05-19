import subprocess
import os
import re
import time
import logging

log = logging.getLogger("tollgate.adb")


class ADBDevice:
    def __init__(self, serial: str | None = None, pin: str | None = None):
        self.serial = serial
        self.pin = pin
        self._base = ["adb"]
        if serial:
            self._base = ["adb", "-s", serial]

    def wifi_mac(self) -> str:
        return self.shell("ip addr show wlan0 2>/dev/null | grep 'link/ether' | awk '{print $2}'")

    def wifi_ip(self) -> str:
        return self.shell("ip -f inet addr show wlan0 2>/dev/null | grep inet | awk '{print $2}' | cut -d/ -f1")

    def shell(self, cmd: str, timeout: int = 30) -> str:
        r = subprocess.run(
            self._base + ["shell", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout.strip()

    def screenshot(self, path: str) -> bool:
        try:
            subprocess.run(
                self._base + ["shell", "screencap", "-p", "/sdcard/tg-test.png"],
                capture_output=True, timeout=15,
            )
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            subprocess.run(
                self._base + ["pull", "/sdcard/tg-test.png", path],
                capture_output=True, timeout=15,
            )
            if os.path.exists(path):
                return True
            log.warning("screenshot pull failed — file not found at %s", path)
            return False
        except Exception as exc:
            log.warning("screenshot failed: %s", exc)
            return False

    def screenshot_portal(self, path: str, report_dir: str = None) -> bool:
        raw_path = path
        self.screenshot(raw_path)
        xml = self.ui_xml()
        portal_keywords = [
            "tollgate", "captive.*portal", "portal_ready", "token_typing",
            "countdown", "data-sm=", "usage.*dashboard", "authed",
        ]
        pattern = "|".join(portal_keywords)
        if re.search(pattern, xml, re.IGNORECASE):
            if report_dir:
                os.makedirs(report_dir, exist_ok=True)
                report_path = os.path.join(report_dir, os.path.basename(path))
                try:
                    with open(raw_path, "rb") as src, open(report_path, "wb") as dst:
                        dst.write(src.read())
                    return True
                except Exception:
                    pass
        return False

    def ui_xml(self) -> str:
        r = subprocess.run(
            self._base + ["shell",
                          "uiautomator dump /sdcard/ui.xml 2>&1 && cat /sdcard/ui.xml"],
            capture_output=True, text=True, timeout=15,
        )
        return r.stdout.strip()

    def tap(self, x: int, y: int):
        subprocess.run(
            self._base + ["shell", "input", "tap", str(x), str(y)],
            capture_output=True, timeout=10,
        )

    def tap_bounds(self, bounds_str: str):
        nums = re.findall(r"\d+", bounds_str)
        if len(nums) >= 4:
            x1, y1, x2, y2 = [int(n) for n in nums[:4]]
            self.tap((x1 + x2) // 2, (y1 + y2) // 2)

    def input_text(self, text: str):
        subprocess.run(
            self._base + ["shell", "input", "text", text],
            capture_output=True, timeout=15,
        )

    def press_key(self, key: str):
        subprocess.run(
            self._base + ["shell", "input", "keyevent", key],
            capture_output=True, timeout=10,
        )

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300):
        subprocess.run(
            self._base + ["shell", "input", "swipe",
                          str(x1), str(y1), str(x2), str(y2), str(duration)],
            capture_output=True, timeout=10,
        )

    def wake_and_unlock(self):
        self.press_key("KEYCODE_WAKEUP")
        time.sleep(0.3)
        self.swipe(540, 2000, 540, 500)
        time.sleep(1)
        self.swipe(540, 2000, 540, 500)
        time.sleep(0.5)
        if self.pin and self.is_screen_locked():
            self.input_text(self.pin)
            time.sleep(0.3)
            self.press_key("KEYCODE_ENTER")
            time.sleep(1)
        return True

    def is_screen_locked(self) -> bool:
        out = self.shell("dumpsys window policy 2>/dev/null | grep 'showing=' | head -1")
        return "showing=true" in out

    def ping(self, host: str = "1.1.1.1", count: int = 2, timeout: int = 3,
             interface: str = None) -> bool:
        iface_opt = f" -I {interface}" if interface else ""
        out = self.shell(f"ping{iface_opt} -c {count} -W {timeout} {host}")
        result = "0% packet loss" in out
        if not result:
            log.debug("ping %s failed: %s", host, out[:100])
        return result

    def force_stop(self, package: str):
        self.shell(f"am force-stop {package}")

    def start_activity(self, action: str = None, data_uri: str = None, component: str = None):
        cmd = "am start"
        if action:
            cmd += f" -a {action}"
        if data_uri:
            cmd += f" -d '{data_uri}'"
        if component:
            cmd += f" -n {component}"
        self.shell(cmd)

    def is_wifi_connected(self, ssid: str) -> bool:
        out = self.shell("dumpsys wifi 2>/dev/null | grep 'mWifiInfo'")
        return ssid in out

    def open_url(self, url: str):
        """Open a URL in the phone's default browser via intent resolution."""
        self.shell(f"am start -a android.intent.action.VIEW -d '{url}'")

    def open_portal(self, host: str, port: int = 80) -> bool:
        """Open the TollGate captive portal in the phone's browser."""
        url = f"http://{host}:{port}/"
        log.info(f"Opening portal at {url}")
        self.open_url(url)
        time.sleep(3)
        return True

    def force_stop_browser(self):
        """Kill browser apps to clean up stale tabs between tests."""
        self.shell("am force-stop com.sec.android.app.sbrowser")
        self.shell("am force-stop com.android.chrome")
