import json
import pytest

pytestmark = [pytest.mark.api, pytest.mark.smoke]


def test_healthz(gateway_http):
    code, body = gateway_http("GET", "/healthz")
    assert code == 200, f"Expected 200, got {code}: {body[:200]}"
    data = json.loads(body)
    assert data.get("status") == "ok", f"Health status not ok: {data}"


def test_readyz(gateway_http):
    code, body = gateway_http("GET", "/readyz")
    assert code == 200, f"Expected 200, got {code}: {body[:200]}"
    data = json.loads(body)
    assert "socket" in body or "path" in body, f"Ready response missing socket/path: {body[:200]}"


def test_metrics(gateway_http):
    code, body = gateway_http("GET", "/metrics")
    import os
    token = os.environ.get("TOLLGATE_ADMIN_TOKEN", "")
    if token:
        assert code in (200, 401), f"Expected 200 or 401, got {code}"
    else:
        assert code == 200, f"Expected 200 without admin token, got {code}: {body[:200]}"
    if code == 200:
        assert len(body) > 0, "Metrics body empty"


def test_daemon_service_active(gateway_ssh):
    r = gateway_ssh("systemctl is-active tollgate-daemon", timeout=10)
    assert r.returncode == 0, f"tollgate-daemon not active: {r.stdout.strip()}"
    assert "active" in r.stdout.lower(), f"Unexpected status: {r.stdout.strip()}"


def test_freeradius_service_active(gateway_ssh):
    r = gateway_ssh("systemctl is-active freeradius", timeout=10)
    assert r.returncode == 0, f"freeradius not active: {r.stdout.strip()}"
    assert "active" in r.stdout.lower(), f"Unexpected status: {r.stdout.strip()}"
