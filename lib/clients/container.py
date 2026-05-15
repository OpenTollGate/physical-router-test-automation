import os
import re
import subprocess
import logging

from lib.constants import POC_GATEWAY, NDS_PORTAL_PORT

log = logging.getLogger("tollgate.container_client")


class ContainerClient:
    """Client that executes commands on a QEMU Debian VM via SSH.

    Connects through a jump host chain (Mac -> jump -> VM). Uses Playwright
    for headless Chromium screenshots and page source capture.

    The adapter exposes the same interface as ADBDevice so existing tests can
    use it as a drop-in replacement for the ``adb`` fixture.
    """

    is_desktop = True
    is_container = True

    def __init__(self, host: str | None = None, jump_host: str | None = None,
                 client_ip: str = "192.168.1.100", client_mac: str | None = None,
                 password: str = "tollgate"):
        self._host = host
        self._jump_host = jump_host
        self._client_ip = client_ip
        self._client_mac = client_mac
        self._password = password

        self._ssh_base = [
            "sshpass", "-p", self._password, "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
        ]
        if self._jump_host:
            self._ssh_base += ["-J", self._jump_host]
        self._ssh_base.append(f"root@{self._client_ip}")

    # -- internal helpers ------------------------------------------------

    def _exec(self, cmd: str, timeout: int = 30) -> str:
        full = self._ssh_base + [cmd]
        log.debug("container exec: %s", " ".join(full))
        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()

    def _scp_from(self, remote_path: str, local_path: str, timeout: int = 15):
        cmd = [
            "sshpass", "-p", self._password, "scp", "-O",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
        ]
        if self._jump_host:
            cmd += ["-J", self._jump_host]
        cmd += [f"root@{self._client_ip}:{remote_path}", local_path]
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)

    # -- ADBDevice-compatible interface ----------------------------------

    def wifi_mac(self) -> str:
        if self._client_mac:
            return self._client_mac
        return self._exec(
            "ip -br link show | grep -v lo | head -1 | awk '{print $2}'"
        ).strip()

    def wifi_ip(self) -> str:
        return self._client_ip

    def shell(self, cmd: str, timeout: int = 30) -> str:
        if cmd.startswith("dumpsys") or cmd.startswith("am ") or cmd.startswith("svc "):
            return ""
        if cmd.startswith("settings "):
            return ""
        if cmd.startswith("cmd "):
            return ""
        return self._exec(cmd, timeout)

    def exec(self, cmd: str, timeout: int = 30) -> str:
        return self._exec(cmd, timeout)

    def curl(self, url: str, timeout: int = 10, **kwargs) -> str:
        flags = []
        for k, v in kwargs.items():
            if len(k) == 1:
                flags.append(f"-{k}")
                if v is not True:
                    flags.append(str(v))
            else:
                k_dashed = k.replace("_", "-")
                if v is True:
                    flags.append(f"--{k_dashed}")
                elif v is False:
                    continue
                else:
                    flags.append(f"--{k_dashed}={v}")
        flag_str = " ".join(flags)
        cmd = f"curl --connect-timeout {timeout} --max-time {timeout + 5} {flag_str} '{url}'".strip()
        return self._exec(cmd, timeout=timeout + 10)

    def ping(self, host: str = "1.1.1.1", count: int = 2, timeout: int = 3,
             interface: str | None = None) -> bool:
        iface_opt = f" -I {interface}" if interface else ""
        out = self._exec(f"ping{iface_opt} -c {count} -W {timeout} {host}",
                         timeout=timeout * count + 10)
        result = "0% packet loss" in out
        if not result:
            log.debug("ping %s failed: %s", host, out[:100])
        return result

    def is_connected(self) -> bool:
        return self.ping(POC_GATEWAY, count=1, timeout=3)

    def screenshot(self, path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            remote_png = "/tmp/tg-screenshot.png"
            portal_url = f"http://{POC_GATEWAY}:{NDS_PORTAL_PORT}/"
            script = (
                "from playwright.sync_api import sync_playwright\n"
                "with sync_playwright() as p:\n"
                f"    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])\n"
                "    page = browser.new_page(viewport={'width': 1280, 'height': 720})\n"
                f"    page.goto('{portal_url}', timeout=15000)\n"
                "    page.wait_for_load_state('networkidle', timeout=10000)\n"
                f"    page.screenshot(path='{remote_png}')\n"
                "    browser.close()\n"
            )
            self._exec(f"cat > /tmp/tg-screenshot.py << 'PYEOF'\n{script}\nPYEOF")
            self._exec("python3 /tmp/tg-screenshot.py", timeout=30)
            self._scp_from(remote_png, path)
            return os.path.exists(path)
        except Exception as exc:
            log.warning("screenshot failed: %s", exc)
            return False

    def screenshot_portal(self, path: str, report_dir: str | None = None) -> bool:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            remote_png = "/tmp/tg-screenshot.png"
            remote_html = "/tmp/tg-page.html"
            portal_url = f"http://{POC_GATEWAY}:{NDS_PORTAL_PORT}/"
            script = (
                "from playwright.sync_api import sync_playwright\n"
                "with sync_playwright() as p:\n"
                f"    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])\n"
                "    page = browser.new_page(viewport={'width': 1280, 'height': 720})\n"
                f"    page.goto('{portal_url}', timeout=15000)\n"
                "    page.wait_for_load_state('networkidle', timeout=10000)\n"
                f"    page.screenshot(path='{remote_png}')\n"
                f"    with open('{remote_html}', 'w') as f:\n"
                "        f.write(page.content())\n"
                "    browser.close()\n"
            )
            self._exec(f"cat > /tmp/tg-screenshot.py << 'PYEOF'\n{script}\nPYEOF")
            self._exec("python3 /tmp/tg-screenshot.py", timeout=30)
            self._scp_from(remote_png, path)

            html_path = path.replace(".png", ".html")
            self._scp_from(remote_html, html_path)
            html = ""
            if os.path.exists(html_path):
                with open(html_path) as f:
                    html = f.read()

            portal_keywords = [
                "tollgate", "captive.*portal", "portal_ready", "token_typing",
                "countdown", "data-sm=", "usage.*dashboard", "authed",
            ]
            pattern = "|".join(portal_keywords)
            if re.search(pattern, html, re.IGNORECASE):
                if report_dir:
                    os.makedirs(report_dir, exist_ok=True)
                    report_path = os.path.join(report_dir, os.path.basename(path))
                    with open(path, "rb") as src, open(report_path, "wb") as dst:
                        dst.write(src.read())
                    return True
            return False
        except Exception as exc:
            log.warning("screenshot_portal failed: %s", exc)
            return False

    def ui_xml(self) -> str:
        try:
            portal_url = f"http://{POC_GATEWAY}:{NDS_PORTAL_PORT}/"
            script = (
                "from playwright.sync_api import sync_playwright\n"
                "with sync_playwright() as p:\n"
                f"    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])\n"
                "    page = browser.new_page(viewport={'width': 1280, 'height': 720})\n"
                f"    page.goto('{portal_url}', timeout=15000)\n"
                "    page.wait_for_load_state('networkidle', timeout=10000)\n"
                "    print(page.content())\n"
                "    browser.close()\n"
            )
            self._exec(f"cat > /tmp/tg-ui.py << 'PYEOF'\n{script}\nPYEOF")
            return self._exec("python3 /tmp/tg-ui.py", timeout=30)
        except Exception:
            return ""

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

    def wake_and_unlock(self):
        return True

    def force_stop(self, package: str):
        pass

    def start_activity(self, action: str | None = None, data_uri: str | None = None,
                       component: str | None = None):
        pass

    def open_url(self, url: str) -> bool:
        try:
            script = (
                "from playwright.sync_api import sync_playwright\n"
                "with sync_playwright() as p:\n"
                f"    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])\n"
                "    page = browser.new_page()\n"
                f"    page.goto('{url}', timeout=30000)\n"
                "    page.wait_for_load_state('domcontentloaded', timeout=15000)\n"
                "    print(page.url)\n"
                "    browser.close()\n"
            )
            self._exec(f"cat > /tmp/tg-open.py << 'PYEOF'\n{script}\nPYEOF")
            result = self._exec("python3 /tmp/tg-open.py", timeout=45)
            return bool(result)
        except Exception:
            return False

    def connect_wifi(self, ssid: str) -> bool:
        return True

    def restore_wifi(self) -> bool:
        return True

    def is_wifi_connected(self, ssid: str) -> bool:
        return True
