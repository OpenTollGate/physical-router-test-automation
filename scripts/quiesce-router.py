#!/usr/bin/env python3
"""Disable all test-router AP interfaces after a run.

Safety properties:
  * refuses protected devices (MT6000 gateway, fleet control hosts);
  * requires the machine-global bench lease unless --allow-unlocked;
  * only touches wireless interfaces whose ``mode`` is ``ap``;
  * leaves STA/LAN/management untouched;
  * verifies the AP state after ``wifi reload``.

Usage:
    scripts/quiesce-router.py --router e750-lab
    scripts/quiesce-router.py --all
    scripts/quiesce-router.py --router e750-lab --dry-run
    scripts/quiesce-router.py --router e750-lab --allow-unlocked  # cleanup hooks

The normal path is inside the bench lease:
    scripts/hw-bench-lease exec --purpose "quiesce" -- scripts/quiesce-router.py --all
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.protected_devices import (  # noqa: E402
    ProtectedDeviceError,
    assert_not_protected,
)

DEFAULT_INVENTORY = ROOT / "config" / "routers.json"

REMOTE_QUIESCE = r"""
set -e
if ! command -v uci >/dev/null 2>&1; then
  echo "NOT_OPENWRT"
  exit 0
fi
changed=0
for s in $(uci show wireless 2>/dev/null | sed -n "s/^wireless\.\([^.]*\)=wifi-iface$/\1/p"); do
  [ "$(uci -q get "wireless.$s.mode")" = "ap" ] || continue
  if [ "$(uci -q get "wireless.$s.disabled")" != "1" ]; then
    uci set "wireless.$s.disabled=1"
    changed=1
    echo "disabled $s"
  fi
done
if [ "$changed" = "1" ]; then
  uci commit wireless
  wifi reload >/dev/null 2>&1 || wifi up >/dev/null 2>&1 || true
  sleep 5
fi
echo "--AP interfaces after quiesce--"
for s in $(uci show wireless 2>/dev/null | sed -n "s/^wireless\.\([^.]*\)=wifi-iface$/\1/p"); do
  [ "$(uci -q get "wireless.$s.mode")" = "ap" ] || continue
  echo "ap $s ssid=$(uci -q get "wireless.$s.ssid") disabled=$(uci -q get "wireless.$s.disabled")"
done
if command -v iw >/dev/null 2>&1; then
  echo "--iw AP count--"
  iw dev 2>/dev/null | grep -c "type AP" || true
fi
echo "QUIESCE_DONE"
"""


def inventory_path() -> Path:
    return Path(os.environ.get("TOLLGATE_ROUTER_INVENTORY", DEFAULT_INVENTORY)).expanduser()


def load_routers() -> dict[str, dict]:
    path = inventory_path()
    if path.is_file():
        doc = json.loads(path.read_text())
        routers = doc.get("routers") or {}
        if isinstance(routers, dict):
            return routers
        raise RuntimeError(f"{path}: 'routers' must be an object")
    host = os.environ.get("TOLLGATE_SSH_HOST")
    if host:
        rid = os.environ.get("TOLLGATE_ROUTER_ID", "default")
        return {rid: {"sshHost": host}}
    raise RuntimeError(
        f"router inventory not found at {path}; set TOLLGATE_ROUTER_INVENTORY "
        "or TOLLGATE_SSH_HOST"
    )


def _ssh_command(router: dict) -> list[str]:
    host = router.get("sshHost") or os.environ.get("TOLLGATE_SSH_HOST")
    if not host:
        raise RuntimeError("router has no sshHost (and TOLLGATE_SSH_HOST is unset)")
    user = router.get("sshUser") or os.environ.get("TOLLGATE_SSH_USER", "root")
    port = str(router.get("sshPort") or os.environ.get("TOLLGATE_SSH_PORT", "22"))
    # The bench wrapper materializes a short-lived key and exports it; it must
    # win over the inventory path so a static key is not used outside the lease.
    key = os.environ.get("TOLLGATE_SSH_KEY") or router.get("sshKey") or router.get("keyfile")
    password = (
        router.get("sshPassword")
        or router.get("password")
        or os.environ.get("TOLLGATE_SSH_PASSWORD")
        or os.environ.get("TOLLGATE_LUCI_PASSWORD")
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
        sshpass = shutil.which("sshpass")
        if not sshpass:
            raise RuntimeError("password auth requested but sshpass is not installed")
        # sshpass cannot feed a password when BatchMode disables the prompt.
        return [sshpass, "-p", password, *opts, "-o", "BatchMode=no", target]
    return [*opts, "-o", "BatchMode=yes", target]


def quiesce_router(router_id: str, router: dict, *, dry_run: bool = False, skip_protected: bool = False) -> int:
    try:
        assert_not_protected(
            board_name=router.get("board_name") or router.get("model"),
            model=router.get("model"),
            macs=router.get("mac") or router.get("lan_mac"),
            hostname=router.get("hostname") or router.get("name"),
            addresses=[router.get("sshHost"), router.get("address")],
        )
    except ProtectedDeviceError as exc:
        if skip_protected:
            print(f"SKIP {router_id}: protected ({exc})")
            return 0
        print(f"REFUSED {router_id}: {exc}", file=sys.stderr)
        return 2

    if dry_run:
        print(f"# quiesce {router_id}: {router.get('sshHost') or os.environ.get('TOLLGATE_SSH_HOST')}")
        print(REMOTE_QUIESCE)
        return 0

    cmd = _ssh_command(router)
    print(f"==> quiescing {router_id} via {router.get('sshHost') or os.environ.get('TOLLGATE_SSH_HOST')}")
    proc = subprocess.run(
        [*cmd, "sh -s"],
        input=REMOTE_QUIESCE,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr.strip(), file=sys.stderr)
        print(f"QUIESCE_FAILED {router_id} rc={proc.returncode}", file=sys.stderr)
        return proc.returncode or 1
    if "QUIESCE_DONE" not in proc.stdout:
        print(f"QUIESCE_INCOMPLETE {router_id}", file=sys.stderr)
        return 1
    return 0


def require_lock(allow_unlocked: bool) -> None:
    if allow_unlocked or os.environ.get("TOLLGATE_ALLOW_UNLOCKED") == "1":
        return
    from lib.hardware_lock import require_hardware_lock

    require_hardware_lock()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--router", help="router id from config/routers.json")
    ap.add_argument("--all", action="store_true", help="quiesce every non-protected router")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-unlocked", action="store_true", help="cleanup hook (no bench lease)")
    args = ap.parse_args()
    if not args.router and not args.all:
        ap.error("pass --router ID or --all")

    try:
        require_lock(args.allow_unlocked)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Run inside: scripts/hw-bench-lease exec --purpose quiesce -- ...", file=sys.stderr)
        return 1

    try:
        routers = load_routers()
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.all:
        rc = 0
        for rid in sorted(routers):
            rc |= quiesce_router(rid, routers[rid], dry_run=args.dry_run, skip_protected=True)
        return rc

    if args.router not in routers:
        print(f"ERROR: router {args.router!r} not in inventory (known: {sorted(routers)})", file=sys.stderr)
        return 1
    return quiesce_router(args.router, routers[args.router], dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
