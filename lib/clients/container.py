import base64
import os
import re
import shlex
import subprocess
import time
import logging

from lib.constants import POC_GATEWAY

# Container clients must access the portal via port 80 (the standard HTTP port),
# NOT the NDS gatewayport (2050) directly. Port 80 traffic is intercepted by
# NDS iptables rules on br-lan, which creates a proper preauthenticated client
# record and serves the splash page correctly. Direct access to port 2050
# bypasses iptables, leaving NDS without client context → HTTP 500.
# See: https://github.com/openNDS/openNDS/issues/195
_PORTAL_URL = f"http://{POC_GATEWAY}/"

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
                 client_ip: str = "10.99.99.100", client_mac: str | None = None,
                 password: str = "tollgate"):
        self._host = host
        if jump_host and jump_host in {"localhost", "127.0.0.1", "::1"}:
            jump_host = None
        if jump_host and jump_host == client_ip:
            jump_host = None
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
                    flags.append(shlex.quote(str(v)))
            else:
                k_dashed = k.replace("_", "-")
                if v is True:
                    flags.append(f"--{k_dashed}")
                elif v is False:
                    continue
                else:
                    flags.append(f"--{k_dashed}={shlex.quote(str(v))}")
        flag_str = " ".join(flags)
        cmd = f"curl --connect-timeout {timeout} --max-time {timeout + 5} {flag_str} {shlex.quote(url)}".strip()
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
            script = (
                "from playwright.sync_api import sync_playwright\n"
                "with sync_playwright() as p:\n"
                f"    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])\n"
                "    page = browser.new_page(viewport={'width': 1280, 'height': 720})\n"
                f"    page.goto('{_PORTAL_URL}', timeout=15000)\n"
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
            ok = self._run_playwright_screenshot(
                url=_PORTAL_URL,
                png_path=remote_png,
                html_path=remote_html,
            )
            if not ok:
                log.warning("playwright screenshot script failed on VM")
                return False
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
                "<!doctype", "<html",
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

    def record_portal_video(self, output_path: str, timeout: int = 20) -> bool:
        return self.record_url_video(_PORTAL_URL, output_path, timeout)

    def screenshot_url(self, url: str, path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            remote_png = "/tmp/tg-screenshot.png"
            remote_html = "/tmp/tg-page.html"
            ok = self._run_playwright_screenshot(url=url, png_path=remote_png, html_path=remote_html)
            if not ok:
                return False
            self._scp_from(remote_png, path)
            html_path = path.replace(".png", ".html")
            self._scp_from(remote_html, html_path)
            return os.path.exists(path)
        except Exception as exc:
            log.warning("screenshot_url failed: %s", exc)
            return False

    def record_url_video(self, url: str, output_path: str, timeout: int = 20,
                         click_selectors: list[str] | None = None) -> bool:
        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            remote_dir = "/tmp/tg-auto-video"
            remote_video = "/tmp/tg-portal-video.webm"

            click_lines = ""
            if click_selectors:
                for sel in click_selectors:
                    safe_sel = sel.replace("'", "\\'")
                    click_lines += (
                        f"    try:\n"
                        f"        page.click('{safe_sel}', timeout=3000)\n"
                        f"        time.sleep(2)\n"
                        f"    except Exception:\n"
                        f"        pass\n"
                    )

            script = (
                "from pathlib import Path\n"
                "from playwright.sync_api import sync_playwright\n"
                "import shutil\n"
                "import time\n"
                f"remote_dir = Path('{remote_dir}')\n"
                "remote_dir.mkdir(parents=True, exist_ok=True)\n"
                "for video_file in remote_dir.glob('*.webm'):\n"
                "    video_file.unlink()\n"
                "with sync_playwright() as p:\n"
                "    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])\n"
                "    ctx = browser.new_context(\n"
                "        viewport={'width': 1280, 'height': 720},\n"
                f"        record_video_dir='{remote_dir}',\n"
                "        record_video_size={'width': 1280, 'height': 720},\n"
                "    )\n"
                "    page = ctx.new_page()\n"
                f"    page.goto('{url}', timeout=15000, wait_until='domcontentloaded')\n"
                "    time.sleep(3)\n"
                f"{click_lines}"
                "    src = page.video.path() if page.video else None\n"
                "    ctx.close()\n"
                "    if src:\n"
                f"        shutil.copy2(str(src), '{remote_video}')\n"
                "        print('VIDEO_OK')\n"
                "    else:\n"
                "        print('VIDEO_ERROR')\n"
                "    browser.close()\n"
            )
            self._exec(
                f"rm -rf {remote_dir} {remote_video} /tmp/tg-e2e && mkdir -p {remote_dir} && "
                f"cat > /tmp/tg-auto-video.py << 'PYEOF'\n{script}\nPYEOF",
                timeout=10,
            )
            result = self._exec("python3 /tmp/tg-auto-video.py", timeout=timeout)
            if "VIDEO_OK" not in result:
                return False
            self._scp_from(remote_video, output_path, timeout=timeout + 10)
            return os.path.exists(output_path)
        except Exception as exc:
            log.warning("record_url_video failed: %s", exc)
            return False

    def _run_playwright_screenshot(self, url: str, png_path: str = "/tmp/tg-screenshot.png",
                                    html_path: str | None = None,
                                    extra_js: str = "") -> bool:
        """Run a Playwright screenshot on the Debian VM.

        Uses domcontentloaded (not networkidle) to avoid crashes when
        the page has pending long-lived connections (SSE, etc).
        Returns True if the script printed SCREENSHOT_OK.
        """
        save_html = ""
        if html_path:
            save_html = f"    with open('{html_path}', 'w') as f:\n        f.write(page.content())\n"
        script = (
            "from playwright.sync_api import sync_playwright\n"
            "import time\n"
            "with sync_playwright() as p:\n"
            "    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])\n"
            "    page = browser.new_page(viewport={'width': 1280, 'height': 720})\n"
            f"    page.goto('{url}', timeout=15000, wait_until='domcontentloaded')\n"
            "    time.sleep(2)\n"
            + extra_js +
            f"    page.screenshot(path='{png_path}')\n"
            + save_html +
            "    browser.close()\n"
            "    print('SCREENSHOT_OK')\n"
        )
        self._exec(f"cat > /tmp/tg-screenshot.py << 'PYEOF'\n{script}\nPYEOF")
        result = self._exec("python3 /tmp/tg-screenshot.py", timeout=30)
        log.debug("playwright screenshot result: %s", result[:200])
        return "SCREENSHOT_OK" in result

    def ui_xml(self, timeout: int = 30) -> str:
        try:
            script = (
                "from playwright.sync_api import sync_playwright\n"
                "with sync_playwright() as p:\n"
                f"    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])\n"
                "    page = browser.new_page(viewport={'width': 1280, 'height': 720})\n"
                f"    page.goto('{_PORTAL_URL}', timeout=15000)\n"
                "    page.wait_for_load_state('networkidle', timeout=10000)\n"
                "    print(page.content())\n"
                "    browser.close()\n"
            )
            self._exec(f"cat > /tmp/tg-ui.py << 'PYEOF'\n{script}\nPYEOF")
            return self._exec("python3 /tmp/tg-ui.py", timeout=timeout)
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

    def start_portal_recording(self) -> None:
        """Start a Playwright session recording video of the portal flow.

        The script runs on the VM and blocks until completion. It supports two
        synchronization modes:

        * Browser-driven payment: the test writes /tmp/tg-token, then Playwright
          pastes the Cashu token into the portal and submits it.
        * Legacy state recording: the test writes /tmp/tg-paid after out-of-band
          payment, then Playwright reloads the portal to capture the paid state.
        """
        # Pre-warm Chromium to avoid cold-start timeout on first launch.
        # Compiles shader cache, builds font config, etc.
        self._exec(
            "python3 -c \""
            "from playwright.sync_api import sync_playwright; "
            "p = sync_playwright().start(); "
            "b = p.chromium.launch(headless=True, args=['--no-sandbox']); "
            "b.close(); p.stop()"
            "\" 2>/dev/null",
            timeout=30,
        )

        script = (
            "from playwright.sync_api import sync_playwright\n"
            "import os, re, shutil, time\n"
            "p = None; browser = None; ctx = None; page = None\n"
            "try:\n"
            "    p = sync_playwright().start()\n"
            "    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])\n"
            "    ctx = browser.new_context(record_video_dir='/tmp/tg-video', record_video_size={'width': 1280, 'height': 720})\n"
            "    page = ctx.new_page()\n"
            "    steps_ok = []\n"
            "    errors = []\n"
            "    def screenshot(path, step):\n"
            "        try:\n"
            "            page.screenshot(path=path)\n"
            "            steps_ok.append(step)\n"
            "        except Exception as e:\n"
            "            errors.append(f'{step}: {e}')\n"
            "    def first_visible_locator(selectors):\n"
            "        for selector in selectors:\n"
            "            locator = page.locator(selector).first\n"
            "            try:\n"
            "                if locator.count() > 0 and locator.is_visible(timeout=1000):\n"
            "                    return locator\n"
            "            except Exception:\n"
            "                continue\n"
            "        return None\n"
            "    def submit_token(token):\n"
            "        page.goto('" + _PORTAL_URL + "', timeout=30000, wait_until='domcontentloaded')\n"
            "        token_input = None\n"
            "        for attempt in range(6):\n"
            "            time.sleep(2 if attempt == 0 else 3)\n"
            "            try:\n"
            "                cashu_tab = page.locator('#tab-cashu, button[class*=tab-cashu]').first\n"
            "                if cashu_tab.count() > 0 and cashu_tab.is_visible(timeout=1000):\n"
            "                    cashu_tab.click()\n"
            "                    time.sleep(0.5)\n"
            "            except Exception:\n"
            "                pass\n"
            "            token_input = first_visible_locator([\n"
            "                'textarea',\n"
            "                'input[name*=token]',\n"
            "                'input[id*=token]',\n"
            "                'input[name*=cashu]',\n"
            "                'input[id*=cashu]',\n"
            "                'input[placeholder*=Cashu]',\n"
            "                'input[placeholder*=cashu]',\n"
            "                'input[type=text]',\n"
            "                'input:not([type])',\n"
            "            ])\n"
            "            if token_input is not None:\n"
            "                break\n"
            "        if token_input is None:\n"
            "            raise RuntimeError('no visible Cashu token input found')\n"
            "        token_input.fill(token)\n"
            "        screenshot('/tmp/tg-e2e/02-token-filled.png', 'token-filled-screenshot')\n"
            "        submitted = False\n"
            "        button = page.get_by_role('button', name=re.compile('purchase|submit|pay|connect|go', re.I)).first\n"
            "        try:\n"
            "            if button.count() > 0 and button.is_visible(timeout=1000):\n"
            "                button.click()\n"
            "                submitted = True\n"
            "        except Exception as e:\n"
            "            errors.append(f'role-submit-click: {e}')\n"
            "        if not submitted:\n"
            "            submit = first_visible_locator(['input[type=submit]', 'button', '[role=button]'])\n"
            "            if submit is not None:\n"
            "                submit.click()\n"
            "                submitted = True\n"
            "        if not submitted:\n"
            "            token_input.press('Enter')\n"
            "        for _ in range(60):\n"
            "            time.sleep(0.5)\n"
            "            content = page.content().lower()\n"
            "            if any(marker in content for marker in ['data-sm=\"authed\"', 'data-sm=\"countdown\"', 'data-sm=\"usage_dashboard\"', 'remaining', 'connected', 'thank you', 'success', 'session active']):\n"
            "                return\n"
            "        try:\n"
            "            page.wait_for_load_state('domcontentloaded', timeout=5000)\n"
            "        except Exception:\n"
            "            pass\n"
            "    try:\n"
            f"        page.goto('{_PORTAL_URL}', timeout=30000, wait_until='domcontentloaded')\n"
            "        time.sleep(2)\n"
            "        try:\n"
            "            cashu_tab = page.locator('#tab-cashu, button[class*=tab-cashu]').first\n"
            "            if cashu_tab.count() > 0 and cashu_tab.is_visible(timeout=1000):\n"
            "                cashu_tab.click()\n"
            "                time.sleep(0.5)\n"
            "        except Exception:\n"
            "            pass\n"
            "        screenshot('/tmp/tg-e2e/01-portal-unpaid.png', 'unpaid-screenshot')\n"
            "    except Exception as e:\n"
            "        errors.append(f'goto-portal: {e}')\n"
            "    open('/tmp/tg-portal-ready', 'w').close()\n"
            "    for _ in range(240):\n"
            "        if os.path.exists('/tmp/tg-token') or os.path.exists('/tmp/tg-paid'):\n"
            "            break\n"
            "        time.sleep(0.5)\n"
            "    else:\n"
            "        errors.append('timeout waiting for token or payment signal')\n"
            "    if os.path.exists('/tmp/tg-token'):\n"
            "        try:\n"
            "            with open('/tmp/tg-token') as token_file:\n"
            "                token = token_file.read().strip()\n"
            "            submit_token(token)\n"
            "            screenshot('/tmp/tg-e2e/03-portal-paid.png', 'paid-screenshot')\n"
            "        except Exception as e:\n"
            "            errors.append(f'browser-token-payment: {e}')\n"
            "            screenshot('/tmp/tg-e2e/99-error.png', 'error-screenshot')\n"
            "    elif os.path.exists('/tmp/tg-paid'):\n"
            "        try:\n"
            f"            page.reload(timeout=15000, wait_until='domcontentloaded')\n"
            "            time.sleep(1)\n"
            "            screenshot('/tmp/tg-e2e/03-portal-paid.png', 'paid-screenshot')\n"
            "        except Exception as e:\n"
            "            errors.append(f'paid-screenshot: {e}')\n"
            "    try:\n"
            "        page.goto('http://1.1.1.1', timeout=15000, wait_until='domcontentloaded')\n"
            "        time.sleep(1)\n"
            "        screenshot('/tmp/tg-e2e/04-internet-access.png', 'internet-access-screenshot')\n"
            "    except Exception as e:\n"
            "        errors.append(f'internet-access-screenshot: {e}')\n"
            "    if 'unpaid-screenshot' in steps_ok and 'paid-screenshot' in steps_ok:\n"
            "        print('RECORD_OK')\n"
            "    else:\n"
            "        print('RECORD_ERROR: ' + ', '.join(errors))\n"
            "except Exception as e:\n"
            "    print(f'RECORD_ERROR: {e}')\n"
            "finally:\n"
            "    open('/tmp/tg-portal-ready', 'w').close()\n"
            "    if ctx:\n"
            "        video_path = None\n"
            "        try:\n"
            "            video_path = page.video.path()\n"
            "        except Exception:\n"
            "            pass\n"
            "        try:\n"
            "            ctx.close()\n"
            "        except Exception:\n"
            "            pass\n"
            "        if video_path:\n"
            "            try:\n"
            "                shutil.copy2(str(video_path), '/tmp/tg-e2e/portal-flow.webm')\n"
            "            except Exception:\n"
            "                pass\n"
            "    if browser:\n"
            "        try:\n"
            "            browser.close()\n"
            "        except Exception:\n"
            "            pass\n"
            "    if p:\n"
            "        try:\n"
            "            p.stop()\n"
            "        except Exception:\n"
            "            pass\n"
        )
        self._exec(
            "rm -f /tmp/tg-portal-ready /tmp/tg-paid /tmp/tg-token && "
            "rm -rf /tmp/tg-e2e /tmp/tg-video && "
            "mkdir -p /tmp/tg-e2e /tmp/tg-video && "
            f"cat > /tmp/tg-record.py << 'PYEOF'\n{script}\nPYEOF",
            timeout=15,
        )
        self._exec(
            "rm -f /tmp/tg-record.out /tmp/tg-record.err; "
            "setsid python3 /tmp/tg-record.py >/tmp/tg-record.out 2>/tmp/tg-record.err &",
            timeout=5,
        )

    def wait_for_portal_ready(self, timeout: int = 60) -> bool:
        for _ in range(timeout * 2):
            try:
                out = self._exec("test -f /tmp/tg-portal-ready && echo YES || echo NO", timeout=5)
                if "YES" in out:
                    return True
            except Exception as e:
                log.debug("portal-ready check failed: %s", e)
            time.sleep(0.5)
        try:
            err = self._exec("echo '=== record.out ==='; cat /tmp/tg-record.out 2>/dev/null; echo '=== record.err ==='; cat /tmp/tg-record.err 2>/dev/null; echo '=== processes ==='; ps aux | grep tg-record | grep -v grep", timeout=5)
            log.warning("Portal ready timeout after %ds. Diagnostics:\n%s", timeout, err[:1000])
        except Exception as e:
            log.warning("Portal ready diagnostics also failed: %s", e)
        return False

    def signal_paid(self) -> None:
        self._exec("touch /tmp/tg-paid")

    def signal_token(self, token: str) -> None:
        token_b64 = base64.b64encode(token.encode()).decode()
        self._exec(
            f"printf %s {shlex.quote(token_b64)} | base64 -d > /tmp/tg-token",
            timeout=10,
        )

    def finish_portal_recording(self, output_dir: str, timeout: int = 90) -> dict[str, object]:
        """Wait for the recording script to finish and collect artifacts.

        Returns dict with 'screenshots' (list of local paths) and 'video' (path or None).
        """
        os.makedirs(output_dir, exist_ok=True)
        for _ in range(max(timeout * 2, 1)):
            result = self._exec("cat /tmp/tg-record.out 2>/dev/null || true", timeout=5)
            if "RECORD_OK" in result or "RECORD_ERROR" in result:
                break
            time.sleep(0.5)
        else:
            result = self._exec(
                "echo RECORD_TIMEOUT; cat /tmp/tg-record.out 2>/dev/null; "
                "cat /tmp/tg-record.err 2>/dev/null",
                timeout=5,
            )
        log.info("portal recording result: %s", result)

        screenshots = []
        for name in [
            "01-portal-unpaid",
            "02-token-filled",
            "03-portal-paid",
            "04-internet-access",
            "99-error",
        ]:
            remote = f"/tmp/tg-e2e/{name}.png"
            local = os.path.join(output_dir, f"{name}.png")
            self._scp_from(remote, local)
            if os.path.exists(local):
                screenshots.append(local)

        video_local = os.path.join(output_dir, "portal-flow.webm")
        self._scp_from("/tmp/tg-e2e/portal-flow.webm", video_local, timeout=60)

        return {
            "screenshots": screenshots,
            "video": video_local if os.path.exists(video_local) else None,
            "ok": "RECORD_OK" in result,
        }
