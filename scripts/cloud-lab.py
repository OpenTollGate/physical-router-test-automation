#!/usr/bin/env python3
"""Manage the TollGate GCP nested-virtualization test lab."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, cast

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lib.cloud_lab.constants import (
    DEFAULT_DISK_SIZE_GB,
    DEFAULT_MACHINE_TYPE,
    DEFAULT_ZONE,
    SSH_KEY,
    SUITE_REPO,
    VM_NAME,
)
from lib.cloud_lab.gcp import (
    cleanup_all,
    cleanup_stale,
    get_project,
    status_run,
    submit_run,
    vm_down,
    vm_external_ip,
    vm_status,
    vm_up,
)
from lib.cloud_lab.resolve import resolve_target


def cmd_up(args: argparse.Namespace) -> int:
    return vm_up(
        cast(str, getattr(args, "vm_name", VM_NAME) or VM_NAME),
        zone=cast(str, args.zone),
        machine_type=cast(str, args.machine_type),
        disk_size_gb=cast(int, args.disk_size),
    )


def cmd_down(args: argparse.Namespace) -> int:
    vm_name = cast(str, getattr(args, "vm_name", VM_NAME) or VM_NAME)
    project = get_project()
    if vm_status(project, cast(str, args.zone), vm_name) is None:
        print(f"VM {vm_name} does not exist")
        return 0
    print(f"Deleting VM {vm_name}...")
    return vm_down(vm_name, zone=cast(str, args.zone))


def cmd_status(args: argparse.Namespace) -> int:
    project = get_project()
    zone = cast(str, args.zone)
    vm_name = cast(str, getattr(args, "vm_name", VM_NAME) or VM_NAME)
    status = vm_status(project, zone, vm_name)
    if not status:
        print(f"VM {vm_name} does not exist")
        return 1
    ip = vm_external_ip(project, zone, vm_name)
    print(f"VM: {vm_name}")
    print(f"Status: {status}")
    print(f"IP: {ip or 'N/A'}")
    print(f"Zone: {zone}")
    print(f"Project: {project}")
    return 0


def cmd_ssh(args: argparse.Namespace) -> int:
    project = get_project()
    zone = cast(str, args.zone)
    vm_name = cast(str, getattr(args, "vm_name", VM_NAME) or VM_NAME)
    ip = vm_external_ip(project, zone, vm_name)
    if not ip:
        print(f"ERROR: VM {vm_name} has no external IP", file=sys.stderr)
        return 1
    user = getattr(args, "user", "root") or "root"
    os.execvp(
        "ssh",
        [
            "ssh", "-i", SSH_KEY,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            f"{user}@{ip}",
        ],
    )
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    target = resolve_target(
        pr=cast(str | None, args.pr),
        branch=cast(str | None, args.branch),
        commit=cast(str | None, args.commit),
        backend=cast(str, args.backend),
        repo_override=cast(str | None, args.repo),
    )
    info = submit_run(
        target,
        zone=cast(str, args.zone),
        publish=cast(bool, args.publish),
        artifact_timeout_s=cast(int, args.artifact_timeout),
        machine_type=cast(str, args.machine_type),
        disk_size_gb=cast(int, args.disk_size),
        reseller_scenarios=cast(bool, args.reseller_scenarios),
        secondary_router_host=cast(str, args.secondary_router_host or ""),
        secondary_router_port=cast(str, args.secondary_router_port or ""),
        keep_vm_on_failure=cast(bool, args.keep_vm_on_failure),
    )
    pr_line = f"  PR:           {target.pr} ({target.repo}@{target.branch})\n" if target.pr else f"  Branch:       {target.repo}@{target.branch}\n"
    print(f"""
Submitted run {info['run_id']}
{pr_line}  SUT commit:   {target.sut_commit or '(branch head)'}
  VM:           {info['vm_name']} ({info['zone']})
  Artifact run: {info['artifact_run_id']}
  Suite ref:    {info['suite_ref']} (must exist on github.com/{SUITE_REPO})
  Logs:         {info['log_hint']}
""")
    if cast(bool, args.wait):
        return _wait_for_run(info["run_id"], cast(str, args.zone))
    return 0


def _wait_for_run(run_id: str, zone: str) -> int:
    print("Waiting for VM to finish (poll every 60s)...")
    while True:
        project = get_project()
        r = subprocess.run(
            [
                "gcloud", "compute", "instances", "list",
                f"--project={project}",
                f"--filter=metadata.tollgate-run-id={run_id}",
                "--format=value(name)",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if r.returncode != 0 or not r.stdout.strip():
            print(f"Run {run_id} finished. Check https://tests.tollgate.me/")
            return 0
        print(f"  VM still running: {r.stdout.strip()}")
        time.sleep(60)


def cmd_status_run(args: argparse.Namespace) -> int:
    return status_run(cast(str, args.run_id), zone=cast(str, args.zone))


def cmd_cleanup_stale(args: argparse.Namespace) -> int:
    return cleanup_stale(zone=cast(str, args.zone), max_age_hours=cast(int, args.max_age_hours))


def cmd_cleanup_all(args: argparse.Namespace) -> int:
    return cleanup_all(zone=cast(str, args.zone))


def cmd_run_tests(args: argparse.Namespace) -> int:
    """Synchronous wrapper: submit + wait (legacy compatibility)."""
    args.wait = True
    args.publish = bool(getattr(args, "publish", False)) or not getattr(args, "no_publish", False)
    return cmd_submit(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--zone", default=DEFAULT_ZONE)
        p.add_argument("--vm-name", default=VM_NAME)

    def target_flags(p: argparse.ArgumentParser) -> None:
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--pr", default=None)
        g.add_argument("--branch", default=None)
        g.add_argument("--commit", default=None)
        p.add_argument("--backend", default="go", choices=["go", "rust"])
        p.add_argument("--repo", default=None,
                        help="Override the artifact repo (e.g. Amperstrand/tollgate-module-basic-go for fork branches)")

    up = sub.add_parser("up", help="Create/start GCP VM from snapshot")
    common(up)
    up.add_argument("--machine-type", default=DEFAULT_MACHINE_TYPE)
    up.add_argument("--disk-size", type=int, default=DEFAULT_DISK_SIZE_GB)
    up.set_defaults(func=cmd_up)

    down = sub.add_parser("down", help="Delete the GCP VM")
    common(down)
    down.set_defaults(func=cmd_down)

    status = sub.add_parser("status", help="Show VM status")
    common(status)
    status.set_defaults(func=cmd_status)

    ssh = sub.add_parser("ssh", help="SSH into the GCP VM")
    common(ssh)
    ssh.add_argument("--user", default="root")
    ssh.set_defaults(func=cmd_ssh)

    submit = sub.add_parser("submit", help="Fire-and-forget: wait for CI artifact, spawn autonomous test VM")
    submit.add_argument("--zone", default=DEFAULT_ZONE)
    submit.add_argument("--machine-type", default=DEFAULT_MACHINE_TYPE)
    submit.add_argument("--disk-size", type=int, default=DEFAULT_DISK_SIZE_GB)
    submit.add_argument("--publish", action="store_true", help="Publish report to gh-pages when done")
    submit.add_argument("--wait", action="store_true", help="Block until VM self-deletes")
    submit.add_argument("--reseller-scenarios", action="store_true", help="Run virtualizable reseller-mode scenario tests")
    submit.add_argument("--secondary-router-host", default="", help="Seller/secondary router IP or host for reseller scenarios")
    submit.add_argument("--secondary-router-port", default="", help="Optional SSH port for the seller/secondary router")
    submit.add_argument("--keep-vm-on-failure", action="store_true", help="Do not self-delete failed worker VMs; useful for debugging")
    submit.add_argument("--artifact-timeout", type=int, default=1800, help="Seconds to wait for CI artifact")
    target_flags(submit)
    submit.set_defaults(func=cmd_submit)

    sr = sub.add_parser("status-run", help="Show status of a submitted run")
    sr.add_argument("--run-id", required=True)
    sr.add_argument("--zone", default=DEFAULT_ZONE)
    sr.set_defaults(func=cmd_status_run)

    clean = sub.add_parser("cleanup-stale", help="Delete tollgate run VMs older than max age")
    clean.add_argument("--zone", default=DEFAULT_ZONE)
    clean.add_argument("--max-age-hours", type=int, default=2)
    clean.set_defaults(func=cmd_cleanup_stale)

    nuke = sub.add_parser("cleanup-all", help="Delete ALL tollgate VMs regardless of age")
    nuke.add_argument("--zone", default=DEFAULT_ZONE)
    nuke.set_defaults(func=cmd_cleanup_all)

    run = sub.add_parser("run-tests", help="Submit cloud run and wait (alias for submit --wait --publish)")
    run.add_argument("--zone", default=DEFAULT_ZONE)
    run.add_argument("--machine-type", default=DEFAULT_MACHINE_TYPE)
    run.add_argument("--disk-size", type=int, default=DEFAULT_DISK_SIZE_GB)
    run.add_argument("--publish", action="store_true", help="Publish to gh-pages (default)")
    run.add_argument("--no-publish", action="store_true", help="Skip gh-pages publish")
    run.add_argument("--reseller-scenarios", action="store_true", help="Run virtualizable reseller-mode scenario tests")
    run.add_argument("--secondary-router-host", default="", help="Seller/secondary router IP or host for reseller scenarios")
    run.add_argument("--secondary-router-port", default="", help="Optional SSH port for the seller/secondary router")
    run.add_argument("--keep-vm-on-failure", action="store_true", help="Do not self-delete failed worker VMs; useful for debugging")
    run.add_argument("--artifact-timeout", type=int, default=1800)
    target_flags(run)
    run.set_defaults(func=cmd_run_tests)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    func = cast(Callable[[argparse.Namespace], int], args.func)
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
