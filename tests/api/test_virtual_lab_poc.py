import os
import subprocess

import pytest

pytestmark = [pytest.mark.api, pytest.mark.smoke, pytest.mark.virtual_lab]


def _ssh_cmd(*args, timeout=10):
    """Run a command on the Debian client VM via SSH jump host."""
    jump_host = os.environ.get("TOLLGATE_SSH_JUMP_HOST", "")
    password = os.environ.get("TOLLGATE_SSH_PASSWORD",
                              os.environ.get("TOLLGATE_LUCI_PASSWORD", "tollgate"))
    client_ip = os.environ.get("TOLLGATE_CLIENT_IP", "192.168.1.100")

    ssh_cmd = ["sshpass", "-p", password, "ssh",
               "-o", "StrictHostKeyChecking=no",
               "-o", "UserKnownHostsFile=/dev/null",
               "-o", "LogLevel=ERROR"]
    if jump_host:
        ssh_cmd += ["-J", jump_host]
    ssh_cmd.append(f"root@{client_ip}")
    ssh_cmd.extend(args)

    return subprocess.run(
        ssh_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_container_reaches_openwrt_gateway():
    if os.environ.get("TOLLGATE_SSH_JUMP_HOST", "") == "" and os.environ.get("TOLLGATE_VIRTUAL_HOST", "") == "":
        pytest.skip("set TOLLGATE_SSH_JUMP_HOST=218 (or TOLLGATE_VIRTUAL_HOST) and run scripts/virtual-lab.py start-poc")

    gateway = os.environ.get("TOLLGATE_VIRTUAL_GATEWAY", "192.168.1.1")
    # Use curl to check L2/L3 reachability — NDS blocks ICMP for unauthenticated
    # clients, but HTTP to the portal port (2050) always works.
    result = _ssh_cmd("curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                      f"http://{gateway}:2050/", timeout=15)
    code = result.stdout.strip()

    assert code.startswith("2"), (
        f"Debian client VM could not reach NDS portal at {gateway}:2050 "
        f"(HTTP {code})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
