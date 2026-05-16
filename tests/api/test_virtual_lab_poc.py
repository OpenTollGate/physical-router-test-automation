import os
import subprocess

import pytest

pytestmark = [pytest.mark.api, pytest.mark.smoke, pytest.mark.virtual_lab]


def _netns_exec(*args, timeout=10):
    """Run a command in the tg-poc-client network namespace via jump host."""
    jump_host = os.environ.get("TOLLGATE_SSH_JUMP_HOST", "")
    password = os.environ.get("TOLLGATE_SSH_PASSWORD",
                              os.environ.get("TOLLGATE_LUCI_PASSWORD", "tollgate"))

    ns_cmd = ["sudo", "ip", "netns", "exec", "tg-poc-client"] + list(args)
    ssh_cmd = ["sshpass", "-p", password, "ssh",
               "-o", "StrictHostKeyChecking=no",
               "-o", "UserKnownHostsFile=/dev/null",
               "-o", "LogLevel=ERROR",
               jump_host] + ns_cmd

    return subprocess.run(
        ssh_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_container_reaches_openwrt_gateway():
    if not (os.environ.get("TOLLGATE_SSH_JUMP_HOST") or os.environ.get("TOLLGATE_VIRTUAL_HOST") or os.environ.get("TOLLGATE_VIRTUAL_LAB")):
        pytest.skip("set TOLLGATE_VIRTUAL_LAB=1 and run scripts/virtual-lab.py start-poc")

    gateway = os.environ.get("TOLLGATE_VIRTUAL_GATEWAY", "192.168.1.1")
    result = _netns_exec("curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                         f"http://{gateway}:2050/", timeout=15)
    code = result.stdout.strip()

    assert code.startswith("2") or code == "404", (
        f"Client namespace could not reach NDS portal at {gateway}:2050 "
        f"(HTTP {code})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
