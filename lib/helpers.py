import time
import json
import logging

import pytest

from lib.constants import PING_HOST

log = logging.getLogger("tollgate.helpers")


def is_session_event(resp: dict) -> bool:
    if not isinstance(resp, dict):
        return False
    tags = resp.get("tags", [])
    return resp.get("kind") == 1022 or any(
        isinstance(t, list) and len(t) > 0 and t[0] == "allotment" for t in tags
    )


def is_mac_lookup_failure(resp: dict) -> bool:
    if resp.get("kind") != 21023:
        return False
    tags = resp.get("tags", [])
    return any(isinstance(t, list) and len(t) >= 2 and t[0] == "code"
               and "mac-address-lookup-failed" in t[1] for t in tags)


def require_client_identity(router):
    if not router.phone_ip and not router.phone_mac:
        pytest.skip("Set TOLLGATE_CLIENT_IP/TOLLGATE_CLIENT_MAC or run a phone client on the TollGate AP")


def parse_json_or_fail(text, label="response", skip=False):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if skip:
            pytest.skip(f"Non-JSON {label}: {text[:200]}")
        else:
            pytest.fail(f"Non-JSON {label}: {text[:200]}")


def assert_session_active(router, ip=None):
    session = router.get_session(ip)
    if session.get("session_active") is True or "allotment" in str(session):
        return session
    if router.get_nds_state() == "Authenticated":
        return session
    raise AssertionError(f"Session not active: {str(session)[:200]}")


def assert_deauthenticated(router, mac=None):
    state = router.get_nds_state(mac)
    assert state != "Authenticated", f"Client still authenticated (state: {state})"


def pay_and_wait(router, adb, token, timeout=60, wake=True):
    if wake:
        adb.wake_and_unlock()
        time.sleep(2)
    resp = router.pay_direct(token)
    log.info(f"pay_direct: kind={resp.get('kind')}, tags={str(resp.get('tags', []))[:200]}")
    assert is_session_event(resp), \
        f"Payment failed: {str(resp)[:200]}"
    assert router.wait_for_auth(timeout=timeout), \
        f"Not authenticated after {timeout}s"
    return resp


def assert_internet(adb, host=PING_HOST, retries=3):
    for i in range(retries):
        if adb.ping(host):
            return True
        time.sleep(1)
    return False


def pay_expire_cutoff(router, adb, cashu, amount=3):
    token = cashu.mint(amount)
    pay_and_wait(router, adb, token)
    assert assert_internet(adb), "No internet during active session"
    return wait_expiry_and_verify_cutoff(router, adb), token


def wait_expiry_and_verify_cutoff(router, adb, post_expiry_wait=5):
    elapsed = router.wait_for_session_expiry()
    time.sleep(post_expiry_wait)

    for attempt in range(8):
        state = router.get_nds_state()
        if state != "Authenticated":
            log.info(f"ndsctl confirmed deauth at {elapsed + attempt}s (state={state})")
            break
        time.sleep(1)
    assert state != "Authenticated", \
        f"Client still authenticated after expiry (state: {state})"

    return _verify_internet_cutoff(router, adb, elapsed)


def _verify_internet_cutoff(router, adb, elapsed):
    gateway = router.gateway_ip
    for attempt in range(4):
        if not adb.ping(gateway, interface="wlan0"):
            log.info(f"Internet confirmed cut off via wlan0 at attempt {attempt}")
            return elapsed
        time.sleep(2)

    if not adb.ping(PING_HOST):
        log.info(f"Ping to {PING_HOST} failed — internet cut off (unbound ping)")
        return elapsed

    assert False, "Internet still accessible after session expiry (wlan0 + unbound ping)"


def metering_test_setup(router, adb, wifi, cashu, test_pricing_fn,
                        amount, step_size, metric):
    router.reset_state(adb=adb)
    test_pricing_fn(step_size=step_size, metric=metric)
    token = cashu.mint(amount)
    assert wifi.reconnect(skip_portal=True), f"WiFi reconnect failed for {metric} metering test"
    router.resolve_phone_client(adb)
    resp = pay_and_wait(router, adb, token)

    allotment = 0
    for tag in resp.get("tags", []):
        if isinstance(tag, list) and tag[0] == "allotment":
            allotment = int(tag[1])
    assert allotment > 0, f"Allotment is 0: {resp}"

    gross_expected = amount * step_size
    assert allotment <= gross_expected, \
        f"Allotment {allotment}{metric[0]} exceeds gross expected {gross_expected}{metric[0]}"

    return {"allotment": allotment, "metric": metric}, token


def post_payment_event(router, token):
    payload = json.dumps({"kind": 21000, "tags": [["payment", token]], "content": ""})
    return router.ssh(
        f"wget -O /tmp/tg-post-resp.txt --timeout=20 "
        f"--post-data='{payload}' "
        f"--header='Content-Type: application/json' "
        f"'{router.backend_url('/')}' 2>/dev/null; "
        f"cat /tmp/tg-post-resp.txt 2>/dev/null; "
        f"rm -f /tmp/tg-post-resp.txt"
    )


def skip_if_no_cli_socket(router):
    try:
        out = router.ssh("ls -S /var/run/tollgate.sock 2>/dev/null", timeout=5)
        if not out.strip():
            pytest.skip("No CLI socket at /var/run/tollgate.sock")
    except Exception:
        pytest.skip("Cannot check CLI socket")


def skip_if_no_luci(router):
    try:
        out = router.ssh(
            "wget --spider --timeout=5 http://127.0.0.1:8080/ 2>&1",
            timeout=5,
        )
        if "200 OK" not in out and "Remote file exists" not in out:
            pytest.skip("LuCI admin UI not available on port 8080")
    except Exception:
        pytest.skip("Cannot check LuCI availability")


INCIDENTS_URL = "https://github.com/OpenTollGate/tollgate-knowledgebase/tree/main/incidents"


def gate_bug_fix(fix_present, *, bug_id="", fix_pr=""):
    """Gate a test on the presence of a bug fix in the deployed firmware.

    If ``fix_present`` is False, marks the test as xfail (appears as
    "known issue" in reports).  If True, does nothing — the test runs
    normally and any failure is a real regression.

    Usage (inline probe)::

        from lib.helpers import gate_bug_fix

        def test_something(router):
            gate_bug_fix(
                _has_profit_share_validation(router),
                bug_id="profit-share-no-validation",
                fix_pr="PR #86",
            )
            # ... test body ...

    Usage (session-scoped, avoids repeating the probe)::

        @pytest.fixture(scope="session")
        def _validation_available(router):
            return _has_profit_share_validation(router)

        @pytest.fixture(autouse=True)
        def _gate(request, _validation_available):
            if request.node.originalname in _MUTATING_TESTS and not _validation_available:
                gate_bug_fix(
                    _validation_available,
                    bug_id="profit-share-no-validation",
                    fix_pr="PR #86",
                )

    Incident cross-reference — link to reported bugs in the knowledgebase::

        See: https://github.com/OpenTollGate/tollgate-knowledgebase/tree/main/incidents/2026-05-01-crypto-rand.md

    Args:
        fix_present: Boolean — True if the fix is confirmed in firmware.
        bug_id: Human-readable identifier (e.g. "crypto-rand-passwords").
        fix_pr: PR or commit that fixes the bug (e.g. "PR #111").
    """
    if not fix_present:
        reason = f"{bug_id} not fixed — expected in {fix_pr}" if fix_pr else f"{bug_id} not fixed in this firmware"
        pytest.xfail(reason=reason)


def skip_if_no_sessions_json(router):
    try:
        out = router.ssh("ls /etc/tollgate/sessions.json 2>/dev/null", timeout=5)
        if not out.strip():
            pytest.skip("No /etc/tollgate/sessions.json (backend uses in-memory sessions)")
    except Exception:
        pytest.skip("Cannot check sessions.json")


def is_full_merchant(router) -> bool:
    code = router.api_status("/")
    if code != 200:
        return False
    body = router.api_body("/")
    try:
        data = json.loads(body)
        if data.get("kind") != 10021:
            return False
        tags = data.get("tags", [])
        return any(
            isinstance(t, list) and len(t) > 0 and t[0] == "price_per_step"
            for t in tags
        )
    except json.JSONDecodeError:
        return False


def is_degraded(router) -> bool:
    body = router.api_body("/")
    try:
        data = json.loads(body)
        return data.get("kind") == 21023
    except json.JSONDecodeError:
        return False


def wait_for_full_merchant(router, timeout=120, interval=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_full_merchant(router):
            return True
        time.sleep(interval)
    return False


def wait_for_degraded(router, timeout=120, interval=5):
    import re
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_degraded(router):
            return True
        logs = router.get_tollgate_logs(lines=500)
        if re.search(r"(degraded|no reachable mints|all mints unreachable)", logs, re.IGNORECASE):
            return True
        time.sleep(interval)
    return False


def get_mint_ip_map(router):
    """Resolve configured mint URLs to IPs via nslookup on the router.

    Returns dict mapping mint URL -> resolved IP address.
    """
    import re
    from urllib.parse import urlparse

    cfg_raw = router.ssh("cat /etc/tollgate/config.json")
    cfg = json.loads(cfg_raw)
    urls = [m["url"] for m in cfg.get("accepted_mints", []) if "url" in m]
    ip_map = {}
    for url in urls:
        parsed = urlparse(url)
        hostname = parsed.hostname
        out = router.ssh(f"nslookup {hostname} 2>/dev/null || echo FAILED")
        ips = re.findall(r"Address:\s*(\d+\.\d+\.\d+\.\d+)", out)
        for ip in reversed(ips):
            if not ip.startswith("127."):
                ip_map[url] = ip
                break
    return ip_map


def block_mints(router, mint_ip_map):
    """Block all mint IPs via iptables OUTPUT REJECT. Returns list of (url, ip) rules."""
    rules = []
    for url, ip in mint_ip_map.items():
        router.ssh(f"iptables -I OUTPUT -d {ip} -p tcp --dport 443 -j REJECT")
        rules.append((url, ip))
    return rules


def unblock_mints(router, rules):
    """Remove iptables OUTPUT REJECT rules created by block_mints()."""
    for url, ip in rules:
        router.ssh(f"iptables -D OUTPUT -d {ip} -p tcp --dport 443 -j REJECT"
                   f" 2>/dev/null || true")


def skip_if_no_degraded_support(router):
    resp = router.get_tollgate_status()
    if resp.get("success") is not True:
        pytest.skip("tollgate status command not available (version predates PR #118)")
    raw = json.dumps(resp).lower()
    if not any(kw in raw for kw in ["degraded", "reachable", "mint_health"]):
        pytest.skip("No mint health tracking in status output (version predates PR #118)")
