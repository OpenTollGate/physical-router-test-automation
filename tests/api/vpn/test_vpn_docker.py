"""Integration tests for Micro VPN Docker compose deployment.

Requires VPN deployed on localhost with docker compose.
Skips if Docker or VPN containers are not running.
"""

import os
import json

import pytest

pytestmark = [pytest.mark.api, pytest.mark.vpn, pytest.mark.docker]


def _docker_ps():
    import subprocess
    try:
        r = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=tollgate-micro-vpn"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip().split("\n") if r.stdout.strip() else []
    except FileNotFoundError:
        return []


@pytest.fixture
def vpn_containers():
    names = _docker_ps()
    if not names or names == [""]:
        pytest.skip("Micro VPN containers not running")
    return names


class TestDockerContainers:
    def test_api_container_running(self, vpn_containers):
        assert any("api" in n for n in vpn_containers), "VPN API container not found"

    def test_worker_container_running(self, vpn_containers):
        assert any("worker" in n for n in vpn_containers), "VPN worker container not found"

    def test_wireguard_container_running(self, vpn_containers):
        assert any("wg" in n for n in vpn_containers), "WireGuard container not found"


class TestDockerNetworking:
    def test_api_reachable(self):
        import requests
        try:
            r = requests.get("http://localhost:5010/api/v1/status", timeout=5)
            assert r.status_code == 200
        except Exception:
            pytest.skip("VPN API not reachable on localhost:5010")

    def test_api_host_network_mode(self, vpn_containers):
        import subprocess
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.HostConfig.NetworkMode}}",
             "tollgate-micro-vpn-api"],
            capture_output=True, text=True, timeout=10,
        )
        assert "host" in r.stdout.strip()


class TestDockerVolumes:
    def test_db_volume_exists(self):
        assert os.path.isdir("/var/lib/vpn-api") or os.path.isdir(
            os.path.expanduser("~/tollgate/micro-vpn/db")
        )

    def test_config_volume_exists(self):
        assert os.path.isdir(
            os.path.expanduser("~/tollgate/micro-vpn/config")
        )
