#!/usr/bin/env python3
"""Bake an SHC VM with all cloud lab dependencies pre-installed.

Equivalent to bake-snapshot.py but for SHC (Sovereign Hybrid Compute) VMs.
SHC doesn't have snapshots, so this creates a VM, installs all deps, then
leaves it running (or the caller can image it manually).

Usage:
    python3 scripts/bake-shc.py bake --ssh-key ~/.ssh/tollgate_cloud_key.pub
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

OPENWRT_VERSION = "24.10.1"
VIRT_LAB_WORKDIR = "/root/tollgate-virtual-lab"


def _step(step_num: int, total: int, name: str) -> None:
    print(f"\n[{step_num}/{total}] {name}...")


def _ssh(ip: str, cmd: str, timeout: int = 300, ssh_key: str = "") -> str:
    ssh_cmd = [
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", f"ConnectTimeout={min(timeout, 30)}",
    ]
    if ssh_key and Path(ssh_key.replace(".pub", "")).exists():
        ssh_cmd.extend(["-i", ssh_key.replace(".pub", "")])
    ssh_cmd.extend([f"root@{ip}", cmd])
    r = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(f"  SSH stderr: {r.stderr[:300]}", file=sys.stderr)
    return r.stdout


def cmd_bake(args: argparse.Namespace) -> int:
    sys.path.insert(0, os.environ.get("SHC_TOOLKIT_PATH", "/home/ubuntu/src/shc-toolkit"))
    from shc_toolkit.client import SHCClient
    from lib.cloud_lab.shc_submit import SHC_PACKAGE_ID_STANDARD, SHC_PRICING_ID_STANDARD

    ssh_key_pub = args.ssh_key
    if not Path(ssh_key_pub).exists():
        print(f"ERROR: SSH public key not found: {ssh_key_pub}", file=sys.stderr)
        return 1
    pubkey = Path(ssh_key_pub).read_text().strip()

    client = SHCClient()
    hostname = f"tollgate-baked-{int(time.time())}"
    total_steps = 11

    print("SHC Bake configuration:")
    print(f"  Hostname:    {hostname}")
    print(f"  SSH key:     {ssh_key_pub}")
    print(f"  OpenWrt:     {OPENWRT_VERSION}")

    _step(1, total_steps, "Ordering SHC VM (2C/8GB/16GB)")
    t0 = time.monotonic()
    PACKAGE_ID = 81
    PRICING_ID = 245

    result = client.submit_order(
        hostname=hostname,
        package_id=PACKAGE_ID,
        pricing_id=PRICING_ID,
        idempotency_key=f"bake-{hostname}",
    )
    sids = result.get("service_ids", [])
    if not sids:
        print(f"ERROR: Order failed: {result}", file=sys.stderr)
        return 1
    sid = int(sids[0])
    print(f"  Ordered service #{sid}")

    _step(2, total_steps, "Waiting for provisioning")
    deadline = time.time() + 600
    vm_ip = ""
    while time.time() < deadline:
        vm = client.get_vm(sid)
        state = vm.get("provisioning_state", "unknown")
        ips = vm.get("ips", [])
        vm_ip = ips[0]["ip"] if ips else ""
        print(f"  state={state} ip={vm_ip or 'pending'}")
        if state == "ready" and vm_ip:
            break
        if state in ("failed", "error", "cancelled"):
            print(f"ERROR: Provisioning failed: {state}", file=sys.stderr)
            return 1
        time.sleep(10)
    else:
        print("ERROR: VM not ready after 600s", file=sys.stderr)
        return 1
    print(f"  VM ready in {time.monotonic() - t0:.1f}s at {vm_ip}")

    _step(3, total_steps, "Injecting SSH key")
    client.apply_ssh_key_live(sid, pubkey)
    time.sleep(5)

    _step(4, total_steps, "Waiting for SSH")
    deadline = time.time() + 300
    ssh_ok = False
    while time.time() < deadline:
        try:
            out = _ssh(vm_ip, "echo SSH_OK", timeout=15, ssh_key=ssh_key_pub)
            if "SSH_OK" in out:
                ssh_ok = True
                break
        except Exception:
            pass
        time.sleep(5)
    if not ssh_ok:
        print("ERROR: SSH not available after 300s", file=sys.stderr)
        return 1
    print("  SSH ready!")

    try:
        _step(5, total_steps, "System update + base packages")
        _ssh(vm_ip, "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && "
                     "apt-get install -y -qq qemu-system-x86 qemu-utils cpu-checker bridge-utils "
                     "dnsmasq iptables iproute2 curl wget git python3 python3-venv python3-pip "
                     "build-essential pkg-config libnl-3-dev libnl-genl-3-dev cmake "
                     "socat sshpass jq nak 2>/dev/null || true", timeout=600, ssh_key=ssh_key_pub)
        _ssh(vm_ip, "echo BASE_PKGS_DONE", timeout=10, ssh_key=ssh_key_pub)

        _step(6, total_steps, "Download OpenWrt base image")
        owrt_img = f"openwrt-{OPENWRT_VERSION}-x86-64-generic-ext4-combined.img"
        owrt_gz = f"{owrt_img}.gz"
        owrt_url = f"https://downloads.openwrt.org/releases/{OPENWRT_VERSION}/targets/x86/64/{owrt_gz}"
        _ssh(vm_ip,
             f"mkdir -p {VIRT_LAB_WORKDIR}/images && cd {VIRT_LAB_WORKDIR}/images && "
             f"[ -f {owrt_gz} ] || curl -fL -o {owrt_gz} {owrt_url} && "
             f"[ -f {owrt_img} ] || gzip -d < {owrt_gz} > {owrt_img} && "
             f"qemu-img convert -f raw -O qcow2 {owrt_img} openwrt-base.qcow2 && "
             f"qemu-img resize openwrt-base.qcow2 2G && echo IMAGES_OK",
             timeout=600, ssh_key=ssh_key_pub)
        print("  OpenWrt image ready")

        _step(7, total_steps, "Create Python venv with test deps")
        _ssh(vm_ip,
             "python3 -m venv /opt/tollgate-venv && "
             "/opt/tollgate-venv/bin/pip install -q --upgrade pip && "
             "/opt/tollgate-venv/bin/pip install -q pytest pytest-timeout pytest-rerunfailures "
             "requests paramiko loguru playwright ruff stakebreaker && "
             "echo VENV_OK",
             timeout=300, ssh_key=ssh_key_pub)
        print("  Python venv ready")

        _step(8, total_steps, "Install cashu CLI")
        _ssh(vm_ip,
             "python3 -m venv /opt/cashu-venv && "
             "/opt/cashu-venv/bin/pip install -q cashu && "
             "echo CASHU_OK",
             timeout=300, ssh_key=ssh_key_pub)
        print("  Cashu CLI ready")

        _step(9, total_steps, "Clone repos + install blossomfs")
        _ssh(vm_ip,
             "mkdir -p /opt/tollgate && cd /opt/tollgate && "
             "git clone --depth 1 https://github.com/OpenTollGate/physical-router-test-automation.git || true && "
             "echo REPOS_OK",
             timeout=120, ssh_key=ssh_key_pub)
        print("  Repos cloned")

        _step(10, total_steps, "Pre-provision OpenWrt base")
        _ssh(vm_ip,
             f"cd {VIRT_LAB_WORKDIR} && "
             "qemu-system-x86_64 -m 256 -nographic -no-reboot "
             "-drive file=images/openwrt-base.qcow2,format=qcow2 "
             "-netdev user,id=net0 -device e1000,netdev=net0 &"
             "sleep 30 && "
             "echo PREPROVISION_DONE",
             timeout=60, ssh_key=ssh_key_pub)
        print("  OpenWrt base pre-provisioned")

        _step(11, total_steps, "Bake complete")
        print(f"\n{'='*60}")
        print("SHC VM baked successfully!")
        print(f"  Service ID: {sid}")
        print(f"  IP:         {vm_ip}")
        print(f"  Hostname:   {hostname}")
        print(f"  SSH:        ssh root@{vm_ip}")
        print("  Cost:       ~$0.27")
        print(f"{'='*60}")
        print("\nThe VM is left running. To use it for cloud-lab tests:")
        print(f"  export TOLLGATE_BAKED_SHC_VM={sid}")
        print(f"  export TOLLGATE_BAKED_SHC_IP={vm_ip}")
        print("\nOr cancel when done:")
        print(f"  python3 -c \"from shc_toolkit.client import SHCClient; SHCClient().cancel_vm({sid}, immediate=True)\"")
        return 0

    except Exception as e:
        print(f"ERROR during bake: {e}", file=sys.stderr)
        print(f"The VM is still running at {vm_ip}. Cancel with service #{sid}.", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Bake an SHC VM for cloud-lab testing")
    sub = parser.add_subparsers(dest="command", required=True)
    bake = sub.add_parser("bake", help="Bake a new SHC VM with all deps")
    bake.add_argument("--ssh-key", default=os.path.expanduser("~/.ssh/tollgate_cloud_key.pub"))
    args = parser.parse_args()
    if args.command == "bake":
        return cmd_bake(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
