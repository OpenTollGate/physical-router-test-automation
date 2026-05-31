"""Cloud lab worker — syslog and VM log streaming."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from lib.cloud_lab.constants import DEBIAN_IP, OPENWRT_IP, SELLER_OPENWRT_IP, VIRT_LAB_PASSWORD
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.shell import _redact, _run, log

def start_syslog_capture(results_dir: str) -> subprocess.Popen[str]:
    """Start socat UDP listener to capture syslog from OpenWrt/Debian VMs."""
    if not shutil.which("socat"):
        _run("apt-get install -y -qq socat >/dev/null 2>&1 || true", timeout=30, check=False)
        if not shutil.which("socat"):
            raise FileNotFoundError("socat not available and install failed")
    syslog_dir = Path(results_dir) / "raw" / "syslog"
    syslog_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["socat", "-u", "UDP4-LISTEN:514,bind=0.0.0.0,fork",
         f"OPEN:{syslog_dir}/all.log,append,create"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    log.info("Syslog capture started on UDP 514 → %s", syslog_dir)
    return proc
def configure_openwrt_syslog(openwrt_ip: str) -> None:
    """Configure OpenWrt to forward syslog to host VM via UDP 514."""
    _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} "
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ControlPath=none root@{openwrt_ip} "
        f"'uci set system.@system[0].log_ip=10.99.99.2 && "
        f"uci set system.@system[0].log_port=514 && "
        f"uci commit system && /etc/init.d/log restart'",
        timeout=15,
        check=False,
    )
    log.info("Configured OpenWrt syslog forwarding on %s → 10.99.99.2:514", openwrt_ip)
def start_vm_log_streaming(
    config: WorkerConfig, results_dir: str
) -> list[tuple[threading.Thread, subprocess.Popen[str], Any]]:
    streams: list[tuple[threading.Thread, subprocess.Popen[str], Any]] = []

    streamed_dir = os.path.join(results_dir, "raw", "streamed")
    if os.path.isdir(results_dir):
        os.makedirs(streamed_dir, exist_ok=True)

    def _stream_reader(prefix: str, proc: subprocess.Popen[str], fh: Any) -> None:
        assert proc.stdout is not None
        try:
            for raw_line in proc.stdout:
                redacted = _redact(raw_line.rstrip("\n"))
                log.info("[%s] %s", prefix, redacted)
                if fh is not None:
                    fh.write(redacted + "\n")
                    fh.flush()
        except Exception:
            pass
        finally:
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass

    targets = [
        ("openwrt", OPENWRT_IP, f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
         f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
         f"-o ConnectTimeout=5 root@{OPENWRT_IP} 'logread -f'"),
        ("debian", DEBIAN_IP, f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
         f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
         f"-o ConnectTimeout=5 root@{DEBIAN_IP} "
         "'journalctl -f -u container-test.service 2>/dev/null || tail -f /var/log/syslog 2>/dev/null || echo NO_LOGS'"),
    ]
    if config.two_router or config.reseller_scenarios:
        targets.append(
            ("seller", SELLER_OPENWRT_IP, f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
             f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
             f"-o ConnectTimeout=5 root@{SELLER_OPENWRT_IP} 'logread -f'"),
        )

    for prefix, _ip, ssh_cmd in targets:
        fh: Any = None
        if os.path.isdir(results_dir):
            log_path = os.path.join(streamed_dir, f"{prefix}.log")
            try:
                fh = open(log_path, buffering=1, encoding="utf-8")  # noqa: SIM115
            except OSError:
                fh = None
        proc = subprocess.Popen(
            ["bash", "-c", ssh_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        t = threading.Thread(target=_stream_reader, args=(prefix, proc, fh), daemon=True)
        t.start()
        streams.append((t, proc, fh))

    return streams
def stop_vm_log_streaming(streams: list[tuple[threading.Thread, subprocess.Popen[str], Any]]) -> None:
    for t, proc, fh in streams:
        try:
            proc.kill()
        except OSError:
            pass
    for t, proc, fh in streams:
        t.join(timeout=5)
    for t, proc, fh in streams:
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
