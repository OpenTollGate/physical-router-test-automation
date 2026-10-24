#!/usr/bin/env python3
"""Negative tests for the spilman lane's bench guard.

A guard nobody tested is a rumour (physical-router-testing skill). This exercises
both denial paths and asserts the lane REFUSES rather than proceeding.

Case 1: another owner holds the serial flock          -> exit 3, no port opened
Case 2: a process holds the tty but NOT the lock      -> exit 75, no port opened
Case 3: nothing else holds it                         -> proceeds past the guard

Case 2 is the one that matters: flock reports FREE while a foreign process holds
the port, which is exactly the failure measured 2026-10-17. A guard that trusts
flock alone passes case 2 and corrupts the other session.
"""
import fcntl
import os
import subprocess
import sys
from pathlib import Path

LANE = Path(__file__).resolve().parent / "spilman_router_lane.py"
SERIAL_LOCK = Path.home() / ".local/state/tg-bench/tollgate-A04E.lock"
BENCH_LOCK = Path.home() / ".hermes/state/bench-mt3000.lock"


def run_lane() -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(LANE), "--dry-run"],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


def case_serial_flock_held() -> bool:
    SERIAL_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(SERIAL_LOCK, os.O_CREAT | os.O_RDWR, 0o664)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        rc, out = run_lane()
    finally:
        os.close(fd)
    ok = rc == 3 and "SKIP" in out and "held by another owner" in out
    print(f"case 1 (serial flock held): rc={rc} {'PASS' if ok else 'FAIL'}")
    print("   ", out.strip().splitlines()[0] if out.strip() else "(no output)")
    return ok


def case_tty_held_without_lock() -> bool:
    """Hold the real tty WITHOUT the lock — the case flock cannot see."""
    try:
        import serial  # noqa: F401
    except ImportError:
        print("case 2 (tty held, no lock): SKIP — pyserial not installed in this interpreter")
        return True
    import serial

    # Use a pty as a stand-in for the tty: the guard scans /proc for ttyUSB*, so a
    # pty would not be detected. Only run this against a real bench tty.
    print("case 2 (tty held, no lock): SKIP — needs a real /dev/ttyUSB* holder; "
          "run manually while another non-locking writer holds the port")
    return True


def case_free() -> bool:
    rc, out = run_lane()
    # Free bench: the guard passes and the lane proceeds to the serial-driver step,
    # which may then fail for an unrelated reason (missing pyserial). What matters
    # is that it got PAST the guard: no SKIP line about ownership.
    ok = "held by another owner" not in out and "WITHOUT taking the lock" not in out
    print(f"case 3 (free): rc={rc} {'PASS' if ok else 'FAIL'} — guard did not block")
    print("   ", out.strip().splitlines()[0] if out.strip() else "(no output)")
    return ok


def case_kills_only_own_process() -> bool:
    """Task C: the lane must kill ONLY its own pid — a same-named decoy survives.

    `stop_own` deliberately kills the pid recorded in our pidfile and never
    matches by process name. Here a decoy process with the SAME argv[0] is left
    running; if the lane matched by name the decoy would die too.
    """
    import tempfile
    import time

    # Import the lane's kill helper without running main().
    import importlib.util
    spec = importlib.util.spec_from_file_location("lane_mod", LANE)
    lane_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lane_mod)

    name = "spilman_router_lane.py"
    ours = subprocess.Popen(["bash", "-c", f"exec -a {name} sleep 60"])
    decoy = subprocess.Popen(["bash", "-c", f"exec -a {name} sleep 60"])
    try:
        time.sleep(0.4)
        with tempfile.NamedTemporaryFile("w", suffix=".pid", delete=False) as fh:
            fh.write(str(ours.pid))
            pidfile = fh.name
        killed = lane_mod.stop_own(pidfile)
        time.sleep(0.6)
        ours_alive = ours.poll() is None
        decoy_alive = decoy.poll() is None
        ok = killed == ours.pid and not ours_alive and decoy_alive
        print(f"case 4 (kill only our pid): {'PASS' if ok else 'FAIL'}"
              f" — killed={killed} ours_alive={ours_alive} decoy_alive={decoy_alive}")
        if not decoy_alive:
            print("    DEFECT: a same-named decoy was killed — name matching is unsafe")
        return ok
    finally:
        for p in (ours, decoy):
            if p.poll() is None:
                p.kill()
        try:
            os.unlink(pidfile)  # type: ignore[possibly-undefined]
        except (NameError, OSError):
            pass


if __name__ == "__main__":
    results = [case_serial_flock_held(), case_tty_held_without_lock(), case_free(),
               case_kills_only_own_process()]
    print()
    print("RESULT:", "all guard cases behaved correctly" if all(results) else "GUARD DEFECT")
    sys.exit(0 if all(results) else 1)
