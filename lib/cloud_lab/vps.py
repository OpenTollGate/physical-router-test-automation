"""VPS-based cloud lab provider using SSH and QEMU on a persistent server."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.cloud_lab.artifact import ensure_target_artifact
from lib.cloud_lab.constants import (
    SUITE_REPO,
    VPS_HOST,
    VPS_RUN_LOCK,
    VPS_SSH_KEY,
    VPS_USER,
    VPS_WORKER_CONFIG,
)
from lib.cloud_lab.gcp import _gh_token, _sanitize_vm_name, _suite_ref, _working_tree_overlay
from lib.cloud_lab.provider import CloudProvider
from lib.cloud_lab.resolve import RunTarget

_VPS_SUITE_FILES = [
    "lib/cloud_lab/__init__.py",
    "lib/cloud_lab/constants.py",
    "lib/cloud_lab/gcp.py",
    "lib/cloud_lab/provider.py",
    "lib/cloud_lab/vps.py",
    "lib/cloud_lab/worker.py",
    "scripts/cloud-lab.py",
]


def _build_suite_overlay() -> str:
    """Build a base64 tar.gz of the cloud_lab module files for VPS overlay."""
    import base64
    import io
    import tarfile

    repo_dir = Path(__file__).resolve().parents[2]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in _VPS_SUITE_FILES:
            full = repo_dir / rel
            if full.is_file():
                tar.add(full, arcname=rel)
    return base64.b64encode(buf.getvalue()).decode()


def _ssh(
    cmd: str,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    args = [
        "ssh",
        "-i", os.path.expanduser(VPS_SSH_KEY),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        f"{VPS_USER}@{VPS_HOST}",
        cmd,
    ]
    r = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and r.returncode != 0:
        raise RuntimeError(
            f"SSH command failed (rc={r.returncode}): {cmd[:200]}\n"
            f"stderr: {(r.stderr or '').strip()[:500]}"
        )
    return r


def _scp_to(local: str, remote: str) -> subprocess.CompletedProcess[str]:
    key = os.path.expanduser(VPS_SSH_KEY)
    args = [
        "scp", "-O",
        "-i", key,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        local,
        f"{VPS_USER}@{VPS_HOST}:{remote}",
    ]
    return subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)


def _require_vps_host() -> None:
    if not VPS_HOST:
        print(
            "ERROR: TOLLGATE_VPS_HOST is not set. "
            "Set it to your VPS IP or hostname.",
            file=sys.stderr,
        )
        sys.exit(1)


class VPSProvider(CloudProvider):
    """Persistent VPS provider using SSH for command execution.

    Unlike GCP, the VPS is always running. Tests execute inside QEMU VMs
    on the VPS. No VM creation/deletion — just process management.
    """

    @property
    def name(self) -> str:
        return "vps"

    def vm_up(self, name: str, **kwargs: Any) -> int:
        _require_vps_host()
        print(f"Checking VPS connectivity ({VPS_USER}@{VPS_HOST})...")
        r = _ssh("echo OK", timeout=15, check=False)
        if r.returncode != 0:
            print(f"ERROR: Cannot reach VPS at {VPS_HOST}", file=sys.stderr)
            return 1
        print(f"VPS {VPS_HOST} is reachable")
        r = _ssh("test -x /usr/bin/qemu-system-x86_64 && echo QEMU_OK", timeout=15, check=False)
        if "QEMU_OK" not in r.stdout:
            print("WARNING: qemu-system-x86_64 not found on VPS. Run the cloud_lab_runner Ansible role first.", file=sys.stderr)
        return 0

    def vm_down(self, name: str, **kwargs: Any) -> int:
        _require_vps_host()
        print("Stopping QEMU VMs on VPS...")
        _ssh(
            "killall -9 qemu-system-x86_64 2>/dev/null || true; "
            f"rm -f {VPS_RUN_LOCK} {VPS_WORKER_CONFIG}",
            timeout=30,
            check=False,
        )
        print("QEMU VMs stopped")
        return 0

    def vm_status(self, name: str, **kwargs: Any) -> str | None:
        _require_vps_host()
        r = _ssh(
            f"if [ -f {VPS_RUN_LOCK} ]; then "
            f"cat {VPS_RUN_LOCK}; "
            "else echo IDLE; fi",
            timeout=15,
            check=False,
        )
        if r.returncode != 0:
            return None
        status = r.stdout.strip()
        return status if status else None

    def vm_external_ip(self, name: str, **kwargs: Any) -> str | None:
        return VPS_HOST if VPS_HOST else None

    def submit_run(
        self,
        target: RunTarget,
        *,
        publish: bool = False,
        artifact_timeout_s: int = 1800,
        reseller_scenarios: bool = False,
        secondary_router_host: str = "",
        secondary_router_port: str = "",
        keep_vm_on_failure: bool = False,
        **kwargs: Any,
    ) -> dict[str, str]:
        _require_vps_host()

        print(f"Waiting for CI artifact ({target.repo}@{target.branch}, arch=x86_64)...")
        artifact_run_id = ensure_target_artifact(target, timeout_s=artifact_timeout_s)
        print(f"Artifact ready: run {artifact_run_id}")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        short = (target.sut_commit or target.branch)[:7].replace("/", "-")
        run_id = f"{timestamp}-{short}"
        suite_ref = _suite_ref()
        if suite_ref != "main":
            local_sha = suite_ref
            suite_ref = "main"
            print(f"Note: suite_ref {local_sha[:7]} not reachable from VPS, using 'main'")
        token = _gh_token()

        r = _ssh(f"if [ -f {VPS_RUN_LOCK} ]; then cat {VPS_RUN_LOCK}; fi", timeout=15, check=False)
        if r.stdout.strip():
            existing = r.stdout.strip()
            print(f"ERROR: VPS already has an active run: {existing}", file=sys.stderr)
            print(f"Wait for it to finish or run: ./scripts/cloud-lab.py --provider vps down", file=sys.stderr)
            return {"run_id": run_id, "error": "VPS busy"}

        config = {
            "run_id": run_id,
            "sut_branch": target.branch,
            "sut_commit": target.sut_commit or "",
            "sut_pr": target.pr or "",
            "artifact_run_id": artifact_run_id,
            "artifact_repo": target.repo,
            "suite_ref": suite_ref,
            "backend": target.backend,
            "reseller_scenarios": reseller_scenarios,
            "secondary_router_host": secondary_router_host,
            "secondary_router_port": secondary_router_port,
            "keep_vm_on_failure": keep_vm_on_failure,
            "publish": publish,
            "provider": "vps",
            "gh_token": token,
        }

        overlay_b64 = _working_tree_overlay()
        suite_overlay_b64 = _build_suite_overlay()

        config_json = json.dumps(config, indent=2)
        local_config = Path(f"/tmp/tollgate-vps-config-{run_id}.json")
        local_config.write_text(config_json)

        print(f"Uploading worker config to VPS...")
        r = _scp_to(str(local_config), VPS_WORKER_CONFIG)
        if r.returncode != 0:
            local_config.unlink(missing_ok=True)
            raise RuntimeError(f"Failed to upload config: {r.stderr.strip()}")
        local_config.unlink(missing_ok=True)

        combined_overlay = suite_overlay_b64
        if overlay_b64:
            print("Including local working tree overlay")

        overlay_local = Path(f"/tmp/tollgate-vps-overlay-{run_id}.b64")
        overlay_local.write_text(combined_overlay)
        remote_overlay = "/tmp/tollgate-suite-overlay.tar.gz.b64"
        r = _scp_to(str(overlay_local), remote_overlay)
        overlay_local.unlink(missing_ok=True)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to upload overlay: {r.stderr.strip()}")
        overlay_setup = (
            "base64 -d /tmp/tollgate-suite-overlay.tar.gz.b64 > /tmp/tollgate-suite-overlay.tar.gz && "
            "tar xzf /tmp/tollgate-suite-overlay.tar.gz -C /opt/tollgate-test && "
            "echo 'Applied suite overlay' && "
        )

        worker_cmd = (
            f"echo '{run_id}' > {VPS_RUN_LOCK} && "
            f"export GH_TOKEN={shlex.quote(token)} && "
            "cd /opt/tollgate-test && "
            "if [ ! -d .git ]; then "
            f"git clone --depth 50 https://github.com/{SUITE_REPO}.git .; "
            "fi && "
            f"git fetch origin && git checkout {shlex.quote(suite_ref)} || git checkout main && "
            f"{overlay_setup}"
            "if [ -d /opt/tollgate-venv ]; then "
            "/opt/tollgate-venv/bin/pip install -q -r requirements.txt 2>/dev/null || true; "
            "else "
            "python3 -m venv /opt/tollgate-venv && "
            "/opt/tollgate-venv/bin/pip install -q -r requirements.txt; "
            "fi && "
            f"/opt/tollgate-venv/bin/python3 -m lib.cloud_lab.worker --from-file {VPS_WORKER_CONFIG} "
            f">> /var/log/tollgate-run.log 2>&1; "
            f"EXIT=$?; "
            f"rm -f {VPS_RUN_LOCK} {VPS_WORKER_CONFIG}; "
            "exit $EXIT"
        )

        print(f"Starting worker on VPS (run_id={run_id})...")
        nohup_cmd = (
            f"nohup bash -c {shlex.quote(worker_cmd)} "
            f">> /var/log/tollgate-run.log 2>&1 & echo $!"
        )
        r = _ssh(nohup_cmd, timeout=30, check=False)
        if r.returncode != 0:
            _ssh(f"rm -f {VPS_RUN_LOCK} {VPS_WORKER_CONFIG}", timeout=10, check=False)
            raise RuntimeError(f"Failed to start worker on VPS: {r.stderr.strip()}")

        pid = r.stdout.strip()
        print(f"Worker started on VPS (pid={pid})")

        log_hint = f"ssh -i {VPS_SSH_KEY} {VPS_USER}@{VPS_HOST} 'tail -f /var/log/tollgate-run.log'"
        return {
            "run_id": run_id,
            "vm_name": VPS_HOST,
            "project": "",
            "zone": "",
            "artifact_run_id": artifact_run_id,
            "suite_ref": suite_ref,
            "log_hint": log_hint,
            "pid": pid,
        }

    def status_run(self, run_id: str, **kwargs: Any) -> int:
        _require_vps_host()
        r = _ssh(
            f"if [ -f {VPS_RUN_LOCK} ]; then "
            f"LOCK=$(cat {VPS_RUN_LOCK}); "
            f"echo \"Run: $LOCK\"; "
            f"if [ \"$LOCK\" = '{run_id}' ]; then echo 'Status: RUNNING'; "
            f"else echo 'Status: OTHER ($LOCK)'; fi; "
            "else echo 'Status: IDLE'; fi",
            timeout=15,
            check=False,
        )
        print(r.stdout.strip())
        if run_id in r.stdout:
            print(f"Logs: ssh -i {VPS_SSH_KEY} {VPS_USER}@{VPS_HOST} 'tail -f /var/log/tollgate-run.log'")
        return 0

    def cleanup_stale(self, max_age_hours: int = 2, **kwargs: Any) -> int:
        _require_vps_host()
        r = _ssh(
            "killall -9 qemu-system-x86_64 2>/dev/null || true; "
            f"rm -f {VPS_RUN_LOCK} {VPS_WORKER_CONFIG}",
            timeout=30,
            check=False,
        )
        if r.returncode != 0:
            return 1
        print("Cleaned up stale QEMU processes and locks on VPS")
        return 0

    def cleanup_all(self, **kwargs: Any) -> int:
        return self.cleanup_stale(max_age_hours=0)

    def ssh_command(self, name: str, user: str = "root") -> list[str]:
        actual_user = user if user != "root" else VPS_USER
        return [
            "ssh", "-i", os.path.expanduser(VPS_SSH_KEY),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            f"{actual_user}@{VPS_HOST}",
        ]
