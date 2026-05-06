import subprocess
import json
import os
import time
import re
import logging

from lib.constants import BACKEND_PORT, CGI_PORT, TEST_MINT_URL

log = logging.getLogger("tollgate.router")


class Router:
    def __init__(self, host: str, phone_ip: str, phone_mac: str, domain: str, identity_file: str = None):
        self.host = host
        self.phone_ip = phone_ip
        self.phone_mac = phone_mac
        self.domain = domain
        self._ssh_base = [
            "ssh",
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
        ]
        if identity_file:
            self._ssh_base.extend(["-i", identity_file])
        self._ssh_base.append(f"root@{host}")

    def resolve_phone_client(self, adb) -> tuple:
        mac = adb.wifi_mac()
        if mac:
            self.phone_mac = mac
        ip = adb.wifi_ip()
        if mac and ip:
            self.phone_ip = ip
            log.info(f"Phone auto-detected: MAC={mac} IP={ip}")
            return mac, ip
        if mac:
            try:
                leases = self.ssh("cat /tmp/dhcp.leases 2>/dev/null")
                for line in leases.strip().split("\n"):
                    fields = line.split()
                    if len(fields) >= 3 and fields[1].lower() == mac.lower():
                        self.phone_ip = fields[2]
                        log.info(f"Phone from DHCP lease: MAC={mac} IP={fields[2]}")
                        return mac, fields[2]
            except Exception:
                pass
        return self.phone_mac, self.phone_ip

    @property
    def gateway_ip(self) -> str:
        if self.domain:
            return self.domain
        gw = self._detect_gateway()
        if gw:
            return gw
        return self.host

    def _detect_gateway(self) -> str:
        try:
            out = self.ssh("ip -4 route show default 2>/dev/null | awk '{print $3}'")
            if out and not out.startswith("Usage"):
                return out.split("\n")[0].strip()
        except Exception:
            pass
        return ""

    def backend_url(self, path="/"):
        return f"http://127.0.0.1:{BACKEND_PORT}{path}"

    def cgi_url(self, endpoint):
        return f"http://127.0.0.1:{CGI_PORT}/cgi-bin/{endpoint}"

    def ssh(self, cmd: str, timeout: int = 30) -> str:
        r = subprocess.run(
            self._ssh_base + [cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            noise = re.compile(r"Warning:.*Permanently added[^\n]*")
            cleaned = noise.sub("", r.stderr).strip()
            if cleaned:
                log.warning("ssh returned %d: %s", r.returncode, cleaned[:200])
        out = r.stdout.strip()
        return re.sub(r"Warning:.*Permanently added[^\n]*\n?", "", out).strip()

    def ssh_stdin(self, cmd: str, data: str, timeout: int = 15):
        return subprocess.run(
            self._ssh_base + [cmd],
            input=data, capture_output=True, text=True, timeout=timeout,
        )

    def api_status(self, path: str) -> int:
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             f"http://{self.host}:2121{path}"],
            capture_output=True, text=True, timeout=15,
        )
        code = r.stdout.strip()
        return int(code) if code.isdigit() else 0

    def api_body(self, path: str) -> str:
        r = subprocess.run(
            ["curl", "-s", f"http://{self.host}:2121{path}"],
            capture_output=True, text=True, timeout=15,
        )
        return r.stdout.strip()

    def backend_curl_xff(self, path: str, ip: str = None, method: str = None,
                         headers: dict = None, data: str = None) -> str:
        ip = ip or self.phone_ip
        parts = ["curl", "-s", "-H", f"'X-Forwarded-For: {ip}'"]
        if method:
            parts += ["-X", method]
        if headers:
            for k, v in headers.items():
                parts += ["-H", f"'{k}: {v}'"]
        if data:
            parts += ["-d", f"'{data}'"]
        parts.append(f"'{path}'")
        return self.ssh(" ".join(parts))

    def pay_direct(self, token: str, ip: str = None) -> dict:
        ip = ip or self.phone_ip
        tmpf = "/tmp/tg-pay-token.txt"
        self.ssh_stdin(f"cat > {tmpf}", token)
        resp = self.ssh(
            f"curl -s -X POST '{self.backend_url('/')}' "
            f"-H 'Content-Type: text/plain' "
            f"-H 'X-Forwarded-For: {ip}' "
            f"-d @{tmpf}; rm -f {tmpf}"
        )
        try:
            return json.loads(resp)
        except json.JSONDecodeError:
            return {"raw": resp}

    def pay_via_header(self, token: str, mac: str = None) -> str:
        mac = mac or self.phone_mac
        return self.ssh(
            f"curl -s -H 'X-Cashu: {token}' "
            f"'http://127.0.0.1:{BACKEND_PORT}/pay?mac={mac}'"
        )

    def get_nds_state(self, mac: str = None) -> str:
        mac = mac or self.phone_mac
        out = self.ssh("ndsctl clients 2>/dev/null")
        lines = out.split("\n")
        for i, line in enumerate(lines):
            if mac in line or mac.replace(":", "").upper() in line.replace(":", "").upper():
                for j in range(i, min(i + 20, len(lines))):
                    m = re.search(r"state=(\S+)", lines[j])
                    if m:
                        return m.group(1)
        return ""

    def wait_for_auth(self, timeout: int = 30, mac: str = None) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if self.get_nds_state(mac) == "Authenticated":
                return True
            time.sleep(1)
        return False

    def get_session(self, ip: str = None) -> dict:
        ip = ip or self.phone_ip
        resp = self.backend_curl_xff(self.backend_url("/balance"), ip)
        try:
            return json.loads(resp)
        except json.JSONDecodeError:
            return {"raw": resp}

    def get_remaining_seconds(self, ip: str = None) -> int:
        session = self.get_session(ip)
        remaining_ms = session.get("remaining", 0)
        return remaining_ms // 1000 if remaining_ms and remaining_ms > 0 else 0

    def wait_for_session_expiry(self, mac: str = None, poll_interval: float = 1, max_wait: int = 120) -> int:
        mac = mac or self.phone_mac
        start = time.time()
        while time.time() - start < max_wait:
            if self.get_nds_state(mac) != "Authenticated":
                return int(time.time() - start)
            time.sleep(poll_interval)
        raise TimeoutError(f"Session did not expire within {max_wait}s")

    def reset_state(self, mac: str = None, adb=None):
        if not mac and not self.phone_mac and adb:
            detected = adb.wifi_mac()
            if detected:
                self.phone_mac = detected
                log.info(f"reset_state auto-detected MAC: {detected}")
        mac = mac or self.phone_mac
        if adb:
            adb.shell("am force-stop com.android.captiveportallogin")
        self.ssh("echo '{}' > /etc/tollgate/sessions.json")
        self.ssh("service tollgate-wrt restart")
        time.sleep(2)
        self.ssh(f"ndsctl deauth {mac} 2>/dev/null; ndsctl block {mac} 2>/dev/null")
        for iface in ["phy0-ap0", "phy0-ap1", "phy1-ap0", "phy1-ap1"]:
            self.ssh(f"iw dev {iface} station del {mac} 2>/dev/null")
        time.sleep(3)
        self.ssh(f"ndsctl unblock {mac} 2>/dev/null")
        self.ssh("echo '' > /tmp/tollgate-portal.log")
        self.ssh("echo '' > /www/pending-token.txt")

    def apply_pricing(self, step_size: int = None, metric: str = "milliseconds"):
        if step_size is None:
            from lib.constants import DEFAULT_STEP_SIZE_MS
            step_size = DEFAULT_STEP_SIZE_MS
        self.ssh(
            f"sed -i 's/\"step_size\":[[:space:]]*[0-9]*/\"step_size\": {step_size}/' "
            f"/etc/tollgate/config.json"
        )
        self.ssh(
            f"sed -i 's/\"metric\":[[:space:]]*\"[^\"]*\"/\"metric\": \"{metric}\"/' "
            f"/etc/tollgate/config.json"
        )
        self.ssh("service tollgate-wrt restart")
        self._wait_for_backend()

    def restore_pricing(self):
        self.ssh("cp /etc/tollgate/config.json.test-backup /etc/tollgate/config.json")
        self.ssh("service tollgate-wrt restart")
        self._wait_for_backend()

    def _wait_for_backend(self, timeout: int = 15):
        start = time.time()
        while time.time() - start < timeout:
            code = self.api_status("/")
            if code == 200:
                return
            time.sleep(1)
        log.warning(f"Backend not healthy after {timeout}s")

    def get_portal_log(self) -> str:
        return self.ssh("cat /tmp/tollgate-portal.log 2>/dev/null")

    def clear_portal_log(self):
        self.ssh("echo '' > /tmp/tollgate-portal.log")

    def enable_debug_portal(self):
        self.ssh("mkdir -p /etc/tollgate && touch /etc/tollgate/debug-portal")

    def disable_debug_portal(self):
        self.ssh("rm -f /etc/tollgate/debug-portal")

    def ensure_test_mint(self):
        cfg_raw = self.ssh("cat /etc/tollgate/config.json")
        cfg = json.loads(cfg_raw)
        if any(m.get("url") == TEST_MINT_URL for m in cfg.get("accepted_mints", [])):
            return
        cfg.setdefault("accepted_mints", []).append({
            "url": TEST_MINT_URL,
            "min_balance": 0,
            "balance_tolerance_percent": 0,
            "payout_interval_seconds": 60,
            "min_payout_amount": 0,
            "price_per_step": 1,
            "price_unit": "sats",
            "purchase_min_steps": 0,
        })
        tmp = "/tmp/config-testmint.json"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        scp_cmd = ["scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]
        if identity_file:
            scp_cmd += ["-i", identity_file]
        scp_cmd += [tmp, f"root@{self.host}:/etc/tollgate/config.json"]
        subprocess.run(scp_cmd, check=True, capture_output=True)
        os.remove(tmp)
        self.ssh("/etc/init.d/tollgate-wrt restart")
        log.info(f"Added {TEST_MINT_URL} to accepted mints, restarted backend")

    def collect_logs(self, results_dir: str, adb=None):
        raw = os.path.join(results_dir, "raw")
        os.makedirs(raw, exist_ok=True)
        for name, cmd in [
            ("portal.log", "cat /tmp/tollgate-portal.log"),
            ("backend.log", "logread -l 200 -e tollgate 2>/dev/null"),
            ("ndsctl-status.txt", "ndsctl status 2>/dev/null"),
            ("ndsctl-clients.txt", "ndsctl clients 2>/dev/null"),
        ]:
            try:
                with open(os.path.join(raw, name), "w") as f:
                    f.write(self.ssh(cmd))
            except Exception:
                pass
        if adb:
            try:
                with open(os.path.join(raw, "logcat.txt"), "w") as f:
                    f.write(adb.shell("logcat -d -t 200"))
            except Exception:
                pass
