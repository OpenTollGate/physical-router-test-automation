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
    return router.ssh(
        f"curl -s -X POST '{router.backend_url('/')}' "
        f"-H 'Content-Type: application/json' "
        f"-d '{{\"kind\":21000,\"tags\":[[\"payment\",\"{token}\"]],\"content\":\"\"}}'"
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
        code = router.ssh(
            "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/ 2>/dev/null",
            timeout=5,
        )
        if not code.strip().startswith("2"):
            pytest.skip("LuCI admin UI not available on port 8080")
    except Exception:
        pytest.skip("Cannot check LuCI availability")


def skip_if_no_sessions_json(router):
    try:
        out = router.ssh("ls /etc/tollgate/sessions.json 2>/dev/null", timeout=5)
        if not out.strip():
            pytest.skip("No /etc/tollgate/sessions.json (backend uses in-memory sessions)")
    except Exception:
        pytest.skip("Cannot check sessions.json")
