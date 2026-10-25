from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def test_bench_scripts_and_units_exist():
    expected = [
        "scripts/openbao-approle-login.sh",
        "scripts/openbao-approle-apply.sh",
        "scripts/prta-bench-run.sh",
        "scripts/prta-quiesce-watchdog.sh",
        "scripts/prta-bench-routes.sh",
        "scripts/bench/apply-mgmt-firewall.py",
        "deploy/systemd/prta-bench-routes.service",
        "deploy/systemd/prta-quiesce.service",
        "deploy/systemd/prta-quiesce.timer",
        "deploy/x280/install-bench.sh",
        "config/routers.fleet.example.json",
        "docs/FLEET-TEST-ROUTERS.md",
        "docs/X280-BENCH.md",
    ]
    for rel in expected:
        path = ROOT / rel
        assert path.is_file(), rel
    for rel in (
        "scripts/openbao-approle-login.sh",
        "scripts/prta-bench-run.sh",
        "scripts/prta-quiesce-watchdog.sh",
        "scripts/prta-bench-routes.sh",
        "deploy/x280/install-bench.sh",
    ):
        assert (ROOT / rel).stat().st_mode & 0o111, rel


def test_approle_policy_is_test_router_only():
    policy = (ROOT / "config" / "openbao-policy.hcl").read_text()
    assert 'path "secret/data/fleet/test-routers-*"' in policy
    assert 'path "secret/data/fleet/mt6000' not in policy.lower()
    apply = (ROOT / "scripts" / "openbao-approle-apply.sh").read_text()
    assert "physical-router-automation" in apply
    assert "secret_id_ttl=24h" in apply
    assert "bind_secret_id=true" in apply


def test_bench_wrapper_requires_lease_and_scoped_key():
    body = (ROOT / "scripts" / "prta-bench-run.sh").read_text()
    assert "require_hardware_lock()" in body
    assert "openbao-approle-login.sh" in body
    assert "secret/fleet/test-routers-bench" in body
    assert "TOLLGATE_SSH_KEY" in body
    assert "quiesce-router.py" in body
    assert "--allow-unlocked" in body
    # The key is removed on every exit, after quiesce.
    assert "trap" in body
    assert 'rm -f "$KEY_FILE"' in body
    assert body.index("quiesce-router.py") < body.index('rm -f "$KEY_FILE"')
    assert "tail -c 1" in body


def test_inventory_example_excludes_mt6000_and_has_capabilities():
    doc = json.loads((ROOT / "config" / "routers.fleet.example.json").read_text())
    routers = doc["routers"]
    assert sorted(routers) == [
        "comfast-b3ba",
        "e750-lab",
        "tollgate-9bd4",
        "tollgate-af92",
    ]
    assert "mt6000" not in routers
    assert not any(r.get("sshHost") == "192.168.50.1" for r in routers.values())
    assert '"192.168.50.1"' not in json.dumps(routers)
    assert routers["e750-lab"]["capabilities"]["wifi_5g"] is False
    assert routers["e750-lab"]["capabilities"]["cellular"] is False
    assert routers["e750-lab"]["openwrtProfile"] == "glinet_gl-e750"
    assert routers["tollgate-9bd4"]["sshHost"] == "10.110.223.1"
    assert routers["tollgate-af92"]["mgmtZone"] == "wan"
    assert routers["comfast-b3ba"]["role"] == "router"
    assert routers["comfast-b3ba"]["tollgate"] is True
    assert "ssh-rsa" in " ".join(routers["comfast-b3ba"]["sshOptions"])


def test_mgmt_firewall_uses_protected_guard():
    body = (ROOT / "scripts" / "bench" / "apply-mgmt-firewall.py").read_text()
    assert "assert_not_protected" in body
    assert "prta_mgmt" in body
    assert "prta_mgmt_deny" in body
    assert "require_lock" in body
    assert "TOLLGATE_SSH_KEY" in body


def test_env_key_wins_over_inventory():
    for rel in ("scripts/quiesce-router.py", "scripts/install-fleet-keys.py"):
        body = (ROOT / rel).read_text()
        assert 'os.environ.get("TOLLGATE_SSH_KEY") or router.get("sshKey")' in body, rel
        assert "sshOptions" in body, rel
        assert "sshBind" in body, rel


def test_docs_call_out_mt6000_protection_and_quiesce():
    text = (ROOT / "docs" / "FLEET-TEST-ROUTERS.md").read_text()
    assert "MT6000" in text
    assert "production" in text
    assert "not in the test inventory" in text
    assert "quiesce" in text.lower()
    assert "x280" in text
    assert "T14" in text


def test_ap_arm_and_quiesce_hooks():
    q = (ROOT / "scripts" / "quiesce-router.py").read_text()
    assert "REMOTE_ENABLE" in q
    assert '"--enable"' in q
    assert "ENABLE_DONE" in q
    assert "QUIESCE_DONE" in q

    test_pr = (ROOT / "scripts" / "test-pr.sh").read_text()
    assert "--enable" in test_pr
    assert "cleanup_router_aps" in test_pr

    conftest = (ROOT / "tests" / "conftest.py").read_text()
    assert '"--enable"' in conftest

    runner = (ROOT / "scripts" / "run-with-quiesce.sh").read_text()
    assert "--enable" in runner
