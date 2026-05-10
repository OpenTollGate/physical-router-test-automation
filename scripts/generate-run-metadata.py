#!/usr/bin/env python3
import subprocess
import json
import os
import re
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

def main():
    parser = argparse.ArgumentParser(description="Generate run metadata for router tests")
    parser.add_argument("--router-ip", required=True, help="Router IP address")
    parser.add_argument("--pr-number", type=int, help="PR number")
    parser.add_argument("--branch", help="Branch name")
    parser.add_argument("--results-dir", required=True, help="Results directory to write run.json")
    parser.add_argument("--tollgate-branch", default=None, help="TollGate branch name (optional)")
    parser.add_argument("--run-id", default=None, help="Run ID (optional)")
    args = parser.parse_args()

    routers = load_router_inventory()
    router_info = get_router_info(args.router_ip, routers) if routers else None

    short_sha = "unknown"
    installed_version = "unknown"
    build_time = "unknown"
    try:
        version_output = ssh(args.router_ip, "echo '{\"command\": \"version\"}' | socat - UNIX-CONNECT:/var/run/tollgate.sock")
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

    pr_url = f"https://github.com/OpenTollGate/tollgate-module-basic-go/pull/{args.pr_number}" if args.pr_number else None
    compare_url = f"https://github.com/OpenTollGate/tollgate-module-basic-go/compare/main...{args.branch}" if args.branch else None
    tollgate_branch = args.tollgate_branch or args.branch or "main"
    commit_url = f"https://github.com/OpenTollGate/tollgate-module-basic-go/commit/{short_sha}" if short_sha != "unknown" else None

    metadata = {
        "run_id": args.run_id or f"pr{args.pr_number}-{short_sha[:7]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pr": args.pr_number,
        "pr_url": pr_url,
        "branch": args.branch or tollgate_branch,
        "commit_short": short_sha,
        "commit_url": commit_url,
        "compare_url": compare_url,
        "installed_version": installed_version,
        "build_time": build_time,
        "router_id": router_info["router_id"] if router_info else "unknown",
        "router_model": router_info["router_model"] if router_info else "unknown",
        "router_arch": router_info["router_arch"] if router_info else "unknown",
        "router_ip": args.router_ip,
        "test_type": "api",
        "repo": "OpenTollGate/tollgate-module-basic-go",
        "test_command": f"pytest tests/api/ -v --no-deploy --expected-pr={args.pr_number}"
    }

    os.makedirs(args.results_dir, exist_ok=True)
    with open(os.path.join(args.results_dir, "run.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    print(json.dumps(metadata, indent=2))

if __name__ == "__main__":
    main()
