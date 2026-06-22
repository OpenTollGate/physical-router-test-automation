"""
Test readiness checker — ensures the router + phone are in a known-good state
before running any UX tests.

Usage:
    from lib.test_readiness import ensure_ready, ReadinessReport
    report = ensure_ready(router_ip="192.168.1.1", password="tollgate123")
    if not report.ready:
        report.fix()  # attempt to reach ready state
        report = ensure_ready(...)  # re-check
    assert report.ready, report.summary()

What "ready" means:
    1. Router reachable via SSH
    2. Tollgate process running
    3. Nodogsplash process running
    4. Port 2121 (API) responding
    5. Port 2050 (portal) responding
    6. NDS gatewayname = net4sats
    7. Phone connected to WiFi (via ADB)
    8. Mobile data disabled on phone
    9. Phone deauthenticated in NDS (Preauthenticated)
    10. Test mint configured (testnut.cashu.exchange)
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("tollgate.readiness")


def _ssh(ip: str, cmd: str, password: str, timeout: int = 15) -> tuple[str, int]:
    full = (
        f"sshpass -p {shlex.quote(password)} ssh "
        f"-o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null "
        f"-o PreferredAuthentications=password "
        f"-o ConnectTimeout=5 "
        f"root@{shlex.quote(ip)} {shlex.quote(cmd)}"
    )
    r = subprocess.run(full, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.returncode


def _adb(cmd: str, timeout: int = 15) -> tuple[str, int]:
    r = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.returncode


def _adb_local(cmd: str, timeout: int = 20) -> tuple[str, int]:
    r = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.returncode


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ReadinessReport:
    checks: list[Check] = field(default_factory=list)
    router_ip: str = ""
    password: str = ""
    phone_mac: str = ""

    @property
    def ready(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def summary(self) -> str:
        lines = [f"Readiness: {'READY ✅' if self.ready else 'NOT READY ❌'}"]
        for c in self.checks:
            mark = "✅" if c.passed else "❌"
            lines.append(f"  {mark} {c.name}: {c.detail}")
        return "\n".join(lines)

    def fix(self) -> None:
        """Attempt to reach ready state from current state."""
        log.info("Attempting to fix readiness issues...")
        for check in self.failed_checks:
            log.info(f"  Fixing: {check.name}")
            if check.name == "router_ssh":
                pass  # can't fix — need physical access
            elif check.name == "tollgate_running":
                _ssh(self.router_ip, "/etc/init.d/tollgate-wrt start", self.password)
                time.sleep(10)
            elif check.name == "nds_running":
                _ssh(self.router_ip, "/etc/init.d/nodogsplash restart", self.password)
                time.sleep(5)
            elif check.name == "phone_adb":
                _adb_local("adb kill-server")
                time.sleep(2)
                _adb_local("adb start-server")
                time.sleep(3)
            elif check.name == "phone_wifi":
                _adb("svc wifi enable")
                time.sleep(8)
            elif check.name == "mobile_data":
                _adb("svc data disable")
                time.sleep(2)
            elif check.name == "phone_deauthed":
                if self.phone_mac:
                    _ssh(self.router_ip, f"/etc/init.d/tollgate-wrt restart", self.password)
                    time.sleep(12)
                    _ssh(self.router_ip, f"ndsctl deauth {self.phone_mac}", self.password)
                    time.sleep(3)
            elif check.name == "nds_branding":
                _ssh(self.router_ip,
                     "uci set nodogsplash.@nodogsplash[0].gatewayname='net4sats'; "
                     "uci set nodogsplash.@nodogsplash[0].gatewaydomainname='net4sats.lan'; "
                     "uci set nodogsplash.@nodogsplash[0].clientid='mac'; "
                     "uci commit nodogsplash; /etc/init.d/nodogsplash restart",
                     self.password)
                time.sleep(5)
            elif check.name == "test_mint":
                _ssh(self.router_ip,
                     """jq '.accepted_mints += [{"url":"https://testnut.cashu.exchange","min_balance":0,"balance_tolerance_percent":0,"payout_interval_seconds":999999,"min_payout_amount":999999,"price_per_step":1,"price_unit":"sats","purchase_min_steps":0}]' /etc/tollgate/config.json > /tmp/cfg.json && cp /tmp/cfg.json /etc/tollgate/config.json; /etc/init.d/tollgate-wrt restart""",
                     self.password)
                time.sleep(15)


def ensure_ready(
    router_ip: str = "192.168.1.1",
    password: str = "tollgate123",
    phone_mac: str = "6e:5e:c0:9d:7a:b8",
    auto_fix: bool = True,
    max_fix_attempts: int = 2,
) -> ReadinessReport:
    """Check readiness, optionally fix issues, return final report."""

    for attempt in range(max_fix_attempts + 1):
        report = _check_ready(router_ip, password, phone_mac)

        if report.ready:
            log.info("Router + phone ready for testing ✅")
            return report

        if attempt < max_fix_attempts and auto_fix:
            log.warning(f"Not ready (attempt {attempt + 1}), fixing...\n{report.summary()}")
            report.router_ip = router_ip
            report.password = password
            report.phone_mac = phone_mac
            report.fix()
            time.sleep(3)
        else:
            log.error(f"Not ready after {attempt} fix attempts:\n{report.summary()}")
            return report

    return _check_ready(router_ip, password, phone_mac)


def _check_ready(router_ip: str, password: str, phone_mac: str) -> ReadinessReport:
    """Run all readiness checks."""
    checks: list[Check] = []

    # 1. Router SSH
    out, rc = _ssh(router_ip, "echo OK", password, timeout=10)
    checks.append(Check("router_ssh", rc == 0 and out == "OK", f"SSH to {router_ip}"))

    if not checks[-1].passed:
        # Can't check anything else without SSH
        return ReadinessReport(checks=checks, router_ip=router_ip, password=password, phone_mac=phone_mac)

    # 2. Tollgate running
    out, _ = _ssh(router_ip, "pgrep tollgate && echo UP || echo DOWN", password)
    checks.append(Check("tollgate_running", "UP" in out, f"pgrep: {out}"))

    # 3. NDS running
    out, _ = _ssh(router_ip, "pgrep nodogsplash && echo UP || echo DOWN", password)
    checks.append(Check("nds_running", "UP" in out, f"pgrep: {out}"))

    # 4. API port 2121
    out, _ = _ssh(router_ip, "netstat -tlnp 2>/dev/null | grep -c 2121", password)
    checks.append(Check("api_port", out.strip() == "1" or out.strip() == "2", f"listeners: {out.strip()}"))

    # 5. Portal port 2050
    out, _ = _ssh(router_ip, "netstat -tlnp 2>/dev/null | grep -c 2050", password)
    checks.append(Check("portal_port", out.strip() == "1", f"listeners: {out.strip()}"))

    # 6. NDS branding
    out, _ = _ssh(router_ip, "uci get nodogsplash.@nodogsplash[0].gatewayname", password)
    checks.append(Check("nds_branding", out.strip() == "net4sats", f"gatewayname: {out.strip()}"))

    # 7. Test mint configured
    out, _ = _ssh(router_ip, "grep -c testnut.cashu.exchange /etc/tollgate/config.json", password)
    checks.append(Check("test_mint", out.strip() != "0", f"mint refs: {out.strip()}"))

    # 8. Phone ADB connected
    out, _ = _adb("echo ADB_OK")
    checks.append(Check("phone_adb", "ADB_OK" in out, f"adb response: {out[:30]}"))

    if not checks[-1].passed:
        return ReadinessReport(checks=checks, router_ip=router_ip, password=password, phone_mac=phone_mac)

    # 9. Phone on WiFi
    out, _ = _adb("dumpsys wifi 2>/dev/null | grep 'mWifiInfo.*SSID' | head -1")
    checks.append(Check("phone_wifi", "net4sats-portal" in out, f"SSID: {out[:60]}"))

    # 10. Mobile data disabled
    out, _ = _adb("settings get global mobile_data")
    checks.append(Check("mobile_data", out.strip() == "0", f"mobile_data: {out.strip()}"))

    # 11. Phone deauthenticated
    out, _ = _ssh(router_ip, f"ndsctl clients 2>/dev/null | grep 'state=' | head -1", password)
    checks.append(Check("phone_deauthed", "Preauthenticated" in out, f"NDS state: {out.strip()}"))

    return ReadinessReport(checks=checks, router_ip=router_ip, password=password, phone_mac=phone_mac)
