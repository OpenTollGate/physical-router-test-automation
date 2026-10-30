"""MeshLink <-> TollGate seller acceptance tests.

Run against the QEMU lane:

    make meshlink-lab-prepare meshlink-lab-up meshlink-lab-provision
    pytest --lg-env=configs/labgrid/meshlink-lab.yaml tests/interop/

The tests talk to the management addresses (10.99.97.15/.16), so they do not
depend on the captive-portal state under test.
"""

from __future__ import annotations

import json

import pytest
from labgrid.driver import SSHDriver

pytestmark = [
    pytest.mark.story("P6"),
    pytest.mark.virtual_lab,
    pytest.mark.smoke,
    pytest.mark.timeout(600),
]

GATEWAY = "meshlink-gateway"
CLIENT = "meshlink-client"
MINT_URL = "http://10.99.98.2:8383"
GATE = "http://10.99.100.1:2121/"


def _ssh(env, name: str) -> SSHDriver:
    return env.get_target(name).get_driver(SSHDriver)


def test_advertisement_is_a_valid_cashu_offer(env):
    ssh = _ssh(env, GATEWAY)
    event = json.loads("\n".join(ssh.run_check("curl -fsS --max-time 5 http://127.0.0.1:2121/")))

    assert event["kind"] == 10021
    assert event["sig"]
    tags = {tag[0]: tag[1:] for tag in event["tags"] if tag}
    assert tags["metric"] == ["milliseconds"]
    assert int(tags["step_size"][0]) > 0
    offer = tags["price_per_step"]
    assert offer[0] == "cashu"
    assert int(offer[1]) > 0
    assert offer[2] == "sat"
    assert offer[3] == MINT_URL
    assert int(offer[4]) >= 1


def test_client_can_reach_the_pre_auth_mint(env):
    ssh = _ssh(env, CLIENT)
    ssh.run_check(f"curl -fsS --max-time 10 {MINT_URL}/v1/info")
    ssh.run_check("ip -4 addr show guest0 | grep -q '10.99.100.'")


def test_tollgate_agent_buys_and_gate_opens(env):
    ssh = _ssh(env, CLIENT)
    if ssh.run("command -v tollgate-agent")[2] != 0:
        pytest.skip("tollgate-agent not installed in the client image")

    setup = """
set -eu
mkdir -p /var/lib/tollgate/db /root/.tollgate
if [ ! -f /var/lib/tollgate/seed ]; then
  head -c 64 /dev/urandom > /var/lib/tollgate/seed
  chmod 600 /var/lib/tollgate/seed
fi
"""
    ssh.run_check(setup)
    _, stderr, code = ssh.run(
        "tollgate-agent --gate " + GATE + " --wallet cdk --test-mint "
        "--token-margin 20 --seed-file /var/lib/tollgate/seed "
        "--wallet-db /var/lib/tollgate/db --journal /root/.tollgate/journal.txt "
        "--attempt-id $(date +%s) --timeout-ms 20000",
        timeout=300,
    )
    assert code == 0, "\n".join(stderr)

    usage = "\n".join(ssh.run_check("curl -fsS --max-time 5 " + GATE.rstrip("/") + "/usage"))
    used, allotment = usage.strip().split("/")
    assert int(allotment) > 0
    assert 0 <= int(used) <= int(allotment)

    _, internet_err, internet_code = ssh.run(
        "curl -fsS -m 10 https://example.com >/dev/null", timeout=30
    )
    assert internet_code == 0, "\n".join(internet_err)
