"""GCP VM lifecycle for fire-and-forget cloud lab runs."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

from lib.cloud_lab.artifact import ensure_target_artifact
from lib.cloud_lab.constants import (
    DEFAULT_DISK_SIZE_GB,
    DEFAULT_MACHINE_TYPE,
    DEFAULT_ZONE,
    FIREWALL_RULE_SSH,
    SNAPSHOT_NAME,
    SUITE_REPO,
)
from lib.cloud_lab.resolve import RunTarget


def _run_gcloud(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    markers = (
        "NameResolutionError", "Failed to resolve", "ConnectionError",
        "Max retries exceeded", "Network is unreachable", "timed out",
    )
    last = subprocess.CompletedProcess(args=["gcloud"], returncode=1, stdout="", stderr="")
    for attempt in range(1, 4):
        last = subprocess.run(
            ["gcloud", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        combined = f"{last.stderr}\n{last.stdout}"
        if last.returncode == 0 or not any(m in combined for m in markers):
            return last
        if attempt < 3:
            print(f"WARNING: transient gcloud failure, retrying ({attempt}/3): {last.stderr[:200]}", file=sys.stderr)
            time.sleep(5 * attempt)
    return last


def vm_status(project: str, zone: str, vm_name: str) -> str | None:
    r = _run_gcloud([
        "compute", "instances", "describe", vm_name,
        f"--project={project}", f"--zone={zone}", "--format=json",
    ], timeout=30)
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
        status = data.get("status")
        return status if isinstance(status, str) else None
    except json.JSONDecodeError:
        return None


def vm_external_ip(project: str, zone: str, vm_name: str) -> str | None:
    r = _run_gcloud([
        "compute", "instances", "describe", vm_name,
        f"--project={project}", f"--zone={zone}", "--format=json",
    ], timeout=30)
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
        for iface in data.get("networkInterfaces", []):
            for ac in iface.get("accessConfigs", []):
                ip = ac.get("natIP")
                if ip:
                    return ip
    except (json.JSONDecodeError, AttributeError):
        return None
    return None


def vm_up(vm_name: str, zone: str = DEFAULT_ZONE, machine_type: str = DEFAULT_MACHINE_TYPE,
          disk_size_gb: int = DEFAULT_DISK_SIZE_GB) -> int:
    from lib.cloud_lab.constants import VM_NAME
    vm_name = vm_name or VM_NAME
    project = get_project()
    status = vm_status(project, zone, vm_name)
    if status == "RUNNING":
        ip = vm_external_ip(project, zone, vm_name)
        print(f"VM {vm_name} already RUNNING at {ip}")
        return 0
    if status and status != "RUNNING":
        print(f"VM {vm_name} exists ({status}), starting...")
        r = _run_gcloud(["compute", "instances", "start", vm_name, f"--project={project}", f"--zone={zone}"], timeout=120)
        return 0 if r.returncode == 0 else 1
    print(f"Creating VM from snapshot {SNAPSHOT_NAME}...")
    r = _run_gcloud([
        "compute", "instances", "create", vm_name,
        f"--project={project}", f"--zone={zone}",
        f"--machine-type={machine_type}",
        f"--source-snapshot={SNAPSHOT_NAME}",
        f"--boot-disk-size={disk_size_gb}GB",
        "--enable-nested-virtualization",
        "--min-cpu-platform=Intel Cascade Lake",
        "--tags=tollgate-runner",
    ], timeout=300)
    if r.returncode != 0 and vm_status(project, zone, vm_name) != "RUNNING":
        print(f"ERROR: {r.stderr}", file=sys.stderr)
        return 1
    ensure_firewall_rules(project)
    print(f"VM {vm_name} created")
    return 0


def vm_down(vm_name: str, zone: str = DEFAULT_ZONE) -> int:
    project = get_project()
    r = _run_gcloud([
        "compute", "instances", "delete", vm_name,
        f"--project={project}", f"--zone={zone}", "--delete-disks=all", "--quiet",
    ], timeout=120)
    return r.returncode


def get_project() -> str:
    r = _run_gcloud(["config", "get-value", "project"], timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        print("ERROR: No GCP project set. Run: gcloud config set project <PROJECT_ID>", file=sys.stderr)
        sys.exit(1)
    return r.stdout.strip()


def ensure_firewall_rules(project: str) -> None:
    r = _run_gcloud(["compute", "firewall-rules", "describe", FIREWALL_RULE_SSH, f"--project={project}"], timeout=30)
    if r.returncode == 0:
        return
    _run_gcloud([
        "compute", "firewall-rules", "create", FIREWALL_RULE_SSH,
        f"--project={project}", "--allow=tcp:22", "--source-ranges=0.0.0.0/0",
        "--description=Allow SSH for TollGate test runner",
    ], timeout=60)


def _sanitize_vm_name(run_id: str) -> str:
    safe = re.sub(r"[^a-z0-9-]", "-", run_id.lower())[:50].strip("-")
    return f"tollgate-run-{safe}"


def _suite_ref() -> str:
    repo_dir = Path(__file__).resolve().parents[2]
    r = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=15,
        check=False,
    )
    local = r.stdout.strip() if r.returncode == 0 else ""
    if not local:
        return "main"
    r2 = subprocess.run(
        ["git", "-C", str(repo_dir), "branch", "-r", "--contains", local],
        capture_output=True, text=True, timeout=15,
        check=False,
    )
    if r2.returncode != 0 or not r2.stdout.strip():
        print(f"WARNING: HEAD ({local[:7]}) not on any remote branch — falling back to 'main'. "
              "Push your changes first for the VM to use them.", file=sys.stderr)
        return "main"
    return local


def _gh_token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15, check=False)
        if r.returncode == 0 and r.stdout.strip():
            token = r.stdout.strip()
    if not token:
        raise RuntimeError("GH_TOKEN or GITHUB_TOKEN required (or run gh auth login)")
    return token


def _build_startup_script() -> str:
    return textwrap.dedent(f"""\
        #!/bin/bash
        set -euo pipefail
        exec >> /var/log/tollgate-run.log 2>&1
        echo "=== TollGate cloud worker started $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

        export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        export GH_TOKEN=$(curl -sf -H "Metadata-Flavor: Google" \\
            http://metadata.google.internal/computeMetadata/v1/instance/attributes/tollgate-gh-token)

        cleanup() {{
            ZONE=$(curl -sf -H "Metadata-Flavor: Google" \\
                http://metadata.google.internal/computeMetadata/v1/instance/attributes/tollgate-zone)
            PROJECT=$(curl -sf -H "Metadata-Flavor: Google" \\
                http://metadata.google.internal/computeMetadata/v1/instance/attributes/tollgate-project)
            NAME=$(curl -sf -H "Metadata-Flavor: Google" \\
                http://metadata.google.internal/computeMetadata/v1/instance/name)
            echo "=== Teardown $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
            gcloud compute instances delete "$NAME" --project="$PROJECT" --zone="$ZONE" \\
                --delete-disks=all --quiet 2>/dev/null || true
        }}
        trap cleanup EXIT

        apt-get update -qq 2>/dev/null || true
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv git curl sshpass \\
            qemu-utils iproute2 iptables 2>/dev/null || true

        if ! command -v gcloud >/dev/null; then
            echo "Installing Google Cloud SDK for self-delete..."
            apt-get install -y -qq apt-transport-https ca-certificates gnupg 2>/dev/null || true
            echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \\
                > /etc/apt/sources.list.d/google-cloud-sdk.list 2>/dev/null || true
            curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \\
                | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg 2>/dev/null || true
            apt-get update -qq && apt-get install -y -qq google-cloud-cli 2>/dev/null || true
        fi

        cd /opt
        rm -rf tollgate-test
        git clone --depth 50 https://github.com/{SUITE_REPO}.git tollgate-test
        cd tollgate-test
        SUITE_REF=$(curl -sf -H "Metadata-Flavor: Google" \\
            http://metadata.google.internal/computeMetadata/v1/instance/attributes/tollgate-suite-ref)
        git checkout "$SUITE_REF"

        if [ -d /opt/tollgate-venv ]; then
            /opt/tollgate-venv/bin/pip install -q -r requirements.txt 2>/dev/null || true
        else
            python3 -m venv /opt/tollgate-venv || true
            /opt/tollgate-venv/bin/pip install -q -r requirements.txt || true
        fi
        /opt/tollgate-venv/bin/python3 -m lib.cloud_lab.worker --from-metadata
        echo "=== Worker exited with code $? ==="
    """)


def submit_run(
    target: RunTarget,
    *,
    zone: str = DEFAULT_ZONE,
    publish: bool = False,
    artifact_timeout_s: int = 1800,
    machine_type: str = DEFAULT_MACHINE_TYPE,
    disk_size_gb: int = DEFAULT_DISK_SIZE_GB,
) -> dict[str, str]:
    """Pre-flight artifact check, then create fire-and-forget GCP VM. Returns run metadata."""
    project = get_project()
    print(f"Waiting for CI artifact ({target.repo}@{target.branch}, arch=x86_64)...")
    artifact_run_id = ensure_target_artifact(target, timeout_s=artifact_timeout_s)
    print(f"Artifact ready: run {artifact_run_id}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = (target.sut_commit or target.branch)[:7]
    run_id = f"{timestamp}-{short}"
    vm_name = _sanitize_vm_name(run_id)
    suite_ref = _suite_ref()
    token = _gh_token()

    startup_script = _build_startup_script()
    script_path = Path(f"/tmp/tollgate-startup-{run_id}.sh")
    script_path.write_text(startup_script)

    metadata = {
        "tollgate-run-id": run_id,
        "tollgate-pr": target.pr or "",
        "tollgate-sut-commit": target.sut_commit or "",
        "tollgate-sut-branch": target.branch,
        "tollgate-artifact-run-id": artifact_run_id,
        "tollgate-artifact-repo": target.repo,
        "tollgate-suite-ref": suite_ref,
        "tollgate-backend": target.backend,
        "tollgate-publish": "true" if publish else "false",
        "tollgate-project": project,
        "tollgate-zone": zone,
        "tollgate-vm-name": vm_name,
        "tollgate-gh-token": token,
    }
    metadata_payload = ",".join(f"{k}={v}" for k, v in metadata.items())

    ensure_firewall_rules(project)
    print(f"Creating VM {vm_name} from snapshot {SNAPSHOT_NAME}...")
    r = _run_gcloud([
        "compute", "instances", "create", vm_name,
        f"--project={project}",
        f"--zone={zone}",
        f"--machine-type={machine_type}",
        f"--source-snapshot={SNAPSHOT_NAME}",
        f"--boot-disk-size={disk_size_gb}GB",
        "--enable-nested-virtualization",
        "--min-cpu-platform=Intel Cascade Lake",
        "--tags=tollgate-runner,tollgate-run",
        "--labels=tollgate_run=true",
        "--scopes=compute-rw,storage-rw",
        f"--metadata={metadata_payload}",
        f"--metadata-from-file=startup-script={script_path}",
    ], timeout=300)
    script_path.unlink(missing_ok=True)

    if r.returncode != 0:
        raise RuntimeError(f"Failed to create VM: {r.stderr.strip() or r.stdout.strip()}")

    log_hint = (
        f"gcloud compute ssh {vm_name} --project={project} --zone={zone} "
        f"--command='tail -f /var/log/tollgate-run.log'"
    )
    info = {
        "run_id": run_id,
        "vm_name": vm_name,
        "project": project,
        "zone": zone,
        "artifact_run_id": artifact_run_id,
        "suite_ref": suite_ref,
        "log_hint": log_hint,
    }
    return info


def status_run(run_id: str, zone: str = DEFAULT_ZONE) -> int:
    project = get_project()
    r = _run_gcloud([
        "compute", "instances", "list",
        f"--project={project}",
        f"--filter=metadata.tollgate-run-id={run_id}",
        "--format=json",
    ], timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        print(f"No VM found for run_id={run_id}")
        print("If the run finished, check https://tests.tollgate.me/")
        return 1
    try:
        instances = json.loads(r.stdout)
    except json.JSONDecodeError:
        print(r.stdout)
        return 1
    if not instances:
        print(f"Run {run_id}: VM deleted (likely finished). Check https://tests.tollgate.me/")
        return 0
    inst = instances[0]
    name = inst.get("name", "?")
    status = inst.get("status", "?")
    print(f"Run: {run_id}")
    print(f"VM:  {name} ({status})")
    for iface in inst.get("networkInterfaces", []):
        for ac in iface.get("accessConfigs", []):
            if ac.get("natIP"):
                print(f"IP:  {ac['natIP']}")
    print(f"Logs: gcloud compute ssh {name} --project={project} --zone={zone} --command='tail -f /var/log/tollgate-run.log'")
    return 0


def cleanup_stale(zone: str = DEFAULT_ZONE, max_age_hours: int = 2) -> int:
    project = get_project()
    r = _run_gcloud([
        "compute", "instances", "list",
        f"--project={project}",
        f"--filter=labels.tollgate_run=true AND status=RUNNING",
        "--format=json",
    ], timeout=60)
    if r.returncode != 0:
        print(f"ERROR: {r.stderr}", file=sys.stderr)
        return 1
    try:
        instances = json.loads(r.stdout) if r.stdout.strip() else []
    except json.JSONDecodeError:
        instances = []
    cutoff = time.time() - max_age_hours * 3600
    deleted = 0
    for inst in instances:
        name = inst.get("name")
        creation = inst.get("creationTimestamp", "")
        if not name:
            continue
        try:
            created = datetime.fromisoformat(creation.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if created > cutoff:
            continue
        print(f"Deleting stale VM {name} (created {creation})...")
        dr = _run_gcloud([
            "compute", "instances", "delete", name,
            f"--project={project}", f"--zone={zone}",
            "--delete-disks=all", "--quiet",
        ], timeout=120)
        if dr.returncode == 0:
            deleted += 1
    print(f"Deleted {deleted} stale VM(s)")
    return 0
