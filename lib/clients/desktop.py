import subprocess
import json
import time
import logging
import os

log = logging.getLogger("tollgate.wifi_client")


class WiFiClient:
    def connect_wifi(self, ssid: str) -> bool:
        raise NotImplementedError

    def is_wifi_connected(self, ssid: str) -> bool:
        raise NotImplementedError

    def ping(self, host: str = "1.1.1.1", count: int = 2, timeout: int = 3,
             interface: str = None) -> bool:
        raise NotImplementedError

    def screenshot(self, path: str) -> bool:
        raise NotImplementedError

    def wake(self) -> bool:
        raise NotImplementedError

    def open_url(self, url: str) -> bool:
        raise NotImplementedError

    def restore_wifi(self) -> bool:
        raise NotImplementedError

    def force_stop(self, package: str):
        pass

    def start_activity(self, action: str = None, data_uri: str = None, component: str = None):
        pass


class DesktopAdapter:
    """Base adapter for desktop WiFi clients (Mac/Linux).

    Provides common no-op implementations for ADB-only operations
    and shared shell routing for curl commands.
    """

    is_desktop = True

    def __init__(self, client: WiFiClient, router_domain: str = ""):
        self._client = client
        self._router_domain = router_domain

    def ui_xml(self) -> str:
        return ""

    def shell(self, cmd: str, timeout: int = 30) -> str:
        if cmd.strip().startswith("curl"):
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
                return r.stdout.strip()
            except (subprocess.SubprocessError, OSError):
                return ""
        if cmd.startswith("dumpsys") or cmd.startswith("am ") or cmd.startswith("svc "):
            return ""
        if cmd.startswith("settings "):
            return ""
        if cmd.startswith("cmd "):
            return ""
        return ""

    def force_stop(self, package: str):
        pass

    def start_activity(self, action: str = None, data_uri: str = None, component: str = None):
        if data_uri:
            self._client.open_url(data_uri)
        elif component and "CaptivePortalLogin" in component:
            if self._router_domain:
                self._client.open_url(f"http://{self._router_domain}:8080/")

    def tap(self, x: int, y: int):
        pass

    def tap_bounds(self, bounds_str: str):
        pass

    def input_text(self, text: str):
        pass

    def press_key(self, key: str):
        pass

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300):
        pass

    def connect_wifi(self, ssid: str) -> bool:
        return self._client.connect_wifi(ssid)

    def restore_wifi(self) -> bool:
        return self._client.restore_wifi()

    def open_url(self, url: str) -> bool:
        return self._client.open_url(url)

    def is_wifi_connected(self, ssid: str) -> bool:
        return self._client.is_wifi_connected(ssid)


class MacWiFiClient(WiFiClient):
    def __init__(self):
        self._iface = self._find_wifi_interface()
        self._original_ssid = self._current_ssid_system_profiler()
        self.mac_address = self._get_mac_address()

    def _find_wifi_interface(self) -> str:
        r = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=10,
        )
        lines = r.stdout.split("\n")
        for i, line in enumerate(lines):
            if "Wi-Fi" in line:
                for j in range(i, min(i + 3, len(lines))):
                    if "Device:" in lines[j]:
                        return lines[j].split(":")[1].strip()
        return "en0"

    def _run(self, cmd: list[str], timeout: int = 15) -> str:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()

    def _get_mac_address(self) -> str:
        r = subprocess.run(
            ["ifconfig", self._iface],
            capture_output=True, text=True, timeout=10,
        )
        for line in r.stdout.split("\n"):
            if "ether" in line:
                parts = line.strip().split()
                if len(parts) >= 2:
                    return parts[1]
        return ""

    def connect_wifi(self, ssid: str) -> bool:
        current = self.is_wifi_connected(ssid)
        if current:
            log.info(f"Already connected to {ssid}")
            return True

        log.info(f"Connecting to {ssid} via networksetup")
        output = self._run(
            ["networksetup", "-setairportnetwork", self._iface, ssid]
        )
        if "could not find" in output.lower() or "error" in output.lower():
            log.warning(f"networksetup error: {output}")
            return False

        for _ in range(10):
            if self.is_wifi_connected(ssid):
                return True
            time.sleep(2)

        log.error(f"Failed to connect to {ssid} within 20s")
        return False

    def restore_wifi(self) -> bool:
        if self._original_ssid:
            log.info(f"Restoring original WiFi: {self._original_ssid}")
            return self.connect_wifi(self._original_ssid)
        return True

    def is_wifi_connected(self, ssid: str) -> bool:
        output = self._run(["networksetup", "-getairportnetwork", self._iface])
        if ssid in output:
            return True
        return ssid in self._current_ssid_system_profiler()

    def _current_ssid_system_profiler(self) -> str:
        try:
            r = subprocess.run(
                ["system_profiler", "SPAirPortDataType", "-json"],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(r.stdout)
            for iface in data.get("SPAirPortDataType", []):
                for ap in iface.get("spairport_airport_interfaces", []):
                    cur = ap.get("spairport_current_network_information", {})
                    name = cur.get("_name", "")
                    if name:
                        return name
        except (json.JSONDecodeError, KeyError, subprocess.SubprocessError, OSError):
            pass
        return ""

    def ping(self, host: str = "1.1.1.1", count: int = 2, timeout: int = 3,
             interface: str = None) -> bool:
        cmd = ["ping", "-c", str(count), "-W", str(timeout)]
        if interface:
            cmd += ["-I", interface]
        cmd.append(host)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * count + 5)
            return "0% packet loss" in r.stdout or f"{count} packets transmitted, {count} received" in r.stdout
        except subprocess.TimeoutExpired:
            return False

    def screenshot(self, path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            r = subprocess.run(
                ["screencapture", "-x", path],
                capture_output=True, timeout=10,
            )
            return os.path.exists(path)
        except Exception:
            return False

    def wake(self) -> bool:
        try:
            subprocess.run(
                ["caffeinate", "-u", "-t", "2"],
                capture_output=True, timeout=5,
            )
            return True
        except Exception:
            return False

    def open_url(self, url: str) -> bool:
        try:
            subprocess.run(["open", url], capture_output=True, timeout=10)
            return True
        except Exception:
            return False

    def screenshot_portal(self, path: str, report_dir: str = None) -> bool:
        return self.screenshot(path)


class MacAdapter(DesktopAdapter):
    """Adapts MacWiFiClient to the ADBDevice interface used by tests and helpers.

    When --client=mac is active, the `adb` fixture returns this adapter
    instead of ADBDevice. Tests use the same `adb.ping()`, `adb.wake_and_unlock()`,
    etc. calls — they route to Mac-native tools underneath.
    """

    def __init__(self, mac: MacWiFiClient, router_domain: str = ""):
        super().__init__(mac, router_domain)
        self._mac = mac
        self.is_mac = True

    def wake_and_unlock(self):
        return self._mac.wake()

    def ping(self, host: str = "1.1.1.1", count: int = 2, timeout: int = 3,
             interface: str = None) -> bool:
        return self._mac.ping(host, count, timeout, interface)

    def screenshot(self, path: str) -> bool:
        return self._mac.screenshot(path)

    def screenshot_portal(self, path: str, report_dir: str = None) -> bool:
        return self._mac.screenshot_portal(path, report_dir)


class LinuxWiFiClient(WiFiClient):
    def __init__(self):
        self._iface = self._find_wifi_interface()
        self._original_ssid = self._current_ssid_nmcli()
        self.mac_address = self._get_mac_address()

    def _find_wifi_interface(self) -> str:
        try:
            r = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE", "dev", "status"],
                capture_output=True, text=True, timeout=10,
            )
            for line in r.stdout.strip().split("\n"):
                if ":wifi" in line:
                    return line.split(":")[0]
        except Exception:
            pass
        return "wlan0"

    def _run(self, cmd: list[str], timeout: int = 15) -> str:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()

    def _get_mac_address(self) -> str:
        try:
            with open(f"/sys/class/net/{self._iface}/address") as f:
                return f.read().strip()
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["ip", "-br", "link", "show", self._iface],
                capture_output=True, text=True, timeout=10,
            )
            parts = r.stdout.strip().split()
            if len(parts) >= 3:
                return parts[2]
        except Exception:
            pass
        return ""

    def _current_ssid_nmcli(self) -> str:
        try:
            r = subprocess.run(
                ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
                capture_output=True, text=True, timeout=10,
            )
            for line in r.stdout.strip().split("\n"):
                if line.startswith("yes:"):
                    return line.split(":", 1)[1]
        except Exception:
            pass
        return ""

    def connect_wifi(self, ssid: str) -> bool:
        if self.is_wifi_connected(ssid):
            log.info(f"Already connected to {ssid}")
            return True

        log.info(f"Connecting to {ssid} via nmcli")

        r = subprocess.run(
            ["nmcli", "-t", "dev", "wifi", "connect", ssid, "--timeout", "20"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return True

        log.info(f"Connect failed, trying con up: {r.stderr.strip()}")
        r2 = subprocess.run(
            ["nmcli", "-t", "con", "up", ssid, "--timeout", "20"],
            capture_output=True, text=True, timeout=30,
        )
        if r2.returncode == 0:
            return True

        log.warning(f"nmcli connect and con up both failed for {ssid}")
        return False

    def restore_wifi(self) -> bool:
        if self._original_ssid:
            log.info(f"Restoring original WiFi: {self._original_ssid}")
            return self.connect_wifi(self._original_ssid)
        return True

    def is_wifi_connected(self, ssid: str) -> bool:
        current = self._current_ssid_nmcli()
        return current == ssid

    def ping(self, host: str = "1.1.1.1", count: int = 2, timeout: int = 3,
             interface: str = None) -> bool:
        cmd = ["ping", "-c", str(count), "-W", str(timeout)]
        if interface:
            cmd += ["-I", interface]
        cmd.append(host)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * count + 5)
            return "0% packet loss" in r.stdout or f"{count} packets transmitted, {count} received" in r.stdout
        except subprocess.TimeoutExpired:
            return False

    def screenshot(self, path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        except Exception:
            pass
        for cmd in [
            ["gnome-screenshot", "-f", path],
            ["scrot", path],
            ["import", "-window", "root", path],
        ]:
            try:
                subprocess.run(cmd, capture_output=True, timeout=10)
                if os.path.exists(path):
                    return True
            except FileNotFoundError:
                continue
            except Exception:
                continue
        return False

    def wake(self) -> bool:
        try:
            subprocess.run(
                ["xdg-screensaver", "reset"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
        return True

    def open_url(self, url: str) -> bool:
        try:
            subprocess.run(
                ["xdg-open", url],
                capture_output=True, timeout=10,
            )
            return True
        except Exception:
            return False

    def screenshot_portal(self, path: str, report_dir: str = None) -> bool:
        return self.screenshot(path)


class LinuxAdapter(DesktopAdapter):
    """Adapts LinuxWiFiClient to the ADBDevice interface used by tests and helpers.

    When --client=linux is active, the `adb` fixture returns this adapter
    instead of ADBDevice. Tests use the same `adb.ping()`, `adb.wake_and_unlock()`,
    etc. calls — they route to Linux-native tools (nmcli, xdg-open) underneath.
    """

    def __init__(self, linux: LinuxWiFiClient, router_domain: str = ""):
        super().__init__(linux, router_domain)
        self._linux = linux
        self.is_linux = True

    def wake_and_unlock(self):
        return self._linux.wake()

    def ping(self, host: str = "1.1.1.1", count: int = 2, timeout: int = 3,
             interface: str = None) -> bool:
        return self._linux.ping(host, count, timeout, interface)

    def screenshot(self, path: str) -> bool:
        return self._linux.screenshot(path)

    def screenshot_portal(self, path: str, report_dir: str = None) -> bool:
        return self._linux.screenshot_portal(path, report_dir)
