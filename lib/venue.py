"""lib/venue.py — the shared venue layer for every TollGate test lane.

One interface for three DUT classes:

  Virtual   QEMU on a bench host (br-laneA pattern on ai-legion-small)
  Bay       physical router on an isolated bench VLAN behind ai-legion-small
  Island    physical router reachable via the VLAN-12 island (e.g. NR7101)

Every lane does the same four things: ensure the DUT is reachable, deploy
an artifact to it, run a health gate, and execute a pytest suite with the
right environment. This module makes that one set of functions instead of
three ad-hoc copies (release-validate.sh inline, run-local-tests.sh, and
the manual bay commands documented across the session notes).

Usage from a script:

    from lib.venue import ensure, deploy, suite, Venue

    v = ensure(Venue.VIRTUAL)               # or Venue.BAY, Venue.ISLAND
    deploy(v, artifact="tollgate-wrt_...ipk")
    result = suite(v, test="tests/api/test_payment_rejection_semantics.py")

Or from the CLI:

    python3 -m lib.venue ensure --type virtual
    python3 -m lib.venue deploy --type virtual --artifact path/to/ipk
    python3 -m lib.venue suite  --type virtual --test tests/api/test_X.py

The shell wrapper (scripts/validate.sh) calls this module.
"""
from __future__ import annotations

import enum
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Venue definitions
# ---------------------------------------------------------------------------

DEFAULT_HOST = "ai-legion-small"


class Venue(enum.Enum):
    VIRTUAL = "virtual"
    BAY = "bay"
    ISLAND = "island"


@dataclass
class VenueConfig:
    type: Venue
    host: str = DEFAULT_HOST          # the lab host that reaches the DUT
    address: str = ""                 # DUT address on the bench network
    gateway: str = ""                 # our address on the DUT's subnet (the paying client)
    password: str = ""                # DUT root password
    mint_url: str = "http://192.168.13.221:8383"
    subnet: str = ""                  # derived from address
    venue_host_user: str = "ubuntu"

    def __post_init__(self):
        if not self.address:
            defaults = {
                Venue.VIRTUAL: "10.99.150.2",
                Venue.BAY: "192.168.104.60",      # override per seat
                Venue.ISLAND: "192.168.12.124",    # NR7101
            }
            self.address = defaults.get(self.type, "")
        if not self.gateway:
            defaults = {
                Venue.VIRTUAL: "192.168.13.221",
                Venue.BAY: "192.168.104.10",
                Venue.ISLAND: "192.168.13.221",
            }
            self.gateway = defaults.get(self.type, "")
        if not self.password:
            defaults = {
                Venue.VIRTUAL: "tollgate-lab-admin",
                Venue.BAY: "",                     # key auth from the venue host
                Venue.ISLAND: "",                  # key auth from the venue host
            }
            self.password = defaults.get(self.type, "")
        if not self.subnet:
            parts = self.address.rsplit(".", 1)
            self.subnet = parts[0] + ".0/24" if parts and parts[0] else ""


def config_for(vtype: str, **overrides) -> VenueConfig:
    v = Venue(vtype)
    return VenueConfig(type=v, **overrides)


# ---------------------------------------------------------------------------
# SSH helpers (all DUT access goes through the venue host)
# ---------------------------------------------------------------------------

def _ssh(cmd: str, host: str = "", timeout: int = 30, check: bool = True,
         stdin_data: str | None = None) -> subprocess.CompletedProcess:
    host = host or DEFAULT_HOST
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
         "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         host, cmd],
        capture_output=True, text=True, timeout=timeout, input=stdin_data,
    )


def _dut_ssh(cfg: VenueConfig, cmd: str, timeout: int = 30,
             stdin_data: str | None = None) -> str:
    """SSH to the DUT through the venue host (two-hop)."""
    inner = (
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=6 "
        f"{'sshpass -p ' + cfg.password + ' ' if cfg.password else ''}"
        f"root@{cfg.address} '{cmd}'"
    )
    r = _ssh(inner, host=cfg.host, timeout=timeout + 15)
    return r.stdout.strip()


def _dut_tcp_probe(cfg: VenueConfig, port: int = 22, host: str = "") -> bool:
    """TCP probe (never ICMP — the liveness lesson)."""
    host = host or cfg.host
    r = _ssh(
        f"timeout 3 bash -c 'echo > /dev/tcp/{cfg.address}/{port}' 2>/dev/null && echo UP",
        host=host, timeout=12, check=False,
    )
    return r.stdout.strip().endswith("UP")


# ---------------------------------------------------------------------------
# Ensure — bring the DUT to a reachable state
# ---------------------------------------------------------------------------

_VIRTUAL_BRINGUP = r"""
set -e
ip link show br-laneA >/dev/null 2>&1 || {
    ip link add br-laneA type bridge
    ip addr add 10.99.150.1/24 dev br-laneA
    ip link set br-laneA up
    ip tuntap add dev laneA0 mode tap user "$(logname 2>/dev/null || echo ubuntu)"
    ip link set laneA0 master br-laneA
    ip link set laneA0 up
    sysctl -w net.ipv4.ip_forward=1 >/dev/null
    iptables -t nat -A POSTROUTING -s 10.99.150.0/24 -j MASQUERADE
    iptables -A FORWARD -i br-laneA -j ACCEPT
    iptables -A FORWARD -o br-laneA -j ACCEPT
    echo fabric-created
}
ufw status 2>/dev/null | grep -q "8383.*10.99.150.0/24" || \
    ufw allow from 10.99.150.0/24 to any port 8383 proto tcp >/dev/null 2>&1 || true
systemctl is-active laneA-vm >/dev/null 2>&1 || {
    systemd-run --unit=laneA-vm --working-directory="$HOME/laneA" \
        -- qemu-system-x86_64 -enable-kvm -cpu host -m 512 -smp 2 \
        -drive file="$HOME/laneA/openwrt.img",format=raw,if=virtio \
        -netdev tap,id=n0,ifname=laneA0,script=no,downscript=no \
        -device virtio-net-pci,netdev=n0 -display none \
        -serial unix:"$HOME/laneA/console.sock",server=on,wait=off \
        -monitor unix:"$HOME/laneA/monitor.sock",server=on,wait=off
    echo vm-started
}
"""


def _ensure_virtual(cfg: VenueConfig) -> VenueConfig:
    _ssh("sudo bash -s", host=cfg.host, timeout=30, stdin_data=_VIRTUAL_BRINGUP)
    # Ensure the driver host can route to the DUT subnet (the venue lives
    # behind the lab host; the route is runtime-only and drops on reboot)
    route_subnet = "10.99.150.0/24"
    route_gw = cfg.host
    try:
        has = subprocess.run(
            ["ip", "route"], capture_output=True, text=True, timeout=5,
        ).stdout
        if route_subnet not in has:
            subprocess.run(
                ["sudo", "-n", "ip", "route", "add", route_subnet,
                 "via", route_gw, "dev", "wlp59s0"],
                capture_output=True, timeout=5,
            )
    except Exception:
        pass  # not on the driver host, or no sudo — the venue host handles it
    for _ in range(15):
        if _dut_tcp_probe(cfg):
            return cfg
        time.sleep(7)
    raise RuntimeError(f"virtual venue DUT {cfg.address} never came up")


def _ensure_bay(cfg: VenueConfig) -> VenueConfig:
    """A bay DUT is physical — ensure the NAT/gateway fabric exists on the
    venue host so the DUT can reach the mint and the internet."""
    gw_addr = cfg.gateway
    subnet_prefix = cfg.subnet.split("/")[0]  # e.g. "192.168.104.0"
    fabric_cmd = (
        f"sudo bash -c '"
        f"ip addr show enp5s0 | grep -q {gw_addr} || "
        f"ip addr add {gw_addr}/24 dev enp5s0 2>/dev/null || true; "
        f"iptables -t nat -C POSTROUTING -s {cfg.subnet} -j MASQUERADE 2>/dev/null || "
        f"iptables -t nat -A POSTROUTING -s {cfg.subnet} -j MASQUERADE; "
        f"iptables -C FORWARD -s {cfg.subnet} -j ACCEPT 2>/dev/null || "
        f"iptables -I FORWARD 1 -s {cfg.subnet} -j ACCEPT; "
        f"iptables -C FORWARD -d {cfg.subnet} -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || "
        f"iptables -I FORWARD 2 -d {cfg.subnet} -m state --state ESTABLISHED,RELATED -j ACCEPT; "
        f"echo bay-fabric-ok'"
    )
    _ssh(fabric_cmd, host=cfg.host, timeout=20)
    if not _dut_tcp_probe(cfg):
        raise RuntimeError(
            f"bay DUT {cfg.address} unreachable — is it powered and seated?"
        )
    return cfg


def ensure(vtype: str, **overrides) -> VenueConfig:
    cfg = config_for(vtype, **overrides)
    if cfg.type == Venue.VIRTUAL:
        return _ensure_virtual(cfg)
    return _ensure_bay(cfg)


# ---------------------------------------------------------------------------
# Deploy — push an artifact to the DUT with the health gate
# ---------------------------------------------------------------------------

_MINT_PIN_JQ = (
    '.accepted_mints = [{"url":"$MINT","min_balance":64,'
    '"balance_tolerance_percent":10,"payout_interval_seconds":60,'
    '"min_payout_amount":128,"price_per_step":1,"price_unit":"sat",'
    '"purchase_min_steps":1}]'
)


def _deploy_virtual(cfg: VenueConfig, artifact: str) -> bool:
    """Deploy via the venue host to the VM."""
    artifact_path = Path(artifact).resolve()
    if not artifact_path.is_file():
        raise FileNotFoundError(artifact)
    subprocess.run(
        ["scp", "-q", str(artifact_path), f"{cfg.host}:~/venue-artifact"],
        check=True, timeout=60,
    )
    deploy_cmd = f"""
set -e
RV={Path(artifact).name}
sshpass -p {cfg.password} scp -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    ~/venue-artifact root@{cfg.address}:/tmp/rv-artifact
sshpass -p {cfg.password} ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@{cfg.address} '
    PKG=$(echo /tmp/rv-artifact | grep -q "\\.apk$" && echo apk || echo opkg)
    $PKG update >/dev/null 2>&1 || true
    if [ "$PKG" = "apk" ]; then
        apk add --allow-untrusted /tmp/rv-artifact 2>&1 | tail -1
    else
        opkg install --force-overwrite /tmp/rv-artifact 2>&1 | tail -1
    fi
    rm -f /etc/tollgate/wallet.db
    jq \'{_MINT_PIN_JQ.replace("$MINT", cfg.mint_url)}\' \
        /etc/tollgate/config.json > /tmp/c.new 2>/dev/null && mv /tmp/c.new /etc/tollgate/config.json
    /etc/init.d/tollgate-wrt restart
    sleep 12
    [ -s /usr/bin/nodogsplash ] || {{ echo NDS-BINARY-EMPTY; exit 1; }}
    ndsctl status >/dev/null 2>&1 || {{ echo NDSCTL-DEAD; exit 1; }}
    wget -q -O- -T 8 http://127.0.0.1:2121/ 2>/dev/null | head -c 60
'
"""
    r = _ssh(deploy_cmd, host=cfg.host, timeout=120)
    return r.returncode == 0 and "NDS-" not in r.stderr


def _deploy_physical(cfg: VenueConfig, artifact: str) -> bool:
    """Deploy via the venue host to a physical DUT (key or password auth)."""
    artifact_path = Path(artifact).resolve()
    subprocess.run(
        ["scp", "-q", str(artifact_path), f"{cfg.host}:~/venue-artifact"],
        check=True, timeout=60,
    )
    auth = f"sshpass -p {cfg.password}" if cfg.password else ""
    deploy_cmd = f"""
set -e
scp -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    ~/venue-artifact root@{cfg.address}:/tmp/rv-artifact
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@{cfg.address} '
    PKG=$(echo /tmp/rv-artifact | grep -q "\\.apk$" && echo "apk add --allow-untrusted" || echo "opkg install --force-overwrite")
    apk update >/dev/null 2>&1 || opkg update >/dev/null 2>&1 || true
    $PKG /tmp/rv-artifact 2>&1 | tail -1
    rm -f /etc/tollgate/wallet.db
    jq \'{_MINT_PIN_JQ.replace("$MINT", cfg.mint_url)}\' \
        /etc/tollgate/config.json > /tmp/c.new 2>/dev/null && mv /tmp/c.new /etc/tollgate/config.json
    /etc/init.d/tollgate-wrt restart
    sleep 12
    [ -s /usr/bin/nodogsplash ] || {{ echo NDS-BINARY-EMPTY; exit 1; }}
    ndsctl status >/dev/null 2>&1 || {{ echo NDSCTL-DEAD; exit 1; }}
    wget -q -O- -T 8 http://127.0.0.1:2121/ 2>/dev/null | head -c 60
'
"""
    r = _ssh(deploy_cmd, host=cfg.host, timeout=120)
    return r.returncode == 0


def deploy(cfg: VenueConfig, artifact: str) -> bool:
    if cfg.type == Venue.VIRTUAL:
        return _deploy_virtual(cfg, artifact)
    return _deploy_physical(cfg, artifact)


# ---------------------------------------------------------------------------
# Suite — run a pytest suite against the DUT
# ---------------------------------------------------------------------------

def suite_env(cfg: VenueConfig) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "TOLLGATE_SSH_HOST": cfg.address,
        "TOLLGATE_SSH_PASSWORD": cfg.password,
        "TOLLGATE_LUCI_PASSWORD": cfg.password,
        "TOLLGATE_SSH_KEY": "",          # .env SSH_KEY disables sshpass (the trap)
        "TOLLGATE_VIRTUAL_LAB": "1",     # payment routing through the client host
        "TOLLGATE_CLIENT_IP": cfg.gateway,
        "TOLLGATE_CLIENT_USER": cfg.venue_host_user,
        "TOLLGATE_TEST_MINT_URL": cfg.mint_url,
        "TOLLGATE_SKIP_ROUTER_INVENTORY": "1",  # dead default DUTs eat the timeout budget
    })
    return env


def suite(cfg: VenueConfig, test: str = "tests/api/test_payment_rejection_semantics.py",
          venv_python: str = "") -> int:
    py = venv_python or os.path.expanduser("~/.tollgate-test-venv/bin/python")
    env = suite_env(cfg)
    r = subprocess.run(
        [py, "-m", "pytest", test, "--no-deploy", "-v", "--tb=short"],
        env=env, timeout=600, cwd=str(Path(__file__).parent.parent),
    )
    return r.returncode


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli():
    import argparse
    p = argparse.ArgumentParser(description="Shared venue layer for TollGate test lanes")
    p.add_argument("action", choices=["ensure", "deploy", "suite", "full"])
    p.add_argument("--type", default="virtual", choices=[v.value for v in Venue])
    p.add_argument("--address", default="", help="override the DUT address")
    p.add_argument("--gateway", default="", help="override the paying-client address")
    p.add_argument("--password", default="", help="DUT root password")
    p.add_argument("--artifact", default="", help="path to the .ipk/.apk to deploy")
    p.add_argument("--test", default="tests/api/test_payment_rejection_semantics.py")
    args = p.parse_args()

    overrides = {}
    if args.address:
        overrides["address"] = args.address
    if args.gateway:
        overrides["gateway"] = args.gateway
    if args.password:
        overrides["password"] = args.password

    cfg = ensure(args.type, **overrides)
    print(f"venue: {cfg.type.value} @ {cfg.address} (gateway {cfg.gateway})")

    if args.action == "ensure":
        return 0
    if args.action in ("deploy", "full"):
        if not args.artifact:
            print("error: --artifact required for deploy", file=sys.stderr)
            return 1
        ok = deploy(cfg, args.artifact)
        print(f"deploy: {'GREEN' if ok else 'RED'}")
        if not ok:
            return 1
    if args.action in ("suite", "full"):
        rc = suite(cfg, args.test)
        print(f"suite: {'GREEN' if rc == 0 else 'RED'} (rc={rc})")
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
