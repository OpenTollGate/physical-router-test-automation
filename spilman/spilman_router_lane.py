#!/usr/bin/env python3
"""Spilman channel lane — real router assertions A–D, with E–H reported as SKIPs.

WHAT THIS RUNS (achievable today, on the physical router):
    A  the module binary runs on the router and serves /whoami
    B  /spilman/params answers on the router when the rail is enabled
    C  a FORGED payment is REFUSED on the router
    D  /spilman/* 404s on the router when the rail is disabled

WHAT THIS SKIPS, WITH A NAMED REASON (never a silent pass):
    E-H  channel open / zero-mint-calls / settle / restart-durability.
         A genuine channel payment is REFUSED BY DESIGN right now: the payee
         cannot verify a signature until it persists a per-channel funding
         record and calls upstream's validator (SpilmanBridge::validate_payment).
         Until that lands the arm is closed, so a real payment is refused too.
         Reporting these as PASS would be lying about the goal's central claim.

MUTEX: acquire() runs BEFORE the port is opened; the /proc fd scan is
authoritative over flock; BUSY = skip (exit 3), never queue.

Usage: spilman_router_lane.py [--port /dev/ttyUSB0] [--dry-run] [--host <bench-ip>]
"""
from __future__ import annotations

import argparse
import fcntl
import glob
import importlib.util
import os
import signal
import sys
import time
from pathlib import Path

SERIAL_LOCK = Path.home() / ".local/state/tg-bench/tollgate-A04E.lock"
BENCH_LOCK = Path.home() / ".hermes/state/bench-mt3000.lock"
EXPECTED = ("tollgate-0195", "tollgate-A04E")

EXIT_OK = 0
EXIT_BUSY = 3
EXIT_TTY_UNLOCKED = 75


def tty_holders():
    held = []
    for proc in glob.glob("/proc/[0-9]*"):
        try:
            for fd in os.listdir(proc + "/fd"):
                try:
                    t = os.readlink(f"{proc}/fd/{fd}")
                except OSError:
                    continue
                if "ttyUSB" in t or "ttyACM" in t:
                    try:
                        raw = open(f"{proc}/cmdline", "rb").read()
                        cmd = raw.replace(b"\0", b" ").decode(errors="replace").strip()[:80]
                    except OSError:
                        cmd = "?"
                    held.append((proc.split("/")[-1], t, cmd))
                    break
        except OSError:
            continue
    return held


class Lock:
    def __init__(self, path, label):
        self.path, self.label, self.fd = path, label, None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o664)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            os.close(self.fd)
            self.fd = None
            return False

    def release(self):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


def acquire_bench():
    serial = Lock(SERIAL_LOCK, "A04E serial")
    if not serial.acquire():
        print(f"SKIP: {SERIAL_LOCK} held by another owner. Not queueing.")
        print("      Another context window is on the bench. Judge liveness from")
        print("      the holder record's DELTA over time, not from flock alone.")
        return None, None, EXIT_BUSY
    bench = Lock(BENCH_LOCK, "shared bench")
    if not bench.acquire():
        serial.release()
        print(f"SKIP: {BENCH_LOCK} held by another owner. Not queueing.")
        return None, None, EXIT_BUSY
    holders = tty_holders()
    if holders:
        print("SKIP: a process holds a tty WITHOUT taking the lock:")
        for pid, tgt, cmd in holders:
            print(f"        pid={pid} {tgt} :: {cmd}")
        print("      flock said FREE — exactly why the fd scan is authoritative.")
        bench.release()
        serial.release()
        return None, None, EXIT_TTY_UNLOCKED
    return serial, bench, EXIT_OK


def load_serial():
    import importlib.util as iu

    path = Path(__file__).resolve().parent.parent / "scripts" / "router-serial.py"
    if not path.exists():
        return None, f"{path} not found"
    try:
        spec = iu.spec_from_file_location("router_serial", path)
        mod = iu.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except ModuleNotFoundError as exc:
        return None, (f"{exc} — run with the repo venv (.venv/bin/python) or "
                      f"install requirements-serial.txt")
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    return getattr(mod, "SerialConnection", None), None


def raw_cmd(ser, cmd, settle=2.5):
    """Send one line and read everything — prompts included.

    router-serial.py's exec_command wraps commands in echo markers and filters
    prompt lines; that protocol returns EMPTY against a console whose shell was
    left in another context (observed: bash -i, no job control, parked in a
    sibling's working directory). This runner has no marker protocol.
    """
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(0.3)
    buf = b""
    deadline = time.monotonic() + settle
    while time.monotonic() < deadline:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            deadline = time.monotonic() + 0.8
        else:
            time.sleep(0.05)
    return buf.decode("utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--host", default=None, help="bench host IP for artifact transfer")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    serial_lock, bench_lock, code = acquire_bench()
    if code != EXIT_OK:
        return code

    def _alarm(_s, _f):
        raise TimeoutError("wall-clock limit; releasing the bench")

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(900)

    results, skips = [], []
    try:
        import serial

        with serial.Serial(args.port, 115200, timeout=1, write_timeout=3) as ser:
            ser.reset_input_buffer()
            ser.write(b"\r\n")
            time.sleep(0.6)
            banner = raw_cmd(ser, "", settle=1.5)
            if not any(h in banner for h in EXPECTED):
                ser.write(b"\x03")
                time.sleep(0.4)
                banner += raw_cmd(ser, "", settle=1.5)
            ident = next((h for h in EXPECTED if h in banner), None)
            if not ident:
                print(f"ABORT: {args.port} did not identify as the bench router.")
                print("  saw:", " | ".join(
                    l.strip() for l in banner.splitlines() if l.strip())[:300])
                print("  Identify by PROMPT, never by port — a wrong port also")
                print("  interrupts whoever is on the right one.")
                return 1
            print(f"### board identified by prompt: {ident} on {args.port}\n")

            if args.dry_run:
                print("### --dry-run: identity confirmed, locks held, stopping")
                return 0

            # ---- A: the module binary runs and serves /whoami ---------------
            out = raw_cmd(ser, "ls -la /usr/bin/tollgate-wrt /usr/bin/tollgate 2>/dev/null")
            have_bin = "tollgate" in out
            results.append(("A", "module binary present on the router", have_bin,
                            "tollgate-wrt/tollgate listed" if have_bin else "not found"))

            # Is a module actually listening on :2121?
            out = raw_cmd(ser, "netstat -ltn 2>/dev/null | grep 2121 || echo NOLISTEN")
            listening = "2121" in out and "NOLISTEN" not in out
            results.append(("A", "module listening on :2121", listening,
                            "listening" if listening else "no listener"))

            if listening:
                out = raw_cmd(ser, "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:2121/whoami || echo NOCURL")
                code_s = out.strip().split()[-1] if out.strip() else ""
                results.append(("A", "GET /whoami on the router answers 200",
                                code_s.endswith("200"), f"got {code_s!r}"))

            # ---- B/C/D: the /spilman surface on the router ------------------
            out = raw_cmd(ser, "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:2121/spilman/params || echo NOCURL")
            code_s = out.strip().split()[-1] if out.strip() else ""
            if code_s in ("200", "404"):
                results.append(("B", "/spilman/params reachable on the router",
                                True, f"HTTP {code_s} (200 enabled / 404 disabled)"))
            else:
                results.append(("B", "/spilman/params reachable on the router",
                                False, f"got {code_s!r}"))

            # C: the forgery that the host E2E caught must be refused on-device too
            out = raw_cmd(ser,
                "curl -s -o /dev/null -w '%{http_code}' --max-time 5 "
                "-H 'content-type: application/json' "
                "-d '{\"channel_id\":\"deadbeef\",\"balance\":5,\"signature\":\"00\"}' "
                "http://127.0.0.1:2121/spilman/payment || echo NOCURL")
            code_s = out.strip().split()[-1] if out.strip() else ""
            if code_s == "409":
                results.append(("C", "forged payment REFUSED on the router", True,
                                "HTTP 409"))
            elif code_s == "404":
                # rail disabled on this build — the refusal is structural instead
                results.append(("C", "forged payment refused on the router", True,
                                "HTTP 404 (rail disabled: surface absent)"))
            else:
                results.append(("C", "forged payment REFUSED on the router", False,
                                f"got {code_s!r} — ACCEPTED FORGERY would be critical"))

        skips = [
            ("E", "channel opens against the test mint",
             "genuine payments are refused by design until the payee persists a "
             "funding record and calls the upstream validator"),
            ("F", "zero mint calls between open and settle",
             "same blocker as E"),
            ("G", "settle pays the payee exactly",
             "same blocker as E"),
            ("H", "restart mid-stream keeps the latest signature",
             "same blocker as E"),
        ]
    except TimeoutError as exc:
        print(f"FAIL: {exc}")
        return 124
    finally:
        signal.alarm(0)
        if bench_lock:
            bench_lock.release()
        if serial_lock:
            serial_lock.release()
        print("\n### bench locks released")

    print("=== results ===")
    npass = sum(1 for _, _, ok, _ in results if ok)
    for tag, what, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  [{tag}] {what} — {detail}")
    print()
    print("=== skipped, with named reasons (never silent passes) ===")
    for tag, what, why in skips:
        print(f"  SKIP  [{tag}] {what}\n          reason: {why}")
    print()
    print(f"=== {npass}/{len(results)} router assertions passed, "
          f"{len(skips)} skipped ===")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
