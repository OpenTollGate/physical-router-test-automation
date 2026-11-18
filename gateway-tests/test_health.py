import json
import os

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


def test_daemon_service_active(service_state):
    # tollgate-daemon on systemd gateways; tollgate-wrt is the OpenWrt
    # init script name (physical routers). TOLLGATE_DAEMON_SERVICE overrides.
    name = os.environ.get("TOLLGATE_DAEMON_SERVICE", "tollgate-daemon")
    state, detail = service_state(name)
    if state == "absent" and name == "tollgate-daemon":
        # try the OpenWrt name before declaring failure
        state, detail = service_state("tollgate-wrt")
    assert state == "active", f"tollgate daemon not active ({state}): {detail}"


def test_freeradius_service_active(service_state):
    # freeradius ships with the radius-gateway species; the OpenWrt
    # tollgate-wrt module legitimately has none — skip there, fail where
    # it is present but down.
    state, detail = service_state("freeradius")
    if state == "absent":
        pytest.skip("freeradius absent on this gateway species (e.g. OpenWrt tollgate-wrt)")
    assert state == "active", f"freeradius not active ({state}): {detail}"
