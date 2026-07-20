import os
import shutil
import subprocess
import time
import uuid

import pytest


@pytest.fixture(autouse=True)
def _reset_nds_client_state(router, request):
    """Deauth NDS client and re-trigger interception before each test.

    NDS 5.0.2 returns exit status 1 when authenticating an already-Authenticated
    client. Without this fixture, the first payment test authenticates the
    client, and all subsequent payment tests fail because valve.go's ndsctl
    auth retry sees exit 1 on every attempt.

    Only runs when TOLLGATE_VM_PROVIDER=local or TOLLGATE_VIRTUAL_LAB is set
    (local QEMU lab). Cloud lab handles this via deploy_session reset.

    Also: assigns a fresh CASHU_DIR per test to prevent "Duplicate outputs"
    errors from the CDK mint when the cashu CLI reuses wallet counter state
    across tests.
    """
    provider = os.environ.get("TOLLGATE_VM_PROVIDER", "")
    is_local = provider == "local" or os.environ.get("TOLLGATE_VIRTUAL_LAB")

    if not is_local:
        yield
        return

    client_ip = os.environ.get("TOLLGATE_CLIENT_IP", "10.99.99.100")
    client_mac = os.environ.get("TOLLGATE_CLIENT_MAC", "")

    fresh_cashu_dir = f"/tmp/cashu-test-{uuid.uuid4().hex[:8]}"
    os.makedirs(fresh_cashu_dir, exist_ok=True)
    old_cashu_dir = os.environ.get("CASHU_DIR")
    os.environ["CASHU_DIR"] = fresh_cashu_dir

    if client_mac:
        try:
            router.ssh(f"ndsctl deauth {client_mac} 2>/dev/null || true", timeout=5)
            time.sleep(1)

            subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
                 f"root@{client_ip}", "curl -s -o /dev/null --max-time 5 http://example.com"],
                capture_output=True, timeout=10,
            )
            time.sleep(1)
        except Exception:
            pass

    yield

    if client_mac:
        try:
            router.ssh(f"ndsctl deauth {client_mac} 2>/dev/null || true", timeout=5)
        except Exception:
            pass

    shutil.rmtree(fresh_cashu_dir, ignore_errors=True)
    if old_cashu_dir:
        os.environ["CASHU_DIR"] = old_cashu_dir
    elif "CASHU_DIR" in os.environ:
        del os.environ["CASHU_DIR"]
