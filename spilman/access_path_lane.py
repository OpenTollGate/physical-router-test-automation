#!/usr/bin/env python3
"""Run the access-path probe ON the physical router. Non-invasive, mutex-guarded.

Deploys one binary to /tmp, fetches it with the router's own wget, verifies the
sha256 on-device, runs it, removes it. No service stopped, no config written, no
binary replaced, no global state touched.

MUTEX: the lane's own acquire_bench() -- serial lock + shared bench lock + /proc
fd scan -- taken BEFORE the port opens. BUSY = skip, never queue.
"""
import hashlib
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
import spilman_router_lane as L

# The wire the router is cabled to. NOT discoverable reliably -- see the
# --host discussion in spilman/README.md: the default-route address is not
# necessarily the one the router can reach, and both auto-detection attempts
# were withdrawn after measuring. Pass --host.
HOST_IP = os.environ.get("SPILMAN_HOST_IP", "")
HTTP_PORT = int(os.environ.get("SPILMAN_HTTP_PORT", "18995"))
SRV_DIR = pathlib.Path(os.environ.get("SPILMAN_PROBE_DIR",
                                      str(pathlib.Path.home() / "artifacts-spilman" / "deploy5")))
BIN = SRV_DIR / "access_credit_probe"
RD = "/tmp/spilman-access"


def raw(ser, line, settle=6.0):
    ser.reset_input_buffer()
    ser.write((line + "\r\n").encode())
    buf = b""
    deadline = time.monotonic() + settle
    while time.monotonic() < deadline:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            deadline = time.monotonic() + 0.7
        else:
            time.sleep(0.05)
    return buf.decode("utf-8", "replace")


def read_file_on_router(ser, path, tries=12):
    """cat a file and return ONLY its content, never the command echo.

    The console echoes the typed line before running it. Distinguishing the echo
    from the output by shape is unreliable -- `cat /tmp/.../sha.txt` contains
    hex-looking characters, so a naive parser "finds" an 8-character answer in the
    echo of the command itself. Instead: poll until a line appears that is neither
    the command nor a prompt, or give up and return raw text for the caller to
    inspect.
    """
    for _ in range(tries):
        out = raw(ser, f"cat {path}", settle=5.0)
        lines = [l.strip() for l in out.splitlines()]
        body = [l for l in lines
                if l and f"cat {path}" not in l and not l.startswith("root@")]
        if body:
            return "\n".join(body)
        time.sleep(0.4)
    return ""


def main():
    if not HOST_IP:
        print("ABORT: set SPILMAN_HOST_IP to the bench-host address the router "
              "can reach (it is NOT the default-route address -- see README.md).")
        return 2
    if not BIN.is_file():
        print(f"ABORT: {BIN} not found. Build it with:\n"
              f"  cargo build --release --target aarch64-unknown-linux-musl \\\n"
              f"    --features test-support --example access_credit_probe\n"
              f"then copy it to {SRV_DIR}/")
        return 2
    want = hashlib.sha256(BIN.read_bytes()).hexdigest()
    print(f"### host sha256 {want[:32]}…  ({BIN.stat().st_size} bytes)")

    srv = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(HTTP_PORT), "--bind", "0.0.0.0"],
        cwd=str(SRV_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Wait for the LISTENER, not for a sleep. A fixed sleep raced the bind and the
    # router answered "Failed to send request: Operation not permitted" from a
    # connect to a closed port -- which reads like a firewall block and is not.
    import socket as _sock
    for _ in range(60):
        try:
            with _sock.create_connection(("127.0.0.1", HTTP_PORT), timeout=0.4):
                break
        except OSError:
            time.sleep(0.15)
    else:
        srv.terminate()
        print(f"ABORT: artifact server never bound :{HTTP_PORT}")
        return 4

    serial_lock, bench_lock, rc = L.acquire_bench()
    if rc != L.EXIT_OK:
        srv.terminate()
        print(f"SKIP: bench busy (rc={rc}) — not queueing")
        return rc
    ser = None
    try:
        import serial
        ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=1)
        # Identify by PROMPT, with retries: a fresh open often returns an empty
        # line because the console was mid-prompt. Never identify by port.
        banner = ""
        for _ in range(6):
            banner += raw(ser, "", settle=2.0)
            if "tollgate-0195" in banner:
                break
            raw(ser, "", settle=1.0)
        if "tollgate-0195" not in banner:
            print(f"ABORT: wrong board: {banner[:200]!r}")
            return 5
        print("### board identified by prompt: tollgate-0195")

        # Deploy: short commands only (the console wraps at ~80 chars).
        raw(ser, f"rm -rf {RD} && mkdir -p {RD}", settle=3.0)
        url = f"http://{HOST_IP}:{HTTP_PORT}/access_credit_probe"
        out = raw(ser, f"wget -q -O {RD}/p {url} && echo FETCH_OK || echo FETCH_FAIL",
                  settle=20.0)
        if "FETCH_OK" not in out:
            print(f"ABORT: fetch failed. Output:\n{out[-400:]}")
            return 4
        # The console wraps long output mid-token, so read the hash from a FILE
        # with a short `cat`, not from an inline `sha256sum` echo.
        # NOTE: no pipe here. `sha256sum ... | cut > file` produced an EMPTY file on
        # this console (the pipe was mangled); the plain redirect works. Measured
        # 2026-10-24. Short commands, no pipes.
        raw(ser, f"sha256sum {RD}/p > {RD}/sha.txt", settle=8.0)
        # `sha256sum` prints "<hash>  <path>"; the path is also hex-ish, so take
        # the FIRST 64-hex run rather than stripping non-hex from the whole line.
        import re as _re
        # Read with a helper that WAITS for the marker, not for a fixed settle: a
        # short settle captures only the console's echo of the typed command, and
        # the echo of `cat <path>` looks exactly like a plausible 8-hex answer.
        sha_text = read_file_on_router(ser, f"{RD}/sha.txt")
        got = ""
        m = _re.search(r"([0-9a-f]{64})", sha_text)
        if m:
            got = m.group(1)
        print(f"### sha.txt raw: {sha_text.strip()[:90]!r}")
        print(f"### router sha256 {got[:32]}…")
        if got != want:
            print("ABORT: sha256 MISMATCH — refusing to run foreign bytes")
            return 4
        print("### integrity OK: router bytes == host bytes")

        raw(ser, f"chmod +x {RD}/p", settle=2.0)
        run = raw(ser, f"cd {RD} && LD_LIBRARY_PATH=/usr/lib:/lib ./p "
                       f"> {RD}/out.txt 2>&1; echo RC=$?", settle=30.0)
        print(f"### run: {run.strip()[-40:] if run else '(no output)'}")
        report = raw(ser, f"cat {RD}/out.txt", settle=6.0)
        print("### ---- on-device report ----")
        print(report)

        # Clean up only what we created.
        raw(ser, f"rm -rf {RD}", settle=3.0)

        failures = [l for l in report.splitlines() if l.strip().startswith("FAIL")]
        if failures:
            print(f"### {len(failures)} case(s) FAILED on hardware")
            return 1
        if "ACCESS_PATH_OK" in report:
            print("### ACCESS PATH PROVEN ON HARDWARE")
            return 0
        print("### INCONCLUSIVE: no ACCESS_PATH_OK marker")
        return 6
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass
        bench_lock.release()
        serial_lock.release()
        srv.terminate()
        print("### locks released, server stopped")


if __name__ == "__main__":
    sys.exit(main())
