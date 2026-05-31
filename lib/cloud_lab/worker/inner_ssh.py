"""Cloud lab worker — SSH helpers for inner VMs."""

from __future__ import annotations

import shlex
import time

from lib.cloud_lab.constants import VIRT_LAB_PASSWORD
from lib.cloud_lab.worker.shell import _run


def inner_ssh(host: str, remote_cmd: str, timeout: int = 15):
    cmd = (
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=5 root@{host} {shlex.quote(remote_cmd)}"
    )
    return _run(cmd, timeout=timeout, check=False)


def wait_inner_ssh(host: str, timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = inner_ssh(host, "echo OK", timeout=10)
        if r.returncode == 0 and "OK" in r.stdout:
            return True
        time.sleep(3)
    return False
