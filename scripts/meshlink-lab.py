#!/usr/bin/env python3
"""Local QEMU lane for MeshLink <-> TollGate interoperability.

Topology (host = upstream router + Cashu fake mint):

    tg-upstream-br 10.99.98.0/24  host .1, mint .2:8383
      meshlink-gateway ens3 (WAN 10.99.98.10)
      beta-seller (optional, later)
    tg-mesh-br 10.99.100.0/24 (no host IP)
      meshlink-gateway ens4 -> br-guest 10.99.100.1
      meshlink-client ens3 (DHCP)
    meshlink-mgmt-br 10.99.97.0/24  host .1
      meshlink-gateway ens5 10.99.97.15
      meshlink-client ens4 10.99.97.16

Both VMs are Debian 12 cloud images. The repos are shared read-only over 9p
(mount tag ``hostrepos``) so edits on the host appear in the guest without a
rebuild cycle.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_WORKDIR = "~/meshlink-lab"
DEFAULT_REPOS = "~/repos"
DEBIAN_IMAGE = "debian-12-generic-amd64.qcow2"
DEBIAN_IMAGE_URL = f"https://cloud.debian.org/images/cloud/bookworm/latest/{DEBIAN_IMAGE}"

UP_BRIDGE = "tg-upstream-br"
MESH_BRIDGE = "tg-mesh-br"
MGMT_BRIDGE = "ml-mgmt-br"
UP_HOST_IP = "10.99.98.1"
MINT_IP = "10.99.98.2"
MINT_PORT = 8383
MINT_URL = f"http://{MINT_IP}:{MINT_PORT}"
MESH_HOST_IP = "10.99.97.1"
GATEWAY_WAN_IP = "10.99.98.10"
GATEWAY_MGMT_IP = "10.99.97.15"
CLIENT_MGMT_IP = "10.99.97.16"
MESH_GUEST_IP = "10.99.100.1"

WAN_TAP = "ml-wan"
GUEST_TAP = "ml-guest"
MGMT_TAP = "ml-mgmt"
CLIENT_GUEST_TAP = "mlc-guest"
CLIENT_MGMT_TAP = "mlc-mgmt"


def run(cmd: list[str], *, check: bool = True, capture: bool = True, timeout: int | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, timeout=timeout, cwd=cwd)


def sudo(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return run(["sudo", "-n", *cmd], **kwargs)


def expand(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


def wait_for(command: list[str], timeout: int, interval: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if run(command, check=False).returncode == 0:
            return True
        time.sleep(interval)
    return False


def ensure_bridge(name: str, ip: str | None = None) -> None:
    if run(["ip", "link", "show", name], check=False).returncode != 0:
        sudo(["ip", "link", "add", "name", name, "type", "bridge"])
    sudo(["ip", "link", "set", name, "up"])
    if ip:
        sudo(["ip", "addr", "replace", ip, "dev", name])


def ensure_tap(name: str, bridge: str) -> None:
    if run(["ip", "link", "show", name], check=False).returncode == 0:
        sudo(["ip", "link", "set", name, "down"])
        sudo(["ip", "link", "del", name])
    sudo(["ip", "tuntap", "add", "dev", name, "mode", "tap", "user", str(os.getuid())])
    sudo(["ip", "link", "set", name, "master", bridge])
    sudo(["ip", "link", "set", name, "up"])


def delete_link(name: str) -> None:
    sudo(["ip", "link", "set", name, "down"], check=False)
    sudo(["ip", "link", "del", name], check=False)


def configure_host_nat() -> None:
    sudo(["sysctl", "-w", "net.ipv4.ip_forward=1"])
    rules = [
        ["iptables", "-C", "INPUT", "-i", UP_BRIDGE, "-p", "tcp", "--dport", str(MINT_PORT), "-j", "ACCEPT"],
        ["iptables", "-t", "nat", "-C", "POSTROUTING", "-s", "10.99.98.0/24", "!", "-o", UP_BRIDGE, "-j", "MASQUERADE"],
        ["iptables", "-C", "FORWARD", "-i", UP_BRIDGE, "-j", "ACCEPT"],
        ["iptables", "-C", "FORWARD", "-o", UP_BRIDGE, "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
    ]
    adds = [
        ["iptables", "-I", "INPUT", "1", "-i", UP_BRIDGE, "-p", "tcp", "--dport", str(MINT_PORT), "-j", "ACCEPT"],
        ["iptables", "-t", "nat", "-A", "POSTROUTING", "-s", "10.99.98.0/24", "!", "-o", UP_BRIDGE, "-j", "MASQUERADE"],
        ["iptables", "-A", "FORWARD", "-i", UP_BRIDGE, "-j", "ACCEPT"],
        ["iptables", "-A", "FORWARD", "-o", UP_BRIDGE, "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
    ]
    for check, add in zip(rules, adds):
        if sudo(check, check=False).returncode != 0:
            sudo(add)


def ssh_pubkey() -> str:
    for name in ("id_ed25519.pub", "id_rsa.pub"):
        path = Path.home() / ".ssh" / name
        if path.exists():
            return path.read_text().strip()
    return ""


def cloud_init_files(workdir: Path, hostname: str, kind: str, repos: Path) -> tuple[Path, Path]:
    images = workdir / "images"
    images.mkdir(parents=True, exist_ok=True)
    pubkey = ssh_pubkey()
    password = os.environ.get("MESHLINK_LAB_PASSWORD", "meshlink-lab")
    authorized = f"ssh_authorized_keys:\n  - {pubkey}\n" if pubkey else ""

    if kind == "gateway":
        network = f"""\
version: 2
ethernets:
  wan0:
    match:
      macaddress: "52:54:00:4d:4c:01"
    set-name: wan0
    addresses: [{GATEWAY_WAN_IP}/24]
    routes:
      - to: default
        via: {UP_HOST_IP}
    nameservers:
      addresses: [1.1.1.1]
  guest0:
    match:
      macaddress: "52:54:00:4d:4c:02"
    set-name: guest0
    dhcp4: false
    optional: true
  mgmt0:
    match:
      macaddress: "52:54:00:4d:4c:03"
    set-name: mgmt0
    addresses: [{GATEWAY_MGMT_IP}/24]
"""
        runcmd = f"""\
runcmd:
  - [ bash, -c, "fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile || true" ]
  - [ mkdir, -p, /mnt/hostrepos ]
  - [ bash, -c, "mount -t 9p -o trans=virtio,version=9p2000.L hostrepos /mnt/hostrepos || true" ]
  - [ bash, -c, "mkdir -p /opt/meshlink && rsync -a --delete --exclude node_modules --exclude dist --exclude .git /mnt/hostrepos/meshlink-wifi-gateway/ /opt/meshlink/gateway/ && rsync -a --delete --exclude node_modules --exclude dist --exclude .git /mnt/hostrepos/meshlink-broker/ /opt/meshlink/broker/ || true" ]
"""
        packages = "openssh-server curl ca-certificates rsync iproute2 iptables ipset dnsmasq python3"
    else:
        network = f"""\
version: 2
ethernets:
  guest0:
    match:
      macaddress: "52:54:00:4d:4c:11"
    set-name: guest0
    dhcp4: true
    optional: true
    nameservers:
      addresses: [{MESH_GUEST_IP}]
  mgmt0:
    match:
      macaddress: "52:54:00:4d:4c:12"
    set-name: mgmt0
    addresses: [{CLIENT_MGMT_IP}/24]
"""
        runcmd = f"""\
runcmd:
  - [ mkdir, -p, /mnt/hostrepos ]
  - [ bash, -c, "mount -t 9p -o trans=virtio,version=9p2000.L hostrepos /mnt/hostrepos || true" ]
  - [ bash, -c, "install -m 755 /mnt/hostrepos/tollgate-client/target/x86_64-unknown-linux-musl/release/tollgate-agent /usr/local/bin/tollgate-agent || install -m 755 /mnt/hostrepos/tollgate-client/target/release/tollgate-agent /usr/local/bin/tollgate-agent || true" ]
"""
        packages = "openssh-server curl ca-certificates iproute2 iputils-ping"

    user_data = f"""#cloud-config
hostname: {hostname}
manage_etc_hosts: true
disable_root: false
ssh_pwauth: true
chpasswd:
  expire: false
  users:
    - name: root
      password: {password}
      type: text
{authorized}packages:
  - {packages.replace(' ', '\n  - ')}
write_files:
  - path: /etc/ssh/sshd_config.d/99-meshlink-lab.conf
    permissions: '0644'
    content: |
      PermitRootLogin yes
      PasswordAuthentication yes
{runcmd}"""
    meta_data = f"instance-id: {hostname}-{kind}-01\nlocal-hostname: {hostname}\n"

    user_path = images / f"{kind}-user-data"
    meta_path = images / f"{kind}-meta-data"
    net_path = images / f"{kind}-network-config"
    user_path.write_text(user_data)
    meta_path.write_text(meta_data)
    net_path.write_text(network)
    return user_path, meta_path


def build_seed(workdir: Path, kind: str) -> Path:
    images = workdir / "images"
    seed = images / f"{kind}-seed.iso"
    if seed.exists():
        seed.unlink()
    mkiso = shutil.which("genisoimage") or shutil.which("mkisofs")
    if not mkiso:
        raise SystemExit("genisoimage/mkisofs not found; install genisoimage")
    staging = images / f"{kind}-seed-files"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    shutil.copy2(images / f"{kind}-user-data", staging / "user-data")
    shutil.copy2(images / f"{kind}-meta-data", staging / "meta-data")
    shutil.copy2(images / f"{kind}-network-config", staging / "network-config")
    run([
        mkiso, "-output", str(seed), "-volid", "cidata", "-rational-rock", "-joliet",
        str(staging / "user-data"),
        str(staging / "meta-data"),
        str(staging / "network-config"),
    ])
    return seed


def prepare(args: argparse.Namespace) -> int:
    workdir = expand(args.workdir)
    repos = expand(args.repos_dir)
    for sub in ("images", "overlays", "run", "logs", "mint"):
        (workdir / sub).mkdir(parents=True, exist_ok=True)

    base = workdir / "images" / DEBIAN_IMAGE
    if not base.exists():
        local = Path.home() / "tollgate-virtual-lab" / "images" / DEBIAN_IMAGE
        if local.exists():
            shutil.copy2(local, base)
        else:
            run(["curl", "-fL", "-o", str(base), DEBIAN_IMAGE_URL], timeout=1200)

    for kind, name in (("gateway", "meshlink-gateway"), ("client", "meshlink-client")):
        overlay = workdir / "overlays" / f"{kind}.qcow2"
        if not overlay.exists():
            run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(base), str(overlay)])
            run(["qemu-img", "resize", str(overlay), "12G"])
        cloud_init_files(workdir, name, kind, repos)
        build_seed(workdir, kind)

    print(f"prepared {workdir}")
    return 0


def mint_pid_path(workdir: Path) -> Path:
    return workdir / "run" / "mint.pid"


def start_mint(workdir: Path, cdk: Path) -> None:
    if not cdk.exists():
        raise SystemExit(f"cdk-mintd not found at {cdk}")
    mint_dir = workdir / "mint"
    config = mint_dir / "config.toml"
    config.write_text(f"""\
[info]
url = "{MINT_URL}/"
listen_host = "0.0.0.0"
listen_port = {MINT_PORT}
mnemonic = "env:CDK_MINTD_MNEMONIC"

[database]
engine = "sqlite"

[payment_backend]
backend = "fakewallet"

[fake_wallet]
fee_percent = 0
reserve_fee_min = 0
min_delay_time = 0
max_delay_time = 0
""")
    db = mint_dir / "cdk-mintd.sqlite"
    env = os.environ.copy()
    env["CDK_MINTD_MNEMONIC"] = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    if not db.exists():
        subprocess.run(
            [str(cdk), "--work-dir", str(mint_dir), "config", "validate", "--file", str(config)],
            text=True, capture_output=True, env=env, timeout=60, check=False,
        )
        result = subprocess.run(
            [str(cdk), "--work-dir", str(mint_dir), "config", "init", "--new-mint", "--file", str(config)],
            text=True, capture_output=True, env=env, timeout=60,
        )
        if result.returncode != 0:
            raise SystemExit(f"mint init failed: {result.stderr or result.stdout}")
    if wait_for(["curl", "-fsS", "-m", "2", f"{MINT_URL}/v1/info"], 2):
        print(f"mint already reachable at {MINT_URL}")
        return

    log = open(workdir / "logs" / "mint.log", "ab")
    proc = subprocess.Popen(
        [str(cdk), "--work-dir", str(mint_dir)],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    mint_pid_path(workdir).write_text(str(proc.pid))
    if not wait_for(["curl", "-fsS", "-m", "2", f"{MINT_URL}/v1/info"], 30):
        raise SystemExit(f"mint did not start; see {workdir / 'logs' / 'mint.log'}")


def vm_pid(workdir: Path, kind: str) -> int | None:
    path = workdir / "run" / f"{kind}.pid"
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
    except ValueError:
        return None
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def launch_vm(workdir: Path, kind: str, taps: list[tuple[str, str]], macs: list[str], memory: int, repos: Path) -> None:
    overlay = workdir / "overlays" / f"{kind}.qcow2"
    seed = workdir / "images" / f"{kind}-seed.iso"
    pidfile = workdir / "run" / f"{kind}.pid"
    log = workdir / "logs" / f"{kind}-serial.log"
    cmd = [
        "qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-machine", "q35",
        "-m", str(memory), "-smp", "2",
        "-drive", f"file={overlay},if=virtio,format=qcow2",
        "-drive", f"file={seed},media=cdrom,readonly=on",
    ]
    for index, ((netdev, tap), mac) in enumerate(zip(taps, macs)):
        cmd += [
            "-netdev", f"tap,id={netdev},ifname={tap},script=no,downscript=no",
            "-device", f"virtio-net-pci,netdev={netdev},mac={mac}",
        ]
    cmd += [
        "-virtfs", f"local,path={repos},mount_tag=hostrepos,security_model=none,readonly=on",
        "-display", "none", "-serial", f"file:{log}",
        "-daemonize", "-pidfile", str(pidfile),
    ]
    run(cmd, timeout=30)


def start(args: argparse.Namespace) -> int:
    workdir = expand(args.workdir)
    repos = expand(args.repos_dir)
    if vm_pid(workdir, "gateway") or vm_pid(workdir, "client"):
        print("VMs already running; use stop first")
        return 1

    ensure_bridge(UP_BRIDGE, f"{UP_HOST_IP}/24")
    ensure_bridge(MESH_BRIDGE)
    ensure_bridge(MGMT_BRIDGE, f"{MESH_HOST_IP}/24")
    sudo(["ip", "addr", "replace", f"{MINT_IP}/24", "dev", UP_BRIDGE])
    for tap, bridge in (
        (WAN_TAP, UP_BRIDGE), (GUEST_TAP, MESH_BRIDGE), (MGMT_TAP, MGMT_BRIDGE),
        (CLIENT_GUEST_TAP, MESH_BRIDGE), (CLIENT_MGMT_TAP, MGMT_BRIDGE),
    ):
        ensure_tap(tap, bridge)
    configure_host_nat()

    start_mint(workdir, expand(args.cdk_mintd))
    launch_vm(
        workdir, "gateway",
        [("wan", WAN_TAP), ("guest", GUEST_TAP), ("mgmt", MGMT_TAP)],
        ["52:54:00:4d:4c:01", "52:54:00:4d:4c:02", "52:54:00:4d:4c:03"],
        args.gateway_memory, repos,
    )
    launch_vm(
        workdir, "client",
        [("guest", CLIENT_GUEST_TAP), ("mgmt", CLIENT_MGMT_TAP)],
        ["52:54:00:4d:4c:11", "52:54:00:4d:4c:12"],
        args.client_memory, repos,
    )
    print(f"started; gateway mgmt {GATEWAY_MGMT_IP}, client mgmt {CLIENT_MGMT_IP}, mint {MINT_URL}")
    return 0


def stop(args: argparse.Namespace) -> int:
    workdir = expand(args.workdir)
    for kind in ("client", "gateway"):
        pid = vm_pid(workdir, kind)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            for _ in range(20):
                if vm_pid(workdir, kind) is None:
                    break
                time.sleep(0.5)
            if vm_pid(workdir, kind):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        (workdir / "run" / f"{kind}.pid").unlink(missing_ok=True)

    pid_path = mint_pid_path(workdir)
    if pid_path.exists():
        try:
            os.kill(int(pid_path.read_text().strip()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        pid_path.unlink(missing_ok=True)

    for rule in (
        ["iptables", "-D", "INPUT", "-i", UP_BRIDGE, "-p", "tcp", "--dport", str(MINT_PORT), "-j", "ACCEPT"],
        ["iptables", "-D", "INPUT", "-i", UP_BRIDGE, "-p", "tcp", "--dport", "2121", "-j", "ACCEPT"],
        ["iptables", "-D", "INPUT", "-i", UP_BRIDGE, "-p", "tcp", "--dport", "3338", "-j", "ACCEPT"],
        ["iptables", "-t", "nat", "-D", "POSTROUTING", "-s", "10.99.98.0/24", "-o", "tg-poc-br", "-j", "MASQUERADE"],
        ["iptables", "-D", "FORWARD", "-i", "tg-poc-br", "-j", "ACCEPT"],
        ["iptables", "-D", "FORWARD", "-o", "tg-poc-br", "-j", "ACCEPT"],
        ["iptables", "-t", "nat", "-D", "POSTROUTING", "-s", "10.99.98.0/24", "!", "-o", UP_BRIDGE, "-j", "MASQUERADE"],
        ["iptables", "-D", "FORWARD", "-i", UP_BRIDGE, "-j", "ACCEPT"],
        ["iptables", "-D", "FORWARD", "-o", UP_BRIDGE, "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
    ):
        sudo(rule, check=False)

    for tap in (WAN_TAP, GUEST_TAP, MGMT_TAP, CLIENT_GUEST_TAP, CLIENT_MGMT_TAP):
        delete_link(tap)
    for bridge in (MESH_BRIDGE, MGMT_BRIDGE, UP_BRIDGE):
        sudo(["ip", "link", "set", bridge, "down"], check=False)
        sudo(["ip", "link", "del", bridge], check=False)
    print("stopped")
    return 0


def status(args: argparse.Namespace) -> int:
    workdir = expand(args.workdir)
    ok = True
    for kind in ("gateway", "client"):
        pid = vm_pid(workdir, kind)
        print(f"{kind}: {'running pid ' + str(pid) if pid else 'stopped'}")
        ok = ok and bool(pid)
    healthy = run(["curl", "-fsS", "-m", "2", f"{MINT_URL}/v1/info"], check=False).returncode == 0
    print(f"mint: {'ok' if healthy else 'down'} ({MINT_URL})")
    ok = ok and healthy
    for bridge in (UP_BRIDGE, MESH_BRIDGE, MGMT_BRIDGE):
        result = run(["ip", "link", "show", bridge], check=False)
        print(f"bridge {bridge}: {'up' if result.returncode == 0 else 'missing'}")
    return 0 if ok else 1


def ssh(host: str, script: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
            f"root@{host}", "bash -s",
        ],
        input=script, text=True, capture_output=True, timeout=timeout,
    )


def wait_ssh(host: str, timeout: int = 180) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ssh(host, "true", timeout=10).returncode == 0:
            return True
        time.sleep(3)
    return False


def provision(args: argparse.Namespace) -> int:
    workdir = expand(args.workdir)
    if not wait_ssh(GATEWAY_MGMT_IP, args.timeout):
        print(f"gateway SSH not reachable at {GATEWAY_MGMT_IP}", file=sys.stderr)
        return 1
    script = """set -eu
mountpoint -q /mnt/hostrepos || mount -t 9p -o trans=virtio,version=9p2000.L hostrepos /mnt/hostrepos
mkdir -p /opt/meshlink
rsync -a --delete --exclude node_modules --exclude dist --exclude .git /mnt/hostrepos/meshlink-wifi-gateway/ /opt/meshlink/gateway/
rsync -a --delete --exclude node_modules --exclude dist --exclude .git /mnt/hostrepos/meshlink-broker/ /opt/meshlink/broker/
rsync -a /mnt/hostrepos/meshlink-broker/dist/ /opt/meshlink/broker/dist/
cd /opt/meshlink/gateway
MESHLINK_LAB_PREBUILT=1 bash install.sh --lab-qemu --guest-interface guest0 --wan-interface wan0 \
  --broker-dir /opt/meshlink/broker --mint-url """ + MINT_URL + " --ap-ip " + MESH_GUEST_IP + "\n"
    result = ssh(GATEWAY_MGMT_IP, script, timeout=args.timeout)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def smoke(args: argparse.Namespace) -> int:
    workdir = expand(args.workdir)
    if not wait_ssh(GATEWAY_MGMT_IP, 30) or not wait_ssh(CLIENT_MGMT_IP, 30):
        print("VMs not reachable over management network", file=sys.stderr)
        return 1

    gateway = ssh(GATEWAY_MGMT_IP, """set -eu
curl -fsS --max-time 5 http://127.0.0.1:3000/api/health
echo
curl -fsS --max-time 5 http://127.0.0.1:2121/ | python3 -c 'import json,sys; e=json.load(sys.stdin); assert e["kind"]==10021; print("advert", e["kind"], [t for t in e["tags"] if t[0]=="price_per_step"][0])'
curl -sS --max-time 5 http://127.0.0.1:2121/whoami || true
echo
""", timeout=30)
    sys.stdout.write(gateway.stdout)
    sys.stderr.write(gateway.stderr)
    if gateway.returncode != 0:
        return gateway.returncode

    client = ssh(CLIENT_MGMT_IP, """set -eu
curl -fsS --max-time 5 http://10.99.100.1:2121/ | python3 -c 'import json,sys; e=json.load(sys.stdin); assert e["kind"]==10021; print("client sees advert")'
ip -4 addr show guest0 | grep 'inet ' || true
""", timeout=30)
    sys.stdout.write(client.stdout)
    sys.stderr.write(client.stderr)
    return client.returncode


def buy(args: argparse.Namespace) -> int:
    if not wait_ssh(CLIENT_MGMT_IP, 30):
        print("client not reachable", file=sys.stderr)
        return 1
    script = """set -eu
mkdir -p /var/lib/tollgate/db /root/.tollgate
if [ ! -f /var/lib/tollgate/seed ]; then
  head -c 64 /dev/urandom > /var/lib/tollgate/seed
  chmod 600 /var/lib/tollgate/seed
fi
tollgate-agent --gate http://10.99.100.1:2121/ --wallet cdk --test-mint \
  --token-margin 20 --seed-file /var/lib/tollgate/seed \
  --wallet-db /var/lib/tollgate/db --journal /root/.tollgate/journal.txt \
  --attempt-id "$(date +%s)" --timeout-ms 15000
echo "usage: $(curl -fsS http://10.99.100.1:2121/usage)"
curl -fsS -m 10 https://example.com >/dev/null && echo "internet: ok" || { echo "internet: blocked"; exit 1; }
"""
    result = ssh(CLIENT_MGMT_IP, script, timeout=args.timeout)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def buyer(args: argparse.Namespace) -> int:
    """Exercise the broker buyer against a local mock kind:10021 seller."""
    repos = expand(args.repos_dir)
    broker = repos / "meshlink-broker"
    workdir = expand(args.workdir)
    mock_log = open(workdir / "logs" / "mock-upstream.log", "ab")
    if sudo(["iptables", "-C", "INPUT", "-i", UP_BRIDGE, "-p", "tcp", "--dport", "2121", "-j", "ACCEPT"], check=False).returncode != 0:
        sudo(["iptables", "-I", "INPUT", "1", "-i", UP_BRIDGE, "-p", "tcp", "--dport", "2121", "-j", "ACCEPT"])

    env = os.environ.copy()
    env["MESHLINK_MINT_URL"] = MINT_URL
    env["MESHLINK_MOCK_DATA_DIR"] = str(workdir / "mock-upstream")
    mock = subprocess.Popen(
        ["npx", "tsx", "scripts/mock-upstream.ts"],
        cwd=str(broker), env=env, stdout=mock_log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        if not wait_for(["curl", "-fsS", "-m", "2", f"http://{MINT_IP}:2121/"], 30):
            raise SystemExit("mock upstream did not start")
        script = """set -eu
python3 - <<'PYCFG'
import json
path = "/opt/meshlink/broker/server/config/meshlink-config.json"
data = json.load(open(path))
data["upstream"] = {
    "enabled": True,
    "url": "http://10.99.98.2:2121",
    "unit": "sat",
    "steps": 1,
    "maxPricePerStep": 10,
    "maxCostPerMinute": 2,
    "dailyBudget": 100,
    "renewAtPercent": 80,
    "autoMint": True,
    "pollIntervalMs": 60,
}
json.dump(data, open(path, "w"), indent=2)
PYCFG
systemctl restart meshlink-broker
for i in $(seq 1 60); do
  status=$(curl -fsS --max-time 3 http://127.0.0.1:3000/api/meshlink/upstream || true)
  case "$status" in *granted*) echo "$status"; exit 0;; *failed*|*ambiguous*) echo "$status"; exit 1;; esac
  sleep 2
done
echo "upstream did not grant: $status"
exit 1
"""
        result = ssh(GATEWAY_MGMT_IP, script, timeout=args.timeout)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mock.kill()


def replay(args: argparse.Namespace) -> int:
    """Redeem one token twice and reject a token from another mint."""
    if not wait_ssh(CLIENT_MGMT_IP, 30):
        print("client not reachable", file=sys.stderr)
        return 1
    broker = expand(args.repos_dir) / "meshlink-broker"
    def token(extra: list[str]) -> str:
        result = run(["npx", "tsx", "scripts/tollgate-test-token.ts", MINT_URL, "1", *extra],
                     check=True, timeout=120, cwd=str(broker))
        return result.stdout.strip()
    def cashu_token(encoded_mint: str | None = None) -> str:
        return token([encoded_mint] if encoded_mint else [])

    first = cashu_token()
    replay_token = first
    wrong_mint = cashu_token("http://10.99.98.2:9999")
    script = f"""set -eu
post() {{
  body=$(cat)
  curl -sS -m 15 -w '\\n%{{http_code}}' -X POST --data-binary "$body" http://10.99.100.1:2121/
}}
first=$(printf '%s' '{first}' | post)
replay=$(printf '%s' '{replay_token}' | post)
wrong=$(printf '%s' '{wrong_mint}' | post)
printf 'first: %s\\n' "$first"
printf 'replay: %s\\n' "$replay"
printf 'wrong-mint: %s\\n' "$wrong"
printf '%s' "$first" | grep -q '^200$'
printf '%s' "$replay" | grep -q '^400$'
printf '%s' "$replay" | grep -q 'payment-error-token-spent'
printf '%s' "$wrong" | grep -q '^400$'
printf '%s' "$wrong" | grep -q 'payment-error-invalid-token'
"""
    result = ssh(CLIENT_MGMT_IP, script, timeout=args.timeout)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    parser.add_argument("--repos-dir", default=DEFAULT_REPOS)
    parser.add_argument("--cdk-mintd", default=str(Path.home() / "cdk-mint" / "cdk-mintd"))
    parser.add_argument("--gateway-memory", type=int, default=2048)
    parser.add_argument("--client-memory", type=int, default=1024)
    parser.add_argument("--timeout", type=int, default=1800)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("prepare", prepare), ("start", start), ("stop", stop), ("status", status), ("provision", provision), ("smoke", smoke), ("buy", buy), ("buyer", buyer), ("replay", replay)):
        sub.add_parser(name, help=func.__doc__ or name).set_defaults(func=func)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
