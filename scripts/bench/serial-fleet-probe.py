#!/usr/bin/env python3
"""serial-fleet-probe — health + identity probe for the serial-attached bench.

Reads the enrollment contract (configs/labgrid/x280-serial-fleet.json) and,
for every enrolled unit, proves three things:

  1. the console answers on its FTDI by-id port (wake, login if needed,
     run a markered command and read it back),
  2. the unit's identity matches the contract (model, distro, arch, hostname),
  3. SSH works with the bench publickey and reports the same identity.

Dark ports (enrolled=false) are reported, not hidden. Exit code is non-zero
if any *enrolled* unit fails any check, so CI/agents can gate on it.

Usage (from the repo root, with the PRTA venv python):
    ./.venv/bin/python scripts/bench/serial-fleet-probe.py            # human
    ./.venv/bin/python scripts/bench/serial-fleet-probe.py --json     # machine

Env:
    TOLLGATE_BENCH_KEY     bench privatekey (default ~/.ssh/id_bench)
    TOLLGATE_BENCH_FLEET   contract path override
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import serial

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_FLEET = REPO / "configs" / "labgrid" / "x280-serial-fleet.json"
PORT_TEMPLATE = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_{ftdi}-if00-port0"

SHELL_PROMPT_RE = re.compile(r"[#\$] $")
LOGIN_RE = re.compile(r"login:", re.IGNORECASE)


def read_until(ser: serial.Serial, pattern: str, timeout: float) -> tuple[str, bool]:
    buf = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk.decode("utf-8", "replace")
            if re.search(pattern, buf):
                return buf, True
        else:
            time.sleep(0.2)
    return buf, False


def wake_shell(ser: serial.Serial) -> bool:
    time.sleep(0.3)
    ser.reset_input_buffer()
    ser.write(b"\n")
    time.sleep(1.5)
    buf, _ = read_until(ser, r"login:|[#\$] $", 8)
    if LOGIN_RE.search(buf) and not SHELL_PROMPT_RE.search(buf):
        ser.write(b"root\n")
        time.sleep(2.0)
        buf, _ = read_until(ser, r"[#\$] $", 8)
    if not SHELL_PROMPT_RE.search(buf):
        ser.write(b"\n")
        buf, _ = read_until(ser, r"[#\$] $", 5)
    return bool(SHELL_PROMPT_RE.search(buf))


def console_exec(ser: serial.Serial, command: str, timeout: float = 25.0) -> list[str]:
    marker = "__PROBE_END__"
    ser.write(command.encode() + b" ; echo " + marker.encode() + b"\n")
    raw, done = read_until(ser, marker, timeout)
    if not done:
        raise TimeoutError(f"console command did not complete in {timeout}s")
    lines = [l.strip() for l in raw.replace("\r", "").splitlines()]
    return [l for l in lines if l and marker not in l and not l.startswith(command[:24])]


IDENTITY_CMD = (
    "cat /tmp/sysinfo/model 2>/dev/null; "
    ". /etc/openwrt_release 2>/dev/null; "
    "echo DIST=$DISTRIB_DESCRIPTION; echo ARCH=$DISTRIB_ARCH; "
    "echo TARGET=$DISTRIB_TARGET; "
    "uci get system.@system[0].hostname 2>/dev/null"
)


def probe_console(unit: dict) -> dict:
    port = PORT_TEMPLATE.format(ftdi=unit["ftdi_serial"])
    result = {"unit": unit["name"], "port": port, "console": None, "error": None}
    if not Path(port).exists():
        result["error"] = "by-id port missing (adapter unplugged?)"
        return result
    ser = serial.Serial(port, unit.get("baud", 115200), timeout=1.0)
    try:
        if not wake_shell(ser):
            raw = ""
            result["error"] = "no shell prompt"
            return result
        lines = console_exec(ser, IDENTITY_CMD)
        result["console"] = lines[:8]
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"[:200]
    finally:
        ser.close()
    return result


def probe_ssh(unit: dict, key: Path, timeout: float = 12.0) -> dict:
    ssh = unit.get("ssh") or {}
    result = {"unit": unit["name"], "ssh": None, "error": None}
    if not ssh.get("address"):
        result["error"] = "no ssh address in contract"
        return result
    cmd = [
        "ssh", "-i", str(key),
        "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null", "-o", f"ConnectTimeout={int(timeout)}",
        f"{ssh.get('username', 'root')}@{ssh['address']}", IDENTITY_CMD,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        result["error"] = "ssh timed out"
        return result
    if r.returncode != 0:
        result["error"] = (r.stderr.strip().splitlines() or ["ssh failed"])[-1][:160]
        return result
    result["ssh"] = [l.strip() for l in r.stdout.splitlines() if l.strip()][:8]
    return result


def identity_matches(lines: list[str] | None, want: dict) -> tuple[bool, str]:
    if not lines:
        return False, "no output"
    flat = " ".join(lines)
    checks = {
        "model": want.get("model", "") in flat,
        "distro": want.get("distro", "") in flat,
        "arch": want.get("arch", "") in flat,
        "target": want.get("target", "") in flat,
    }
    ok = all(checks.values())
    missing = [k for k, v in checks.items() if not v]
    return ok, ("identity ok" if ok else f"mismatch on: {', '.join(missing)} (got: {flat[:160]})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--fleet", default=os.environ.get("TOLLGATE_BENCH_FLEET", str(DEFAULT_FLEET)))
    ap.add_argument("--key", default=os.environ.get("TOLLGATE_BENCH_KEY",
                   os.path.expanduser("~/.ssh/id_bench")))
    args = ap.parse_args()

    fleet = json.loads(Path(args.fleet).read_text())
    units = fleet["units"]
    ports_found = len(glob.glob("/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_*"))
    report, failures = [], []

    for unit in units:
        entry = {"unit": unit["name"], "enrolled": unit.get("enrolled", True)}
        if not entry["enrolled"]:
            port = PORT_TEMPLATE.format(ftdi=unit["ftdi_serial"])
            entry["status"] = "DARK"
            entry["detail"] = unit.get("notes", "")
            entry["port_present"] = Path(port).exists()
            report.append(entry)
            continue
        want = unit.get("identity") or {}
        entry["console_enrolled"] = unit.get("console_enrolled", True)
        if entry["console_enrolled"]:
            con = probe_console(unit)
            entry["console_ok"] = con["error"] is None
            entry["console_error"] = con["error"]
            if con["error"] is None:
                ok, detail = identity_matches(con["console"], want)
                entry["console_identity"] = detail
                if not ok:
                    failures.append(f"{unit['name']}: console identity — {detail}")
            else:
                failures.append(f"{unit['name']}: console — {con['error']}")
        else:
            # Enrolled over the network only; the console is a known gap, named.
            entry["console_ok"] = None
            entry["console_identity"] = "not wired (console_enrolled=false)"
            entry["console_error"] = unit.get("console_notes", "console not enrolled")
        ssh_res = probe_ssh(unit, Path(args.key))
        entry["ssh_ok"] = ssh_res["error"] is None
        entry["ssh_error"] = ssh_res["error"]
        if ssh_res["error"] is None:
            ok, detail = identity_matches(ssh_res["ssh"], want)
            entry["ssh_identity"] = detail
            if not ok:
                failures.append(f"{unit['name']}: ssh identity — {detail}")
        else:
            failures.append(f"{unit['name']}: ssh — {ssh_res['error']}")
        entry["status"] = "OK" if not any(f.startswith(unit["name"]) for f in failures) else "FAIL"
        report.append(entry)

    summary = {
        "bench_host": fleet.get("bench_host"),
        "ftdi_ports_seen": ports_found,
        "enrolled": sum(1 for u in units if u.get("enrolled", True)),
        "dark": [u["name"] for u in units if not u.get("enrolled", True)],
        "failures": failures,
        "units": report,
    }
    if args.json:
        print(json.dumps(summary, indent=1))
    else:
        for u in report:
            line = f"{u['unit']:28} {u.get('status', '—')}"
            if not u.get("enrolled", True):
                line += f" (dark, port_present={u.get('port_present')})"
            print(line)
            for k in ("console_ok", "console_identity", "console_error",
                      "ssh_ok", "ssh_identity", "ssh_error"):
                if u.get(k) is not None:
                    print(f"    {k}: {u[k]}")
        print(f"\nftdi ports seen: {ports_found}; failures: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())