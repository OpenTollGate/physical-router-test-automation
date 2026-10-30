"""QEMU lane substrate: one isolated DUT guest per lane, driven over serial.

Purpose (audit gate 8): the throughput measurement is only honest if each lane
really is an independent DUT — its own overlay, its own NIC (its own slirp
network), its own host port, its own console socket, its own log path — and if
the *workload* is identical whether one lane or several run.

Deliberately NOT here: any claim about timing. This module boots a guest and
runs commands on it; the benchmark that decides whether parallelism helps lives
in ``scripts/fleet-bench.py`` and refuses to run without measured headroom.

The console driver is used instead of SSH because the stock OpenWrt x86 image
has no password set and no injected key: SSH would need provisioning, which is
a different (and slower, murkier) story than "boot the DUT and run the same
commands". The console is also the failure-relevant path (a guest with a broken
network stack still answers on the console), which is what PRTA lanes spend
their time waiting on.

Every command is bracketed by unique markers, so a command whose output arrives
late cannot be attributed to the next one — the classic serial-test bug.
"""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "LaneError",
    "LaneConfig",
    "Console",
    "QemuLane",
    "PROBE_COMMANDS",
    "lane_plan",
]

#: The identical workload every configuration runs. Read-only on purpose: a
#: throughput number measured by mutating the DUT measures the mutation, not the
#: lane. Each entry is (name, command).
PROBE_COMMANDS: tuple[tuple[str, str], ...] = (
    ("release", "cat /etc/openwrt_release"),
    ("board", "ubus call system board"),
    ("addr", "ip -4 addr show br-lan"),
    ("rules", "iptables -S 2>/dev/null | wc -l"),
    ("load", "cat /proc/loadavg"),
    ("uptime", "cut -d' ' -f1 /proc/uptime"),
)


class LaneError(RuntimeError):
    """Lane could not be brought up, or its console stopped answering."""


@dataclass(frozen=True)
class LaneConfig:
    """Everything that must be unique per lane — the isolation contract."""

    lane_id: str
    base_image: Path
    workdir: Path
    mem_mib: int = 256
    vcpus: int = 1
    ssh_port: int = 0            # host port (unique); user-mode NIC is optional
    console_sock: Path = field(default=Path(""))
    overlay: Path = field(default=Path(""))
    log_path: Path = field(default=Path(""))
    events_path: Path = field(default=Path(""))

    def resolved(self) -> "LaneConfig":
        """Fill the derived paths and assert nothing collides across lanes."""
        lane_dir = self.workdir / self.lane_id
        lane_dir.mkdir(parents=True, exist_ok=True)
        return LaneConfig(
            lane_id=self.lane_id,
            base_image=self.base_image,
            workdir=self.workdir,
            mem_mib=self.mem_mib,
            vcpus=self.vcpus,
            ssh_port=self.ssh_port,
            console_sock=lane_dir / "console.sock",
            overlay=lane_dir / f"{self.lane_id}.qcow2",
            log_path=lane_dir / "serial.log",
            events_path=lane_dir / "events.jsonl",
        )


def lane_plan(
    *, workdir: Path, base_image: Path, lane_ids: list[str], mem_mib: int, vcpus: int, first_port: int = 0
) -> list[LaneConfig]:
    """Build one config per lane and prove the isolation contract holds.

    Raises instead of returning a plan with a shared path/port: two lanes on one
    overlay would corrupt each other's disk, and two lanes on one host port would
    make the second lane's readiness check pass against the first lane's guest.
    """
    if not lane_ids:
        raise ValueError("at least one lane is required")
    if len(set(lane_ids)) != len(lane_ids):
        raise ValueError(f"duplicate lane ids: {lane_ids}")
    cfgs = [
        LaneConfig(
            lane_id=lid,
            base_image=base_image,
            workdir=workdir,
            mem_mib=mem_mib,
            vcpus=vcpus,
            ssh_port=(first_port + i) if first_port else 0,
        ).resolved()
        for i, lid in enumerate(lane_ids)
    ]
    for attr in ("console_sock", "overlay", "log_path", "events_path"):
        values = [str(getattr(c, attr)) for c in cfgs]
        if len(set(values)) != len(values):
            raise ValueError(f"lanes share {attr}: {values}")
    ports = [c.ssh_port for c in cfgs if c.ssh_port]
    if len(set(ports)) != len(ports):
        raise ValueError(f"lanes share a host port: {ports}")
    if not Path(base_image).is_file():
        raise LaneError(f"base image not found: {base_image}")
    return cfgs


class Console:
    """Line-oriented driver for a QEMU ``-serial unix:...`` socket."""

    def __init__(self, path: Path, *, timeout: float = 30.0, log_path: Path | None = None):
        self.path = Path(path)
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = ""
        self._log = open(log_path, "ab") if log_path else None

    # -- lifecycle ---------------------------------------------------------
    def connect(self, *, deadline: float = 60.0) -> None:
        end = time.monotonic() + deadline
        last = ""
        while time.monotonic() < end:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(str(self.path))
                sock.setblocking(False)
                self._sock = sock
                return
            except OSError as exc:  # socket not created yet / qemu died
                last = str(exc)
                time.sleep(0.2)
        raise LaneError(f"console socket {self.path} never became connectable: {last}")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        if self._log is not None:
            self._log.close()
            self._log = None

    # -- io ----------------------------------------------------------------
    def _pump(self, *, timeout: float) -> str:
        assert self._sock is not None, "connect() first"
        deadline = time.monotonic() + timeout
        chunks: list[str] = []
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self._sock], [], [], max(0.0, min(0.2, deadline - time.monotonic())))
            if not ready:
                continue
            try:
                data = self._sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                continue
            if not data:
                raise LaneError(f"console {self.path} closed by the guest")
            text = data.decode("utf-8", "replace")
            if self._log is not None:
                self._log.write(data)
                self._log.flush()
            chunks.append(text)
            self._buf += text
        return "".join(chunks)

    def write(self, data: str) -> None:
        assert self._sock is not None, "connect() first"
        self._sock.sendall(data.encode())

    def read_until(self, needle: str, *, timeout: float | None = None) -> str:
        """Return everything up to and including ``needle``."""
        end = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while True:
            idx = self._buf.find(needle)
            if idx >= 0:
                out = self._buf[: idx + len(needle)]
                self._buf = self._buf[idx + len(needle) :]
                return out
            if time.monotonic() >= end:
                raise LaneError(f"timed out waiting for {needle!r}; tail={self._buf[-400:]!r}")
            self._pump(timeout=max(0.05, end - time.monotonic()))

    def wait_for_prompt(self, *, timeout: float = 120.0) -> None:
        """Wait for a root shell prompt (OpenWrt ``root@<host>:~#``)."""
        end = time.monotonic() + timeout
        pattern = re.compile(r"root@[^\s:]+:[^\s#]*#\s*$", re.MULTILINE)
        while time.monotonic() < end:
            if pattern.search(self._buf):
                return
            self._pump(timeout=min(2.0, max(0.05, end - time.monotonic())))
        raise LaneError(f"no root shell prompt within {timeout}s; tail={self._buf[-400:]!r}")

    def command(self, cmd: str, *, timeout: float = 30.0) -> tuple[int, str]:
        """Run one shell command, return (exit status, stdout)."""
        token = uuid.uuid4().hex[:12]
        begin, end = f"__PRTA_B{token}__", f"__PRTA_E{token}__"
        self.write(f"echo {begin}; {cmd}; echo {end}:$?\r")
        raw = self.read_until(f"{end}:", timeout=timeout)
        status_line = self._buf.split("\n", 1)[0] if self._buf else ""
        self._buf = self._buf[len(status_line) :]
        match = re.search(r"(\d+)\s*$", status_line)
        if not match:
            raise LaneError(f"could not parse exit status for {cmd!r}: {status_line!r}")
        body = raw.split(begin, 1)[-1]
        body = body.rsplit(f"\n{status_line}", 1)[0] if status_line else body
        return int(match.group(1)), body.strip("\r\n")


class QemuLane:
    """One lane: overlay, guest process, console, event log."""

    def __init__(self, cfg: LaneConfig, *, qemu_bin: str = "qemu-system-x86_64"):
        self.cfg = cfg
        self._qemu_bin = qemu_bin
        self._proc: subprocess.Popen | None = None
        self.console: Console | None = None
        self.pid: int | None = None
        self.started_at: float | None = None
        self.ready_at: float | None = None
        self.cleanup_s: float | None = None
        self._events = open(cfg.events_path, "a", encoding="utf-8")

    # -- events: the overlap proof ----------------------------------------
    def event(self, kind: str, **fields) -> None:
        record = {"ts": time.time(), "lane": self.cfg.lane_id, "event": kind, **fields}
        self._events.write(json.dumps(record, sort_keys=True) + "\n")
        self._events.flush()
        os.fsync(self._events.fileno())

    # -- lifecycle ---------------------------------------------------------
    def start(self, *, boot_timeout: float = 180.0) -> None:
        if not self._qemu_bin or shutil.which(self._qemu_bin) is None:
            raise LaneError(f"{self._qemu_bin} not found — a lane needs real QEMU, not a stub")
        if Path("/dev/kvm").exists() is False:
            raise LaneError("/dev/kvm missing — refusing to silently fall back to TCG (unmeasured slowdown)")

        subprocess.run(
            ["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(self.cfg.base_image), str(self.cfg.overlay)],
            check=True,
            capture_output=True,
            text=True,
        )
        cmd = [
            self._qemu_bin,
            "-enable-kvm",
            "-cpu", "host",
            "-machine", "q35",
            "-m", str(self.cfg.mem_mib),
            "-smp", str(self.cfg.vcpus),
            "-display", "none",
            "-drive", f"file={self.cfg.overlay},if=virtio,format=qcow2",
            "-netdev", f"user,id=n0,hostfwd=tcp:127.0.0.1:{self.cfg.ssh_port}-:22" if self.cfg.ssh_port
            else "user,id=n0",
            "-device", "virtio-net-pci,netdev=n0",
            "-serial", f"unix:{self.cfg.console_sock},server=on,wait=off",
            "-monitor", "none",
        ]
        self.event("lane_start", qemu=cmd)
        self.started_at = time.time()
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        self.pid: int | None = self._proc.pid
        self.console = Console(self.cfg.console_sock, log_path=self.cfg.log_path)
        self.console.connect(deadline=boot_timeout)
        self.console.wait_for_prompt(timeout=boot_timeout)
        self.ready_at = time.time()
        self.event("lane_ready", boot_s=round(self.ready_at - self.started_at, 3))

    def run_job(self, *, timeout: float = 60.0) -> dict:
        """The identical unit of work: one pass over PROBE_COMMANDS."""
        if self.console is None:
            raise LaneError("lane not started")
        assert self.console is not None
        job_start = time.time()
        results = {}
        for name, cmd in PROBE_COMMANDS:
            rc, out = self.console.command(cmd, timeout=timeout)
            # Some probes legitimately return non-zero on a bare image (no
            # iptables in the squashfs, no br-lan yet). The workload is the
            # EXECUTION, so a probe failure is recorded, not retried away.
            results[name] = {"rc": rc, "out": out[:400]}
        job_end = time.time()
        self.event("job", start=job_start, end=job_end, duration_s=round(job_end - job_start, 3))
        return {"start": job_start, "end": job_end, "commands": results}

    def stop(self) -> float:
        """Kill the guest and remove its overlay. Returns cleanup seconds."""
        if self.cleanup_s is not None:
            return self.cleanup_s
        t0 = time.time()
        if self.console is not None:
            self.console.close()
            self.console = None
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=15)
            self._proc = None
        for path in (self.cfg.overlay, self.cfg.console_sock):
            try:
                Path(path).unlink()
            except FileNotFoundError:
                pass
        self.cleanup_s = time.time() - t0
        self.event("lane_stop", cleanup_s=round(self.cleanup_s, 3))
        self._events.close()
        return self.cleanup_s

    # -- diagnostics -------------------------------------------------------
    def stderr_tail(self) -> str:
        if self._proc is None or self._proc.stderr is None:
            return ""
        try:
            return (self._proc.stderr.read() or "")[-800:]
        except Exception:  # noqa: BLE001 - diagnostics only
            return ""
