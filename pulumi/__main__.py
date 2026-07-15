"""Pulumi program: provision an SHC VPS for TollGate test runs.

Creates a single VM via the shc-pulumi dynamic provider. The worker
pipeline is bootstrapped via SSH by pulumi_submit.py after the VM is ready.
"""

import os
import time

import pulumi
from shc_pulumi import SHCVMResource

cfg = pulumi.Config()
size = cfg.get("size") or "nvme-2c-8gb"
hostname_prefix = cfg.get("hostname_prefix") or "tollgate"
api_key = os.environ.get("SHC_API_KEY", "")

ssh_pubkey = ""
for path in [os.path.expanduser("~/.ssh/id_rsa.pub"), os.path.expanduser("~/.ssh/id_ed25519.pub")]:
    if os.path.isfile(path):
        with open(path) as f:
            ssh_pubkey = f.read().strip()
        break

hostname = f"{hostname_prefix}-{int(time.time())}Z"

vm = SHCVMResource(
    "tollgate-runner",
    hostname=hostname,
    size=size,
    api_key=api_key,
    ssh_key=ssh_pubkey or None,
    auto_cancel=True,
)

pulumi.export("ip", vm.ip)
pulumi.export("hostname", vm.hostname)
pulumi.export("service_id", vm.service_id)
pulumi.export("os_user", vm.os_user)
