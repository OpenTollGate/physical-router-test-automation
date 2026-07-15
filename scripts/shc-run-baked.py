#!/usr/bin/env python3
"""Run a TollGate test on a pre-baked SHC VM via snapshot-restore.

Usage:
    python3 scripts/shc-run-baked.py --service-id 1405 --ip 66.92.204.236 \\
        --snapshot-id <snap_id> --branch main [--pr N] [--publish]

Restores the baked snapshot (instant reset to provisioned state), re-fetches the
suite at the current suite_ref, applies the local overlay, then runs ONLY the
worker pipeline — skipping the ~10-15 min bootstrap that shc-bake.py already did.
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib.cloud_lab.constants import SUITE_REPO_URL, TEST_DIR, VIRT_LAB_PASSWORD
from lib.cloud_lab.shc_submit import (
    _build_bootstrap_script,
    _gh_token,
    _nsec_hex,
    _suite_ref,
    _working_tree_overlay_b64,
)


def _ssh_base(ip: str, user: str = "debian") -> list[str]:
    key = Path.home() / ".ssh/id_rsa"
    base = [
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
    ]
    if key.exists():
        base.extend(["-i", str(key)])
    base.append(f"{user}@{ip}")
    return base


def _ssh(ip: str, cmd: str, user: str = "debian", timeout: int = 30, stdin: str | None = None) -> tuple[int, str, str]:
    base = _ssh_base(ip, user)
    try:
        r = subprocess.run(base + [cmd], capture_output=True, text=True, timeout=timeout, input=stdin)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "TIMEOUT"
    except Exception:
        return 1, "", "SSH_ERROR"


def _wait_ssh(ip: str, user: str = "debian", timeout: int = 300) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rc, _, _ = _ssh(ip, "echo OK", user=user, timeout=10)
        if rc == 0:
            return True
        time.sleep(5)
    return False


def _restore_snapshot(service_id: int, snapshot_id: str) -> bool:
    """Restore a snapshot on the VM. SHC requires the VM to be stopped first.

    Stop → restore → start → wait for SSH. Returns True if restore completed.
    """
    from shc_toolkit import SHCClient
    client = SHCClient()

    # 1. Stop the VM (required before restore — restore wipes the disk)
    print(f"Stopping VM {service_id}...", end=" ", flush=True)
    try:
        vm = client.get_vm(service_id)
        if vm.get("service_status") == "stopped":
            print("already stopped")
        else:
            client.stop_vm(service_id)
            for _ in range(36):
                time.sleep(5)
                vm = client.get_vm(service_id)
                if vm.get("service_status") in ("stopped", "offline", "maintenance"):
                    print("stopped")
                    break
            else:
                print("stop timeout (continuing anyway)")
    except Exception as e:
        print(f"stop error ({e}) — continuing")

    # 2. Restore the snapshot
    print(f"Restoring snapshot {snapshot_id}...", end=" ", flush=True)
    try:
        client.restore_snapshot(service_id, snapshot_id)
        print("queued")
    except Exception as e:
        if "confirm" in str(e).lower() or "queued" in str(e).lower():
            print("queued")
        else:
            print(f"FAILED: {e}")
            return False

    # 3. Wait for restore → VM auto-starts
    print("  Waiting for restore...", end=" ", flush=True)
    for _ in range(72):
        time.sleep(5)
        try:
            vm = client.get_vm(service_id)
            prov = vm.get("provisioning_state", "")
            status = vm.get("service_status", "")
            if prov == "ready" and status == "active":
                print("done (active|ready)")
                break
        except Exception:
            pass
    else:
        print("timeout — proceeding")

    # 4. Ensure VM is started
    try:
        vm = client.get_vm(service_id)
        if vm.get("service_status") != "active":
            client.start_vm(service_id)
            print(f"Starting VM {service_id}...")
    except Exception:
        pass

    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service-id", type=int, required=True)
    ap.add_argument("--ip", required=True)
    ap.add_argument("--user", default="debian")
    ap.add_argument("--snapshot-id", required=True, help="SHC snapshot ID to restore")
    ap.add_argument("--branch", required=True)
    ap.add_argument("--pr", default="")
    ap.add_argument("--commit", default="")
    ap.add_argument("--repo", default="OpenTollGate/tollgate-module-basic-go")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--complete", action="store_true")
    ap.add_argument("--two-router", action="store_true")
    ap.add_argument("--mint", default="auto")
    ap.add_argument("--portal", default="builtin")
    ap.add_argument("--no-restore", action="store_true", help="Skip snapshot restore (VM already baked)")
    ap.add_argument("--no-monitor", action="store_true", help="Fire-and-forget: upload + launch, then exit")
    args = ap.parse_args()

    ip = args.ip
    user = args.user

    # 1. Restore snapshot (reset to baked state)
    if not args.no_restore:
        if not _restore_snapshot(args.service_id, args.snapshot_id):
            print("Snapshot restore failed — aborting")
            return 1

    # 2. Re-apply SSH key (snapshot restore may reset authorized_keys)
    if not args.no_restore:
        from shc_toolkit import SHCClient
        client = SHCClient()
        key_path = Path.home() / ".ssh/id_rsa.pub"
        if key_path.exists():
            pubkey = key_path.read_text().strip()
            print("Re-applying SSH key...", end=" ", flush=True)
            applied = False
            for attempt in range(12):
                try:
                    client.apply_ssh_key_live(args.service_id, pubkey)
                    print(f"OK ({attempt+1} tries)")
                    applied = True
                    break
                except Exception:
                    time.sleep(10)
            if not applied:
                print("SKIP (guest agent unavailable — trying SSH anyway, key may be in snapshot)")
        time.sleep(5)

    # 3. Wait for SSH
    print("Waiting for SSH...", end=" ", flush=True)
    if not _wait_ssh(ip, user=user, timeout=300):
        print("FAILED — SSH not reachable after restore")
        return 1
    print("OK")

    # 3. Build env + overlay (mirrors submit_run_shc)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = (args.commit or args.branch)[:7].replace("/", "-")
    run_id = f"{timestamp}-{short}"

    suite_ref = _suite_ref()
    token = _gh_token()
    nsec = _nsec_hex()
    overlay_b64 = _working_tree_overlay_b64()
    if overlay_b64:
        print(f"Overlay: {len(overlay_b64)} bytes b64")

    bootstrap_env = " ".join([
        f"TOLLGATE_RUN_ID={shlex.quote(run_id)}",
        f"TOLLGATE_SUT_BRANCH={shlex.quote(args.branch)}",
        f"TOLLGATE_SUT_COMMIT={shlex.quote(args.commit)}",
        f"TOLLGATE_SUT_PR={shlex.quote(args.pr)}",
        "TOLLGATE_ARTIFACT_RUN_ID=blossom",
        f"TOLLGATE_ARTIFACT_REPO={shlex.quote(args.repo)}",
        f"TOLLGATE_PR_REPO={shlex.quote(args.repo)}",
        f"TOLLGATE_SUITE_REF={shlex.quote(suite_ref)}",
        "OPENWRT_VERSION=24.10.1",
        "TOLLGATE_DEPLOY_MODE=framework",
        "BLOSSOM_SERVER=https://blossom.psbt.me",
        "NOSTR_RELAYS=wss://relay.cashu.email,wss://relay1.orangesync.tech",
        "TOLLGATE_BACKEND=go",
        f"TOLLGATE_PUBLISH={'true' if args.publish else 'false'}",
        f"TOLLGATE_QUICK={'true' if args.quick else 'false'}",
        f"TOLLGATE_SMOKE={'true' if args.smoke else 'false'}",
        "SKIP_BLOSSOMFS=1",
        "SKIP_VWIFI=1",
        f"TOLLGATE_COMPLETE={'true' if args.complete else 'false'}",
        f"TOLLGATE_MINT={shlex.quote(args.mint)}",
        f"TOLLGATE_PORTAL={shlex.quote(args.portal)}",
        "TOLLGATE_KEEP_VM_ON_FAILURE=true",
        "TOLLGATE_GCP_PROJECT=tollgate-test-lab",
        "TOLLGATE_GCP_ZONE=shc",
        f"TOLLGATE_VM_NAME=baked-{short[:8]}",
        "TOLLGATE_CLOUD=shc",
        f"TOLLGATE_SERVICE_ID={args.service_id}",
        f"TOLLGATE_TWO_ROUTER={'true' if args.two_router else 'false'}",
        f"SHC_API_KEY={shlex.quote(os.environ.get('SHC_API_KEY', ''))}",
        f"GH_TOKEN={shlex.quote(token)}",
        f"BOT_NSEC_HEX={shlex.quote(nsec)}",
        f"VIRT_LAB_PASSWORD={VIRT_LAB_PASSWORD}",
        "NSEC_FILE=/root/nsec",
        "HOME=/root",
    ])

    # 4. Build a WORKER-ONLY script (skip provisioning steps 1-14; the VM is baked).
    #    Re-fetch suite_ref + apply overlay, then run the worker.
    overlay_step = (
        f"base64 -d /tmp/overlay.b64 | sudo tar xzf - -C {TEST_DIR}\n"
        f'echo "[2] Applied suite overlay"'
        if overlay_b64
        else 'echo "[2] No overlay to apply"'
    )

    worker_script = f"""#!/bin/bash
set -eo pipefail
exec >> /var/log/tollgate-run.log 2>&1
echo "=== TollGate baked-worker started $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

export {bootstrap_env}
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.cargo/bin"

echo "[1] Re-fetching suite at $TOLLGATE_SUITE_REF..."
cd {TEST_DIR}
sudo git fetch --depth 1 origin "$TOLLGATE_SUITE_REF" 2>/dev/null || true
sudo git checkout "$TOLLGATE_SUITE_REF" 2>/dev/null || echo "  (already at $TOLLGATE_SUITE_REF)"
echo "[1] done"

echo "[2] Applying overlay..."
cat > /tmp/overlay.b64 <<'OVERLAY_EOF'
{overlay_b64}
OVERLAY_EOF
{overlay_step}
echo "[2] done"

echo "[3] Running worker pipeline..."
unset BOT_NSEC_HEX
echo "  Worker env: TOLLGATE_RUN_ID=$TOLLGATE_RUN_ID SUT_BRANCH=$TOLLGATE_SUT_BRANCH PUBLISH=$TOLLGATE_PUBLISH"
/opt/tollgate-venv/bin/python3 -m lib.cloud_lab.worker --from-env
WORKER_EXIT=$?
echo "PIPELINE_DONE (exit=$WORKER_EXIT)" >> /tmp/tollgate-status
echo "[3] done (exit=$WORKER_EXIT)"
echo "=== Baked worker complete ==="
touch /tmp/tollgate-done
"""

    # 5. Upload + launch
    script_path = "/tmp/tollgate-baked-worker.sh"
    print("Uploading worker script...")
    import base64
    b64_script = base64.b64encode(worker_script.encode()).decode()
    _ssh(ip, "sudo rm -f /tmp/tollgate-status /tmp/tollgate-done /var/log/tollgate-run.log", user=user)
    rc, _, err = _ssh(ip, f"echo '{b64_script}' | base64 -d > {script_path} && chmod +x {script_path}",
                      user=user, timeout=30)
    if rc != 0:
        print(f"Upload failed: {err}")
        return 1

    print("Launching worker pipeline...")
    _ssh(ip, f"nohup sudo bash {script_path} > /dev/null 2>&1 & echo LAUNCHED", user=user)

    # 6. Monitor
    print(f"Run ID: {run_id}")
    print(f"Logs: ssh {user}@{ip} 'tail -f /var/log/tollgate-run.log'")
    if args.no_monitor:
        print(f"\nFire-and-forget mode. Monitor with:")
        print(f"  ssh {user}@{ip} 'tail -f /var/log/tollgate-run.log'")
        return 0

    print("\nMonitoring (worker-only, should be fast)...")
    last_line = ""
    deadline = time.time() + 2400  # 40 min max
    while time.time() < deadline:
        time.sleep(20)
        rc, out, _ = _ssh(ip, "cat /tmp/tollgate-status 2>/dev/null; echo '---'; tail -4 /var/log/tollgate-run.log 2>/dev/null", user=user, timeout=10)
        if "tollgate-done" in out or "Baked worker complete" in out:
            print("\n=== RUN COMPLETE ===")
            # Show final summary
            rc, out, _ = _ssh(ip, "grep -iE 'Pipeline complete|passed=|failed=|skipped=' /var/log/tollgate-run.log 2>/dev/null | tail -3", user=user, timeout=10)
            print(out.strip())
            return 0
        if "BOOTSTRAP_FAILED" in out:
            print(f"\n=== RUN FAILED ===\n{out[-400:]}")
            return 1
        for line in out.splitlines():
            if line.startswith("[") and line != last_line:
                last_line = line
                print(f"  {line}")
    print("\n=== RUN TIMED OUT (40 min) ===")
    return 1


if __name__ == "__main__":
    sys.exit(main())
