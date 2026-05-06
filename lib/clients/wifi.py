import re
import time
import logging

log = logging.getLogger("tollgate.wifi")


def _is_desktop_client(adb):
    return getattr(adb, "is_desktop", False)


class WiFi:
    def __init__(self, adb, router, ssid: str):
        self.adb = adb
        self.router = router
        self.ssid_prefix = ssid.split("-")[0] if "-" in ssid else ssid
        self.ssid = self._resolve_ssid(ssid)

    def _resolve_ssid(self, fallback: str) -> str:
        try:
            out = self.router.ssh("iwinfo 2>/dev/null | grep ESSID | grep -v private")
            for line in out.strip().split("\n"):
                m = re.search(r'ESSID:\s*"([^"]+)"', line)
                if m and m.group(1).startswith(self.ssid_prefix + "-"):
                    log.info(f"Auto-detected SSID: {m.group(1)}")
                    return m.group(1)
        except Exception:
            pass
        try:
            out = self.router.ssh("uci show wireless 2>/dev/null | grep '\\.ssid=' | grep -v private")
            for line in out.strip().split("\n"):
                _, _, val = line.partition("=")
                val = val.strip("'\"")
                if val.startswith(self.ssid_prefix + "-"):
                    log.info(f"Auto-detected SSID from config: {val}")
                    return val
        except Exception:
            pass
        return fallback

    def _tap_ssid(self, xml: str, ssid: str) -> bool:
        if _is_desktop_client(self.adb):
            return False
        node_match = re.search(f'<node[^>]*"{re.escape(ssid)}"[^>]*>', xml)
        if not node_match:
            return False
        node = node_match.group(0)
        bounds_match = re.search(r'bounds="\[([^]]*)\]\[([^]]*)\]"', node)
        if not bounds_match:
            return False
        nums1 = re.findall(r"\d+", bounds_match.group(1))
        nums2 = re.findall(r"\d+", bounds_match.group(2))
        x1, y1 = int(nums1[0]), int(nums1[1])
        x2, y2 = int(nums2[0]), int(nums2[1])
        self.adb.tap((x1 + x2) // 2, (y1 + y2) // 2)
        return True

    def open_wifi_settings(self):
        if _is_desktop_client(self.adb):
            return
        self.adb.shell("am start -a android.settings.WIFI_SETTINGS")
        time.sleep(3)
        xml = self.adb.ui_xml()
        wifi_indicators = ["Available networks", "Current network", "Wi-Fi.*On",
                           "Turn on Wi-Fi", self.ssid]
        if not any(re.search(p, xml) for p in wifi_indicators):
            log.info("WIFI_SETTINGS opened wrong page, navigating via Settings")
            self.adb.press_key("KEYCODE_BACK")
            time.sleep(1)
            self.adb.press_key("KEYCODE_BACK")
            time.sleep(1)
            self.adb.shell("am start -n com.android.settings/.Settings")
            time.sleep(3)
            xml = self.adb.ui_xml()
            conn_match = re.search(r'text="Connections"[^>]*bounds="\[([^]]*)\]\[([^]]*)\]"', xml)
            if conn_match:
                full = re.search(r'bounds="\[([^]]*)\]\[([^]]*)\]"',
                                 re.search(r'text="Connections"[^>]*>', xml).group(0))
                if full:
                    self.adb.tap_bounds(f"[{full.group(1)}][{full.group(2)}]")
                    time.sleep(3)
            xml = self.adb.ui_xml()
            wifi_match = re.search(r'text="Wi-Fi"[^>]*>', xml)
            if wifi_match:
                bounds = re.search(r'bounds="\[([^]]*)\]\[([^]]*)\]"', wifi_match.group(0))
                if bounds:
                    self.adb.tap_bounds(f"[{bounds.group(1)}][{bounds.group(2)}]")
                    time.sleep(3)

    def _tap_sign_in(self) -> bool:
        if _is_desktop_client(self.adb):
            return False
        xml = self.adb.ui_xml()
        log.info("Looking for sign-in button or portal URL")

        domain_pattern = re.escape(self.router.domain) if self.router.domain else ""
        for pattern in [
            f"https?://{domain_pattern}[:/]" if domain_pattern else None,
            "https?://tollgate",
        ]:
            if pattern:
                m = re.search(f'text="({pattern}[^"]*)"', xml)
                if m:
                    text = m.group(1)
                    node = re.search(f'text="{re.escape(text)}"[^>]*bounds="\\[([^\\]]*)\\]\\[([^\\]]*)\\]"', xml)
                    if node:
                        self.adb.tap_bounds(f"[{node.group(1)}][{node.group(2)}]")
                        time.sleep(4)
                        return True

        for label in ["Sign in to network", "Sign in", "Network sign-in",
                       "Log in", "Connect to network", "Open"]:
            m = re.search(f'text="{label}"[^>]*bounds="\\[([^\\]]*)\\]\\[([^\\]]*)\\]"', xml)
            if m:
                self.adb.tap_bounds(f"[{m.group(1)}][{m.group(2)}]")
                time.sleep(4)
                return True

        return False

    def _connect_to_wifi(self) -> bool:
        if _is_desktop_client(self.adb):
            return self._connect_to_wifi_desktop()

        log.info("Ensuring airplane mode is off")
        self.adb.shell("settings put global airplane_mode_on 0")
        self.adb.shell("cmd connectivity airplane-mode disable")

        log.info("Disabling WiFi to force fresh scan")
        self.adb.shell("svc wifi disable")
        time.sleep(3)

        log.info("Waking phone")
        self.adb.wake_and_unlock()

        log.info("Enabling WiFi")
        self.adb.shell("svc wifi enable")
        time.sleep(4)

        log.info("Opening WiFi settings")
        self.open_wifi_settings()
        time.sleep(4)

        found = False
        for attempt in range(1, 11):
            xml = self.adb.ui_xml()
            if self.ssid in xml:
                log.info(f"Found {self.ssid} on scan attempt {attempt}")
                found = True
                break
            m = re.search(f'text="({self.ssid_prefix}-[^"]*)"', xml)
            if m:
                self.ssid = m.group(1)
                log.info(f"Found SSID via prefix match: {self.ssid}")
                found = True
                break
            log.info(f"Scan {attempt}: {self.ssid} not visible yet")
            if attempt == 5:
                log.info("Re-opening WiFi settings for fresh scan")
                self.open_wifi_settings()
                time.sleep(3)
            else:
                time.sleep(4)

        if not found:
            log.warning(f"{self.ssid} not found after 10 scans")
            return False

        xml = self.adb.ui_xml()
        if not self._tap_ssid(xml, self.ssid):
            log.warning(f"Failed to tap {self.ssid} in UI")
            return False
        log.info(f"Tapped {self.ssid}, waiting for connection...")
        time.sleep(6)

        for attempt in range(1, 6):
            if self.adb.is_wifi_connected(self.ssid):
                log.info(f"WiFi connected after {attempt} checks")
                return True
            time.sleep(3)

        log.warning(f"SSID tapped but not connected after 5 checks")
        return False

    def _connect_to_wifi_desktop(self) -> bool:
        log.info(f"Connecting to {self.ssid} via desktop WiFi client")
        self.adb.wake_and_unlock()

        for attempt in range(1, 4):
            if self.adb.connect_wifi(self.ssid):
                log.info(f"Connected to {self.ssid} on attempt {attempt}")
                return True
            log.info(f"Desktop WiFi connect attempt {attempt} failed, retrying...")
            time.sleep(3)

        log.error(f"Failed to connect to {self.ssid} from desktop")
        return False

    def reconnect(self, skip_portal: bool = False) -> bool:
        if not self._connect_to_wifi():
            return False

        if _is_desktop_client(self.adb):
            return self._reconnect_desktop()

        log.info(f"Connected to {self.ssid}")

        if skip_portal:
            log.info("Portal detection skipped")
            return True

        return self._open_portal_on_phone(
            r'data-sm="[^"]*"|Tollgate Captive Portal|TollGate.*portal_ready',
        )

    def _open_portal_on_phone(self, state_pattern: str, timeout: int = 30) -> bool:
        self.router.ssh("echo '' > /tmp/tollgate-portal.log")
        self._tap_ssid(self.adb.ui_xml(), self.ssid)
        time.sleep(4)
        if not self._tap_sign_in():
            log.info("Sign-in button not found — waiting for portal auto-redirect")

        log.info("Waiting for portal to render in captive WebView...")
        start = time.time()
        while time.time() - start < timeout:
            xml = self.adb.ui_xml()
            if re.search(state_pattern, xml):
                sm = re.search(r'data-sm="([^"]*)"', xml)
                if sm:
                    log.info(f"Portal reached state '{sm.group(1)}' after {int(time.time()-start)}s")
                else:
                    log.info(f"Portal page loaded in WebView after {int(time.time()-start)}s")
                return True
            time.sleep(3)

        log.warning(f"Portal did not reach expected state within {timeout}s")
        return False

    def _reconnect_desktop(self) -> bool:
        log.info(f"Connected to {self.ssid} from desktop")
        self.router.ssh("echo '' > /tmp/tollgate-portal.log")

        domain = self.router.domain
        if domain:
            portal_url = f"http://{domain}:8080/"
            log.info(f"Opening portal at {portal_url}")
            self.adb.open_url(portal_url)
            time.sleep(5)

        log.info("Desktop connected to TollGate WiFi — portal accessible via browser or API")
        return True

    def reconnect_no_fallback(self) -> bool:
        if not self._connect_to_wifi():
            return False

        if _is_desktop_client(self.adb):
            return True

        if not self.adb.is_wifi_connected(self.ssid):
            log.error(f"Failed to connect to {self.ssid}")
            return False

        return self._open_portal_on_phone(
            r'data-sm="(portal_ready|token_typing)"',
            timeout=30,
        )
