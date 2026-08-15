"""Pulumi-based VM provisioning for TollGate test runs.

Replaces both gcp.py and shc_submit.py with a single code path.
Calls `pulumi up` to create an SHC (or GCP) VM, then bootstraps the
worker pipeline via SSH — same bootstrap script as the legacy SHC path.
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import time
from io import BytesIO
from pathlib import Path
from tarfile import TarFile

from lib.cloud_lab.constants import SUITE_REPO, VIRT_LAB_PASSWORD
from lib.cloud_lab.shc_submit import (
    _build_bootstrap_script,
    _generate_run_id,
    _resolve_artifact_run,
    _wait_for_ssh,
)
from lib.cloud_lab.worker.shell import log

PULUMI_STACK_DIR = Path(__file__).resolve().parent.parent / "pulumi"


def submit_run_pulumi(
    target,
    *,
    publish: bool = False,
    artifact_timeout_s: int = 1800,
    quick: bool = False,
    smoke: bool = False,
    complete: bool = False,
    mint: str = "auto",
    portal: str = "builtin",
    keep_vm_on_failure: bool = False,
    lease_minutes: int = 90,
    two_router: bool = False,
    router_count: int = 0,
    provider: str = "shc",
    tier: str = "standard",
) -> dict[str, str]:
    """Provision a VM via Pulumi, bootstrap the worker, return run info."""

    run_id = _generate_run_id(target)
    suite_ref = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[2],
    ).stdout.strip()

    token = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
    nsec = os.environ.get("BOT_NSEC_HEX", "")
    artifact_run_id = _resolve_artifact_run(target, token, artifact_timeout_s)

    overlay_b64 = _working_tree_overlay_b64()

    log.info("[pulumi] Running pulumi up on stack 'dev' (provider=%s)...", provider)
    stack_outputs = _pulumi_up(provider, tier)

    ip = stack_outputs.get("ip", "")
    hostname = stack_outputs.get("hostname", "")
    service_id = stack_outputs.get("service_id", "")
    os_user = stack_outputs.get("os_user", "debian")

    if not ip:
        raise RuntimeError("[pulumi] No IP in stack outputs")

    log.info("[pulumi] VM ready: %s @ %s (service_id=%s)", hostname, ip, service_id)

    ssh_key_path = os.path.expanduser("~/.ssh/id_rsa")
    if not os.path.isfile(ssh_key_path):
        ssh_key_path = os.path.expanduser("~/.ssh/id_ed25519")

    ssh_base = [
        "ssh", "-i", ssh_key_path,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
    ]
    ssh_target = f"{os_user}@{ip}"

    _wait_for_ssh(ssh_base, ssh_target, timeout=300, sshpass_password=VIRT_LAB_PASSWORD)

    bootstrap_env = "\n".join([
        f"TOLLGATE_RUN_ID={run_id}",
        f"TOLLGATE_SUT_BRANCH={target.branch}",
        f"TOLLGATE_SUT_COMMIT={target.commit}",
        f"TOLLGATE_SUT_PR={target.pr}",
        f"TOLLGATE_ARTIFACT_REPO={target.repo}",
        f"TOLLGATE_ARTIFACT_RUN_ID={artifact_run_id}",
        f"TOLLGATE_SUITE_REF={suite_ref}",
        "TOLLGATE_BACKEND=go",
        f"TOLLGATE_PUBLISH={'true' if publish else 'false'}",
        f"TOLLGATE_TWO_ROUTER={'true' if two_router else 'false'}",
        f"TOLLGATE_ROUTER_COUNT={router_count or 0}",
        "TOLLGATE_VIRTUAL_LAB=1",
        "BLOSSOM_SERVER=https://blossom.psbt.me",
        "TOLLGATE_CLOUD=shc",
        f"TOLLGATE_SERVICE_ID={service_id}",
        f"SHC_API_KEY={shlex.quote(os.environ.get('SHC_API_KEY', ''))}",
        f"GH_TOKEN={shlex.quote(token)}",
        f"BOT_NSEC_HEX={shlex.quote(nsec)}",
        f"VIRT_LAB_PASSWORD={VIRT_LAB_PASSWORD}",
        "NSEC_FILE=/root/nsec",
        "HOME=/root",
    ])

    script = _build_bootstrap_script(
        bootstrap_env=bootstrap_env,
        overlay_b64=overlay_b64,
        skip_blossomfs=quick or smoke,
    )

    script_path = f"/tmp/tollgate-bootstrap-{run_id}.sh"
    _scp_script(ssh_base, ssh_target, script, script_path)

    log.info("[pulumi] Launching worker pipeline...")
    try:
        subprocess.run(
            [*ssh_base, ssh_target, f"sudo bash {script_path}"],
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass  # bootstrap script detaches; the short timeout is intentional

    log_hint = f"ssh {os_user}@{ip} 'tail -f /var/log/tollgate-run.log'"

    return {
        "run_id": run_id,
        "vm_name": hostname,
        "service_id": service_id,
        "ip": ip,
        "log_hint": log_hint,
        "suite_ref": suite_ref,
        "artifact_run_id": artifact_run_id,
    }


def _pulumi_up(provider: str, tier: str) -> dict[str, str]:
    """Run `pulumi up` and return stack outputs as a dict."""
    env = os.environ.copy()
    env["PULUMI_SKIP_UPDATE_CHECK"] = "1"

    size_map = {
        ("shc", "starter"): "dev-1c-4gb",
        ("shc", "standard"): "dev-2c-8gb",
        ("gcp", "starter"): "n2-standard-2",
        ("gcp", "standard"): "n2-standard-4",
    }
    size = size_map.get((provider, tier), "nvme-2c-8gb")

    subprocess.run(
        ["pulumi", "config", "set", "size", size],
        cwd=str(PULUMI_STACK_DIR),
        env=env, check=True, capture_output=True,
    )

    result = subprocess.run(
        ["pulumi", "up", "--yes", "--skip-preview"],
        cwd=str(PULUMI_STACK_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pulumi up failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout[-500:]}\n"
            f"stderr: {result.stderr[-500:]}"
        )

    stack_result = subprocess.run(
        ["pulumi", "stack", "output", "--json"],
        cwd=str(PULUMI_STACK_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if stack_result.returncode != 0:
        raise RuntimeError(f"pulumi stack output failed: {stack_result.stderr}")

    outputs = json.loads(stack_result.stdout)
    return {k: str(v) for k, v in outputs.items()}


def _scp_script(ssh_base: list[str], ssh_target: str, script: str, remote_path: str) -> None:
    import subprocess as sp
    proc = sp.run(
        [*ssh_base, ssh_target, f"cat > {remote_path} && chmod +x {remote_path}"],
        input=script,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to upload bootstrap script: rc={proc.returncode}")


def _working_tree_overlay_b64() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    tracked = subprocess.run(
        ["git", "diff", "--name-only"], capture_output=True, text=True, cwd=repo_root,
    ).stdout.strip().split("\n")
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True, cwd=repo_root,
    ).stdout.strip().split("\n")
    changes = [c for c in tracked + untracked if c and not c.startswith(".omo/") and not c.startswith(".playwright-mcp/")]
    if not changes:
        return ""
    buf = BytesIO()
    import tarfile
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in changes:
            full = repo_root / f
            if full.exists() and full.is_file():
                tar.add(str(full), arcname=f)
    return base64.b64encode(buf.getvalue()).decode()


def destroy_pulumi_vm() -> None:
    """Destroy the Pulumi-managed VM."""
    env = os.environ.copy()
    env["PULUMI_SKIP_UPDATE_CHECK"] = "1"
    subprocess.run(
        ["pulumi", "destroy", "--yes", "--skip-preview"],
        cwd=str(PULUMI_STACK_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
