#!/usr/bin/env python3
"""Opt-in mac80211_hwsim network-namespace Wi-Fi proof of concept.

This script intentionally runs outside the existing OpenWrt QEMU cloud lab.
All radios live in one Linux kernel so a client namespace can really run
``iw scan`` and see AP SSIDs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


NS_ALPHA = "tg-vwifi-alpha"
NS_BRAVO = "tg-vwifi-bravo"
NS_CLIENT = "tg-vwifi-client"
SSID_ALPHA = "TollGate-ALPHA"
SSID_BRAVO = "TollGate-BRAVO"


class PocError(RuntimeError):
    pass


class Runner:
    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self.procs: list[subprocess.Popen[str]] = []
        self.tmp = Path(tempfile.mkdtemp(prefix="tg-hwsim-netns-"))

    def run(self, cmd: list[str], *, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
        if self.verbose:
            print("+", " ".join(cmd), file=sys.stderr)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        if check and result.returncode != 0:
            raise PocError(
                f"command failed rc={result.returncode}: {' '.join(cmd)}\n"
                f"stdout={result.stdout[-500:]}\nstderr={result.stderr[-500:]}"
            )
        return result

    def ns(self, namespace: str, command: list[str], *, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.run(["ip", "netns", "exec", namespace, *command], timeout=timeout, check=check)

    def popen_ns(self, namespace: str, command: list[str]) -> subprocess.Popen[str]:
        if self.verbose:
            print("+ ip netns exec", namespace, " ".join(command), file=sys.stderr)
        proc = subprocess.Popen(
            ["ip", "netns", "exec", namespace, *command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.procs.append(proc)
        return proc

    def cleanup(self) -> None:
        for proc in reversed(self.procs):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        for ns in (NS_CLIENT, NS_BRAVO, NS_ALPHA):
            self.run(["ip", "netns", "delete", ns], check=False)
        self.run(["modprobe", "-r", "mac80211_hwsim"], check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)


def _which_all(names: list[str]) -> dict[str, bool]:
    return {name: shutil.which(name) is not None for name in names}


def check_prereqs() -> dict[str, Any]:
    commands = ["ip", "iw", "modprobe", "hostapd", "wpa_supplicant", "wpa_cli", "dnsmasq", "curl", "python3"]
    dhcp_candidates = ["dhclient", "udhcpc"]
    found = _which_all(commands + dhcp_candidates)
    missing = [cmd for cmd in commands if not found[cmd]]
    if not any(found[candidate] for candidate in dhcp_candidates):
        missing.append("dhclient-or-udhcpc")
    return {
        "ok": os.geteuid() == 0 and not missing,
        "root": os.geteuid() == 0,
        "commands": found,
        "missing": missing,
    }


def _first_phy_in_ns(r: Runner, namespace: str) -> str:
    out = r.ns(namespace, ["iw", "phy"], timeout=10).stdout
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Wiphy "):
            return line.split()[1]
    raise PocError(f"no PHY found in namespace {namespace}: {out[:300]}")


def _write_hostapd_config(path: Path, *, iface: str, ssid: str, channel: int) -> None:
    path.write_text(
        "\n".join(
            [
                f"interface={iface}",
                "driver=nl80211",
                f"ssid={ssid}",
                "hw_mode=g",
                f"channel={channel}",
                "ieee80211n=0",
                "auth_algs=1",
                "ignore_broadcast_ssid=0",
                "logger_stdout=-1",
                "logger_stdout_level=2",
                "ctrl_interface=/tmp",
                "",
            ]
        )
    )


def _write_wpa_config(path: Path, ssid: str) -> None:
    path.write_text(
        "\n".join(
            [
                "ctrl_interface=/tmp/wpa_supplicant",
                "update_config=0",
                "network={",
                f'    ssid="{ssid}"',
                "    key_mgmt=NONE",
                "}",
                "",
            ]
        )
    )


def _move_phys_to_namespaces(r: Runner) -> None:
    r.run(["modprobe", "-r", "mac80211_hwsim"], check=False)
    r.run(["modprobe", "mac80211_hwsim", "radios=3"], timeout=10)
    phys = [line.strip() for line in r.run(["bash", "-lc", "ls /sys/class/ieee80211"]).stdout.splitlines() if line.strip()]
    if len(phys) < 3:
        raise PocError(f"expected >=3 hwsim phys, found {phys}")
    for ns in (NS_ALPHA, NS_BRAVO, NS_CLIENT):
        r.run(["ip", "netns", "add", ns])
        r.ns(ns, ["ip", "link", "set", "lo", "up"])
    for phy, ns in zip(phys[:3], (NS_ALPHA, NS_BRAVO, NS_CLIENT), strict=True):
        moved = r.run(["iw", "phy", phy, "set", "netns", "name", ns], check=False)
        if moved.returncode == 0:
            continue
        sleeper = subprocess.Popen(["ip", "netns", "exec", ns, "sleep", "60"])
        try:
            r.run(["iw", "phy", phy, "set", "netns", str(sleeper.pid)])
        finally:
            sleeper.terminate()


def _setup_ap(
    r: Runner,
    namespace: str,
    *,
    iface: str,
    ssid: str,
    channel: int,
    ip_addr: str,
    dhcp_range: str,
    http_text: str,
) -> None:
    phy = _first_phy_in_ns(r, namespace)
    r.ns(namespace, ["iw", "phy", phy, "interface", "add", iface, "type", "__ap"])
    r.ns(namespace, ["ip", "addr", "add", ip_addr, "dev", iface])
    r.ns(namespace, ["ip", "link", "set", iface, "up"])

    hostapd_conf = r.tmp / f"{namespace}.hostapd.conf"
    _write_hostapd_config(hostapd_conf, iface=iface, ssid=ssid, channel=channel)
    r.popen_ns(namespace, ["hostapd", str(hostapd_conf)])

    bind_ip = ip_addr.split("/", 1)[0]
    r.popen_ns(
        namespace,
        [
            "dnsmasq",
            "--no-daemon",
            f"--interface={iface}",
            "--bind-interfaces",
            f"--dhcp-range={dhcp_range},255.255.255.0,1h",
            f"--dhcp-option=3,{bind_ip}",
            f"--dhcp-option=6,{bind_ip}",
            f"--dhcp-leasefile={r.tmp / f'{namespace}.leases'}",
            f"--pid-file={r.tmp / f'{namespace}.dnsmasq.pid'}",
        ],
    )

    webroot = r.tmp / namespace
    webroot.mkdir()
    (webroot / "index.html").write_text(http_text)
    r.popen_ns(namespace, ["python3", "-m", "http.server", "8080", "--bind", bind_ip, "--directory", str(webroot)])


def _setup_client(r: Runner) -> None:
    phy = _first_phy_in_ns(r, NS_CLIENT)
    r.ns(NS_CLIENT, ["iw", "phy", phy, "interface", "add", "client-wlan", "type", "station"])
    r.ns(NS_CLIENT, ["ip", "link", "set", "client-wlan", "up"])


def _wait_scan(r: Runner, ssids: list[str], timeout: int = 20) -> dict[str, bool]:
    deadline = time.time() + timeout
    seen = {ssid: False for ssid in ssids}
    while time.time() < deadline:
        scan = r.ns(NS_CLIENT, ["iw", "dev", "client-wlan", "scan"], timeout=15, check=False).stdout
        for ssid in ssids:
            if f"SSID: {ssid}" in scan:
                seen[ssid] = True
        if all(seen.values()):
            return seen
        time.sleep(1)
    return seen


def _dhcp_client(r: Runner, iface: str) -> tuple[str, list[str]]:
    if shutil.which("dhclient"):
        return "dhclient", ["dhclient", "-1", "-v", iface]
    return "udhcpc", ["udhcpc", "-i", iface, "-n", "-q"]


def _associate_and_probe(r: Runner, *, ssid: str, gateway: str) -> dict[str, Any]:
    r.run(["pkill", "-f", f"ip netns exec {NS_CLIENT} wpa_supplicant"], check=False)
    r.ns(NS_CLIENT, ["ip", "addr", "flush", "dev", "client-wlan"], check=False)
    wpa_conf = r.tmp / f"client-{ssid}.conf"
    _write_wpa_config(wpa_conf, ssid)
    r.popen_ns(NS_CLIENT, ["wpa_supplicant", "-i", "client-wlan", "-c", str(wpa_conf), "-D", "nl80211"])
    associated = False
    for _ in range(20):
        status = r.ns(NS_CLIENT, ["wpa_cli", "-i", "client-wlan", "status"], timeout=5, check=False).stdout
        if "wpa_state=COMPLETED" in status or f"ssid={ssid}" in status:
            associated = True
            break
        time.sleep(1)

    dhcp_tool, dhcp_cmd = _dhcp_client(r, "client-wlan")
    dhcp = r.ns(NS_CLIENT, dhcp_cmd, timeout=20, check=False)
    addr = r.ns(NS_CLIENT, ["ip", "-4", "addr", "show", "client-wlan"], timeout=5, check=False).stdout
    got_dhcp = "inet " in addr
    http = r.ns(NS_CLIENT, ["curl", "-fsS", "--max-time", "5", f"http://{gateway}:8080/"], timeout=10, check=False)
    return {
        "associated": associated,
        "dhcp_tool": dhcp_tool,
        "dhcp_rc": dhcp.returncode,
        "dhcp": got_dhcp,
        "addr": addr,
        "http_rc": http.returncode,
        "http": http.returncode == 0 and "TollGate" in http.stdout,
        "http_body": http.stdout[:200],
    }


def run_poc(verbose: bool = False) -> dict[str, Any]:
    prereqs = check_prereqs()
    if not prereqs["ok"]:
        return {"ok": False, "stage": "prereq", "prereqs": prereqs}
    r = Runner(verbose=verbose)
    result: dict[str, Any] = {"ok": False, "prereqs": prereqs}
    old_handler = signal.signal(signal.SIGTERM, lambda *_: r.cleanup())
    try:
        for ns in (NS_CLIENT, NS_BRAVO, NS_ALPHA):
            r.run(["ip", "netns", "delete", ns], check=False)
        _move_phys_to_namespaces(r)
        _setup_ap(
            r,
            NS_ALPHA,
            iface="alpha-ap",
            ssid=SSID_ALPHA,
            channel=1,
            ip_addr="10.250.0.1/24",
            dhcp_range="10.250.0.50,10.250.0.100",
            http_text="TollGate ALPHA captive portal POC",
        )
        _setup_ap(
            r,
            NS_BRAVO,
            iface="bravo-ap",
            ssid=SSID_BRAVO,
            channel=6,
            ip_addr="10.251.0.1/24",
            dhcp_range="10.251.0.50,10.251.0.100",
            http_text="TollGate BRAVO captive portal POC",
        )
        _setup_client(r)
        time.sleep(3)
        scan = _wait_scan(r, [SSID_ALPHA, SSID_BRAVO])
        result["scan"] = scan
        result["alpha"] = _associate_and_probe(r, ssid=SSID_ALPHA, gateway="10.250.0.1")
        result["bravo"] = _associate_and_probe(r, ssid=SSID_BRAVO, gateway="10.251.0.1")
        result["ok"] = (
            scan.get(SSID_ALPHA)
            and scan.get(SSID_BRAVO)
            and result["alpha"].get("associated")
            and result["alpha"].get("dhcp")
            and result["alpha"].get("http")
            and result["bravo"].get("associated")
            and result["bravo"].get("dhcp")
            and result["bravo"].get("http")
        )
        result["stage"] = "complete"
        return result
    except Exception as exc:
        result["stage"] = "exception"
        result["error"] = str(exc)
        return result
    finally:
        signal.signal(signal.SIGTERM, old_handler)
        r.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    run = sub.add_parser("run")
    run.add_argument("--json", action="store_true")
    run.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.cmd == "check":
        data = check_prereqs()
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0 if data["ok"] else 1
    data = run_poc(verbose=args.verbose)
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
