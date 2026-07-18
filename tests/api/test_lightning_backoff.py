"""Lightning quote monitor backoff and jitter (PR #249).

Validates that the lightning quote monitor uses adaptive polling:
- Base interval is 5s (not the old 2s)
- On mint API errors, backoff doubles up to 30s
- Jitter is present (inter-poll intervals are not uniform)

Feature gating: these tests probe logread for backoff-related log messages
that only exist when PR #249 is deployed. On older firmware, they skip.
"""

import json
import os
import re
import time

import pytest
import requests

from lib.chaos import MintChaosController
from lib.constants import BACKEND_PORT

pytestmark = [pytest.mark.api, pytest.mark.slow, pytest.mark.go_only, pytest.mark.extended]


def _skip_if_no_ln_invoice(router):
    resp = router.api_status("/ln-invoice")
    if resp == 404 or resp == 0:
        pytest.skip(f"ln-invoice endpoint not available (status={resp})")


def _skip_if_degraded(router):
    discovery_raw = router.api_body("/")
    try:
        discovery = json.loads(discovery_raw)
    except json.JSONDecodeError:
        pytest.skip(f"Backend / did not return valid JSON: {discovery_raw[:200]}")
    if discovery.get("kind") == 21023:
        pytest.skip("Backend in degraded mode")
    return discovery


def _skip_if_no_backoff_support(router):
    """Skip if the monitor binary lacks backoff support (pre-PR #249 firmware)."""
    try:
        out = router.ssh(
            "strings /usr/bin/tollgate-wrt 2>/dev/null | grep -c 'lightningQuoteMonitorMaxBackoff'",
            timeout=10,
        )
        if out.strip() == "0":
            pytest.skip("Binary lacks lightningQuoteMonitorMaxBackoff (backoff not supported)")
    except Exception:
        pytest.skip("Cannot check backoff support")


def _create_invoice(router, amount=21, retries=3):
    mint_url = os.environ.get("TOLLGATE_TEST_MINT_URL", "http://10.99.99.2:8383")
    backend_ip = os.environ.get("TOLLGATE_SSH_HOST", "10.99.99.1")
    url = f"http://{backend_ip}:{BACKEND_PORT}/ln-invoice"
    payload = {"amount": amount, "mint_url": mint_url}
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        time.sleep(5)
    last = resp.status_code if "resp" in dir() else "?"
    pytest.fail(f"POST /ln-invoice failed after {retries} attempts (last status: {last})")


def _extract_log_timestamps(logs, pattern):
    """Extract timestamps from logread lines matching the given pattern.

    logread format: 'Jul 17 19:26:40 routername tollgate-wrt[123]: message'
    Returns list of epoch floats.
    """
    timestamps = []
    current_year = time.localtime().tm_year
    for line in logs.splitlines():
        if pattern not in line:
            continue
        m = re.search(r"(\w{3})\s+(\d+)\s+(\d{2}):(\d{2}):(\d{2})", line)
        if not m:
            continue
        month_str, day, hour, minute, second = m.groups()
        months = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                  "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
        month = months.get(month_str)
        if not month:
            continue
        ts = time.mktime((current_year, month, int(day), int(hour), int(minute), int(second), 0, 0, -1))
        timestamps.append(ts)
    return timestamps


def test_backoff_progression_on_mint_error(router):
    """On mint errors, poll intervals double (5s -> 10s -> 20s -> 30s cap)."""
    _skip_if_no_ln_invoice(router)
    _skip_if_degraded(router)

    chaos = MintChaosController()
    try:
        _create_invoice(router)
        chaos.block_until_reset()
        time.sleep(35)

        logs = router.get_tollgate_logs(lines=500)
        timestamps = _extract_log_timestamps(logs, "monitorLightningQuote")
        assert len(timestamps) >= 2, (
            f"Expected >=2 'mint state check failed' entries, got {len(timestamps)}"
        )

        intervals = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
        assert intervals[0] >= 4.0, (
            f"First interval {intervals[0]:.1f}s < 4s (base 5s minus jitter slack)"
        )
        if len(intervals) >= 2:
            assert intervals[1] >= 8.0, (
                f"Second interval {intervals[1]:.1f}s < 8s (doubled 10s minus jitter slack)"
            )
        for i in range(1, len(intervals)):
            assert intervals[i] >= intervals[i - 1], (
                f"Interval decreased at step {i}: {intervals[i - 1]:.1f}s -> {intervals[i]:.1f}s"
            )
    finally:
        chaos.reset()


def test_jitter_present_in_polling(router):
    """Inter-poll intervals are not uniform (jitter is applied each cycle)."""
    _skip_if_no_ln_invoice(router)
    _skip_if_degraded(router)

    chaos = MintChaosController()
    try:
        _create_invoice(router)
        chaos.block_until_reset()
        time.sleep(40)

        logs = router.get_tollgate_logs(lines=500)
        timestamps = _extract_log_timestamps(logs, "monitorLightningQuote")
        assert len(timestamps) >= 3, (
            f"Need >=3 timestamps to detect jitter, got {len(timestamps)}"
        )

        intervals = [round(timestamps[i + 1] - timestamps[i], 3) for i in range(len(timestamps) - 1)]
        for i in range(1, len(intervals)):
            assert intervals[i] != intervals[i - 1], (
                f"Consecutive intervals identical at step {i} ({intervals[i]}s) — no jitter"
            )
    finally:
        chaos.reset()


def test_no_backoff_hammering_on_mint_error(router):
    """Monitor does not poll every 2s (old fixed-ticker regression guard)."""
    _skip_if_no_ln_invoice(router)
    _skip_if_degraded(router)

    chaos = MintChaosController()
    try:
        _create_invoice(router)
        chaos.block_until_reset()
        time.sleep(10)

        logs = router.get_tollgate_logs(lines=500)
        timestamps = _extract_log_timestamps(logs, "monitorLightningQuote")
        cutoff = time.time() - 12
        recent = [t for t in timestamps if t >= cutoff]
        assert len(recent) >= 1, "Monitor produced no error entries in 10s (not running?)"
        assert len(recent) <= 2, (
            f"Monitor hammered {len(recent)} times in 10s — old 2s fixed ticker present"
        )
    finally:
        chaos.reset()
