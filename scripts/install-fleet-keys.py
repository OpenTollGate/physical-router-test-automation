#!/usr/bin/env python3
"""Install the committed fleet SSH public keys on test routers.

Public keys only. Private keys stay on their hosts. A managed block is written
to /etc/dropbear/authorized_keys and /root/.ssh/authorized_keys; unrelated keys
are preserved. Protected devices are refused; the bench lease is required.
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

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.protected_devices import (  # noqa: E402
    ProtectedDeviceError,
    assert_not_protected,
)

DEFAULT_INVENTORY = ROOT / "config" / "routers.json"
DEFAULT_KEYS = ROOT / "config" / "fleet_ssh_keys.yaml"

REMOTE_INSTALL = r"""
set -e
[ -s /tmp/hermes-fleet-keys ] || { echo "NO_KEYS"; exit 1; }
umask 077
mkdir -p /root/.ssh
f=/etc/dropbear/authorized_keys
touch "$f"
cp "$f" "/tmp/authorized_keys.bak.$(date +%s)" 2>/dev/null || true
awk '
  /^# BEGIN hermes-fleet$/ {skip=1; next}
  /^# END hermes-fleet$/ {skip=0; next}
  !skip {print}
' "$f" > /tmp/ak
{
  cat /tmp/ak
  echo
  echo "# BEGIN hermes-fleet"
  cat /tmp/hermes-fleet-keys
  echo "# END hermes-fleet"
} > "$f"
chmod 600 "$f"
cp "$f" /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
rm -f /tmp/ak /tmp/hermes-fleet-keys
echo "fleet_keys=$(grep -c '^ssh-' "$f" || true)"
echo "FLEET_KEYS_DONE"
"""


def load_keys(path: Path) -> list[str]:
    doc = yaml.safe_load(path.read_text()) or {}
    keys: list[str] = []
    for host, entry in (doc.get("hosts") or {}).items():
        for item in (entry or {}).get("keys") or []:
            key = str(item.get("key", "")).strip()
            if not key:
                continue
            if not key.startswith("ssh-"):
                raise RuntimeError(f"{host}: key does not start with ssh-: {item.get('comment')}")
            if "PRIVATE KEY" in key:
                raise RuntimeError(f"{host}: private key material in public key list")
            if key not in keys:
                keys.append(key)
    if not keys:
        raise RuntimeError(f"no public keys in {path}")
    return keys


def load_routers() -> dict[str, dict]:
    path = Path(os.environ.get("TOLLGATE_ROUTER_INVENTORY", DEFAULT_INVENTORY)).expanduser()
    if path.is_file():
        doc = json.loads(path.read_text())
        return doc.get("routers") or {}
    host = os.environ.get("TOLLGATE_SSH_HOST")
    if host:
        return {os.environ.get("TOLLGATE_ROUTER_ID", "default"): {"sshHost": host}}
    raise RuntimeError(f"router inventory not found at {path}")


def ssh_command(router: dict) -> list[str]:
    host = router.get("sshHost") or os.environ.get("TOLLGATE_SSH_HOST")
    if not host:
        raise RuntimeError("router has no sshHost")
    user = router.get("sshUser") or os.environ.get("TOLLGATE_SSH_USER", "root")
    port = str(router.get("sshPort") or os.environ.get("TOLLGATE_SSH_PORT", "22"))
    # Prefer the lease-scoped key injected by scripts/prta-bench-run.sh.
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
        "ssh", "-o", "ConnectTimeout=10",
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


def install(router_id: str, router: dict, keys: list[str], *, dry_run: bool, skip_protected: bool = False) -> int:
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
        print(f"# install {len(keys)} keys on {router_id} ({router.get('sshHost')})")
        for key in keys:
            print(key)
        return 0

    cmd = ssh_command(router)
    print(f"==> installing {len(keys)} fleet keys on {router_id}")
    proc = subprocess.run(
        [*cmd, "umask 077; cat > /tmp/hermes-fleet-keys"],
        input="\n".join(keys) + "\n", text=True, capture_output=True, timeout=60, check=False,
    )
    if proc.returncode != 0:
        print(proc.stderr.strip(), file=sys.stderr)
        return proc.returncode
    proc = subprocess.run(
        [*cmd, "sh -s"], input=REMOTE_INSTALL, text=True, capture_output=True,
        timeout=90, check=False,
    )
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.returncode != 0 or "FLEET_KEYS_DONE" not in proc.stdout:
        print(proc.stderr.strip(), file=sys.stderr)
        print(f"FLEET_KEYS_FAILED {router_id}", file=sys.stderr)
        return proc.returncode or 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--router")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--keys-file", default=str(DEFAULT_KEYS))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-unlocked", action="store_true")
    args = ap.parse_args()
    if not args.router and not args.all:
        ap.error("pass --router ID or --all")

    try:
        keys = load_keys(Path(args.keys_file))
        require_lock(args.allow_unlocked)
        routers = load_routers()
    except (RuntimeError, ProtectedDeviceError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.all:
        rc = 0
        for rid in sorted(routers):
            rc |= install(rid, routers[rid], keys, dry_run=args.dry_run, skip_protected=True)
        return rc
    if args.router not in routers:
        print(f"ERROR: router {args.router!r} not in inventory", file=sys.stderr)
        return 1
    return install(args.router, routers[args.router], keys, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
