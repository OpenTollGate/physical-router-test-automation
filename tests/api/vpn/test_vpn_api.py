"""Integration tests for Micro VPN API endpoints.

Requires TOLLGATE_VPN_API_URL environment variable (default: http://localhost:5010).
"""

import os
import time

import pytest

pytestmark = [pytest.mark.api, pytest.mark.vpn]

VPN_API = os.environ.get("TOLLGATE_VPN_API_URL", "http://localhost:5010")


@pytest.fixture
def vpn_api():
    import requests
    return requests.Session()


class TestStatusEndpoint:
    def test_status_returns_200(self, vpn_api):
        r = vpn_api.get(f"{VPN_API}/api/v1/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"
        assert "server_ip" in data
        assert "wireguard_port" in data
        assert "cashu_mint_url" in data
        assert "price_per_port_sats" in data
        assert "duration_days" in data

    def test_status_has_port_info(self, vpn_api):
        r = vpn_api.get(f"{VPN_API}/api/v1/status")
        data = r.json()
        assert isinstance(data["available_ports"], int)
        assert isinstance(data["active_subscriptions"], int)
        assert isinstance(data["port_pool_ranges"], str)


class TestPortsEndpoint:
    def test_available_ports_returns_list(self, vpn_api):
        r = vpn_api.get(f"{VPN_API}/api/v1/ports/available")
        assert r.status_code == 200
        data = r.json()
        assert "ports" in data
        assert "total" in data
        assert isinstance(data["ports"], list)

    def test_available_ports_pagination(self, vpn_api):
        r = vpn_api.get(f"{VPN_API}/api/v1/ports/available?limit=5&offset=0")
        assert r.status_code == 200
        data = r.json()
        assert len(data["ports"]) <= 5
        assert data["limit"] == 5
        assert data["offset"] == 0

    def test_allocated_ports_returns_list(self, vpn_api):
        r = vpn_api.get(f"{VPN_API}/api/v1/ports/allocated")
        assert r.status_code == 200
        data = r.json()
        assert "allocated" in data
        assert "total" in data


class TestSubscribeEndpoint:
    def test_subscribe_no_ports_returns_400(self, vpn_api):
        r = vpn_api.post(f"{VPN_API}/api/v1/subscribe", json={})
        assert r.status_code == 400
        assert "error" in r.json()

    def test_subscribe_too_many_ports_returns_400(self, vpn_api):
        ports = list(range(10001, 10102))
        r = vpn_api.post(f"{VPN_API}/api/v1/subscribe", json={"ports": ports})
        assert r.status_code == 400

    def test_subscribe_creates_subscription(self, vpn_api):
        r = vpn_api.get(f"{VPN_API}/api/v1/ports/available?limit=1")
        if r.json()["total"] == 0:
            pytest.skip("No available ports")
        port = r.json()["ports"][0]
        r = vpn_api.post(f"{VPN_API}/api/v1/subscribe", json={"ports": [port], "duration_days": 30})
        assert r.status_code == 201
        data = r.json()
        assert "subscription_id" in data
        assert "client_id" in data
        assert port in data["ports"]
        assert data["state"] == "pending_payment"
        assert "bolt11_invoice" in data
        assert data["amount_sats"] > 0


class TestSubscriptionStatus:
    def test_status_nonexistent_returns_404(self, vpn_api):
        r = vpn_api.get(f"{VPN_API}/api/v1/subscribe/99999/status")
        assert r.status_code == 404

    def test_config_nonexistent_returns_404(self, vpn_api):
        r = vpn_api.get(f"{VPN_API}/api/v1/subscribe/99999/config")
        assert r.status_code == 404


class TestIndexPage:
    def test_index_returns_html(self, vpn_api):
        r = vpn_api.get(f"{VPN_API}/")
        assert r.status_code == 200
        assert "Micro VPN" in r.text or "VPN" in r.text
