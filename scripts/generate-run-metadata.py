#!/usr/bin/env python3
import subprocess
import json
import os
import argparse
import sys
from datetime import datetime, timezone

TOLLGATE_SSH_KEY = os.environ.get("TOLLGATE_SSH_KEY", f"{os.environ.get('HOME')}/.ssh/id_ed25519")
ROUTER_INVENTORY_PATH = os.environ.get(
    "TOLLGATE_ROUTER_INVENTORY",
    os.path.join(os.path.dirname(__file__), "..", "config", "routers.json"),
)


def load_router_inventory():
    if not os.path.isfile(ROUTER_INVENTORY_PATH):
        return None
    with open(ROUTER_INVENTORY_PATH) as f:
        inventory = json.load(f)
    return inventory.get("routers", {})


def ssh(host, cmd):
    ssh_key = TOLLGATE_SSH_KEY if os.path.isfile(TOLLGATE_SSH_KEY) else None
    ssh_cmd = [
        "ssh", "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
    ]
    if ssh_key:
        ssh_cmd.extend(["-i", ssh_key])
    ssh_cmd.extend([f"root@{host}", cmd])
    result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"SSH failed: {result.stderr.strip()}")
    return result.stdout.strip()


def get_router_info(ip, routers):
    for router_id, router_data in routers.items():
        if router_data.get("sshHost") == ip:
            return {
                "router_id": router_id,
                "router_model": router_data.get("model", "unknown"),
                "router_arch": router_data.get("arch", "aarch64_cortex-a53"),
            }
    return {"router_id": "unknown", "router_model": "unknown", "router_arch": "unknown"}


def query_installed_version(ip):
    short_sha = "unknown"
    installed_version = "unknown"
    build_time = "unknown"
    try:
        version_output = ssh(ip, "echo '{\"command\": \"version\"}' | socat - UNIX-CONNECT:/var/run/tollgate.sock")
        version_data = json.loads(version_output)
        msg = version_data.get("message", "")
        for line in msg.split("\n"):
            line = line.strip()
            if line.startswith("commit:"):
                short_sha = line.split(":", 1)[1].strip()
            elif line.startswith("version:"):
                installed_version = line.split(":", 1)[1].strip()
            elif line.startswith("build_time:"):
                build_time = line.split(":", 1)[1].strip()
    except Exception as e:
        print(f"Warning: Could not get version: {e}", file=sys.stderr)
    return short_sha, installed_version, build_time


def generate_metadata(args):
    routers = load_router_inventory()
    router_info = get_router_info(args.router_ip, routers) if routers else {
        "router_id": "unknown", "router_model": "unknown", "router_arch": "unknown",
    }

    short_sha, installed_version, _ = query_installed_version(args.router_ip)

    test_suite_commit = "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        if result.returncode == 0:
            test_suite_commit = result.stdout.strip()
    except Exception:
        pass

    pr_num = args.pr_number
    branch = args.branch or args.tollgate_branch or "main"
    backend = args.backend or os.environ.get("TOLLGATE_BACKEND", "go")
    client_type = args.client_type or os.environ.get("TOLLGATE_CLIENT_TYPE", "adb")
    viewport = args.viewport or os.environ.get("TOLLGATE_VIEWPORT", "desktop")
    virtual_lab = args.virtual_lab or os.environ.get("TOLLGATE_VIRTUAL_LAB", "false").lower() in ("true", "1", "yes")

    router_model = args.router_model or router_info.get("router_model", "unknown")
    router_arch = args.router_arch or router_info.get("router_arch", "unknown")

    now = datetime.now(timezone.utc)

    metadata = {
        "schema_version": 1,
        "run_id": args.run_id or f"{now.strftime('%Y%m%dT%H%M%SZ')}-{short_sha[:7]}",
        "status": "unknown",
        "started_at": now.isoformat(),
        "finished_at": None,
        "duration_ms": None,
        "test_plan": "pr-smoke",
        "sut": {
            "repo": "OpenTollGate/tollgate-module-basic-go",
            "commit": short_sha if short_sha != "unknown" else None,
            "commit_short": short_sha,
            "branch": branch,
            "pr": pr_num,
            "backend": backend,
            "installed_version": installed_version,
        },
        "test_suite": {
            "repo": "OpenTollGate/physical-router-test-automation",
            "commit": test_suite_commit,
        },
        "lab": {
            "router_id": router_info.get("router_id", "unknown") if not args.router_model else os.environ.get("TOLLGATE_ROUTER_ID", "unknown"),
            "router_model": router_model,
            "router_arch": router_arch,
            "router_ip": args.router_ip,
            "client_type": client_type,
            "viewport": viewport,
            "virtual_lab": virtual_lab,
        },
        "counts": {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "flaky": 0,
        },
        "runners": [],
    }

    return metadata


def main():
    parser = argparse.ArgumentParser(description="Generate run metadata for router tests")
    parser.add_argument("--router-ip", required=True, help="Router IP address")
    parser.add_argument("--pr-number", type=int, default=None, help="PR number")
    parser.add_argument("--branch", default=None, help="Branch name")
    parser.add_argument("--results-dir", required=True, help="Results directory to write run.json")
    parser.add_argument("--tollgate-branch", default=None, help="TollGate branch name")
    parser.add_argument("--run-id", default=None, help="Run ID")
    parser.add_argument("--backend", default=None, help="Backend type (go, rust, etc.)")
    parser.add_argument("--client-type", default=None, help="Client type (adb, mac, linux, container)")
    parser.add_argument("--viewport", default=None, help="Viewport (desktop, mobile)")
    parser.add_argument("--virtual-lab", action="store_true", default=False, help="Mark as virtual lab run")
    parser.add_argument("--router-model", default=None, help="Router model override")
    parser.add_argument("--router-arch", default=None, help="Router architecture override")
    args = parser.parse_args()

    metadata = generate_metadata(args)

    os.makedirs(args.results_dir, exist_ok=True)
    with open(os.path.join(args.results_dir, "run.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
