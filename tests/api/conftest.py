import os
import subprocess
import time

import pytest


@pytest.fixture(autouse=True)
def _reset_nds_and_trigger(router):
    provider = os.environ.get("TOLLGATE_VM_PROVIDER", "")
    if provider != "local" and not os.environ.get("TOLLGATE_VIRTUAL_LAB"):
        yield
        return

    client_ip = os.environ.get("TOLLGATE_CLIENT_IP", "10.99.99.100")
    client_mac = os.environ.get("TOLLGATE_CLIENT_MAC", "")

    if client_mac:
        try:
            router.ssh(f"ndsctl deauth {client_mac} 2>/dev/null || true", timeout=5)
            time.sleep(2)
        except Exception:
            pass

    try:
        subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
             f"root@{client_ip}", "curl -s -o /dev/null --max-time 5 http://example.com"],
            capture_output=True, timeout=10,
        )
        time.sleep(2)
    except Exception:
        pass

    yield

    if client_mac:
        try:
            router.ssh(f"ndsctl deauth {client_mac} 2>/dev/null || true", timeout=5)
        except Exception:
            pass
