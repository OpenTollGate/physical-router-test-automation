import os
import subprocess

import pytest


pytestmark = [pytest.mark.api, pytest.mark.smoke, pytest.mark.virtual_lab]


def _container_cmd(*args, timeout=10):
    host = os.environ.get("TOLLGATE_VIRTUAL_HOST", "218")
    return subprocess.run(
        ["ssh", host, "docker", "exec", "tg-poc-client", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_container_reaches_openwrt_gateway():
    if os.environ.get("TOLLGATE_VIRTUAL_LAB") != "1":
        pytest.skip("set TOLLGATE_VIRTUAL_LAB=1 and run scripts/virtual-lab.py start-poc --host 218")

    gateway = os.environ.get("TOLLGATE_VIRTUAL_GATEWAY", "192.168.1.1")
    result = _container_cmd("ping", "-c", "1", "-W", "2", gateway)

    assert result.returncode == 0, (
        f"container tg-poc-client could not reach OpenWrt gateway {gateway}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
