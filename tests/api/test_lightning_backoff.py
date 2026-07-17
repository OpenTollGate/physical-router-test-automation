"""Lightning quote monitor backoff and jitter (PR #249).

Validates that the lightning quote monitor uses adaptive polling:
- Base interval is 5s (not the old 2s)
- On mint API errors, backoff doubles up to 30s
- Jitter is present (inter-poll intervals are not uniform)

Feature gating: these tests probe logread for backoff-related log messages
that only exist when PR #249 is deployed. On older firmware, they skip.
"""

import json
import re
import time

import pytest

from lib.constants import BACKEND_PORT

pytestmark = [pytest.mark.api, pytest.mark.slow, pytest.mark.go_only, pytest.mark.extended]


def _skip_if_no_ln_invoice(router):
    resp = router.api_status("/ln-invoice")
    if resp != 405:
        pytest.skip(f"ln-invoice endpoint not available (status={resp}, expected 405 on GET)")


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
    """Skip if the monitor doesn't have backoff logging (pre-PR #249 firmware)."""
    logs = router.get_tollgate_logs(lines=500)
    if "monitorLightningQuote" not in logs:
        pytest.skip("No monitorLightningQuote log entries (backoff logging not present)")


def _create_invoice(router, amount=21):
    create_resp = router.ssh(
        f"wget -qO- --timeout=15 --post-data='{{\"amount\": {amount}}}' "
        f"--header='Content-Type: application/json' "
        f"'http://[::1]:{BACKEND_PORT}/ln-invoice'",
        timeout=30,
    )
    assert create_resp, "Empty response from POST /ln-invoice"
    try:
        invoice = json.loads(create_resp)
    except json.JSONDecodeError:
        pytest.fail(f"ln-invoice response not JSON: {create_resp[:300]}")
    return invoice


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
        m = re.match(r"^(\w{3})\s+(\d+)\s+(\d{2}):(\d{2}):(\d{2})", line)
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

    try:
        router.block_mint()
        _create_invoice(router)
        time.sleep(35)

        logs = router.get_tollgate_logs(lines=500)
        timestamps = _extract_log_timestamps(logs, "mint state check failed")
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
        router.unblock_mint()


def test_jitter_present_in_polling(router):
    """Inter-poll intervals are not uniform (jitter is applied each cycle)."""
    _skip_if_no_ln_invoice(router)
    _skip_if_degraded(router)

    try:
        router.block_mint()
        _create_invoice(router)
        time.sleep(40)

        logs = router.get_tollgate_logs(lines=500)
        timestamps = _extract_log_timestamps(logs, "mint state check failed")
        assert len(timestamps) >= 3, (
            f"Need >=3 timestamps to detect jitter, got {len(timestamps)}"
        )

        intervals = [round(timestamps[i + 1] - timestamps[i], 3) for i in range(len(timestamps) - 1)]
        for i in range(1, len(intervals)):
            assert intervals[i] != intervals[i - 1], (
                f"Consecutive intervals identical at step {i} ({intervals[i]}s) — no jitter"
            )
    finally:
        router.unblock_mint()


def test_no_backoff_hammering_on_mint_error(router):
    """Monitor does not poll every 2s (old fixed-ticker regression guard)."""
    _skip_if_no_ln_invoice(router)
    _skip_if_degraded(router)

    try:
        router.block_mint()
        _create_invoice(router)
        time.sleep(10)

        logs = router.get_tollgate_logs(lines=500)
        timestamps = _extract_log_timestamps(logs, "mint state check failed")
        cutoff = time.time() - 12
        recent = [t for t in timestamps if t >= cutoff]
        assert len(recent) >= 1, "Monitor produced no error entries in 10s (not running?)"
        assert len(recent) <= 2, (
            f"Monitor hammered {len(recent)} times in 10s — old 2s fixed ticker present"
        )
    finally:
        router.unblock_mint()
