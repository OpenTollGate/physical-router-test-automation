import json
import time
import os
import pytest

pytestmark = [pytest.mark.api, pytest.mark.extended]


def _lnurlw_code():
    ts = int(time.time() * 1000)
    return f"lnurlwDP3qXmWG{ts}"


def _skip_no_radclient(r):
    if r.returncode == -1 and "not found" in r.stderr:
        pytest.skip("radclient not installed locally")


def _wg_connect(gateway_http, token, mac):
    payload = json.dumps({"token": token, "mac": mac}).encode()
    code, body = gateway_http("POST", "/v1/wg/connect", data=payload, headers={
        "Content-Type": "application/json",
    })
    return code, body


def _wg_disconnect(gateway_http, pubkey):
    payload = json.dumps({"pubkey": pubkey}).encode()
    code, body = gateway_http("POST", "/v1/wg/disconnect", data=payload, headers={
        "Content-Type": "application/json",
    })
    return code, body


def test_wg_connect_response_fields(gateway_http, radtest, unique_mac):
    code_lnurlw = _lnurlw_code()
    r = radtest(code_lnurlw, "", mac=unique_mac)
    _skip_no_radclient(r)
    if "Access-Accept" not in r.stdout:
        pytest.skip("Cannot generate valid payment token for WG test (radclient unavailable or auth failed)")

    code, body = _wg_connect(gateway_http, code_lnurlw, unique_mac)
    if code == 0:
        pytest.skip(f"Gateway HTTP unreachable for WG connect: {body[:200]}")
    if code in (404, 501):
        pytest.skip(f"WG bridge API not available (HTTP {code})")
    assert code in (200, 201), f"Expected 200/201, got {code}: {body[:200]}"

    data = json.loads(body)
    assert "client_ip" in data, f"Missing client_ip: {data}"
    assert "session_timeout" in data, f"Missing session_timeout: {data}"
    assert "server_pubkey" in data, f"Missing server_pubkey: {data}"

    _wg_disconnect(gateway_http, data.get("server_pubkey", ""))


def test_wg_disconnect_response(gateway_http, radtest, unique_mac):
    code_lnurlw = _lnurlw_code()
    r = radtest(code_lnurlw, "", mac=unique_mac)
    _skip_no_radclient(r)
    if "Access-Accept" not in r.stdout:
        pytest.skip("Cannot generate valid payment token for WG test")

    code, body = _wg_connect(gateway_http, code_lnurlw, unique_mac)
    if code == 0 or code in (404, 501):
        pytest.skip(f"WG connect unavailable: code={code}")

    data = json.loads(body)
    pubkey = data.get("server_pubkey", "test_pubkey")

    dcode, dbody = _wg_disconnect(gateway_http, pubkey)
    assert dcode in (200, 204), f"Disconnect expected 200/204, got {dcode}: {dbody[:200]}"


def test_wg0_interface_exists(gateway_ssh):
    r = gateway_ssh("ip link show wg0 2>/dev/null || true", timeout=10)
    assert "wg0:" in r.stdout, f"wg0 interface not found: {r.stdout}"
