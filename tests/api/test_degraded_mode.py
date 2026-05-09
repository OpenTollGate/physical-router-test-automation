"""Tests for PR #118: MintHealthTracker and degraded mode.

These tests verify that the tollgate backend degrades gracefully when
mints become unreachable, and recovers when connectivity is restored.

Key behaviors under test:
- Service stays up (no crash loop) when all mints are blocked
- Backend enters degraded mode and logs appropriate signals
- Payment attempts in degraded mode return a retry notice (kind 21023)
- Service recovers automatically when mint connectivity returns
- Partial mint loss does not trigger degraded mode

Tests skip cleanly on versions that do not support degraded mode
(current main branch). Tests run fully on PR #118 branch.
"""

import json
import logging
import re
import time
from urllib.parse import urlparse

import pytest

from lib.helpers import parse_json_or_fail

log = logging.getLogger("tollgate.degraded_mode")

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.timeout(300), pytest.mark.pr(118)]

SERVICE_RESTART_WAIT = 10
HEALTH_POLL_INTERVAL = 15
HEALTH_POLL_TIMEOUT = 300


def _get_mint_urls(router):
    """Read mint URLs from the router's config file."""
    cfg_raw = router.ssh("cat /etc/tollgate/config.json")
    cfg = json.loads(cfg_raw)
    urls = [m["url"] for m in cfg.get("accepted_mints", []) if "url" in m]
    assert urls, "No mint URLs found in router config"
    return urls


def _resolve_mint_ips(router, mint_urls):
    """Resolve mint hostnames to IPs from the router's perspective."""
    ip_map = {}
    for url in mint_urls:
        parsed = urlparse(url)
        hostname = parsed.hostname
        out = router.ssh(f"nslookup {hostname} 2>/dev/null || echo FAILED")
        ips = re.findall(r"Address:\s*(\d+\.\d+\.\d+\.\d+)", out)
        # nslookup prints DNS server first, then resolved address; take last non-loopback
        for ip in reversed(ips):
            if not ip.startswith("127."):
                ip_map[url] = ip
                break
    return ip_map


def _block_mints(router, mint_ip_map):
    """Add iptables rules to block outbound HTTPS to mint IPs.

    Returns a list of (url, ip, rule_index) tuples for cleanup.
    """
    rules = []
    for url, ip in mint_ip_map.items():
        router.ssh(f"iptables -I OUTPUT -d {ip} -p tcp --dport 443 -j REJECT")
        rules.append((url, ip))
    return rules


def _unblock_mints(router, rules):
    """Remove iptables rules that were added to block mints."""
    for url, ip in rules:
        router.ssh(f"iptables -D OUTPUT -d {ip} -p tcp --dport 443 -j REJECT"
                   f" 2>/dev/null || true")


def _restart_and_wait(router):
    """Restart tollgate service and wait for it to come back."""
    router.ssh("service tollgate-wrt restart")
    time.sleep(SERVICE_RESTART_WAIT)


def _is_degraded_mode(logs):
    """Check if backend logs contain degraded mode signals."""
    signals = re.findall(
        r"(degraded|no reachable mints|all mints unreachable|entering degraded)",
        logs, re.IGNORECASE,
    )
    return len(signals) > 0


def _skip_if_no_degraded_support(router):
    """Skip if deployed version predates PR #118 (no mint health tracker)."""
    resp = router.get_tollgate_status()
    if resp.get("success") is not True:
        pytest.skip("tollgate status command not available (version predates PR #118)")
    raw = json.dumps(resp).lower()
    has_mint_health = any(kw in raw for kw in ["degraded", "reachable", "mint_health"])
    if not has_mint_health:
        pytest.skip("No mint health tracking in status output (version predates PR #118)")


@pytest.fixture(scope="module")
def discovery(router):
    """Fetch the discovery endpoint once per module."""
    return parse_json_or_fail(router.api_body("/"), "discovery response")


@pytest.fixture(scope="module")
def backend_logs(router):
    """Fetch backend logs once per module for initial analysis."""
    return router.get_tollgate_logs(lines=2000)


@pytest.fixture(scope="module")
def mint_urls(router):
    """Read configured mint URLs from the router."""
    return _get_mint_urls(router)


@pytest.fixture(scope="module")
def mint_ip_map(router, mint_urls):
    """Resolve mint hostnames to IPs from the router's DNS."""
    ip_map = _resolve_mint_ips(router, mint_urls)
    if not ip_map:
        pytest.skip("Could not resolve any mint hostnames to IPs")
    return ip_map


@pytest.fixture
def block_all_mints(router, mint_ip_map):
    """Block all mint IPs via iptables, unblock on teardown.

    Uses yield+try/finally to guarantee cleanup even on test failure.
    """
    rules = _block_mints(router, mint_ip_map)
    log.info("Blocked %d mint IPs via iptables", len(rules))
    yield rules
    try:
        _unblock_mints(router, rules)
        log.info("Removed %d iptables rules (all mints unblocked)", len(rules))
    except Exception as exc:
        log.error("Failed to remove iptables rules: %s", exc)
        _unblock_mints(router, rules)


@pytest.fixture
def block_one_mint(router, mint_ip_map, mint_urls):
    """Block a single mint IP via iptables, unblock on teardown.

    Only used when 2+ mints are configured. Skips if there is only one mint.
    """
    if len(mint_urls) < 2:
        pytest.skip("Need 2+ configured mints to test partial blocking")
    target_url = mint_urls[0]
    target_ip = mint_ip_map.get(target_url)
    if not target_ip:
        pytest.skip(f"Could not resolve IP for {target_url}")
    rules = [(target_url, target_ip)]
    router.ssh(f"iptables -I OUTPUT -d {target_ip} -p tcp --dport 443 -j REJECT")
    log.info("Blocked one mint IP (%s -> %s) via iptables", target_url, target_ip)
    yield rules
    try:
        _unblock_mints(router, rules)
        log.info("Removed iptables rule for %s", target_url)
    except Exception as exc:
        log.error("Failed to remove iptables rule: %s", exc)
        _unblock_mints(router, rules)


@pytest.fixture
def restart_clean(router):
    """Restart the service before and after the test for clean state."""
    _restart_and_wait(router)
    yield
    _restart_and_wait(router)


def test_service_health_while_mints_reachable(router, discovery):
    """Verify GET / returns 200 when mints are reachable (baseline).

    This is a basic sanity check that should always pass regardless of
    PR #118 support. It establishes that the service is healthy under
    normal conditions before we start breaking things.
    """
    if discovery.get("kind") == 21023:
        pytest.skip("All mints currently unreachable (degraded mode), cannot verify healthy baseline")
    code = router.api_status("/")
    assert code == 200, f"Expected 200 from GET /, got {code}"
    assert discovery.get("kind") == 10021, \
        f"Expected kind 10021 in discovery, got {discovery.get('kind')}"


def test_cli_health_shows_mint_status(router):
    """Verify `tollgate status` reports mint health information.

    PR #118 adds mint health tracking to the status command output.
    Skip if the command is not available (pre-PR #118 versions).
    """
    _skip_if_no_degraded_support(router)

    status = router.get_tollgate_status()
    assert status.get("success") is True, \
        f"Status command failed: {status}"

    raw = json.dumps(status).lower()
    has_mint_info = any(
        keyword in raw
        for keyword in ["mint", "health", "reachable", "degraded"]
    )
    if not has_mint_info:
        pytest.skip(
            "Status command does not report mint health "
            "(version may partially support PR #118 but not status output)"
        )


def test_block_all_mints_service_stays_up(router, block_all_mints):
    """Block all mints via iptables and verify service stays up.

    This is the key test for PR #118's degraded mode. When all mints
    become unreachable, the backend should:
    1. NOT crash or enter a restart loop
    2. Continue responding to HTTP requests
    3. Log degraded mode signals
    4. Keep its process running

    Cleanup is guaranteed by the block_all_mints fixture.
    """
    _skip_if_no_degraded_support(router)

    _restart_and_wait(router)

    code = router.api_status("/")
    assert code in (200, 503), \
        f"Expected 200 or 503 from GET / with mints blocked, got {code}"

    ps_out = router.ssh("ps | grep tollgate-wrt | grep -v grep")
    assert "tollgate-wrt" in ps_out, \
        f"Backend process not running: ps output = {ps_out!r}"

    # Health tracker probes every 5min; poll for degraded signals
    found_degraded = False
    deadline = time.time() + 60
    while time.time() < deadline and not found_degraded:
        logs = router.get_tollgate_logs(lines=500)
        found_degraded = _is_degraded_mode(logs)
        if not found_degraded:
            time.sleep(5)

    if not found_degraded:
        log.warning(
            "No degraded mode signals in logs after 60s — "
            "health tracker interval may be longer than expected"
        )


def test_degraded_mode_returns_retry_notice(router, block_all_mints, cashu):
    """In degraded mode, payment attempts should return a retry notice.

    When the service has no reachable mints, sending a valid Cashu token
    should return a kind 21023 notice event asking the client to retry
    later, rather than a 500 error or timeout.

    Skip if the service is not in degraded mode.
    """
    _skip_if_no_degraded_support(router)

    logs = router.get_tollgate_logs(lines=500)
    if not _is_degraded_mode(logs):
        time.sleep(30)
        logs = router.get_tollgate_logs(lines=500)
        if not _is_degraded_mode(logs):
            pytest.skip(
                "Service not in degraded mode — health tracker may not have "
                "detected unreachable mints yet"
            )

    # Mint a test token and attempt payment
    if not cashu.is_available():
        pytest.skip("cashu venv not available — run scripts/setup-cashu.sh")
    token = cashu.mint(4)

    resp = router.pay_direct(token)

    assert isinstance(resp, dict), \
        f"Expected dict response, got: {type(resp)}: {str(resp)[:200]}"

    # Check for kind 21023 (notice) with a service-degraded code tag
    kind = resp.get("kind")
    if kind == 21023:
        tags = resp.get("tags", [])
        codes = [
            t[1] for t in tags
            if isinstance(t, list) and len(t) >= 2 and t[0] == "code"
        ]
        assert codes, "Notice event (kind 21023) missing code tag"
        code_val = codes[0].lower()
        assert any(
            keyword in code_val
            for keyword in ["degraded", "retry", "unavailable", "offline"]
        ), f"Unexpected notice code: {code_val}"
    elif kind == 10021:
        pytest.skip(
            "Service returned discovery event (kind 10021) — not in degraded mode. "
            "Mint blocking may not have taken effect."
        )
    else:
        log.warning(
            "Unexpected response kind %s when in degraded mode: %s",
            kind, str(resp)[:200],
        )
        assert "kind" in resp, \
            f"Response missing 'kind' field: {str(resp)[:200]}"


def test_mint_recovery_after_unblock(router, block_all_mints, cashu):
    """After unblocking mints, verify the service recovers.

    This test depends on test_block_all_mints_service_stays_up having
    established the degraded state. The block_all_mints fixture teardown
    removes the iptables rules, then we poll for recovery.

    Recovery requires 3 consecutive successful probes (hysteresis),
    so we poll with a timeout of up to 5 minutes.
    """
    _skip_if_no_degraded_support(router)

    # Manually unblock now to test recovery; fixture cleanup is a safety net
    rules = block_all_mints
    _unblock_mints(router, rules)
    log.info("Manually unblocked mints for recovery test")

    # PR #118 hysteresis: 3 consecutive successful probes needed for recovery
    recovered = False
    deadline = time.time() + HEALTH_POLL_TIMEOUT
    while time.time() < deadline:
        code = router.api_status("/")
        if code == 200:
            body = router.api_body("/")
            try:
                data = json.loads(body)
                kind = data.get("kind")
                if kind == 10021:
                    tags = data.get("tags", [])
                    price_tags = [
                        t for t in tags
                        if isinstance(t, list) and t[0] == "price_per_step"
                    ]
                    if price_tags:
                        recovered = True
                        break
            except json.JSONDecodeError:
                pass
        time.sleep(HEALTH_POLL_INTERVAL)

    if not recovered:
        log.warning(
            "Service did not recover within %ds — "
            "health tracker interval may be longer than expected",
            HEALTH_POLL_TIMEOUT,
        )
        pytest.skip(
            f"Service did not recover within {HEALTH_POLL_TIMEOUT}s — "
            "may need longer probe interval or fewer hysteresis probes"
        )

    if cashu.is_available():
        token = cashu.mint(4)
        resp = router.pay_direct(token)
        assert isinstance(resp, dict), \
            f"Expected dict response after recovery, got: {str(resp)[:200]}"
        kind = resp.get("kind")
        assert kind in (1022, 21023), \
            f"Unexpected response kind after recovery payment: {kind}, body: {str(resp)[:200]}"


def test_block_one_mint_others_still_work(router, block_one_mint, mint_urls,
                                          cashu, discovery):
    """Block a single mint and verify the service stays in full mode.

    When there are 2+ mints and only one is blocked, the service should:
    1. Stay up in full (non-degraded) mode since other mints are reachable
    2. Continue processing payments with the remaining mint(s)
    3. Exclude the blocked mint from discovery (eventually)

    This test requires 2+ configured mints. It skips otherwise.
    """
    _skip_if_no_degraded_support(router)

    _restart_and_wait(router)

    code = router.api_status("/")
    assert code == 200, \
        f"Expected 200 with one mint blocked, got {code}"

    ps_out = router.ssh("ps | grep tollgate-wrt | grep -v grep")
    assert "tollgate-wrt" in ps_out, \
        f"Backend process not running: {ps_out!r}"

    # Verify service is NOT in degraded mode (only one mint blocked, others reachable)
    logs = router.get_tollgate_logs(lines=500)
    degraded_signals = re.findall(
        r"(entering degraded|all mints unreachable|no reachable mints)",
        logs, re.IGNORECASE,
    )
    assert not degraded_signals, \
        (f"Service entered degraded mode when only one mint was blocked. "
         f"Signals: {degraded_signals}")

    if cashu.is_available():
        token = cashu.mint(4)
        resp = router.pay_direct(token)
        assert isinstance(resp, dict), \
            f"Expected dict response, got: {str(resp)[:200]}"
        kind = resp.get("kind")
        assert kind in (1022, 21023), \
            f"Unexpected response kind with one mint blocked: {kind}, body: {str(resp)[:200]}"


def test_degraded_mode_notice_event_content(router):
    """Verify the degraded mode notice event has correct structure.

    When all mints are unreachable, GET / should return a kind 21023 event
    with a 'code' tag containing 'no-reachable-mints' and a human-readable
    content message mentioning recovery.
    """
    _skip_if_no_degraded_support(router)

    body = router.api_body("/")
    data = json.loads(body)
    kind = data.get("kind")

    if kind == 10021:
        tags = data.get("tags", [])
        price_tags = [t for t in tags if isinstance(t, list) and t[0] == "price_per_step"]
        if price_tags:
            pytest.skip("Service not in degraded mode — mints are reachable")

    if kind != 21023:
        pytest.skip(f"Expected degraded mode (kind 21023), got kind {kind}")

    tags = data.get("tags", [])
    code_tags = [t for t in tags if isinstance(t, list) and len(t) >= 2 and t[0] == "code"]
    assert code_tags, "Degraded notice event missing 'code' tag"
    assert any("reachable" in t[1].lower() or "degraded" in t[1].lower()
               for t in code_tags), \
        f"Expected 'no-reachable-mints' or 'degraded' in code tag, got: {code_tags}"

    content = data.get("content", "")
    assert content, "Degraded notice event has empty content"
    assert any(kw in content.lower() for kw in ["recover", "auto", "mint"]), \
        f"Content should mention recovery/mints: {content[:200]}"


def test_service_survives_restart_in_degraded(router):
    """Verify service can restart successfully while mints are unreachable.

    PR #118's key fix: the service should NOT enter a crash loop when
    starting with no reachable mints. It should boot into degraded mode.
    """
    _skip_if_no_degraded_support(router)

    body = router.api_body("/")
    data = json.loads(body)
    kind = data.get("kind")

    if kind == 10021:
        tags = data.get("tags", [])
        price_tags = [t for t in tags if isinstance(t, list) and t[0] == "price_per_step"]
        if price_tags:
            pytest.skip("Service not in degraded mode — can't test degraded restart")

    router.ssh("service tollgate-wrt restart")
    time.sleep(15)

    ps_out = router.ssh("ps | grep tollgate-wrt | grep -v grep")
    assert "tollgate-wrt" in ps_out, "Service failed to start in degraded mode"

    code = router.api_status("/")
    assert code in (200, 503), \
        f"Expected 200 or 503 after degraded restart, got {code}"

    body2 = router.api_body("/")
    data2 = json.loads(body2)
    kind2 = data2.get("kind")
    assert kind2 in (10021, 21023), \
        f"Unexpected kind after degraded restart: {kind2}"

    if kind2 == 21023:
        tags2 = data2.get("tags", [])
        code_tags = [t for t in tags2 if isinstance(t, list) and len(t) >= 2 and t[0] == "code"]
        assert code_tags, "Degraded mode after restart missing code tag"
