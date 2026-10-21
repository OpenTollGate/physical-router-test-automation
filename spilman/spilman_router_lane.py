#!/usr/bin/env python3
"""Spilman channel lane — end-to-end against the physical router over serial.

WHAT THIS PROVES
    One router, one channel, one direction, no roaming:
      (a) the channel opens against the LAN-bound test mint
      (b) ZERO mint HTTP calls between open and settle
      (c) settle pays the payee exactly the signed balance
      (d) kill-restart mid-stream does not lose the latest signature

WHAT IT SKIPS, WITH A NAMED REASON
    Two-router lane ............ only one router on the bench
    Roaming / upstream switch .. needs two upstreams; stranding is unresolved
    Keyset rotation ............ needs mint-side rotation control
    mips targets ............... no Rust std for stable; no mips musl toolchain
    A skip is reported as a skip WITH its reason. It is never a silent pass.

MUTEX DISCIPLINE (non-negotiable; this bench is shared)
    Two locks, and the /proc fd scan is AUTHORITATIVE — not flock. Measured
    2026-10-17: flock reported FREE while a foreign process held the tty, because
    that process never took the lock.

    Lock order: acquire() runs BEFORE anything opens the port. A /proc scan is a
    check, not a lock — the discriminating question is not "did you check?" but
    "is acquire() in the code path that opens the port?"

    BUSY = skip and report. Never queue behind another owner on single-owner
    hardware; that is how two "sequential" runs interleave.

USAGE
    python3 spilman/spilman_router_lane.py [--port /dev/ttyUSB0] [--dry-run]

EXIT CODES
    0   lane ran; read the per-check results in the output
    3   bench busy — another owner holds it (SKIPPED, not failed)
    75  a process holds the tty without taking the lock (SKIPPED, not failed)
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import signal
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

SERIAL_LOCK = Path.home() / ".local/state/tg-bench/tollgate-A04E.lock"
BENCH_LOCK = Path.home() / ".hermes/state/bench-mt3000.lock"

# Board identity is decided by the PROMPT, never by the port. The FTDI<->tty
# mapping drifts; measured across sessions.
EXPECTED_HOSTNAMES = {"tollgate-A04E", "tollgate-0195"}

# Billing parity: 1 sat per 22,020,096-byte step. The rate that yields EXACTLY
# 1000 msat per step is 22,021 — at 22,020 the exact ceil is 1001 msat, which
# rounds up to 2 sat and overcharges by 100% at step granularity.
STEP_BYTES = 22_020_096
PARITY_IN_BYTES_PER_MILLISAT = 22_021
STEP_COUNT = 5  # 5 x 21 MiB = 5 sats at parity

EXIT_OK = 0
EXIT_BUSY = 3
EXIT_TTY_UNLOCKED_HOLDER = 75


# --------------------------------------------------------------------------
# Mutex
# --------------------------------------------------------------------------

def tty_holders() -> list[tuple[str, str, str]]:
    """Authoritative check: ask the kernel who has a tty open.

    This is a CHECK, not a lock. It can only ever tell us about OTHER processes;
    it grants no ownership and cannot stop a process that ignores the mutex.
    """
    held: list[tuple[str, str, str]] = []
    for proc in glob.glob("/proc/[0-9]*"):
        try:
            for fd in os.listdir(proc + "/fd"):
                try:
                    target = os.readlink(f"{proc}/fd/{fd}")
                except OSError:
                    continue
                if "ttyUSB" in target or "ttyACM" in target:
                    pid = proc.split("/")[-1]
                    try:
                        raw = open(f"/proc/{pid}/cmdline", "rb").read()
                        cmd = raw.replace(b"\0", b" ").decode(errors="replace").strip()
                    except OSError:
                        cmd = "?"
                    held.append((pid, target, cmd[:100]))
                    break
        except OSError:
            continue
    return held


def competing_tools() -> list[str]:
    """Processes that drive the console. Advisory, and unanchored patterns can
    produce phantoms (a '|tio' alternative matches 'migration/0'), so this is
    reported for context and never trusted alone."""
    import subprocess

    try:
        out = subprocess.run(
            ["pgrep", "-af", r"console-exec|console-probe|router-console|live_harness|tio"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return []
    lines = []
    for line in out.splitlines():
        # Drop the obvious kernel-thread phantom and our own pgrep.
        if "migration/" in line or "pgrep" in line:
            continue
        lines.append(line[:120])
    return lines


class BenchLock:
    """flock on a lock file. The OS drops it if we die, so a crash cannot wedge
    the bench — unlike a pidfile convention."""

    def __init__(self, path: Path, label: str):
        self.path = path
        self.label = label
        self.fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o664)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            os.close(self.fd)
            self.fd = None
            return False

    def release(self) -> None:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


def acquire_bench() -> tuple[BenchLock | None, BenchLock | None, int]:
    """Acquire both locks. Returns (serial, bench, exit_code); exit_code != 0
    means we must stop and report rather than proceed."""
    serial = BenchLock(SERIAL_LOCK, "A04E serial")
    if not serial.acquire():
        print(f"SKIP: {SERIAL_LOCK} is held by another owner. Not queueing.")
        print("      Judge liveness from the holder record's DELTA over time,")
        print("      not from flock alone — a moving pid/start is a live owner.")
        return None, None, EXIT_BUSY

    bench = BenchLock(BENCH_LOCK, "shared bench")
    if not bench.acquire():
        serial.release()
        print(f"SKIP: {BENCH_LOCK} is held by another owner. Not queueing.")
        return None, None, EXIT_BUSY

    # The /proc guard runs AFTER acquiring the lock, and is still only a check.
    holders = tty_holders()
    if holders:
        print("SKIP: a process holds a tty WITHOUT taking the lock:")
        for pid, target, cmd in holders:
            print(f"        pid={pid} {target} :: {cmd}")
        print("      flock said FREE, which is exactly why the fd scan is")
        print("      authoritative. Stopping rather than corrupting their session.")
        bench.release()
        serial.release()
        return None, None, EXIT_TTY_UNLOCKED_HOLDER

    return serial, bench, EXIT_OK


# --------------------------------------------------------------------------
# Serial
# --------------------------------------------------------------------------

def load_serial_module():
    """Load PRTA's SerialConnection by path (as the hedgehog lane does).

    Returns (class, None) on success or (None, reason) on failure, so the caller
    can report a precise reason instead of a traceback. The most common failure by
    far is a missing pyserial in the *interpreter running this lane* — the repo's
    .venv has it, a bare `python3` often does not (recorded trap).
    """
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "scripts" / "router-serial.py"
    if not path.exists():
        return None, f"{path} not found"
    try:
        spec = importlib.util.spec_from_file_location("router_serial", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except ModuleNotFoundError as exc:
        return None, (f"{exc} — run this lane with the repo venv "
                      f"(e.g. .venv/bin/python) or install requirements-serial.txt")
    except Exception as exc:  # noqa: BLE001 - report, never traceback
        return None, f"{type(exc).__name__}: {exc}"
    if not hasattr(mod, "SerialConnection"):
        return None, f"{path} has no SerialConnection"
    return mod.SerialConnection, None


def probe_identity(conn, port: str) -> str:
    """Identify the board by its PROMPT/hostname, never by the port."""
    out, _ = conn.exec_command("cat /proc/sys/kernel/hostname", timeout=20)
    return (out or "").strip()


# --------------------------------------------------------------------------
# Lane
# --------------------------------------------------------------------------

def run_lane(port: str, dry_run: bool) -> int:
    serial_lock, bench_lock, code = acquire_bench()
    if code != EXIT_OK:
        return code

    # A wall-clock alarm so a wedged shell can never pin the bench forever.
    def _alarm(_sig, _frm):
        raise TimeoutError("wall-clock limit reached; releasing the bench")

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(900)

    results: list[dict] = []
    try:
        SerialConnection, why = load_serial_module()
        if SerialConnection is None:
            print(f"FAIL: cannot load the serial driver: {why}")
            return 1

        print(f"[lane] opening {port} (holding the bench lock)")
        with SerialConnection(port) as conn:
            hostname = probe_identity(conn, port)
            print(f"[lane] board hostname: {hostname!r}")
            if hostname not in EXPECTED_HOSTNAMES:
                print(f"FAIL: {port} is not the bench router (expected one of "
                      f"{sorted(EXPECTED_HOSTNAMES)}, got {hostname!r}).")
                print("      Identify the board by its prompt, never by the port.")
                return 1

            if dry_run:
                print("[lane] --dry-run: identity confirmed; stopping before mutation")
                return 0

            # ---- the four assertions -------------------------------------
            results.append({"check": "(a) channel opens against the LAN mint",
                            "status": "TODO", "note": "wire to module /channel"})
            results.append({"check": "(b) zero mint calls between open and settle",
                            "status": "TODO", "note": "read the mint request log"})
            results.append({"check": "(c) settle pays the payee exactly",
                            "status": "TODO", "note": f"{STEP_COUNT} steps = "
                                                       f"{STEP_COUNT} sat at rate "
                                                       f"{PARITY_IN_BYTES_PER_MILLISAT}"})
            results.append({"check": "(d) restart mid-stream keeps the signature",
                            "status": "TODO", "note": "kill after step 3, then resume"})

            skips = [
                ("two-router lane", "only one router on the bench"),
                ("roaming / upstream switch", "needs two upstreams; stranding unresolved"),
                ("keyset rotation mid-channel", "needs mint-side rotation control"),
                ("mips targets", "no Rust std for stable; no mips musl toolchain"),
            ]
    finally:
        signal.alarm(0)
        if bench_lock:
            bench_lock.release()
        if serial_lock:
            serial_lock.release()
        print("[lane] bench locks released")

    print()
    print("=== results ===")
    for r in results:
        print(f"  {r['status']:>6}  {r['check']}  ({r['note']})")
    print()
    print("=== skipped (named reasons, never silent passes) ===")
    for what, why in skips:
        print(f"  SKIP  {what}: {why}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyUSB0",
                    help="serial port; the board is identified by prompt, not port")
    ap.add_argument("--dry-run", action="store_true",
                    help="take the locks, confirm identity, then stop before mutating")
    args = ap.parse_args()
    try:
        return run_lane(args.port, args.dry_run)
    except TimeoutError as exc:
        print(f"FAIL: {exc}")
        return 124


if __name__ == "__main__":
    sys.exit(main())
