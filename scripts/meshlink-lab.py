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
from urllib.parse import urlparse

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
BETA_BRIDGE = "tg-poc-br"
BETA_TAP = "tg-poc-tap"
BETA_HOST_IP = "10.99.99.2"
BETA_SELLER_IP = "10.99.99.1"
BETA_MINT_PORT = 3338
BETA_MINT_URL = f"http://{BETA_HOST_IP}:{BETA_MINT_PORT}"
BETA_CLIENT_NETNS = "ml-beta-client"
BETA_CLIENT_IF = "ml-beta-n"
BETA_HOST_IF = "ml-beta-h"
BETA_CLIENT_IP = "10.99.99.3"
BETA_HOST_BRIDGE_MAC = "72:fc:b0:ec:4c:09"
BETA_CLIENT_MAC = "02:00:00:00:99:03"
HOST_DNS_IP = UP_HOST_IP
BETA_SERIAL = "beta-serial.sock"

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
        ["iptables", "-C", "INPUT", "-i", UP_BRIDGE, "-p", "tcp", "--dport", str(BETA_MINT_PORT), "-j", "ACCEPT"],
        ["iptables", "-C", "INPUT", "-i", UP_BRIDGE, "-p", "udp", "--dport", "53", "-j", "ACCEPT"],
        ["iptables", "-C", "INPUT", "-i", UP_BRIDGE, "-p", "tcp", "--dport", "53", "-j", "ACCEPT"],
        ["iptables", "-t", "nat", "-C", "POSTROUTING", "-s", "10.99.98.0/24", "!", "-o", UP_BRIDGE, "-j", "MASQUERADE"],
        ["iptables", "-C", "FORWARD", "-i", UP_BRIDGE, "-j", "ACCEPT"],
        ["iptables", "-C", "FORWARD", "-o", UP_BRIDGE, "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
    ]
    adds = [
        ["iptables", "-I", "INPUT", "1", "-i", UP_BRIDGE, "-p", "tcp", "--dport", str(MINT_PORT), "-j", "ACCEPT"],
        ["iptables", "-I", "INPUT", "1", "-i", UP_BRIDGE, "-p", "tcp", "--dport", str(BETA_MINT_PORT), "-j", "ACCEPT"],
        ["iptables", "-I", "INPUT", "1", "-i", UP_BRIDGE, "-p", "udp", "--dport", "53", "-j", "ACCEPT"],
        ["iptables", "-I", "INPUT", "1", "-i", UP_BRIDGE, "-p", "tcp", "--dport", "53", "-j", "ACCEPT"],
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


def host_dns_pid_path(workdir: Path) -> Path:
    return workdir / "run" / "host-dns.pid"


def beta_pid_path(workdir: Path) -> Path:
    return workdir / "run" / "beta.pid"


def beta_mint_pid_path(workdir: Path) -> Path:
    return workdir / "run" / "beta-mint.pid"


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


def stop_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.25)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def start_host_dns(workdir: Path) -> None:
    if run(["ss", "-lun"], check=False).stdout and f"{HOST_DNS_IP}:53" in run(["ss", "-lun"], check=False).stdout:
        print(f"host DNS already listening on {HOST_DNS_IP}:53")
        return
    dnsmasq = shutil.which("dnsmasq") or "/usr/sbin/dnsmasq"
    if not Path(dnsmasq).exists():
        raise SystemExit("dnsmasq not found for the host lab DNS proxy")
    log = open(workdir / "logs" / "host-dnsmasq.log", "ab")
    proc = subprocess.Popen(
        ["sudo", "-n", dnsmasq, "--keep-in-foreground", f"--listen-address={HOST_DNS_IP}",
         "--bind-interfaces", "--no-resolv", "--server=127.0.0.53",
         "--cache-size=1000", f"--pid-file={workdir / 'run' / 'host-dnsmasq.pid'}",
         "--log-facility=-"],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    host_dns_pid_path(workdir).write_text(str(proc.pid))
    if not wait_for(["getent", "hosts", "example.com"], 10):
        print("warning: host DNS proxy started but example.com did not resolve immediately", file=sys.stderr)


def stop_host_dns(workdir: Path) -> None:
    path = host_dns_pid_path(workdir)
    if not path.exists():
        return
    try:
        pid = int(path.read_text().strip())
    except ValueError:
        pid = 0
    if pid:
        sudo(["kill", str(pid)], check=False)
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.25)
        else:
            sudo(["kill", "-9", str(pid)], check=False)
    path.unlink(missing_ok=True)


def start_beta_mint(workdir: Path, args: argparse.Namespace) -> None:
    mint_dir = expand(args.beta_mint_dir)
    mint = mint_dir / "cdk-mintd"
    if not mint.exists():
        raise SystemExit(f"Beta mint binary missing at {mint}; pass --beta-mint-dir")
    if wait_for(["curl", "-fsS", "-m", "2", f"{BETA_MINT_URL}/v1/info"], 2):
        print(f"Beta mint already reachable at {BETA_MINT_URL}")
        return
    path = beta_mint_pid_path(workdir)
    if path.exists():
        try:
            stop_pid(int(path.read_text().strip()))
        except ValueError:
            pass
        path.unlink(missing_ok=True)
    log = open(workdir / "logs" / "beta-mint.log", "ab")
    env = os.environ.copy()
    env["CDK_MINTD_MNEMONIC"] = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    proc = subprocess.Popen(
        [str(mint), "-w", "./datadir", "--enable-logging"],
        cwd=str(mint_dir), env=env, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    path.write_text(str(proc.pid))
    if not wait_for(["curl", "-fsS", "-m", "2", f"{BETA_MINT_URL}/v1/info"], 30):
        raise SystemExit(f"Beta mint did not start; see {workdir / 'logs' / 'beta-mint.log'}")


def stop_beta_mint(workdir: Path) -> None:
    path = beta_mint_pid_path(workdir)
    if not path.exists():
        return
    try:
        stop_pid(int(path.read_text().strip()))
    except ValueError:
        pass
    path.unlink(missing_ok=True)


def start_beta(workdir: Path, args: argparse.Namespace) -> None:
    beta_workdir = expand(args.beta_workdir)
    overlay = beta_workdir / "overlays" / "tollgate.qcow2"
    if not overlay.exists():
        raise SystemExit(f"Beta overlay missing at {overlay}; pass --beta-workdir")
    pidfile = beta_pid_path(workdir)
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text().strip()), 0)
            print("Beta seller already running")
            return
        except (OSError, ValueError):
            pidfile.unlink(missing_ok=True)

    ensure_bridge(BETA_BRIDGE, f"{BETA_HOST_IP}/24")
    ensure_tap(BETA_TAP, BETA_BRIDGE)
    start_beta_mint(workdir, args)

    log = beta_workdir / "logs" / "serial.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "qemu-system-x86_64", "-enable-kvm", "-m", "512", "-smp", "2",
        "-drive", f"file={overlay},if=virtio,format=qcow2",
        "-netdev", f"tap,id=beta,ifname={BETA_TAP},script=no,downscript=no",
        "-device", "virtio-net-pci,netdev=beta,mac=52:54:00:88:88:01",
        "-display", "none", "-serial", f"unix:{beta_workdir / 'run' / BETA_SERIAL},server,nowait",
        "-daemonize", "-pidfile", str(pidfile),
    ]
    run(cmd, timeout=30)
    if not wait_for(["curl", "-fsS", "-m", "3", f"http://{BETA_SELLER_IP}:2121/"], 180):
        raise SystemExit(f"Beta seller did not answer; see {log}")


def beta_client_unsnat() -> None:
    sudo(["iptables", "-t", "nat", "-D", "POSTROUTING", "-s", "10.99.98.0/24", "-o", BETA_BRIDGE,
          "-j", "SNAT", "--to-source", BETA_CLIENT_IP], check=False)
    sudo(["iptables", "-t", "nat", "-D", "POSTROUTING", "-s", "10.99.98.0/24",
          "-o", BETA_BRIDGE, "-j", "RETURN"], check=False)


def host_route_to(address: str) -> tuple[str | None, str]:
    output = run(["ip", "-4", "route", "get", address], check=True).stdout.split()
    via = output[output.index("via") + 1] if "via" in output else None
    dev = output[output.index("dev") + 1]
    return via, dev


def host_upstream_route(seller_ip: str = BETA_SELLER_IP) -> None:
    via, dev = host_route_to(seller_ip)
    if via and seller_ip != BETA_SELLER_IP:
        raise SystemExit(
            f"physical seller {seller_ip} is off-link on {dev} via {via}; "
            "MeshLink guest traffic cannot use it as a default gateway. "
            "Use an on-link seller or validate payment/renewal only."
        )
    run(["sudo", "ip", "rule", "del", "from", GATEWAY_WAN_IP, "lookup", "2121"], check=False)
    run(["sudo", "ip", "rule", "del", "fwmark", "0x2121", "lookup", "2121"], check=False)
    run(["sudo", "ip", "rule", "add", "fwmark", "0x2121", "lookup", "2121", "pref", "900"])
    rule = ["-t", "mangle", "-C", "PREROUTING", "-i", UP_BRIDGE, "-s", GATEWAY_WAN_IP,
            "-j", "MARK", "--set-xmark", "0x2121/0xffffffff"]
    if run(["sudo", "iptables", *rule], check=False).returncode != 0:
        run(["sudo", "iptables", "-t", "mangle", "-I", "PREROUTING", "1", "-i", UP_BRIDGE,
             "-s", GATEWAY_WAN_IP, "-j", "MARK", "--set-xmark", "0x2121/0xffffffff"])
    if via:
        run(["sudo", "ip", "route", "replace", "table", "2121", f"{seller_ip}/32", "via", via, "dev", dev])
    else:
        run(["sudo", "ip", "route", "replace", "table", "2121", f"{seller_ip}/32", "dev", dev])
    run(["sudo", "ip", "route", "replace", "table", "2121", "default", "via", seller_ip, "dev", dev])
    if dev == BETA_BRIDGE:
        masq = ["-t", "nat", "-C", "POSTROUTING", "-s", "10.99.99.0/24", "!", "-o", BETA_BRIDGE, "-j", "MASQUERADE"]
        if run(["sudo", "iptables", *masq], check=False).returncode != 0:
            run(["sudo", "iptables", "-t", "nat", "-I", "POSTROUTING", "1", "-s", "10.99.99.0/24",
                 "!", "-o", BETA_BRIDGE, "-j", "MASQUERADE"])


def host_upstream_route_remove() -> None:
    run(["sudo", "ip", "rule", "del", "from", GATEWAY_WAN_IP, "lookup", "2121"], check=False)
    run(["sudo", "ip", "rule", "del", "fwmark", "0x2121", "lookup", "2121"], check=False)
    run(["sudo", "iptables", "-t", "mangle", "-D", "PREROUTING", "-i", UP_BRIDGE, "-s", GATEWAY_WAN_IP,
         "-j", "MARK", "--set-xmark", "0x2121/0xffffffff"], check=False)
    run(["sudo", "iptables", "-t", "nat", "-D", "POSTROUTING", "-s", "10.99.99.0/24",
         "!", "-o", BETA_BRIDGE, "-j", "MASQUERADE"], check=False)
    run(["sudo", "ip", "route", "flush", "table", "2121"], check=False)


def stop_beta_client() -> None:
    beta_client_unsnat()
    sudo(["iptables", "-t", "nat", "-D", "POSTROUTING", "-s", "10.99.98.0/24", "-d", BETA_SELLER_IP,
          "-p", "tcp", "--dport", "2121", "-o", BETA_BRIDGE, "-j", "SNAT", "--to-source", BETA_CLIENT_IP],
         check=False)
    sudo(["iptables", "-t", "nat", "-D", "POSTROUTING", "-s", "10.99.98.0/24",
          "-o", BETA_BRIDGE, "-j", "RETURN"], check=False)
    sudo(["ip", "netns", "del", BETA_CLIENT_NETNS], check=False)
    delete_link(BETA_HOST_IF)


def stop_beta(workdir: Path) -> None:
    path = beta_pid_path(workdir)
    if path.exists():
        try:
            stop_pid(int(path.read_text().strip()))
        except ValueError:
            pass
        path.unlink(missing_ok=True)
    stop_beta_mint(workdir)


def beta_status(workdir: Path) -> None:
    pid = None
    path = beta_pid_path(workdir)
    if path.exists():
        try:
            pid = int(path.read_text().strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            pid = None
    print(f"beta: {'running pid ' + str(pid) if pid else 'stopped'}")
    for name, url in (("beta-mint", f"{BETA_MINT_URL}/v1/info"), ("beta-seller", f"http://{BETA_SELLER_IP}:2121/")):
        ok = wait_for(["curl", "-fsS", "-m", "2", url], 1)
        print(f"{name}: {'ok' if ok else 'down'}")


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
        "-display", "none", "-serial", f"unix:{beta_workdir / 'run' / BETA_SERIAL},server,nowait",
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
    start_host_dns(workdir)
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
    if getattr(args, "with_beta", False):
        start_beta(workdir, args)
    print(f"started; gateway mgmt {GATEWAY_MGMT_IP}, client mgmt {CLIENT_MGMT_IP}, mint {MINT_URL}")
    return 0


def stop(args: argparse.Namespace) -> int:
    workdir = expand(args.workdir)
    stop_beta(workdir)
    stop_beta_client()
    stop_host_dns(workdir)
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
        ["iptables", "-D", "INPUT", "-i", UP_BRIDGE, "-p", "tcp", "--dport", str(BETA_MINT_PORT), "-j", "ACCEPT"],
        ["iptables", "-D", "INPUT", "-i", UP_BRIDGE, "-p", "udp", "--dport", "53", "-j", "ACCEPT"],
        ["iptables", "-D", "INPUT", "-i", UP_BRIDGE, "-p", "tcp", "--dport", "53", "-j", "ACCEPT"],
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
    beta_status(workdir)
    dns_ok = bool(run(["ss", "-lun"], check=False).stdout and f"{HOST_DNS_IP}:53" in run(["ss", "-lun"], check=False).stdout)
    print(f"host-dns: {'ok' if dns_ok else 'down'} ({HOST_DNS_IP}:53)")
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
  --broker-dir /opt/meshlink/broker --mint-url """ + MINT_URL + " --ap-ip " + MESH_GUEST_IP + " --dns-server " + UP_HOST_IP + "\n"
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
    "renewAtPercent": 50,
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


def beta_start_cmd(args: argparse.Namespace) -> int:
    workdir = expand(args.workdir)
    start_beta(workdir, args)
    beta_status(workdir)
    return 0


def beta_stop_cmd(args: argparse.Namespace) -> int:
    stop_beta(expand(args.workdir))
    return 0


def beta_status_cmd(args: argparse.Namespace) -> int:
    beta_status(expand(args.workdir))
    return 0


def beta_client_reset() -> None:
    sudo(["ip", "link", "set", "dev", BETA_BRIDGE, "address", BETA_HOST_BRIDGE_MAC], check=False)
    sudo(["ip", "netns", "del", BETA_CLIENT_NETNS], check=False)
    delete_link(BETA_HOST_IF)
    sudo(["ip", "netns", "add", BETA_CLIENT_NETNS])
    sudo(["ip", "link", "add", BETA_HOST_IF, "type", "veth", "peer", "name", BETA_CLIENT_IF])
    sudo(["ip", "link", "set", BETA_HOST_IF, "master", BETA_BRIDGE])
    sudo(["ip", "link", "set", BETA_HOST_IF, "up"])
    sudo(["ip", "link", "set", BETA_CLIENT_IF, "netns", BETA_CLIENT_NETNS])
    sudo(["ip", "netns", "exec", BETA_CLIENT_NETNS, "ip", "link", "set", "lo", "up"])
    sudo(["ip", "netns", "exec", BETA_CLIENT_NETNS, "ip", "link", "set", BETA_CLIENT_IF,
          "address", BETA_CLIENT_MAC, "up"])
    sudo(["ip", "netns", "exec", BETA_CLIENT_NETNS, "ip", "addr", "replace",
          f"{BETA_CLIENT_IP}/24", "dev", BETA_CLIENT_IF])
    sudo(["ip", "netns", "exec", BETA_CLIENT_NETNS, "ip", "route", "replace",
          "default", "via", BETA_SELLER_IP, "dev", BETA_CLIENT_IF])


def host_claim_paid_mac() -> None:
    run(["sudo", "ip", "link", "set", "dev", BETA_BRIDGE, "address", BETA_CLIENT_MAC])
    run(["sudo", "ip", "neigh", "flush", "dev", BETA_BRIDGE], check=False)


def beta_client_snat() -> None:
    rule = ["-t", "nat", "-C", "POSTROUTING", "-s", "10.99.98.0/24", "-o", BETA_BRIDGE,
            "-j", "SNAT", "--to-source", BETA_CLIENT_IP]
    if sudo(["iptables", *rule], check=False).returncode != 0:
        sudo(["iptables", "-t", "nat", "-I", "POSTROUTING", "1", "-s", "10.99.98.0/24",
              "-o", BETA_BRIDGE, "-j", "SNAT", "--to-source", BETA_CLIENT_IP])
    for direction in ("-i", "-o"):
        if sudo(["iptables", "-C", "FORWARD", direction, BETA_BRIDGE, "-j", "ACCEPT"], check=False).returncode != 0:
            sudo(["iptables", "-I", "FORWARD", "1", direction, BETA_BRIDGE, "-j", "ACCEPT"])


def beta_client_pay(repos: Path) -> None:
    for url in (f"http://{BETA_SELLER_IP}:2050/splash.html", f"http://{BETA_SELLER_IP}:2051/splash.html"):
        run(["sudo", "-n", "ip", "netns", "exec", BETA_CLIENT_NETNS, "curl", "-fsS", "-m", "5", url],
            check=False)
    token = run(["npx", "tsx", "scripts/tollgate-test-token.ts", BETA_MINT_URL, "1"],
                check=True, timeout=120, cwd=str(repos / "meshlink-broker")).stdout.strip()
    result = run([
        "sudo", "-n", "ip", "netns", "exec", BETA_CLIENT_NETNS,
        "curl", "-sS", "-m", "20", "-H", "Content-Type: text/plain",
        "-w", "\n%{http_code}", "-X", "POST", "--data-binary", token,
        f"http://{BETA_SELLER_IP}:2121/",
    ], check=False)
    body, _, code = result.stdout.rpartition("\n")
    if code.strip() != "200" or '"kind":1022' not in body:
        raise SystemExit(f"Beta payment failed (HTTP {code.strip()}): {body[-300:]}")
    print("beta-client: paid")


def configure_gateway_upstream(url: str = f"http://{BETA_SELLER_IP}:2121", route_nexthop: str = "10.99.98.1") -> None:
    seller_ip = urlparse(url).hostname or BETA_SELLER_IP
    route_line = (
        f"ip route replace {seller_ip}/32 via {route_nexthop} dev wan0"
        if seller_ip != BETA_SELLER_IP else f"ip route replace 10.99.99.0/24 via {route_nexthop} dev wan0"
    )
    script = f"""set -eu
{route_line}
python3 - <<'PYCFG'
import json
path = "/opt/meshlink/broker/server/config/meshlink-config.json"
data = json.load(open(path))
data["upstream"] = {{
    "enabled": True,
    "url": "{url}",
    "unit": "sat",
    "steps": 1,
    "maxPricePerStep": 10,
    "maxCostPerMinute": 2,
    "dailyBudget": 100,
    "renewAtPercent": 50,
    "autoMint": True,
    "pollIntervalMs": 10,
    "routeThrough": True,
    "routeTable": 2121,
    "routeNexthop": "{route_nexthop}",
    "guestInterface": "br-guest",
}}
json.dump(data, open(path, "w"), indent=2)
PYCFG
systemctl restart meshlink-broker
for i in $(seq 1 90); do
  status=$(curl -fsS --max-time 3 http://127.0.0.1:3000/api/meshlink/upstream || true)
  case "$status" in *'"route":"installed"'*) echo "$status"; exit 0;; *failed*|*ambiguous*) echo "$status"; exit 1;; esac
  sleep 2
done
echo "upstream route did not install: $status"
exit 1
"""
    result = ssh(GATEWAY_MGMT_IP, script, timeout=300)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise SystemExit("gateway upstream configuration failed")


def chain(args: argparse.Namespace) -> int:
    """Pay Beta from a netns, steer MeshLink guest traffic, and verify renewal."""
    workdir = expand(args.workdir)
    repos = expand(args.repos_dir)
    if not wait_ssh(GATEWAY_MGMT_IP, 60) or not wait_ssh(CLIENT_MGMT_IP, 60):
        print("MeshLink VMs are not reachable", file=sys.stderr)
        return 1
    seller_url = args.seller_url or os.environ.get("MESHLINK_SELLER_URL") or f"http://{BETA_SELLER_IP}:2121"
    if args.seller_place and not (args.seller_url or os.environ.get("MESHLINK_SELLER_URL")):
        print("--seller-place requires MESHLINK_SELLER_URL for the physical seller", file=sys.stderr)
        return 1
    seller_ip = urlparse(seller_url).hostname or BETA_SELLER_IP
    physical_seller = bool(args.seller_place) or seller_ip != BETA_SELLER_IP
    if not physical_seller:
        if not wait_for(["curl", "-fsS", "-m", "2", f"http://{BETA_SELLER_IP}:2121/"], 10):
            start_beta(workdir, args)
        beta_client_reset()
        beta_client_unsnat()
        beta_client_pay(repos)
        stop_beta_client()
        beta_client_snat()
        host_claim_paid_mac()
        beta_allow_chain_source()
        beta_upstream_setup()
    host_upstream_route(seller_ip)
    configure_gateway_upstream(seller_url)
    if buy(args) != 0:
        print("client could not buy a MeshLink session after enabling upstream", file=sys.stderr)
        return 1

    print("seller usage before:", run(["curl", "-fsS", "-m", "3", f"{seller_url.rstrip('/')}/usage"], check=False).stdout.strip())
    traffic = ssh(CLIENT_MGMT_IP, """set -eu
getent hosts example.com >/dev/null
for i in 1 2 3 4 5; do
  curl -fsS --max-time 180 -o /dev/null -w 'traffic %{http_code} %{size_download}\n' \
    'https://speed.cloudflare.com/__down?bytes=50000000'
done
""", timeout=660)
    sys.stdout.write(traffic.stdout)
    sys.stderr.write(traffic.stderr)
    if traffic.returncode != 0:
        print("chain traffic failed", file=sys.stderr)
        return traffic.returncode

    print("seller usage after:", run(["curl", "-fsS", "-m", "3", f"{seller_url.rstrip('/')}/usage"], check=False).stdout.strip())
    deadline = time.monotonic() + 300
    last = ""
    while time.monotonic() < deadline:
        last = ssh(GATEWAY_MGMT_IP, "curl -fsS --max-time 3 http://127.0.0.1:3000/api/meshlink/upstream || true", timeout=15).stdout
        if '"purchases":2' in last or '"purchases":3' in last or '"purchases":4' in last:
            print(last)
            return 0
        time.sleep(5)
    print(f"renewal did not complete: {last}", file=sys.stderr)
    return 1


def beta_serial(script: str, timeout: int = 10) -> str:
    socket = expand(default_beta_workdir()) / "run" / BETA_SERIAL
    settle = max(1, timeout - 2)
    command = (
        f"(sleep 1; printf '\\n%s\\n' {shlex.quote(script)}; sleep {settle}) "
        f"| timeout {timeout} socat - UNIX-CONNECT:{shlex.quote(str(socket))}"
    )
    result = subprocess.run(command, shell=True, text=True, capture_output=True, check=False)
    return result.stdout

def default_workdir() -> str:
    return os.environ.get("MESHLINK_LAB_WORKDIR", DEFAULT_WORKDIR)


def default_beta_workdir() -> str:
    return os.environ.get("MESHLINK_BETA_WORKDIR", str(Path.home() / "tollgate-virtual-lab-clean"))


def beta_allow_chain_source() -> None:
    commands = [
        "nft insert rule ip filter nds_enforce_forward ip saddr 10.99.99.3 counter accept 2>/dev/null || true",
        "nft insert rule ip filter nds_enforce_forward ip saddr 10.99.98.0/24 counter accept 2>/dev/null || true",
        "nft list chain ip filter FORWARD | grep -q 'ip saddr 10.99.99.3' || nft insert rule ip filter FORWARD ip saddr 10.99.99.3 counter accept",
        "nft list chain ip filter FORWARD | grep -q 'ip daddr 10.99.99.3' || nft insert rule ip filter FORWARD ip daddr 10.99.99.3 counter accept",
        "nft list chain ip filter FORWARD | grep -q 'ip saddr 10.99.98.0/24' || nft insert rule ip filter FORWARD ip saddr 10.99.98.0/24 counter accept",
        "nft list chain ip filter FORWARD | grep -q 'ip daddr 10.99.98.0/24' || nft insert rule ip filter FORWARD ip daddr 10.99.98.0/24 counter accept",
    ]
    for command in commands:
        beta_serial(command, timeout=8)


def beta_upstream_setup() -> None:
    beta_serial("ip route del 10.99.99.3/32 via 10.99.99.2 dev br-lan 2>/dev/null || true", timeout=8)
    beta_serial("nft list chain ip nat POSTROUTING >/dev/null 2>&1 || nft add chain ip nat POSTROUTING '{ type nat hook postrouting priority 100; policy accept; }'", timeout=8)
    beta_serial("nft list chain ip nat POSTROUTING | grep -q 'ip saddr 10.99.99.3 masquerade' || nft add rule ip nat POSTROUTING oifname \"br-lan\" ip saddr 10.99.99.3 masquerade", timeout=8)
    beta_serial("nft list chain ip nat POSTROUTING | grep -q 'ip saddr 10.99.98.0/24 masquerade' || nft add rule ip nat POSTROUTING oifname \"br-lan\" ip saddr 10.99.98.0/24 masquerade", timeout=8)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    parser.add_argument("--repos-dir", default=DEFAULT_REPOS)
    parser.add_argument("--cdk-mintd", default=str(Path.home() / "cdk-mint" / "cdk-mintd"))
    parser.add_argument("--beta-workdir", default=str(Path.home() / "tollgate-virtual-lab-clean"))
    parser.add_argument("--beta-mint-dir", default=str(Path.home() / "cdk-mint"))
    parser.add_argument("--with-beta", action="store_true", help="start the Beta OpenWrt seller with the lab")
    parser.add_argument("--seller-url", default="", help="physical TollGate seller URL for chain mode")
    parser.add_argument("--seller-place", default="", help="labgrid place name for the physical seller (with MESHLINK_SELLER_URL)")
    parser.add_argument("--gateway-memory", type=int, default=2048)
    parser.add_argument("--client-memory", type=int, default=1024)
    parser.add_argument("--timeout", type=int, default=1800)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("prepare", prepare), ("start", start), ("stop", stop), ("status", status), ("provision", provision), ("smoke", smoke), ("buy", buy), ("buyer", buyer), ("replay", replay), ("beta-start", beta_start_cmd), ("beta-stop", beta_stop_cmd), ("beta-status", beta_status_cmd), ("chain", chain)):
        sub.add_parser(name, help=func.__doc__ or name).set_defaults(func=func)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
