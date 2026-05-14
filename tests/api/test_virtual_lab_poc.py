import os
import subprocess

import pytest


pytestmark = [pytest.mark.api, pytest.mark.smoke, pytest.mark.virtual_lab]


def test_virtual_linux_client_reaches_openwrt_gateway():
    """Proof-of-concept: Linux client namespace can reach the OpenWrt VM LAN."""
    if os.environ.get("TOLLGATE_VIRTUAL_LAB") != "1":
        pytest.skip("set TOLLGATE_VIRTUAL_LAB=1 and start scripts/virtual-lab.py poc")

    namespace = os.environ.get("TOLLGATE_VIRTUAL_CLIENT_NS", "tg-poc-client")
    gateway = os.environ.get("TOLLGATE_VIRTUAL_GATEWAY", "192.168.1.1")
    result = subprocess.run(
        ["sudo", "ip", "netns", "exec", namespace, "ping", "-c", "1", "-W", "2", gateway],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, (
        f"client namespace {namespace} could not reach OpenWrt gateway {gateway}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
