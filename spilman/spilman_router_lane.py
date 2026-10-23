#!/usr/bin/env python3
"""Spilman channel lane — real router assertions A–H, under the bench mutex.

WHAT THIS RUNS, on the physical bench router
--------------------------------------------
    A  the deployed module runs on the router and serves /whoami
    B  /spilman/params answers on the router when the rail is enabled
    C  a FORGED payment is REFUSED on the router
    D  /spilman/* 404s on the router when the rail is disabled
    E  channel opens against the test mint
    F  ZERO mint calls between open and settle  (MEASURED by a counting proxy)
    G  settle pays the payee exactly
    H  restart mid-stream keeps the latest signature

TASK B — DEPLOY
---------------
The lane pushes the built module binary and the `spilman-payer` driver to the
router itself, verifies sha256 ON THE ROUTER against the host, and runs
everything on a PRIVATE port + PRIVATE state dir so it can never collide with
another context window's services. Transfer is HTTP (`http.server` on this host,
BusyBox `wget` on the router) — never serial, which wraps at ~80 columns.

TASK F — WHY A PROXY
--------------------
"Zero mint calls between open and settle" is a claim about bytes on the wire, so
it is measured by `spilman/mint_proxy.py` sitting in front of the test mint and
counting the requests it forwards. The proxy is armed only for the open→settle
window (open legitimately spends one funding swap). If the count is > 0 the
assertion FAILS. A log-scrape could never fail, which is why it is not used.

SKIP vs FAIL
------------
E–H depend on the sibling's `POST /spilman/channel` route and the `spilman-payer`
driver binary. Both are PROBED, never assumed. If the prerequisite is absent the
assertion is SKIP with a named reason. If the prerequisite is present and the
protocol misbehaves, the assertion FAILS. A SKIP is never a silent pass.

MUTEX: acquire() runs BEFORE the port is opened; the /proc fd scan is
authoritative over flock; BUSY = skip (exit 3), never queue; every exit path
kills only our own pid and releases the locks.

Usage:
    python3 spilman/spilman_router_lane.py [--port /dev/ttyUSB0] [--dry-run]
"""
from __future__ import annotations

import argparse
import fcntl
import glob
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mint_proxy import CountingMintProxy  # noqa: E402

SERIAL_LOCK = Path.home() / ".local/state/tg-bench/tollgate-A04E.lock"
BENCH_LOCK = Path.home() / ".hermes/state/bench-mt3000.lock"
EXPECTED = ("tollgate-0195", "tollgate-A04E")

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BUSY = 3
EXIT_TTY_UNLOCKED = 75

# Ports this lane must NEVER take: :2121 is another context window's module and
# :2122 is the port a sibling window used in device-test-cmds.txt. The lane runs
# on a private port and refuses to start on a reserved one.
RESERVED_PORTS = {"2121", "2122"}
DEFAULT_LANE_PORT = os.environ.get("SPILMAN_LANE_PORT", "2123")

# The agreed marker contract the payer driver must emit on stdout. The lane
# parses these; a driver that exists but does not emit them is a FAIL (the
# binary does not meet the interface), not a SKIP.
RE_OPEN_OK = re.compile(r"PAYER_OPEN_OK\s+channel_id=([0-9a-fA-F]{8,})")
RE_SETTLE_OK = re.compile(
    r"PAYER_SETTLE_OK\s+channel_id=([0-9a-fA-F]{8,})\s+balance=(\d+)\s+payee_credited=(\d+)")
RE_EXIT = re.compile(r"PAYER_EXIT=(\d+)")


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


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PROMPT = re.compile(r"^\s*[\w.-]+@[\w.-]+:.*#\s*$")


def clean(out: str) -> list[str]:
    """Strip ANSI, CRs, echoed command lines, and prompt lines.

    WHY THIS EXISTS (two real bugs, both of which made green code report red):
      1. `last_line()` returned the trailing shell PROMPT
         (`root@tollgate-0195:/tmp/x#`), not the command's output — so every
         sha256/code comparison failed against a prompt string.
      2. `if ':2123 ' in out and 'NOLISTEN' not in out` was ALWAYS false: the
         console ECHOES the command, so the sentinel `NOLOOK`/`NOLISTEN` from
         `|| echo NOLISTEN` appeared in the transcript even on success.

    The durable fix is not to parse free-form output at all: commands print a
    `<<M:value>>` sentinel built from a shell variable, and the lane regex-extracts
    it (see `marked`). The echoed command contains the literal `$V`, never the
    value, so echo cannot forge a result.
    """
    text = ANSI.sub("", out).replace("\r", "")
    lines = []
    for line in text.splitlines():
        s = line.rstrip()
        if not s.strip():
            continue
        if PROMPT.match(s):
            continue
        lines.append(s)
    return lines


def extract_marked(text: str, nonce: str) -> str | None:
    """Pull the `<<M<nonce>:value>>` sentinel out of a transcript.

    TWO DEFENCES, both earned from a test failure:
      1. A per-call RANDOM NONCE, so a sentinel left over from an earlier command
         cannot be mistaken for this one's answer.
      2. A value containing `$` is REJECTED. The console echoes the command
         `echo "<<M<nonce>:$V>>"`, and that echoed line matches the sentinel
         pattern with the literal value `$V`. Without this the parser returns the
         string `$V` and every assertion silently compares against it.
    """
    pat = re.compile(rf"<<M{nonce}:([^>]*)>>")
    vals = []
    for line in clean(text):
        m = pat.search(line)
        if m is not None:
            v = m.group(1).strip()
            if "$" not in v:
                vals.append(v)
    return vals[-1] if vals else None


def marked(ser, expr: str, settle=2.5) -> str | None:
    """Run `expr` (which must set $V) and return its `<<M<nonce>:$V>>` value.

    `None` means the sentinel never appeared with a real value — a genuine
    failure, not a parse accident.
    """
    nonce = secrets.token_hex(4)
    out = raw_cmd(ser, f'V=$({expr}); echo "<<M{nonce}:$V>>"', settle=settle)
    return extract_marked(out, nonce)


def http_code(ser, url, extra="") -> str:
    """HTTP status of `url` as seen from the router. '' when curl failed."""
    val = marked(ser, f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 "
                      f"{extra} {url}")
    if not val or not val.isdigit():
        return ""
    return val


def sha256_host(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


def find_module_binary(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    candidates = [
        Path.home() / "worktrees/spilman-rust/target/aarch64-unknown-linux-musl/release/tollgate-module-basic-rust",
        Path.home() / "repos/cashu_spilman_channels/target/aarch64-unknown-linux-musl/release/tollgate-module-basic-rust",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def find_payer_binary(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    candidates = [
        Path.home() / "worktrees/spilman-rust/target/aarch64-unknown-linux-musl/release/spilman-payer",
        Path.home() / "repos/cashu_spilman_channels/target/aarch64-unknown-linux-musl/release/spilman-payer",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


# ---------------------------------------------------------------------------
# Router-side process control. BusyBox has NO pkill, so we kill by pid.
# The rule "never stop a service you did not start" is enforced by killing ONLY
# the pid we recorded in our own pidfile (with a `ps w` fallback scoped to our
# private state dir, which is unique to this run).
# ---------------------------------------------------------------------------
def stop_own(pidfile: str, kill=os.kill, is_alive=None, read=None) -> int | None:
    """Kill ONLY the pid recorded in `pidfile`. Returns the pid, or None.

    Deliberately does not match by process NAME: a decoy process with a similar
    name must survive. `test_lane_guard.py` proves that.
    """
    read = read or (lambda p: Path(p).read_text().strip() if Path(p).exists() else "")
    is_alive = is_alive or (lambda pid: Path(f"/proc/{pid}").exists())
    raw = read(pidfile)
    if not raw or not raw.isdigit():
        return None
    pid = int(raw)
    if not is_alive(pid):
        return None
    try:
        kill(pid, signal.SIGTERM)
    except OSError:
        return None
    return pid


def router_pids_by_marker(ser, marker: str) -> list[int]:
    """PIDs whose command line contains `marker`, via `ps w` (no pkill on BusyBox).

    `clean()` is mandatory here: the console echoes the `grep -F '<marker>'`
    command itself, and that echoed line contains the marker and a leading digit
    — so parsing the raw transcript would return the SHELL's pid as a match.
    """
    out = raw_cmd(ser, f"ps w | grep -F '{marker}' | grep -v grep")
    # A plain substring would let `/tmp/spilman-lane-1` also match
    # `/tmp/spilman-lane-1-off`. Require a non-path, non-word character after the
    # marker so each run dir identifies exactly one set of processes.
    pat = re.compile(re.escape(marker) + r"(?![\w.-])")
    pids = []
    for line in clean(out):
        m = re.match(r"^\s*(\d+)\s", line)
        if m and pat.search(line) and "grep" not in line:
            pids.append(int(m.group(1)))
    return pids


def kill_router_process(ser, marker: str, pidfile: str | None = None) -> None:
    """Kill our own router process: recorded pid first, then marker fallback."""
    killed = False
    if pidfile:
        pid_s = marked(ser, f"cat {pidfile} 2>/dev/null")
        if pid_s and pid_s.isdigit():
            raw_cmd(ser, f"kill {pid_s} 2>/dev/null; echo sent={pid_s}")
            killed = True
    for pid in router_pids_by_marker(ser, marker):
        raw_cmd(ser, f"kill {pid} 2>/dev/null; echo sent={pid}")
        killed = True
    if not killed:
        print(f"    (nothing of ours to kill for marker {marker!r})")


# ---------------------------------------------------------------------------
# Artifact transfer: HTTP from this host, wget on the router.
# ---------------------------------------------------------------------------
class ArtifactServer:
    def __init__(self, root: Path, port: int):
        self.root, self.port, self.proc = root, port, None
        self.log = Path("/tmp") / f"spilman-lane-artifacts-{port}.log"

    def start(self) -> "ArtifactServer":
        fh = open(self.log, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(self.port), "--bind", "0.0.0.0"],
            cwd=str(self.root), stdout=fh, stderr=fh,
            # close_fds is the Python equivalent of `8>&- 9>&-`: a child must
            # never inherit the bench lock fds.
            close_fds=True)
        return self

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def looks_bound(self) -> bool:
        time.sleep(1.0)
        return self.proc is not None and self.proc.poll() is None


class TestMint:
    """Start the Spilman test mint with its ADVERTISED base-url pointing at the proxy.

    Why the base-url matters for assertion F: a client learns mint endpoints from
    the mint's own `/v1/info` and keyset responses. If the mint advertises itself
    directly, the payer's later calls bypass the counting proxy and the "zero mint
    calls" measurement silently measures nothing. Advertising the proxy address
    forces every client-learned URL back through the counter, so the measurement
    covers the whole protocol, not just the calls we happened to hardcode.
    """

    def __init__(self, repo: Path, port: int, advertised_url: str, data_dir: Path):
        self.repo, self.port = repo, port
        self.advertised_url = advertised_url
        self.data_dir = data_dir
        self.proc = None
        # Default upstream: our own mint on its private port. The lane overwrites
        # this when it adopts an operator-supplied mint instead.
        self.upstream_url = f"http://127.0.0.1:{port}"
        self.log = data_dir / "mint.log"

    def binary(self) -> Path | None:
        for c in (self.repo / "target/release/cdk-spilman-test-mintd",
                  Path.home() / "repos/cashu_spilman_channels"
                  / "target/release/cdk-spilman-test-mintd"):
            if c.is_file():
                return c
        return None

    def start(self) -> bool:
        binp = self.binary()
        if binp is None:
            return False
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fh = open(self.log, "wb")
        self.proc = subprocess.Popen(
            # NOTE: the flag is `--listen-port`. `--port` is silently NOT the
            # listen port, and the resident 127.0.0.1:3338 mint makes a default
            # bind fail with "Address already in use" — hence a private default.
            [str(binp), "--listen-host", "0.0.0.0", "--listen-port", str(self.port),
             "--base-url", self.advertised_url],
            cwd=str(self.data_dir), stdout=fh, stderr=fh, close_fds=True)
        return True

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def probe_url(url: str, timeout=3.0) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception:  # noqa: BLE001
        return None


def wait_url(url: str, tries=25, delay=0.4) -> int | None:
    """Poll until `url` answers. A freshly spawned server is not bound yet — a
    single probe right after Popen() races the bind and reports a false failure."""
    for _ in range(tries):
        code = probe_url(url)
        if code is not None:
            return code
        time.sleep(delay)
    return None


def wait_listen(ser, port: str, tries=15) -> bool:
    """Is something listening on 127.0.0.1:<port>? Y/N via sentinel.

    A substring check on the raw transcript cannot answer this: the console
    echoes the grep pattern, so `':{port} '` is always "present"; and a
    `|| echo NOLISTEN` sentinel is always present too, because the echo is
    echoed. Hence the `<<M:Y>>` sentinel.
    """
    for _ in range(tries):
        val = marked(ser, f"netstat -ltn 2>/dev/null | grep -qF ':{port} ' "
                           f"&& echo Y")
        if val == "Y":
            return True
        time.sleep(0.5)
    return False


def module_start_cmd(runtime_dir: str, port: str, mint_url: str) -> str:
    return (
        f"cd {runtime_dir} && TOLLGATE_BIND=127.0.0.1:{port} "
        f"TOLLGATE_STATE_DB={runtime_dir}/tollgate.db "
        f"TOLLGATE_TEST_CONFIG_DIR={runtime_dir} "
        f"./tollgate-module-basic-rust > {runtime_dir}/mod.log 2>&1 & "
        f"echo $! > {runtime_dir}/mod.pid"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--host", default=None, help="bench host LAN IP for artifact transfer")
    ap.add_argument("--module-binary", default=None)
    ap.add_argument("--payer-binary", default=None)
    ap.add_argument("--lane-port", default=DEFAULT_LANE_PORT,
                    help="private port for OUR module (never 2121/2122)")
    ap.add_argument("--http-port", type=int, default=18998,
                    help="private port for the lane's artifact HTTP server")
    ap.add_argument("--proxy-port", type=int, default=3339,
                    help="private port for the counting mint proxy")
    ap.add_argument("--mint-port", type=int, default=int(os.environ.get(
                        "SPILMAN_MINT_PORT", "3438")),
                    help="private port for OUR test mint. Not 3338: a resident "
                         "mint may already own it")
    ap.add_argument("--mint-repo", default=os.environ.get(
                        "SPILMAN_REPO", str(Path.home() / "repos/cashu_spilman_channels")),
                    help="repo containing target/release/cdk-spilman-test-mintd")
    ap.add_argument("--mint-url", default=None,
                    help="adopt an already-running mint instead of starting our own "
                         "(its advertised base-url cannot be retargeted, so "
                         "assertion F measures less; the lane says so)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.lane_port in RESERVED_PORTS:
        print(f"ABORT: --lane-port {args.lane_port} is reserved for another context "
              f"window ({sorted(RESERVED_PORTS)}). Pick a private port.")
        return EXIT_FAIL

    serial_lock, bench_lock, code = acquire_bench()
    if code != EXIT_OK:
        return code

    def _alarm(_s, _f):
        raise TimeoutError("wall-clock limit; releasing the bench")

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(1200)

    results: list[tuple[str, str, bool, str]] = []
    skips: list[tuple[str, str, str]] = []
    proxy: CountingMintProxy | None = None
    art_srv: ArtifactServer | None = None
    mint: TestMint | None = None
    mint_ours = False          # did WE start the mint? (only ours may be stopped)
    measurable = False         # can assertion F be measured at all?
    run_dir = f"/tmp/spilman-lane-{os.getpid()}"
    router_started = False

    def record(tag, what, ok, detail):
        results.append((tag, what, ok, detail))

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
                return EXIT_FAIL
            print(f"### board identified by prompt: {ident} on {args.port}")
            print(f"### lane port {args.lane_port} (private), run dir {run_dir}\n")

            if args.dry_run:
                print("### --dry-run: identity confirmed, locks held, stopping")
                return EXIT_OK

            lan = args.host or discover_lan_ip()
            if not lan:
                print("ABORT: could not discover the bench host LAN IP; pass --host")
                return EXIT_FAIL
            # The mint the OPERATOR pointed at, if any. `None` means "run our own".
            adopted_mint = args.mint_url
            mint_origin = adopted_mint or "(none — this lane starts its own)"
            print(f"### bench host {lan}; adopted mint: {mint_origin}")

            # ============ TASK B: DEPLOY ==============================
            module = find_module_binary(args.module_binary)
            payer = find_payer_binary(args.payer_binary)
            if module is None:
                print("ABORT: no module binary found; pass --module-binary")
                return EXIT_FAIL
            stage = Path(run_dir) / "stage"
            stage.mkdir(parents=True, exist_ok=True)
            shutil.copy2(module, stage / "tollgate-module-basic-rust")
            host_shas = {"tollgate-module-basic-rust":
                         sha256_host(stage / "tollgate-module-basic-rust")}
            if payer is not None:
                shutil.copy2(payer, stage / "spilman-payer")
                host_shas["spilman-payer"] = sha256_host(stage / "spilman-payer")
            print(f"### staged artifacts: {sorted(host_shas)}")

            art_srv = ArtifactServer(stage, args.http_port).start()
            if wait_url(f"http://{lan}:{args.http_port}/") is None:
                print(f"ABORT: artifact server not reachable on {lan}:{args.http_port}")
                print(f"       (log: {art_srv.log})")
                return EXIT_FAIL

            raw_cmd(ser, f"rm -rf {run_dir} && mkdir -p {run_dir} && echo READY")
            for name, want in host_shas.items():
                url = f"http://{lan}:{args.http_port}/{name}"
                out = raw_cmd(ser, f"cd {run_dir} && wget -q -O {name} {url} "
                                   f"&& echo WGET_OK || echo WGET_FAIL", settle=4.0)
                if "WGET_OK" not in out:
                    record("B", f"deploy {name} to the router", False, "wget failed")
                    continue
                raw_cmd(ser, f"chmod +x {run_dir}/{name}")
                got = (marked(ser, f"sha256sum {run_dir}/{name} | cut -d' ' -f1")
                       or "")
                ok = bool(got) and got == want
                record("B", f"deploy {name}: router sha256 == host",
                       ok, f"router={got[:16]}… host={want[:16]}…" if ok
                       else f"MISMATCH router={got} host={want}")
            print(f"### deployed to {run_dir} on the router\n")

            # ============ counting proxy in front of the mint ==========
            # THE ORDERING IS THE ASSERTION. It has to be:
            #   1. start our own test mint bound to the LAN, on a private port,
            #      with its ADVERTISED base-url set to the proxy;
            #   2. start the proxy with that mint as its upstream.
            # Why: a Spilman client learns endpoints from the mint's own /v1/info
            # and keyset responses. If the mint advertises itself directly, the
            # payer's later calls bypass the counter and assertion F measures
            # nothing while still reporting zero — a fake green. Advertising the
            # proxy forces every client-learned URL back through the counter.
            mint = TestMint(Path(args.mint_repo).expanduser(), args.mint_port,
                            advertised_url="", data_dir=Path(run_dir) / "mint")
            proxied_mint = f"http://{lan}:{args.proxy_port}"
            mint.upstream_url = f"http://{lan}:{args.mint_port}"

            if adopted_mint is None and mint.binary() is not None:
                mint.advertised_url = proxied_mint
                mint_ours = mint.start()
            if adopted_mint is not None:
                mint.upstream_url = adopted_mint
            elif not mint_ours:
                print("### WARNING: no local test-mint binary and no --mint-url. "
                      "Assertion F cannot be measured; it will SKIP, not pass.")

            proxy = CountingMintProxy(mint.upstream_url, bind_host="0.0.0.0",
                                      bind_port=args.proxy_port).start()
            print(f"### counting proxy {proxied_mint} -> {mint.upstream_url}")
            if mint_ours:
                if wait_url(f"{mint.upstream_url}/v1/info", tries=60, delay=1.0) is None:
                    print(f"ABORT: our test mint did not answer on "
                          f"{mint.upstream_url} (log: {mint.log})")
                    return EXIT_FAIL
                print(f"### test mint live on :{args.mint_port}, advertising "
                      f"{proxied_mint}")
                # Prove the advertised URL actually routes through the counter.
                if wait_url(f"{proxied_mint}/v1/info", tries=20, delay=0.5) is None:
                    print(f"ABORT: the mint's advertised URL {proxied_mint} does not "
                          "answer — assertion F could not be measured")
                    return EXIT_FAIL
                print("### proven: the mint's advertised URL answers via the proxy")
                measurable = True
            elif adopted_mint is not None:
                # An adopted mint advertises itself directly, so client-learned
                # URLs bypass the counter. Zero would be unfalsifiable, not
                # measured — so F reports SKIP rather than a fake green.
                measurable = False
            print()

            # ============ private config with the rail ENABLED =========
            cfg = json.dumps({"spilman": {
                "enabled": True,
                "mint_url": proxied_mint,
                "in_bytes_per_millisat": 22021,
                "channel_db_path": f"{run_dir}/spilman.db",
            }})
            raw_cmd(ser, f"printf '%s' '{cfg}' > {run_dir}/config.json && echo CFG_OK")

            # ============ start OUR module on the private port =========
            raw_cmd(ser, module_start_cmd(run_dir, args.lane_port, proxied_mint))
            listening = wait_listen(ser, args.lane_port)
            router_started = True
            base = f"http://127.0.0.1:{args.lane_port}"
            print(f"### our module listening on :{args.lane_port} = {listening}\n")

            # ---- A ---------------------------------------------------
            record("A", "module binary runs on the router", listening,
                   "listening" if listening else "not listening")
            if listening:
                code_s = http_code(ser, f"{base}/whoami")
                record("A", "GET /whoami answers 200", code_s == "200", f"got {code_s!r}")

            # ---- B ---------------------------------------------------
            code_s = http_code(ser, f"{base}/spilman/params")
            record("B", "/spilman/params reachable (rail enabled)",
                   code_s == "200", f"HTTP {code_s!r}")
            body = marked(ser, f"curl -s --max-time 5 {base}/spilman/params") or ""
            mint_seen = proxied_mint in body
            record("B", "params points the payer at the counting proxy", mint_seen,
                   "mint_url is the proxy" if mint_seen else f"params body: {body[:120]}")

            # ---- C ---------------------------------------------------
            code_s = http_code(
                ser, f"{base}/spilman/payment",
                extra="-H 'content-type: application/json' "
                      "-d '{\"channel_id\":\"deadbeef\",\"balance\":5,\"signature\":\"00\"}'")
            record("C", "forged payment REFUSED", code_s == "409",
                   f"HTTP {code_s!r}" + ("" if code_s == "409"
                                         else " — ACCEPTED FORGERY would be critical"))

            # ---- E–H: prerequisites, then the protocol -----------------
            channel_code = http_code(
                ser, f"{base}/spilman/channel",
                extra="-X POST -H 'content-type: application/json' -d '{}'")
            channel_mounted = channel_code not in ("", "404", "405")
            payer_deployed = "spilman-payer" in host_shas and any(
                t == "B" and ok and "spilman-payer" in w for t, w, ok, _ in results)
            print(f"### prerequisites: POST /spilman/channel mounted={channel_mounted} "
                  f"(HTTP {channel_code!r}); payer deployed={payer_deployed}\n")

            if not (channel_mounted and payer_deployed):
                reasons = []
                if not channel_mounted:
                    reasons.append("POST /spilman/channel is not mounted "
                                   f"(HTTP {channel_code!r})")
                if not payer_deployed:
                    reasons.append("the spilman-payer driver binary is not present "
                                   "on the deployed image")
                why = "protocol E2E blocked: " + "; ".join(reasons)
                for tag, what in [
                    ("E", "channel opens against the test mint"),
                    ("F", "zero mint calls between open and settle"),
                    ("G", "settle pays the payee exactly"),
                    ("H", "restart mid-stream keeps the latest signature"),
                ]:
                    skips.append((tag, what, why))
            elif not measurable:
                # The driver exists, but the counter cannot see the mint traffic
                # the clients will actually make — zero would be unfalsifiable.
                why = ("assertion F is unmeasurable here: the mint in use "
                       "advertises its own address, so client-learned URLs bypass "
                       "the counting proxy. Run without --mint-url to measure.")
                skips.append(("E", "channel opens against the test mint", why))
                skips.append(("F", "zero mint calls between open and settle", why))
                skips.append(("G", "settle pays the payee exactly", why))
                skips.append(("H", "restart mid-stream keeps the latest signature",
                              why))
            else:
                run_protocol(ser, args, run_dir, base, proxy, record, skips,
                             proxied_mint, lan)

            # ---- D: /spilman/* 404s when the rail is DISABLED --------
            # Kill our module, restart it with the rail off on a second private
            # port, confirm the surface is absent, then stop it.
            kill_router_process(ser, run_dir, f"{run_dir}/mod.pid")
            router_started = False
            time.sleep(1)
            off_port = str(int(args.lane_port) + 10)
            off_dir = f"{run_dir}-off"
            raw_cmd(ser, f"rm -rf {off_dir} && mkdir -p {off_dir}")
            raw_cmd(ser, f"printf '%s' '{{\"spilman\":{{\"enabled\":false}}}}' "
                         f"> {off_dir}/config.json")
            raw_cmd(ser, f"cd {off_dir} && TOLLGATE_BIND=127.0.0.1:{off_port} "
                         f"TOLLGATE_STATE_DB={off_dir}/tollgate.db "
                         f"TOLLGATE_TEST_CONFIG_DIR={off_dir} "
                         f"{run_dir}/tollgate-module-basic-rust > {off_dir}/mod.log 2>&1 & "
                         f"echo $! > {off_dir}/mod.pid")
            if wait_listen(ser, off_port):
                code_s = http_code(ser, f"http://127.0.0.1:{off_port}/spilman/params")
                record("D", "/spilman/* 404s when the rail is disabled",
                       code_s == "404", f"HTTP {code_s!r}")
            else:
                record("D", "/spilman/* 404s when the rail is disabled", False,
                       "disabled-rail module did not come up")
            kill_router_process(ser, off_dir, f"{off_dir}/mod.pid")
            raw_cmd(ser, f"rm -rf {off_dir}")

    except TimeoutError as exc:
        print(f"FAIL: {exc}")
        return 124
    finally:
        signal.alarm(0)
        # Kill ONLY our own process; never a service we did not start.
        try:
            import serial as _s  # noqa: F401
            if router_started:
                with serial.Serial(args.port, 115200, timeout=1, write_timeout=3) as ser:
                    kill_router_process(ser, run_dir, f"{run_dir}/mod.pid")
        except Exception as exc:  # noqa: BLE001
            print(f"    (could not stop the router process cleanly: {exc})")
        if proxy is not None:
            proxy.stop()
        if art_srv is not None:
            art_srv.stop()
        # Only stop the mint WE started. An adopted mint belongs to someone else.
        if mint_ours and mint is not None:
            mint.stop()
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
    if skips:
        print("=== skipped, with named reasons (never silent passes) ===")
        for tag, what, why in skips:
            print(f"  SKIP  [{tag}] {what}\n          reason: {why}")
        print()
    nfail = len(results) - npass
    print(f"=== {npass}/{len(results)} router assertions passed, "
          f"{nfail} failed, {len(skips)} skipped ===")
    return EXIT_OK if nfail == 0 else EXIT_FAIL


def run_protocol(ser, args, run_dir, base, proxy, record, skips, proxied_mint, lan):
    """E–H, driven by the spilman-payer binary running on the router.

    The driver emits a documented marker contract on stdout; the lane polls its
    log over serial. The counting proxy is armed ONLY for the open→settle window.
    """
    log = f"{run_dir}/payer.log"
    pidf = f"{run_dir}/payer.pid"

    def poll_count(pattern, tries=40, delay=0.5):
        """How many times has the driver emitted `pattern`? 0 means not yet."""
        for _ in range(tries):
            val = marked(ser, f"grep -c '{pattern}' {log} 2>/dev/null || echo 0")
            if val and val.isdigit() and int(val) > 0:
                return int(val)
            time.sleep(delay)
        return 0

    def tail_log(n=12):
        return marked(ser, f"tail -n {n} {log} 2>/dev/null", settle=3.0) or "(no log)"

    def diagnose_missing_driver_log() -> str:
        """Why is there no log? Three different causes, each with a different fix.

        A bare "(no log)" was reported live on 2026-10-22 and it hid the real cause:
        the driver rejected the lane's flags and exited 2 before writing anything, so
        the empty log said nothing about the interface. Distinguishing these cases is
        the difference between "re-run it" and "the two halves disagree".
        """
        exists = marked(ser, f"[ -f {log} ] && wc -c < {log} || echo MISSING")
        if exists and exists.isdigit() and int(exists) == 0:
            # The file exists and is empty: the process ran and printed nothing, or
            # exited before its first line. Most likely a usage/arg error.
            probe = marked(ser, f"cd {run_dir} && ./spilman-payer --help 2>&1 | head -2")
            return (f"driver log exists but is EMPTY (0 bytes). Its --help says: "
                    f"{(probe or '<no help output>')[:120]!r}. "
                    "If the lane's flags are absent from that usage, the deployed "
                    "driver does not implement this lane's CLI contract.")
        if exists == "MISSING":
            alive = marked(ser, f"ls {run_dir}/ 2>/dev/null | tr '\\n' ' '")
            return (f"driver never created {log}. Directory holds: {(alive or '?')[:120]}. "
                    "Either the binary is not executable/deployed, or the launch line "
                    "failed before the redirect took effect.")
        return f"driver log has {exists} bytes but no markers; tail: {tail_log()[:160]}"

    # E: open. The funding swap legitimately hits the mint — arm AFTER open.
    raw_cmd(ser, f"cd {run_dir} && ./spilman-payer --mint-url {proxied_mint} "
                 f"--module-url {base} --flow open-settle "
                 f"> {log} 2>&1 & echo $! > {pidf}", settle=3.0)

    if not poll_count("PAYER_OPEN_OK"):
        record("E", "channel opens against the test mint", False,
               "driver did not emit PAYER_OPEN_OK; " + diagnose_missing_driver_log())
        skips.append(("F", "zero mint calls between open and settle",
                      "E failed — nothing to measure"))
        skips.append(("G", "settle pays the payee exactly", "E failed"))
        skips.append(("H", "restart mid-stream keeps the latest signature", "E failed"))
        kill_router_process(ser, run_dir, pidf)
        return

    opened = marked(ser, f"grep -m1 PAYER_OPEN_OK {log}") or ""
    m = RE_OPEN_OK.search(opened)
    channel_id = m.group(1) if m else "?"
    record("E", "channel opens against the test mint", True, f"channel_id={channel_id}")

    # F: MEASURE the mint calls in the open→settle window.
    proxy.reset()
    proxy.arm()
    settle_seen = poll_count("PAYER_SETTLE_OK")
    proxy.disarm()
    count = proxy.count()
    records = proxy.records()
    record("F", "ZERO mint calls between open and settle", count == 0,
           f"counting proxy saw {count} request(s)"
           + ("" if count == 0 else f": {records[:5]}"))

    # G: settle pays the payee exactly.
    if not settle_seen:
        record("G", "settle pays the payee exactly", False,
               "driver did not emit PAYER_SETTLE_OK; tail: " + tail_log()[:160])
    else:
        settled = marked(ser, f"grep -m1 PAYER_SETTLE_OK {log}") or ""
        m = RE_SETTLE_OK.search(settled)
        if not m:
            record("G", "settle pays the payee exactly", False,
                   "PAYER_SETTLE_OK present but malformed")
        else:
            balance, credited = int(m.group(2)), int(m.group(3))
            record("G", "settle pays the payee exactly", balance == credited,
                   f"balance={balance} payee_credited={credited}")
        # the module must agree the channel is settled (still known, not 404)
        code_s = http_code(ser, f"{base}/spilman/status?channel_id={channel_id}")
        record("G", "module status shows the settled channel", code_s == "200",
               f"HTTP {code_s!r}")

    # H: restart mid-stream keeps the latest signature.
    pre = marked(ser, f"curl -s --max-time 5 "
                      f"'{base}/spilman/status?channel_id={channel_id}'") or ""
    pre_decision = _decision(pre)
    kill_router_process(ser, run_dir, f"{run_dir}/mod.pid")
    time.sleep(1)
    raw_cmd(ser, module_start_cmd(run_dir, args.lane_port, proxied_mint))
    back = wait_listen(ser, args.lane_port)
    post = marked(ser, f"curl -s --max-time 5 "
                       f"'{base}/spilman/status?channel_id={channel_id}'") or ""
    post_decision = _decision(post)
    survived = back and post_decision is not None and post_decision == pre_decision
    record("H", "restart keeps the latest recorded balance",
           survived, f"pre={pre_decision} post={post_decision} (module back={back})")

    # ...and it is still VERIFIABLE: a stale (lower) balance must be refused.
    if survived:
        stale = http_code(
            ser, f"{base}/spilman/payment",
            extra="-H 'content-type: application/json' "
                  f"-d '{{\"channel_id\":\"{channel_id}\",\"balance\":1,\"signature\":\"00\"}}'")
        record("H", "after restart a stale/replayed balance is refused",
               stale == "409", f"HTTP {stale!r}")

    kill_router_process(ser, run_dir, pidf)


def _decision(body: str) -> str | None:
    """Pull the module's channel decision out of a /spilman/status body."""
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("status") == 1:
            return str(payload.get("decision", "known"))
    return None


if __name__ == "__main__":
    sys.exit(main())
