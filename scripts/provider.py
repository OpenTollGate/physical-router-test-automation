#!/usr/bin/env python3
"""VM provider CLI — create, check, destroy cloud or local VMs for testing.

Usage:
    # Create a VM (provider from TOLLGATE_VM_PROVIDER env var)
    python3 scripts/provider.py create --name my-test

    # Check if a VM is ready
    python3 scripts/provider.py wait --service-id 690

    # Run a command on the VM
    python3 scripts/provider.py ssh --service-id 690 --command "uname -a"

    # Destroy the VM when done
    python3 scripts/provider.py destroy --service-id 690

    # List all test VMs
    python3 scripts/provider.py list

    # Check if results can be published (privacy flag)
    python3 scripts/provider.py can-publish

Environment:
    TOLLGATE_VM_PROVIDER  shc|gcloud|local|physical (default: shc)
    SHC_API_KEY           Required for SHC provider
"""
import argparse
import json
import sys

sys.path.insert(0, ".")

from lib.cloud_lab.provider import get_provider, VMInfo


def cmd_create(args):
    provider = get_provider(args.provider)
    vm = provider.create_vm(
        name=args.name,
        machine_type=args.machine_type,
        disk_size_gb=args.disk_size,
    )
    print(json.dumps({
        "name": vm.name,
        "service_id": vm.service_id,
        "ip": vm.ip,
        "provider": vm.provider,
        "can_publish": provider.can_publish,
    }, indent=2))


def cmd_wait(args):
    provider = get_provider(args.provider)
    vm = VMInfo(name="", service_id=args.service_id, ip=args.ip or "", provider=args.provider or "")
    vm = provider.wait_for_ready(vm, timeout=args.timeout)
    print(f"READY: {vm.ip}")


def cmd_ssh(args):
    provider = get_provider(args.provider)
    vm = VMInfo(name="", service_id=args.service_id, ip=args.ip, provider=args.provider or "")
    output = provider.ssh(vm, args.command, timeout=args.timeout)
    print(output)


def cmd_destroy(args):
    provider = get_provider(args.provider)
    vm = VMInfo(name="", service_id=args.service_id, provider=args.provider or "")
    provider.destroy_vm(vm, immediate=not args.graceful)
    print(f"DESTROYED: {args.service_id}")


def cmd_list(args):
    provider = get_provider(args.provider)
    vms = provider.list_vms()
    for vm in vms:
        print(f"  {vm.name:30s} id={vm.service_id}  ip={vm.ip}  provider={vm.provider}")


def cmd_can_publish(args):
    provider = get_provider(args.provider)
    if provider.can_publish:
        print("yes")
        sys.exit(0)
    else:
        print("no")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="VM provider lifecycle CLI")
    parser.add_argument("--provider", "-p", default=None,
                        help="Override provider (shc, gcloud, local, physical)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a new VM")
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--machine-type", default="2C/8GB")
    p_create.add_argument("--disk", type=int, default=0)
    p_create.add_argument("--provider", "-p", default=None)
    p_create.set_defaults(func=cmd_create)

    p_wait = sub.add_parser("wait", help="Wait for VM to be ready")
    p_wait.add_argument("--service-id", required=True)
    p_wait.add_argument("--ip", default="")
    p_wait.add_argument("--timeout", type=int, default=300)
    p_wait.add_argument("--provider", "-p", default=None)
    p_wait.set_defaults(func=cmd_wait)

    p_ssh = sub.add_parser("ssh", help="Run command on VM")
    p_ssh.add_argument("--service-id", required=True)
    p_ssh.add_argument("--ip", required=True)
    p_ssh.add_argument("--command", required=True)
    p_ssh.add_argument("--timeout", type=int, default=300)
    p_ssh.add_argument("--provider", "-p", default=None)
    p_ssh.set_defaults(func=cmd_ssh)

    p_destroy = sub.add_parser("destroy", help="Destroy a VM")
    p_destroy.add_argument("--service-id", required=True)
    p_destroy.add_argument("--graceful", action="store_true")
    p_destroy.add_argument("--provider", "-p", default=None)
    p_destroy.set_defaults(func=cmd_destroy)

    p_list = sub.add_parser("list", help="List all test VMs")
    p_list.add_argument("--provider", "-p", default=None)
    p_list.set_defaults(func=cmd_list)

    p_pub = sub.add_parser("can-publish", help="Check if results can be published (privacy flag)")
    p_pub.add_argument("--provider", "-p", default=None)
    p_pub.set_defaults(func=cmd_can_publish)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
