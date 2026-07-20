import os
import shutil
import subprocess
import time
import uuid

import pytest


@pytest.fixture(autouse=True)
def _reset_nds_client_state(router, request):
    """Reset NDS client state + CDK mint state before each test (local lab only).

    Three problems this solves:
    1. NDS 5.0.2: ndsctl auth fails (exit 1) when client already Authenticated.
       Fix: deauth + re-trigger interception.
    2. CDK mint: "Duplicate outputs" when cashu CLI reuses wallet counter.
       Fix: restart CDK mint to clear in-memory swap state.
    3. Cashu CLI: fresh wallet directory per test to avoid counter collisions.
    """
    provider = os.environ.get("TOLLGATE_VM_PROVIDER", "")
    is_local = provider == "local" or os.environ.get("TOLLGATE_VIRTUAL_LAB")

    if not is_local:
        yield
        return

    client_ip = os.environ.get("TOLLGATE_CLIENT_IP", "10.99.99.100")
    client_mac = os.environ.get("TOLLGATE_CLIENT_MAC", "")
    mint_url = os.environ.get("TOLLGATE_TEST_MINT_URL", "http://10.99.99.2:8383")

    fresh_cashu_dir = f"/tmp/cashu-test-{uuid.uuid4().hex[:8]}"
    os.makedirs(fresh_cashu_dir, exist_ok=True)
    old_cashu_dir = os.environ.get("CASHU_DIR")
    os.environ["CASHU_DIR"] = fresh_cashu_dir

    # Restart CDK mint to clear swap state (prevents "Duplicate outputs")
    cdk_pid_file = "/tmp/cdk-mintd-local.pid"
    if os.path.exists(cdk_pid_file):
        try:
            with open(cdk_pid_file) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 15)
            time.sleep(2)
        except Exception:
            pass

    import pathlib
    config_path = "/tmp/cdk-mintd-local/config.toml"
    pathlib.Path(config_path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(config_path).write_text(f"""\
[info]
url = "{mint_url}/"
listen_host = "0.0.0.0"
listen_port = 8383
mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"

[database]
engine = "sqlite"

[ln]
ln_backend = "fakewallet"

[fake_wallet]
supported_units = ["sat"]
fee_percent = 0
reserve_fee_min = 0
min_delay_time = 0
max_delay_time = 0
""")

    cdk_bin = "/opt/cdk-mintd/cdk-mintd"
    if os.path.exists(cdk_bin):
        subprocess.Popen(
            ["setsid", "bash", "-c", f"exec {cdk_bin} -c {config_path}"],
            stdout=open("/tmp/cdk-mintd-local.log", "w"),
            stderr=subprocess.STDOUT,
        )
        for _ in range(10):
            try:
                import urllib.request
                urllib.request.urlopen(f"{mint_url}/v1/info", timeout=2)
                break
            except Exception:
                time.sleep(1)

    # Reset NDS client
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
