#!/usr/bin/env python3
"""Manage the local TollGate virtual lab.

The first implementation target is the Ubuntu machine reachable as `218`.
This script intentionally starts with diagnostics/bootstrap commands so the VM
orchestration can be built on a verified host instead of assumptions.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, cast


REQUIRED_COMMANDS = [
    "qemu-system-x86_64",
    "qemu-img",
    "ip",
    "python3",
    "curl",
]

OPTIONAL_COMMANDS = [
    "docker",
    "podman",
    "dnsmasq",
    "brctl",
]

APT_PACKAGES = [
    "qemu-system-x86",
    "qemu-utils",
    "iproute2",
    "curl",
    "dnsmasq-base",
    "bridge-utils",
    "python3",
]

DEFAULT_OPENWRT_VERSION = "24.10.1"
DEFAULT_WORKDIR = "~/tollgate-virtual-lab"
POC_BRIDGE = "tg-poc-br"
POC_TAP = "tg-poc-tap"
POC_NETNS = "tg-poc-client"
POC_VETH_HOST = "tg-poc-vh"
POC_VETH_CLIENT = "tg-poc-vc"
POC_GATEWAY = "192.168.1.1"
POC_CLIENT_IP = "192.168.1.50/24"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_local(command: list[str], timeout: int = 30) -> CommandResult:
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(proc.returncode, proc.stdout.strip(), proc.stderr.strip())


def run_remote(host: str, script: str, timeout: int = 60) -> CommandResult:
    if host in {"", "local", "localhost", "127.0.0.1"}:
        return run_local(["bash", "-lc", script.removeprefix("bash -lc ").strip("'")], timeout=timeout)
    return run_local(["ssh", host, script], timeout=timeout)


def quote_script(script: str) -> str:
    return "bash -lc " + shlex.quote(script)


def remote_exists(host: str, command: str) -> bool:
    result = run_remote(host, quote_script(f"command -v {shlex.quote(command)} >/dev/null 2>&1"))
    return result.returncode == 0


def doctor(args: argparse.Namespace) -> int:
    host = cast(str, args.host)
    script = r'''
set +e
printf '== host ==\n'
hostname
uname -a
printf '\n== os ==\n'
if [ -r /etc/os-release ]; then . /etc/os-release; printf '%s %s\n' "$NAME" "$VERSION"; fi
printf '\n== cpu/memory ==\n'
nproc
free -h
printf '\n== kvm ==\n'
ls -l /dev/kvm 2>&1 || true
if [ -e /dev/kvm ] && [ -r /dev/kvm ] && [ -w /dev/kvm ]; then printf 'current user can access /dev/kvm\n'; else printf 'current user cannot access /dev/kvm\n'; fi
if [ -r /sys/module/kvm_amd/parameters/nested ]; then printf 'kvm_amd nested=%s\n' "$(cat /sys/module/kvm_amd/parameters/nested)"; fi
if [ -r /sys/module/kvm_intel/parameters/nested ]; then printf 'kvm_intel nested=%s\n' "$(cat /sys/module/kvm_intel/parameters/nested)"; fi
printf '\n== commands ==\n'
for c in qemu-system-x86_64 qemu-img ip python3 curl docker podman dnsmasq brctl; do
  printf '%s: ' "$c"
  command -v "$c" || true
done
printf '\n== user ==\n'
id
groups
'''
    result = run_remote(host, quote_script(script), timeout=60)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    missing_required = [cmd for cmd in REQUIRED_COMMANDS if not remote_exists(host, cmd)]
    missing_optional = [cmd for cmd in OPTIONAL_COMMANDS if not remote_exists(host, cmd)]

    print("\n== virtual lab readiness ==")
    if missing_required:
        print("missing required commands: " + ", ".join(missing_required))
        print("install on host:")
        print("  sudo apt update")
        print("  sudo apt install -y " + " ".join(APT_PACKAGES))
        return 1

    print("required commands: ok")
    if missing_optional:
        print("missing optional commands: " + ", ".join(missing_optional))
    else:
        print("optional commands: ok")

    kvm = run_remote(host, quote_script("test -e /dev/kvm"))
    if kvm.returncode != 0:
        print("/dev/kvm missing: QEMU can still run, but it will be too slow for the full lab")
        return 1

    kvm_access = run_remote(host, quote_script("test -r /dev/kvm -a -w /dev/kvm"))
    if kvm_access.returncode != 0:
        print("/dev/kvm exists, but the SSH user cannot access it")
        print("fix on host:")
        print("  sudo usermod -aG kvm $(whoami)")
        print("  # then open a fresh SSH session")
        return 1

    print("/dev/kvm: ok")
    return 0


def install_deps(args: argparse.Namespace) -> int:
    host = cast(str, args.host)
    packages = " ".join(shlex.quote(pkg) for pkg in APT_PACKAGES)
    script = f"sudo apt update && sudo apt install -y {packages}"
    result = run_remote(host, quote_script(script), timeout=900)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def prepare_image(args: argparse.Namespace) -> int:
    host = cast(str, args.host)
    version = cast(str, args.openwrt_version)
    workdir = cast(str, args.workdir)
    image_name = f"openwrt-{version}-x86-64-generic-ext4-combined.img"
    image_gz = f"{image_name}.gz"
    image_url = (
        f"https://downloads.openwrt.org/releases/{version}/targets/x86/64/"
        f"{image_gz}"
    )
    script = f'''
set -eu
workdir={shlex.quote(workdir)}
workdir=$(eval printf '%s' "$workdir")
mkdir -p "$workdir/images" "$workdir/overlays"
cd "$workdir/images"
if [ ! -f {shlex.quote(image_gz)} ]; then
  curl -fL -o {shlex.quote(image_gz)} {shlex.quote(image_url)}
fi
if [ ! -f {shlex.quote(image_name)} ]; then
  gzip -dk {shlex.quote(image_gz)} || test -f {shlex.quote(image_name)}
fi
if [ ! -f openwrt-base.qcow2 ]; then
  qemu-img convert -f raw -O qcow2 {shlex.quote(image_name)} openwrt-base.qcow2
  qemu-img resize openwrt-base.qcow2 2G
fi
for router in seller reseller; do
  overlay="$workdir/overlays/${{router}}.qcow2"
  if [ ! -f "$overlay" ]; then
    qemu-img create -f qcow2 -F qcow2 -b "$workdir/images/openwrt-base.qcow2" "$overlay"
  fi
done
qemu-img info "$workdir/images/openwrt-base.qcow2"
printf '\nPrepared overlays:\n'
ls -lh "$workdir/overlays"
'''
    result = run_remote(host, quote_script(script), timeout=900)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def _print_result(result: CommandResult) -> int:
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def _poc_paths(workdir: str) -> tuple[str, str, str]:
    expanded = "$(eval printf '%s' " + shlex.quote(workdir) + ")"
    pidfile = f"{expanded}/run/tollgate.pid"
    logfile = f"{expanded}/run/tollgate.log"
    disk = f"{expanded}/overlays/tollgate-poc.qcow2"
    return pidfile, logfile, disk


def start_poc(args: argparse.Namespace) -> int:
    host = cast(str, args.host)
    workdir = cast(str, args.workdir)
    pidfile, logfile, disk = _poc_paths(workdir)
    script = f'''
set -eu
workdir={shlex.quote(workdir)}
workdir=$(eval printf '%s' "$workdir")
mkdir -p "$workdir/run" "$workdir/overlays"
base="$workdir/images/openwrt-base.qcow2"
disk={disk}
pidfile={pidfile}
logfile={logfile}

if [ ! -f "$base" ]; then
  printf 'OpenWrt base image missing. Run prepare-image first.\n' >&2
  exit 1
fi

if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  printf 'POC VM already running with pid %s\n' "$(cat "$pidfile")"
  exit 0
fi

sudo ip netns del {POC_NETNS} 2>/dev/null || true
sudo ip link del {POC_VETH_HOST} 2>/dev/null || true
sudo ip link del {POC_TAP} 2>/dev/null || true
sudo ip link del {POC_BRIDGE} 2>/dev/null || true

sudo ip link add name {POC_BRIDGE} type bridge
sudo ip link set {POC_BRIDGE} up
sudo ip tuntap add dev {POC_TAP} mode tap user "$USER"
sudo ip link set {POC_TAP} master {POC_BRIDGE}
sudo ip link set {POC_TAP} up

sudo ip netns add {POC_NETNS}
sudo ip link add {POC_VETH_HOST} type veth peer name {POC_VETH_CLIENT}
sudo ip link set {POC_VETH_HOST} master {POC_BRIDGE}
sudo ip link set {POC_VETH_HOST} up
sudo ip link set {POC_VETH_CLIENT} netns {POC_NETNS}
sudo ip netns exec {POC_NETNS} ip link set lo up
sudo ip netns exec {POC_NETNS} ip addr add {POC_CLIENT_IP} dev {POC_VETH_CLIENT}
sudo ip netns exec {POC_NETNS} ip link set {POC_VETH_CLIENT} up
sudo ip netns exec {POC_NETNS} ip route add default via {POC_GATEWAY}

if [ ! -f "$disk" ]; then
  qemu-img create -f qcow2 -F qcow2 -b "$base" "$disk"
fi

nohup qemu-system-x86_64 \
  -enable-kvm \
  -m 256 \
  -smp 1 \
  -nographic \
  -serial file:"$logfile" \
  -drive file="$disk",if=virtio,format=qcow2 \
  -netdev tap,id=lan,ifname={POC_TAP},script=no,downscript=no \
  -device virtio-net-pci,netdev=lan \
  >"$workdir/run/qemu.stdout" 2>"$workdir/run/qemu.stderr" &
printf '%s\n' "$!" > "$pidfile"

printf 'Started POC OpenWrt VM pid=%s\n' "$(cat "$pidfile")"
printf 'Linux client namespace: {POC_NETNS} ({POC_CLIENT_IP})\n'
printf 'OpenWrt gateway expected at: {POC_GATEWAY}\n'
'''
    return _print_result(run_remote(host, quote_script(script), timeout=120))


def stop_poc(args: argparse.Namespace) -> int:
    host = cast(str, args.host)
    workdir = cast(str, args.workdir)
    pidfile, _, _ = _poc_paths(workdir)
    script = f'''
set +e
workdir={shlex.quote(workdir)}
workdir=$(eval printf '%s' "$workdir")
pidfile={pidfile}
if [ -f "$pidfile" ]; then
  pid=$(cat "$pidfile")
  kill "$pid" 2>/dev/null || true
  sleep 1
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$pidfile"
fi
sudo ip netns del {POC_NETNS} 2>/dev/null || true
sudo ip link del {POC_VETH_HOST} 2>/dev/null || true
sudo ip link del {POC_TAP} 2>/dev/null || true
sudo ip link del {POC_BRIDGE} 2>/dev/null || true
printf 'Stopped POC virtual lab\n'
'''
    return _print_result(run_remote(host, quote_script(script), timeout=60))


def status_poc(args: argparse.Namespace) -> int:
    host = cast(str, args.host)
    workdir = cast(str, args.workdir)
    pidfile, logfile, disk = _poc_paths(workdir)
    script = f'''
set +e
workdir={shlex.quote(workdir)}
workdir=$(eval printf '%s' "$workdir")
pidfile={pidfile}
logfile={logfile}
disk={disk}
printf '== process ==\n'
if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  printf 'running pid=%s\n' "$(cat "$pidfile")"
else
  printf 'not running\n'
fi
printf '\n== links ==\n'
ip link show {POC_BRIDGE} 2>/dev/null || true
ip link show {POC_TAP} 2>/dev/null || true
ip netns list | grep -F {POC_NETNS} || true
printf '\n== client ==\n'
sudo ip netns exec {POC_NETNS} ip addr show {POC_VETH_CLIENT} 2>/dev/null || true
sudo ip netns exec {POC_NETNS} ip route 2>/dev/null || true
printf '\n== disk ==\n'
if [ -f "$disk" ]; then qemu-img info "$disk"; else printf 'missing %s\n' "$disk"; fi
printf '\n== recent serial log ==\n'
if [ -f "$logfile" ]; then tail -40 "$logfile"; fi
'''
    return _print_result(run_remote(host, quote_script(script), timeout=60))


def smoke_poc(args: argparse.Namespace) -> int:
    host = cast(str, args.host)
    timeout = cast(int, args.timeout)
    script = f'''
set +e
deadline=$((SECONDS + {timeout}))
while [ "$SECONDS" -lt "$deadline" ]; do
  if sudo ip netns exec {POC_NETNS} ping -c 1 -W 1 {POC_GATEWAY} >/dev/null 2>&1; then
    printf 'PASS: Linux client namespace {POC_NETNS} reached OpenWrt gateway {POC_GATEWAY}\n'
    exit 0
  fi
  sleep 2
done
printf 'FAIL: Linux client namespace {POC_NETNS} could not reach OpenWrt gateway {POC_GATEWAY}\n' >&2
exit 1
'''
    return _print_result(run_remote(host, quote_script(script), timeout=timeout + 10))


def run_poc(args: argparse.Namespace) -> int:
    rc = prepare_image(args)
    if rc != 0:
        return rc
    rc = start_poc(args)
    if rc != 0:
        return rc
    return smoke_poc(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the TollGate virtual lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="Check host readiness")
    _ = doctor_parser.add_argument("--host", default="218", help="SSH host for the Ubuntu lab machine")
    doctor_parser.set_defaults(func=doctor)

    deps_parser = subparsers.add_parser("install-deps", help="Install host dependencies via apt")
    _ = deps_parser.add_argument("--host", default="218", help="SSH host for the Ubuntu lab machine")
    deps_parser.set_defaults(func=install_deps)

    image_parser = subparsers.add_parser("prepare-image", help="Download and prepare OpenWrt x86 images")
    _ = image_parser.add_argument("--host", default="218", help="SSH host for the Ubuntu lab machine")
    _ = image_parser.add_argument("--openwrt-version", default=DEFAULT_OPENWRT_VERSION)
    _ = image_parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    image_parser.set_defaults(func=prepare_image)

    start_parser = subparsers.add_parser("start-poc", help="Start one OpenWrt VM plus Linux client namespace")
    _ = start_parser.add_argument("--host", default="218", help="SSH host for the Ubuntu lab machine")
    _ = start_parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    start_parser.set_defaults(func=start_poc)

    stop_parser = subparsers.add_parser("stop-poc", help="Stop the POC VM and client namespace")
    _ = stop_parser.add_argument("--host", default="218", help="SSH host for the Ubuntu lab machine")
    _ = stop_parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    stop_parser.set_defaults(func=stop_poc)

    status_parser = subparsers.add_parser("status-poc", help="Show POC VM/client status")
    _ = status_parser.add_argument("--host", default="218", help="SSH host for the Ubuntu lab machine")
    _ = status_parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    status_parser.set_defaults(func=status_poc)

    smoke_parser = subparsers.add_parser("smoke-poc", help="Verify Linux client reaches OpenWrt gateway")
    _ = smoke_parser.add_argument("--host", default="218", help="SSH host for the Ubuntu lab machine")
    _ = smoke_parser.add_argument("--timeout", type=int, default=120)
    smoke_parser.set_defaults(func=smoke_poc)

    poc_parser = subparsers.add_parser("poc", help="Prepare image, start VM/client, and run smoke proof")
    _ = poc_parser.add_argument("--host", default="218", help="SSH host for the Ubuntu lab machine")
    _ = poc_parser.add_argument("--openwrt-version", default=DEFAULT_OPENWRT_VERSION)
    _ = poc_parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    _ = poc_parser.add_argument("--timeout", type=int, default=120)
    poc_parser.set_defaults(func=run_poc)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    func = cast(Callable[[argparse.Namespace], int], args.func)
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
