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
    "sshpass",
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
POC_NETNS = "tg-poc-client"  # kept for compatibility
POC_CONTAINER = "tg-poc-client"
POC_VETH_HOST = "tg-poc-dc0"
POC_VETH_CLIENT = "tg-poc-dc1"
POC_GATEWAY = "192.168.1.1"
POC_HOST_BRIDGE_IP = "192.168.1.2/24"
POC_CLIENT_IP = "192.168.1.100/24"
POC_PASSWORD = "tollgate"
POC_SUBNET = "192.168.1.0/24"

# Template for the serial-console provisioning script.  Placeholders
# ``__WORKDIR__`` and ``__PASSWORD__`` are substituted at runtime by
# ``_generate_provision_script()``.  We use a plain string (not an
# f-string) so that Python braces inside the generated code do not need
# to be doubled.
_PROVISION_TEMPLATE = r"""
import socket, time, sys, os

SOCK_PATH = os.path.expanduser('__WORKDIR__') + '/run/serial.sock'
PASSWORD = '__PASSWORD__'
BOOT_TIMEOUT = 60

def recv_all(s, timeout=5):
    s.settimeout(timeout)
    chunks = []
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except socket.timeout:
        pass
    return b''.join(chunks).decode('utf-8', errors='replace')

def send_and_wait(s, cmd, wait=3):
    s.sendall((cmd + '\n').encode())
    time.sleep(wait)
    return recv_all(s, timeout=2)

# Connect to serial console (retry until socket appears)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
deadline = time.time() + 30
connected = False
while time.time() < deadline:
    try:
        s.connect(SOCK_PATH)
        connected = True
        break
    except (ConnectionRefusedError, FileNotFoundError):
        time.sleep(2)
if not connected:
    print('TIMEOUT: could not connect to serial socket at ' + SOCK_PATH)
    sys.exit(1)

print('Connected to serial console, waiting for VM to boot...')

# Wait for the boot prompt
deadline = time.time() + BOOT_TIMEOUT
booted = False
while time.time() < deadline:
    data = recv_all(s, timeout=2)
    if data.strip():
        sys.stdout.write(data)
        sys.stdout.flush()
    if 'Please press Enter' in data:
        booted = True
        break
    time.sleep(2)
if not booted:
    print('TIMEOUT: VM did not reach boot prompt within ' + str(BOOT_TIMEOUT) + 's')
    s.close()
    sys.exit(1)

print('\nVM booted, activating console...')
time.sleep(1)
send_and_wait(s, '', wait=3)
recv_all(s, timeout=1)

# --- Set root password (BusyBox has no chpasswd) ---
print('Setting root password...')
resp = send_and_wait(s, "printf '%s\\n%s\\n' '" + PASSWORD + "' '" + PASSWORD + "' | passwd root", wait=5)
print(resp.strip())

# --- Enable SSH password authentication ---
print('Enabling SSH password auth...')
send_and_wait(s, "uci set dropbear.@dropbear[0].PasswordAuth='on'", wait=2)
send_and_wait(s, 'uci commit dropbear', wait=2)
send_and_wait(s, '/etc/init.d/dropbear restart', wait=3)

# --- Add WAN SSH firewall rule ---
print('Adding WAN SSH firewall rule...')
send_and_wait(s, 'uci add firewall rule', wait=2)
send_and_wait(s, "uci set firewall.@rule[-1].name='Allow-SSH-WAN'", wait=2)
send_and_wait(s, "uci set firewall.@rule[-1].src='wan'", wait=2)
send_and_wait(s, "uci set firewall.@rule[-1].dest_port='22'", wait=2)
send_and_wait(s, "uci set firewall.@rule[-1].proto='tcp'", wait=2)
send_and_wait(s, "uci set firewall.@rule[-1].target='ACCEPT'", wait=2)
send_and_wait(s, 'uci commit firewall', wait=2)
send_and_wait(s, 'fw4 restart', wait=5)

print('Configuring internet access via host bridge...')
send_and_wait(s, "uci set network.lan.gateway='192.168.1.2'", wait=2)
send_and_wait(s, "uci set network.lan.dns='8.8.8.8'", wait=2)
send_and_wait(s, 'uci commit network', wait=2)
send_and_wait(s, '/etc/init.d/network restart', wait=5)

print('PROVISIONED OK')
s.close()
"""


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
        return run_local(
            ["bash", "-lc", script.removeprefix("bash -lc ").strip("'")],
            timeout=timeout,
        )
    return run_local(["ssh", host, script], timeout=timeout)


def run_python_on_host(host: str, python_code: str, timeout: int = 120) -> CommandResult:
    """Execute Python code on the target host (local or remote via SSH)."""
    if host in {"", "local", "localhost", "127.0.0.1"}:
        proc = subprocess.run(
            ["python3"],
            input=python_code,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    else:
        proc = subprocess.run(
            ["ssh", host, "python3"],
            input=python_code,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    return CommandResult(proc.returncode, proc.stdout.strip(), proc.stderr.strip())


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
    """Return (pidfile, serial_sock, disk) as shell-expanded path expressions."""
    expanded = "$(eval printf '%s' " + shlex.quote(workdir) + ")"
    pidfile = f"{expanded}/run/tollgate.pid"
    serial_sock = f"{expanded}/run/serial.sock"
    disk = f"{expanded}/overlays/tollgate-poc.qcow2"
    return pidfile, serial_sock, disk


def _generate_provision_script(workdir: str) -> str:
    """Return a self-contained Python script that provisions the OpenWrt VM
    over the QEMU serial-console Unix socket."""
    pwd = POC_PASSWORD.replace("'", "'\\''")
    wdir = workdir.replace("'", "'\\''")
    return _PROVISION_TEMPLATE.replace("__WORKDIR__", wdir).replace("__PASSWORD__", pwd)


def start_poc(args: argparse.Namespace) -> int:
    host = cast(str, args.host)
    workdir = cast(str, args.workdir)
    pidfile, _serial_sock, disk = _poc_paths(workdir)

    # Step 1: bridge, tap, host IP, overlay, QEMU
    infra_script = f'''
set -eu
workdir={shlex.quote(workdir)}
workdir=$(eval printf '%s' "$workdir")
mkdir -p "$workdir/run" "$workdir/overlays"
base="$workdir/images/openwrt-base.qcow2"
disk={disk}
pidfile={pidfile}

if [ ! -f "$base" ]; then
  printf 'OpenWrt base image missing. Run prepare-image first.\n' >&2
  exit 1
fi

if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  printf 'POC VM already running with pid %s\n' "$(cat "$pidfile")"
  exit 0
fi

# Clean up old resources
docker rm -f {POC_CONTAINER} 2>/dev/null || true
sudo ip link del {POC_VETH_HOST} 2>/dev/null || true
sudo ip link del tg-poc-vh 2>/dev/null || true
sudo ip netns del {POC_NETNS} 2>/dev/null || true
sudo ip link del {POC_TAP} 2>/dev/null || true
sudo ip link del {POC_BRIDGE} 2>/dev/null || true

# Create bridge and tap
sudo ip link add name {POC_BRIDGE} type bridge
sudo ip link set {POC_BRIDGE} up
sudo ip addr add {POC_HOST_BRIDGE_IP} dev {POC_BRIDGE} 2>/dev/null || true

_subnet=$(ip -4 route show exact {POC_SUBNET} | grep -v "dev {POC_BRIDGE}" | head -1)
if [ -n "$_subnet" ]; then
  _dev=$(echo "$_subnet" | sed -n 's/.*dev \\([^ ]*\\).*/\\1/p')
  echo "Route conflict: moving {POC_SUBNET} from $_dev to {POC_BRIDGE}"
  sudo ip route del {POC_SUBNET} dev "$_dev" 2>/dev/null || true
fi

sudo ip tuntap add dev {POC_TAP} mode tap user "$USER"
sudo ip link set {POC_TAP} master {POC_BRIDGE}
sudo ip link set {POC_TAP} up

sudo iptables -C FORWARD -i {POC_BRIDGE} -j ACCEPT 2>/dev/null || sudo iptables -I FORWARD 1 -i {POC_BRIDGE} -j ACCEPT
sudo iptables -C FORWARD -o {POC_BRIDGE} -j ACCEPT 2>/dev/null || sudo iptables -I FORWARD 2 -o {POC_BRIDGE} -j ACCEPT
sudo iptables -t nat -C POSTROUTING -s {POC_SUBNET} ! -o {POC_BRIDGE} -j MASQUERADE 2>/dev/null || sudo iptables -t nat -A POSTROUTING -s {POC_SUBNET} ! -o {POC_BRIDGE} -j MASQUERADE

# Create overlay if needed
if [ ! -f "$disk" ]; then
  qemu-img create -f qcow2 -F qcow2 -b "$base" "$disk"
fi

# Start QEMU with serial/monitor Unix sockets
nohup qemu-system-x86_64 \
  -enable-kvm \
  -m 256 \
  -smp 1 \
  -nographic \
  -serial unix:"$workdir/run/serial.sock",server,nowait \
  -monitor unix:"$workdir/run/monitor.sock",server,nowait \
  -drive file="$disk",if=virtio,format=qcow2 \
  -netdev tap,id=lan,ifname={POC_TAP},script=no,downscript=no \
  -device virtio-net-pci,netdev=lan \
  >"$workdir/run/qemu.stdout" 2>"$workdir/run/qemu.stderr" &
printf '%s\n' "$!" > "$pidfile"

printf 'Started POC OpenWrt VM pid=%s\n' "$(cat "$pidfile")"
'''
    rc = _print_result(run_remote(host, quote_script(infra_script), timeout=120))
    if rc != 0:
        return rc

    # Step 2: provision VM via serial console
    print("Provisioning OpenWrt VM via serial console...")
    provision_script = _generate_provision_script(workdir)
    result = run_python_on_host(host, provision_script, timeout=120)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        print("Serial console provisioning failed.", file=sys.stderr)
        return result.returncode

    # Step 3: verify SSH login
    print("Verifying SSH access to OpenWrt VM...")
    ssh_verify = (
        f"sshpass -p {POC_PASSWORD} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=10 -o LogLevel=ERROR "
        f"root@{POC_GATEWAY} 'echo SSH_OK'"
    )
    rc = _print_result(run_remote(host, quote_script(ssh_verify), timeout=30))
    if rc != 0:
        print("SSH verification failed.", file=sys.stderr)
        return rc

    # Step 4: Debian client container
    print("Starting Debian client container...")
    container_script = f'''
set -eu

# Remove old container/veth if they exist
docker rm -f {POC_CONTAINER} 2>/dev/null || true
sudo ip link del {POC_VETH_HOST} 2>/dev/null || true

# Start container with Docker's default bridge (has internet for apt),
# install packages, then disconnect and wire into tg-poc-br manually.
docker run -d \
  --name {POC_CONTAINER} \
  --cap-add NET_ADMIN \
  debian:bookworm-slim \
  sleep infinity

docker exec {POC_CONTAINER} bash -c "apt update -qq && apt install -y -qq curl iputils-ping iproute2"

docker network disconnect bridge {POC_CONTAINER}

PID=$(docker inspect -f '{{{{.State.Pid}}}}' {POC_CONTAINER})
sudo ip link add {POC_VETH_HOST} type veth peer name {POC_VETH_CLIENT}
sudo ip link set {POC_VETH_CLIENT} netns $PID
sudo ip link set {POC_VETH_HOST} master {POC_BRIDGE}
sudo ip link set {POC_VETH_HOST} up

sudo nsenter -t $PID -n ip link set lo up
sudo nsenter -t $PID -n ip link set {POC_VETH_CLIENT} up
sudo nsenter -t $PID -n ip addr add {POC_CLIENT_IP} dev {POC_VETH_CLIENT}
sudo nsenter -t $PID -n ip route add default via {POC_GATEWAY}

printf 'Container {POC_CONTAINER} ready at {POC_CLIENT_IP}\n'
'''
    rc = _print_result(run_remote(host, quote_script(container_script), timeout=300))
    if rc != 0:
        return rc

    print(f"\nPOC environment ready:")
    print(f"  OpenWrt VM: {POC_GATEWAY}")
    print(f"  Client container: {POC_CONTAINER} at {POC_CLIENT_IP}")
    print(f"  Host bridge IP: {POC_HOST_BRIDGE_IP}")
    return 0


def stop_poc(args: argparse.Namespace) -> int:
    host = cast(str, args.host)
    workdir = cast(str, args.workdir)
    pidfile, _, _ = _poc_paths(workdir)
    script = f'''
set +e
workdir={shlex.quote(workdir)}
workdir=$(eval printf '%s' "$workdir")
pidfile={pidfile}

# Stop Docker container
docker rm -f {POC_CONTAINER} 2>/dev/null || true

# Clean up veth pairs (new and old names)
sudo ip link del {POC_VETH_HOST} 2>/dev/null || true
sudo ip link del tg-poc-vh 2>/dev/null || true

# Stop QEMU
if [ -f "$pidfile" ]; then
  pid=$(cat "$pidfile")
  kill "$pid" 2>/dev/null || true
  sleep 1
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$pidfile"
fi

# Clean up remaining network resources
sudo ip netns del {POC_NETNS} 2>/dev/null || true
sudo ip link del {POC_TAP} 2>/dev/null || true
sudo ip link del {POC_BRIDGE} 2>/dev/null || true

printf 'Stopped POC virtual lab\n'
'''
    return _print_result(run_remote(host, quote_script(script), timeout=60))


def status_poc(args: argparse.Namespace) -> int:
    host = cast(str, args.host)
    workdir = cast(str, args.workdir)
    pidfile, _serial_sock, disk = _poc_paths(workdir)
    script = f'''
set +e
workdir={shlex.quote(workdir)}
workdir=$(eval printf '%s' "$workdir")
pidfile={pidfile}
disk={disk}

printf '== QEMU process ==\n'
if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  printf 'running pid=%s\n' "$(cat "$pidfile")"
else
  printf 'not running\n'
fi

printf '\n== network ==\n'
ip link show {POC_BRIDGE} 2>/dev/null || true
ip link show {POC_TAP} 2>/dev/null || true
ip link show {POC_VETH_HOST} 2>/dev/null || true
ip addr show {POC_BRIDGE} 2>/dev/null | grep -F 'inet ' || true

printf '\n== container ==\n'
docker ps -f name={POC_CONTAINER} --format '{{{{.Names}}}} {{{{.Status}}}}' 2>/dev/null || printf 'not running\n'

printf '\n== disk ==\n'
if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  ls -lh "$disk" 2>/dev/null || true
  printf 'qcow2 is in use by the running VM; skipping qemu-img info\n'
elif [ -f "$disk" ]; then
  qemu-img info "$disk"
else
  printf 'missing %s\n' "$disk"
fi

printf '\n== serial console ==\n'
if [ -S "$workdir/run/serial.sock" ]; then
  printf 'serial socket: $workdir/run/serial.sock (ready)\n'
else
  printf 'serial socket not found\n'
fi
'''
    return _print_result(run_remote(host, quote_script(script), timeout=60))


def smoke_poc(args: argparse.Namespace) -> int:
    host = cast(str, args.host)
    timeout = cast(int, args.timeout)
    script = f'''
set +e
deadline=$((SECONDS + {timeout}))
while [ "$SECONDS" -lt "$deadline" ]; do
  if docker exec {POC_CONTAINER} ping -c 1 -W 2 {POC_GATEWAY} >/dev/null 2>&1; then
    printf 'PASS: Container {POC_CONTAINER} reached OpenWrt gateway {POC_GATEWAY}\n'
    exit 0
  fi
  sleep 2
done
printf 'FAIL: Container {POC_CONTAINER} could not reach OpenWrt gateway {POC_GATEWAY}\n' >&2
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

    start_parser = subparsers.add_parser("start-poc", help="Start one OpenWrt VM plus Debian client container")
    _ = start_parser.add_argument("--host", default="218", help="SSH host for the Ubuntu lab machine")
    _ = start_parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    start_parser.set_defaults(func=start_poc)

    stop_parser = subparsers.add_parser("stop-poc", help="Stop the POC VM and client container")
    _ = stop_parser.add_argument("--host", default="218", help="SSH host for the Ubuntu lab machine")
    _ = stop_parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    stop_parser.set_defaults(func=stop_poc)

    status_parser = subparsers.add_parser("status-poc", help="Show POC VM/client status")
    _ = status_parser.add_argument("--host", default="218", help="SSH host for the Ubuntu lab machine")
    _ = status_parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    status_parser.set_defaults(func=status_poc)

    smoke_parser = subparsers.add_parser("smoke-poc", help="Verify client container reaches OpenWrt gateway")
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
