#!/usr/bin/env python3
"""Bootstrap an existing SHC VM (bake it) and create a snapshot.

Usage:
    python3 scripts/shc-bake.py --service-id 1405 --ip 66.92.204.236 [--snapshot-name baked]

Reuses lib.cloud_lab.shc_submit internals to generate + run the bootstrap script
on an existing VM, then optionally creates a snapshot of the baked state.
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service-id", type=int, required=True)
    ap.add_argument("--ip", required=True)
    ap.add_argument("--user", default="debian")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--snapshot-name", default="baked")
    ap.add_argument("--no-snapshot", action="store_true")
    args = ap.parse_args()

    ip = args.ip
    user = args.user

    print(f"=== Baking SHC VM {args.service_id} @ {ip} ===")

    # Wait for SSH
    print("Waiting for SSH...", end=" ", flush=True)
    for _ in range(60):
        rc, _, _ = _ssh(ip, "echo OK", user=user, timeout=10)
        if rc == 0:
            print("OK")
            break
        time.sleep(5)
    else:
        print("FAILED — SSH not reachable")
        return 1

    # Build the bootstrap env (mirrors submit_run_shc)
    run_id = f"bake-{int(time.time())}"
    suite_ref = _suite_ref()
    token = _gh_token()
    nsec = _nsec_hex()
    overlay_b64 = _working_tree_overlay_b64()
    if overlay_b64:
        print(f"Overlay: {len(overlay_b64)} bytes b64")

    bootstrap_env = " ".join([
        f"TOLLGATE_RUN_ID={shlex.quote(run_id)}",
        f"TOLLGATE_SUT_BRANCH={shlex.quote(args.branch)}",
        "TOLLGATE_SUT_COMMIT=",
        "TOLLGATE_SUT_PR=",
        "TOLLGATE_ARTIFACT_RUN_ID=blossom",
        f"TOLLGATE_ARTIFACT_REPO=OpenTollGate/tollgate-module-basic-go",
        f"TOLLGATE_PR_REPO=OpenTollGate/tollgate-module-basic-go",
        f"TOLLGATE_SUITE_REF={shlex.quote(suite_ref)}",
        "OPENWRT_VERSION=24.10.1",
        "TOLLGATE_DEPLOY_MODE=framework",
        "BLOSSOM_SERVER=https://blossom.psbt.me",
        "NOSTR_RELAYS=wss://relay.cashu.email,wss://relay1.orangesync.tech",
        "TOLLGATE_BACKEND=go",
        "TOLLGATE_PUBLISH=false",
        "TOLLGATE_QUICK=false",
        "TOLLGATE_SMOKE=false",
        "SKIP_BLOSSOMFS=1",
        "SKIP_VWIFI=1",
        "TOLLGATE_COMPLETE=false",
        "TOLLGATE_MINT=auto",
        "TOLLGATE_PORTAL=builtin",
        "TOLLGATE_KEEP_VM_ON_FAILURE=true",
        "TOLLGATE_GCP_PROJECT=tollgate-test-lab",
        "TOLLGATE_GCP_ZONE=shc",
        f"TOLLGATE_VM_NAME=bake",
        "TOLLGATE_CLOUD=shc",
        f"TOLLGATE_SERVICE_ID={args.service_id}",
        f"SHC_API_KEY={shlex.quote(os.environ.get('SHC_API_KEY', ''))}",
        f"GH_TOKEN={shlex.quote(token)}",
        f"BOT_NSEC_HEX={shlex.quote(nsec)}",
        f"VIRT_LAB_PASSWORD={VIRT_LAB_PASSWORD}",
        "NSEC_FILE=/root/nsec",
        "HOME=/root",
    ])

    bootstrap_script = _build_bootstrap_script(
        bootstrap_env=bootstrap_env,
        overlay_b64=overlay_b64,
        test_dir=TEST_DIR,
        suite_repo_url=SUITE_REPO_URL,
        lease_minutes=480,  # 8h lease for baking
    )

    # Upload + launch
    script_path = "/tmp/tollgate-shc-worker.sh"
    print("Uploading bootstrap script...")
    rc, _, err = _ssh(ip, f"cat > {script_path} && chmod +x {script_path}",
                      user=user, timeout=30, stdin=bootstrap_script)
    if rc != 0:
        print(f"Upload failed: {err}")
        return 1

    # Clear old status
    _ssh(ip, "sudo rm -f /tmp/tollgate-status /tmp/tollgate-done /var/log/tollgate-run.log", user=user)

    print("Launching bootstrap (this takes ~10-15 min)...")
    _ssh(ip, f"nohup sudo bash {script_path} > /dev/null 2>&1 & echo LAUNCHED", user=user)

    # Monitor
    print("Monitoring bootstrap progress...")
    last_step = ""
    deadline = time.time() + 1800  # 30 min max
    while time.time() < deadline:
        time.sleep(15)
        rc, out, _ = _ssh(ip, "cat /tmp/tollgate-status 2>/dev/null; echo '---'; tail -3 /var/log/tollgate-run.log 2>/dev/null", user=user, timeout=10)
        if "BOOTSTRAP_DONE" in out:
            print("\n=== BAKE COMPLETE ===")
            break
        if "BOOTSTRAP_FAILED" in out:
            failed_step = out.split("BOOTSTRAP_FAILED at step")[-1].split("\n")[0] if "BOOTSTRAP_FAILED" in out else "?"
            print(f"\n=== BAKE FAILED at step {failed_step} ===")
            print(out[-500:])
            return 1
        # Progress
        for line in out.splitlines():
            if line.startswith("[") and "done" in line and line != last_step:
                last_step = line
                print(f"  {line}")
    else:
        print("\n=== BAKE TIMED OUT (30 min) ===")
        return 1

    # Verify baked state
    print("\nVerifying baked state...")
    rc, out, _ = _ssh(ip,
        "test -f /opt/tollgate-venv/bin/python3 && echo VENV_OK; "
        "test -f /root/tollgate-virtual-lab/images/openwrt-base.qcow2 && echo OWRT_OK; "
        "test -f /root/tollgate-virtual-lab/images/debian-12-base.qcow2 && echo DEBIAN_OK; "
        "test -x /opt/cdk-mintd/cdk-mintd && echo CDK_OK; "
        "test -x /usr/local/bin/nak && echo NAK_OK; "
        "test -e /dev/kvm && echo KVM_OK",
        user=user, timeout=15)
    print(out.strip())
    checks = [l for l in out.splitlines() if l.endswith("_OK")]
    if len(checks) < 6:
        print(f"WARNING: only {len(checks)}/6 checks passed — bake may be incomplete")

    # Create snapshot
    if not args.no_snapshot:
        print(f"\nCreating snapshot '{args.snapshot_name}' on VM {args.service_id}...")
        r = subprocess.run(["shc", "snapshot-create", str(args.service_id), "--name", args.snapshot_name],
                           capture_output=True, text=True, timeout=60)
        print(r.stdout[:300] if r.stdout else r.stderr[:300])
        if r.returncode != 0:
            print("Snapshot creation failed (may still be queued)")
        else:
            # Wait for snapshot to complete
            print("Waiting for snapshot to complete...", end=" ", flush=True)
            for _ in range(60):
                time.sleep(10)
                r2 = subprocess.run(["shc", "snapshots", str(args.service_id)],
                                    capture_output=True, text=True, timeout=30)
                if args.snapshot_name in (r2.stdout or "") or "completed" in (r2.stdout or "").lower():
                    print("DONE")
                    print(r2.stdout[:400])
                    break
            else:
                print("snapshot still processing (check later with: shc snapshots %d)" % args.service_id)

    print(f"\n=== BAKE SUMMARY ===")
    print(f"VM: {args.service_id} @ {ip}")
    print(f"Checks: {len(checks)}/6 passed")
    if not args.no_snapshot:
        print(f"Snapshot: {args.snapshot_name}")
    print(f"To run tests on this baked VM:")
    print(f"  python3 scripts/shc-run-baked.py --service-id {args.service_id} --ip {ip} --branch <branch> --pr <N>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
