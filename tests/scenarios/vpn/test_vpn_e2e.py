"""E2E tests for Micro VPN full subscription lifecycle.

Tests the complete flow: check ports -> subscribe -> verify payment quote -> verify status.
Requires VPN API deployed and accessible.
"""

import os
import time

import pytest

pytestmark = [pytest.mark.api, pytest.mark.vpn, pytest.mark.e2e]

VPN_API = os.environ.get("TOLLGATE_VPN_API_URL", "http://localhost:5010")


@pytest.fixture
def api_client():
    import requests
    return requests.Session()


class TestFullSubscribeLifecycle:
    def test_subscribe_and_check_status(self, api_client):
        r = api_client.get(f"{VPN_API}/api/v1/ports/available?limit=2")
        assert r.status_code == 200
        available = r.json()["ports"]
        if len(available) < 2:
            pytest.skip("Need at least 2 available ports")
        port1, port2 = available[0], available[1]

        r = api_client.post(f"{VPN_API}/api/v1/subscribe", json={
            "ports": [port1, port2],
            "target_ports": {str(port1): 8080, str(port2): 443},
            "duration_days": 30,
        })
        assert r.status_code == 201
        sub = r.json()
        sub_id = sub["subscription_id"]
        assert sub["state"] == "pending_payment"
        assert len(sub["ports"]) == 2
        assert sub["amount_sats"] > 0
        assert sub["bolt11_invoice"].startswith("lnbc") or len(sub["bolt11_invoice"]) > 20

        r = api_client.get(f"{VPN_API}/api/v1/subscribe/{sub_id}/status")
        assert r.status_code == 200
        status = r.json()
        assert status["subscription_id"] == sub_id
        assert status["state"] in ("pending_payment", "active")
        assert len(status["ports"]) == 2
        assert status["ports"][0]["public"] == port1

    def test_config_not_available_before_payment(self, api_client):
        r = api_client.get(f"{VPN_API}/api/v1/ports/available?limit=1")
        available = r.json()["ports"]
        if not available:
            pytest.skip("No available ports")
        port = available[0]

        r = api_client.post(f"{VPN_API}/api/v1/subscribe", json={"ports": [port]})
        assert r.status_code == 201
        sub_id = r.json()["subscription_id"]

        r = api_client.get(f"{VPN_API}/api/v1/subscribe/{sub_id}/config")
        assert r.status_code == 403

    def test_port_unavailable_after_subscription(self, api_client):
        r = api_client.get(f"{VPN_API}/api/v1/ports/available?limit=1")
        available = r.json()["ports"]
        if not available:
            pytest.skip("No available ports")
        port = available[0]

        api_client.post(f"{VPN_API}/api/v1/subscribe", json={"ports": [port]})

        r = api_client.get(f"{VPN_API}/api/v1/ports/available")
        assert port not in r.json()["ports"]

    def test_duplicate_port_returns_409(self, api_client):
        r = api_client.get(f"{VPN_API}/api/v1/ports/available?limit=1")
        available = r.json()["ports"]
        if not available:
            pytest.skip("No available ports")
        port = available[0]

        r1 = api_client.post(f"{VPN_API}/api/v1/subscribe", json={"ports": [port]})
        assert r1.status_code == 201

        r2 = api_client.post(f"{VPN_API}/api/v1/subscribe", json={"ports": [port]})
        assert r2.status_code == 409
        assert "not available" in r2.json()["error"].lower()


class TestRenewalFlow:
    def test_renew_nonexistent_returns_404(self, api_client):
        r = api_client.post(f"{VPN_API}/api/v1/subscribe/99999/renew", json={"duration_days": 30})
        assert r.status_code == 404

    def test_renew_pending_returns_400(self, api_client):
        r = api_client.get(f"{VPN_API}/api/v1/ports/available?limit=1")
        available = r.json()["ports"]
        if not available:
            pytest.skip("No available ports")
        port = available[0]

        r = api_client.post(f"{VPN_API}/api/v1/subscribe", json={"ports": [port]})
        sub_id = r.json()["subscription_id"]

        r = api_client.post(f"{VPN_API}/api/v1/subscribe/{sub_id}/renew", json={"duration_days": 30})
        assert r.status_code == 400


class TestAdminEndpoints:
    def test_admin_subscriptions(self, api_client):
        r = api_client.get(f"{VPN_API}/api/v1/admin/subscriptions")
        assert r.status_code == 200
        data = r.json()
        assert "subscriptions" in data
        assert "total" in data

    def test_admin_expiring(self, api_client):
        r = api_client.get(f"{VPN_API}/api/v1/admin/expiring?hours=48")
        assert r.status_code == 200
        data = r.json()
        assert "expiring" in data
        assert "total" in data
