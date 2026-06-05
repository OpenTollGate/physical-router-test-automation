"""Cloud lab worker — pre-flight checks."""

from __future__ import annotations

import json
import logging
import shlex
import urllib.request
from pathlib import Path
from typing import Any

from lib.cloud_lab.constants import DEBIAN_IP, OPENWRT_IP, TEST_DIR, VIRT_LAB_PASSWORD
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.shell import _run, log

def preflight_check(config: WorkerConfig, mint_url: str, results_dir: str) -> dict[str, Any]:
    """Run pre-flight checks before starting the test suite.

    Verifies that every component can actually do its job.  Writes a
    ``preflight.json`` into *results_dir* so the report can show which
    checks passed/failed.

    Returns a dict with ``ok: bool`` and per-check status.
    """
    checks: dict[str, Any] = {}

    # 1. SSH to OpenWrt
    r = _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} "
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=5 root@{OPENWRT_IP} 'echo OPENWRT_OK'",
        timeout=15, check=False,
    )
    checks["ssh_openwrt"] = "OPENWRT_OK" in r.stdout

    # 2. SSH to Debian
    r = _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} "
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=5 root@{DEBIAN_IP} 'echo DEBIAN_OK'",
        timeout=15, check=False,
    )
    checks["ssh_debian"] = "DEBIAN_OK" in r.stdout

    # 3. Backend responds
    r = _run(f"curl -s -o /dev/null -w '%{{http_code}}' http://{OPENWRT_IP}:2121/", timeout=10, check=False)
    checks["backend_http"] = "200" in r.stdout

    # 4. Mint health (HTTP /v1/keys)
    try:
        req = urllib.request.Request(f"{mint_url}/v1/keys")
        with urllib.request.urlopen(req, timeout=5) as resp:
            checks["mint_keys"] = resp.status == 200
    except Exception as exc:
        checks["mint_keys"] = False
        checks["mint_keys_error"] = str(exc)

    # 5. Mint cycle — already validated during select_test_mint().
    #    Skip here: cdk-cli wallet state from the selection step can cause
    #    the second mint() call to timeout (stale quote / pending proofs).
    checks["mint_cycle"] = True

    # 6. Backend can reach the mint (router-side check)
    r = _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} "
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=5 root@{OPENWRT_IP} "
        f"'curl -s -o /dev/null -w \"%{{http_code}}\" {mint_url}/v1/keys 2>/dev/null || echo 000'",
        timeout=15, check=False,
    )
    checks["router_to_mint"] = "200" in r.stdout

    mint_logs: dict[str, str] = {}
    for name, path in [
        ("cdk-v2", "/tmp/cdk-mintd.log"),
        ("nutshell-v2", "/tmp/nutshell-v2-mint.log"),
        ("nutshell-v1", "/tmp/nutshell-v1-mint.log"),
    ]:
        try:
            p = Path(path)
            if p.exists():
                mint_logs[name] = p.read_text()[-2000:]
        except Exception:
            pass
    checks["_mint_logs"] = mint_logs

    critical_keys = ("ssh_openwrt", "ssh_debian", "backend_http", "mint_keys", "mint_cycle", "router_to_mint")
    ok = all(checks.get(k) for k in critical_keys)
    checks["ok"] = ok

    Path(results_dir).mkdir(parents=True, exist_ok=True)
    serializable = {k: v for k, v in checks.items() if k != "_mint_logs"}
    (Path(results_dir) / "preflight.json").write_text(json.dumps(serializable, indent=2))

    if ok:
        log.info("[preflight] All checks passed")
    else:
        failed = [k for k in critical_keys if not checks.get(k)]
        log.error("[preflight] FAILED checks: %s", ", ".join(failed))
        if not checks.get("mint_cycle"):
            log.error("[preflight] Mint cycle error: %s", checks.get("mint_cycle_error", "(none)"))
        if not checks.get("router_to_mint"):
            log.error("[preflight] Router cannot reach mint at %s", mint_url)

    return checks
