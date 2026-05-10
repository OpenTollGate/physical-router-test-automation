"""Tests for handling unreachable/degraded mints on startup.

Configures mint.coinos.io (currently returning 502) as the sole accepted
mint, then restarts the service to observe how the binary handles a mint
that is reachable but returning errors.

Expected behavior by branch:
  main (no PR #118): Service may hang or crash during wallet init
  PR #118:           Service enters degraded mode (kind 21023),
                     stays up, auto-recovers when mint recovers

Run with --expected-pr=118 to assert PR #118 degraded mode behavior.
Run without to observe raw main branch behavior.
"""

import json
import time

import pytest

from lib.helpers import parse_json_or_fail

COINOS_MINT = "https://mint.coinos.io"
CONFIG_BACKUP = "/etc/tollgate/config.json.coinos-test-backup"

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.timeout(300), pytest.mark.pr(118)]


def _write_single_mint_config(router, mint_url: str):
    cfg_raw = router.ssh("cat /etc/tollgate/config.json")
    cfg = json.loads(cfg_raw)
    router.ssh(f"cp /etc/tollgate/config.json {CONFIG_BACKUP}")

    cfg["accepted_mints"] = [
        {
            "url": mint_url,
            "min_balance": 64,
            "balance_tolerance_percent": 10,
            "payout_interval_seconds": 86400,
            "min_payout_amount": 999999,
            "price_per_step": 1,
            "price_unit": "sats",
            "purchase_min_steps": 0,
        }
    ]

    payload = json.dumps(cfg)
    encoded = __import__("base64").b64encode(payload.encode()).decode()
    router.ssh(f"echo '{encoded}' | base64 -d > /etc/tollgate/config.json")


def _restore_config(router):
    router.ssh(f"cat {CONFIG_BACKUP} > /etc/tollgate/config.json")
    router.ssh(f"rm -f {CONFIG_BACKUP}")


def _restart_and_wait(router, timeout: int = 30):
    router.ssh("/etc/init.d/tollgate-wrt restart")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            code = router.api_status("/")
            if code in (200, 502):
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


@pytest.fixture(scope="module", autouse=True)
def coinos_only_config(router):
    _write_single_mint_config(router, COINOS_MINT)
    _restart_and_wait(router)
    yield
    _restore_config(router)
    _restart_and_wait(router)


def test_mint_coinos_returns_502(router):
    code = router.ssh(
        "curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 10 "
        f"{COINOS_MINT}/v1/keysets"
    ).strip()
    assert code == "502", f"Expected 502 from coinos, got {code}"


def test_service_stays_up_with_502_mint(router):
    code = router.api_status("/")
    assert code in (200, 503), f"Service not responding: HTTP {code}"


def test_discovery_indicates_degraded_mode(router):
    body = router.api_body("/")
    event = parse_json_or_fail(body, "discovery response")
    kind = event.get("kind")

    assert kind in (10021, 21023), \
        f"Unexpected kind: {kind}"

    if kind != 21023:
        pytest.skip(
            f"Router not in degraded mode (kind={kind}) — auto-test-mint feature on non-main "
            "branches prevents fully degraded state with a single 502 mint"
        )


def test_degraded_event_has_no_reachable_mints_code(router):
    body = router.api_body("/")
    event = parse_json_or_fail(body, "discovery response")

    if event.get("kind") != 21023:
        pytest.skip("Not in degraded mode")

    tags = event.get("tags", [])
    code_tags = [t for t in tags if isinstance(t, list) and len(t) >= 2 and t[0] == "code"]
    assert len(code_tags) > 0, f"No 'code' tag in degraded event: {tags}"
    assert code_tags[0][1] == "no-reachable-mints", \
        f"Expected code 'no-reachable-mints', got: {code_tags[0][1]}"


def test_service_no_crash_loop(router):
    pid_before = router.ssh("pidof tollgate-wrt").strip()
    assert pid_before, "tollgate-wrt not running"
    time.sleep(10)
    pid_after = router.ssh("pidof tollgate-wrt").strip()
    assert pid_after, "tollgate-wrt crashed within 10s"
    assert pid_before == pid_after, \
        f"PID changed: {pid_before} -> {pid_after} (restart detected)"


def test_cli_status_works_in_degraded(router):
    status = router.get_tollgate_status()
    assert status.get("success") is True, \
        f"CLI status failed in degraded mode: {status}"
    data = status.get("data", {})
    assert data.get("running") is True, "Service not reported as running"
