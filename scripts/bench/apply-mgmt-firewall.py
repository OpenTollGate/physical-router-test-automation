#!/usr/bin/env python3
"""Allow management only from x280 on a test router.

This writes one idempotent UCI firewall rule (and optionally a deny rule after
it). It is deliberately separate from the test suite so the network boundary is
applied before any test package is installed.

Examples:
    scripts/bench/apply-mgmt-firewall.py --router tollgate-af92 \
        --zone wan --src-ip 192.168.50.10/32 --deny-others
    scripts/bench/apply-mgmt-firewall.py --router tollgate-9bd4 \
        --zone private --src-ip 10.110.223.2/32 --deny-others
    scripts/bench/apply-mgmt-firewall.py --router e750-lab \
        --zone lan --src-ip 192.168.8.244/32 --deny-others

The caller must hold the bench lease unless --allow-unlocked is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib.protected_devices import ProtectedDeviceError, assert_not_protected  # noqa: E402

DEFAULT_INVENTORY = ROOT / "config" / "routers.json"


def load_router(router_id: str) -> dict:
    path = Path(os.environ.get("TOLLGATE_ROUTER_INVENTORY", DEFAULT_INVENTORY)).expanduser()
    doc = json.loads(path.read_text())
    routers = doc.get("routers") or {}
    if router_id not in routers:
        raise RuntimeError(f"router {router_id!r} not in {path}")
    return routers[router_id]


def ssh_command(router: dict) -> list[str]:
    host = router.get("sshHost") or os.environ.get("TOLLGATE_SSH_HOST")
    if not host:
        raise RuntimeError("router has no sshHost")
    user = router.get("sshUser") or os.environ.get("TOLLGATE_SSH_USER", "root")
    port = str(router.get("sshPort") or os.environ.get("TOLLGATE_SSH_PORT", "22"))
    key = os.environ.get("TOLLGATE_SSH_KEY") or router.get("sshKey") or router.get("keyfile")
    password = (
        router.get("sshPassword")
        or router.get("password")
        or os.environ.get("TOLLGATE_SSH_PASSWORD")
    )
    jump = router.get("jumpHost") or os.environ.get("TOLLGATE_SSH_JUMP")
    bind = router.get("sshBind") or os.environ.get("TOLLGATE_SSH_BIND")
    opts = [
        "ssh",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={os.environ.get('TOLLGATE_KNOWN_HOSTS', '/tmp/tollgate-known-hosts')}",
        "-p", port,
    ]
    if key:
        opts += ["-i", str(key)]
    if jump:
        opts += ["-J", str(jump)]
    if bind:
        opts += ["-b", str(bind)]
    extra = router.get("sshOptions") or []
    if isinstance(extra, str):
        extra = shlex.split(extra)
    opts += [str(item) for item in extra]
    target = f"{user}@{host}"
    if password and not key:
        sp = shutil.which("sshpass")
        if not sp:
            raise RuntimeError("password auth requested but sshpass is not installed")
        return [sp, "-p", password, *opts, "-o", "BatchMode=no", target]
    return [*opts, "-o", "BatchMode=yes", target]


def require_lock(allow_unlocked: bool) -> None:
    if allow_unlocked or os.environ.get("TOLLGATE_ALLOW_UNLOCKED") == "1":
        return
    from lib.hardware_lock import require_hardware_lock

    require_hardware_lock()


def remote_script(zone: str, src_ip: str, ports: str, deny_others: bool) -> str:
    lines = [
        "set -e",
        "uci set firewall.prta_mgmt=rule",
        "uci set firewall.prta_mgmt.name='prta-mgmt'",
        f"uci set firewall.prta_mgmt.src='{zone}'",
        f"uci set firewall.prta_mgmt.src_ip='{src_ip}'",
        "uci set firewall.prta_mgmt.proto='tcp'",
        f"uci set firewall.prta_mgmt.dest_port='{ports}'",
        "uci set firewall.prta_mgmt.target='ACCEPT'",
        "uci set firewall.prta_mgmt.family='ipv4'",
    ]
    if deny_others:
        lines += [
            "uci set firewall.prta_mgmt_deny=rule",
            "uci set firewall.prta_mgmt_deny.name='prta-mgmt-deny'",
            f"uci set firewall.prta_mgmt_deny.src='{zone}'",
            "uci set firewall.prta_mgmt_deny.proto='tcp'",
            f"uci set firewall.prta_mgmt_deny.dest_port='{ports}'",
            "uci set firewall.prta_mgmt_deny.target='REJECT'",
            "uci set firewall.prta_mgmt_deny.family='ipv4'",
        ]
    lines += [
        "uci commit firewall",
        "/etc/init.d/firewall reload >/dev/null 2>&1 || true",
        "uci show firewall.prta_mgmt",
    ]
    if deny_others:
        lines.append("uci show firewall.prta_mgmt_deny")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--router", required=True)
    ap.add_argument("--zone", default=None, help="UCI firewall zone (wan/private/lan)")
    ap.add_argument("--src-ip", default=None, help="Allowed source IP/CIDR, space separated")
    ap.add_argument("--ports", default="22 443")
    ap.add_argument("--deny-others", action="store_true", help="Reject the same ports from the zone after the allow rule")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-unlocked", action="store_true")
    args = ap.parse_args()

    try:
        router = load_router(args.router)
        assert_not_protected(
            board_name=router.get("board_name") or router.get("model"),
            model=router.get("model"),
            macs=router.get("mac") or router.get("lan_mac"),
            hostname=router.get("hostname") or router.get("name"),
            addresses=[router.get("sshHost"), router.get("address")],
        )
        require_lock(args.allow_unlocked)
    except (RuntimeError, ProtectedDeviceError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    zone = args.zone or router.get("mgmtZone")
    src_ip = args.src_ip or router.get("mgmtSource")
    if not zone or not src_ip:
        print("ERROR: --zone and --src-ip (or mgmtZone/mgmtSource in inventory) are required", file=sys.stderr)
        return 1

    script = remote_script(zone, src_ip, args.ports, args.deny_others)
    if args.dry_run:
        print(f"# {args.router} zone={zone} src_ip={src_ip} ports={args.ports}")
        print(script)
        return 0

    cmd = ssh_command(router)
    print(f"==> applying management firewall on {args.router} ({zone} <- {src_ip})")
    proc = subprocess.run(
        [*cmd, "sh -s"], input=script, text=True, capture_output=True, timeout=90, check=False
    )
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr.strip(), file=sys.stderr)
        return proc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
