from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib.protected_devices import (  # noqa: E402
    ProtectedDeviceError,
    assert_not_protected,
    is_protected,
)


def test_mt6000_and_control_hosts_are_protected():
    assert is_protected(board_name="glinet,gl-mt6000")
    assert is_protected(model="GL-MT6000")
    assert is_protected(macs=["94:83:c4:d1:57:56"])
    assert is_protected(addresses=["192.168.50.1"])
    assert is_protected(hostname="OpenMPTCProuter")
    assert is_protected(hostname="x280")
    assert is_protected(hostname="cobrador")
    assert is_protected(hostname="dq05")
    assert is_protected(hostname="T14Gen5")

    assert not is_protected(board_name="glinet,gl-e750", model="GL-E750")
    with pytest.raises(ProtectedDeviceError):
        assert_not_protected(board_name="glinet,gl-mt6000")
    assert_not_protected(board_name="glinet,gl-e750", model="GL-E750")


def _load_quiesce_module():
    path = ROOT / "scripts" / "quiesce-router.py"
    spec = importlib.util.spec_from_file_location("quiesce_router", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_quiesce_only_touches_ap_interfaces():
    mod = _load_quiesce_module()
    body = mod.REMOTE_QUIESCE
    assert 'mode")" = "ap"' in body
    assert "disabled=1" in body
    assert "wifi reload" in body
    assert "QUIESCE_DONE" in body
    # STA and LAN must not be disabled by this script.
    assert "mode=\"sta\"" not in body
    assert "network.lan" not in body


def test_fleet_ssh_key_list_is_public_and_unique():
    doc = yaml.safe_load((ROOT / "config" / "fleet_ssh_keys.yaml").read_text())
    hosts = doc["hosts"]
    for expected in ("t14", "bench", "cobrador", "t440", "dq05"):
        assert expected in hosts
    keys = [k["key"].strip() for h in hosts.values() for k in h["keys"]]
    assert keys
    assert all(k.startswith("ssh-") for k in keys)
    assert all("PRIVATE KEY" not in k for k in keys)
    assert len(keys) == len(set(keys))


def test_e750_profile_and_capabilities():
    profile = json.loads((ROOT / "config" / "routers.e750.example.json").read_text())
    router = profile["routers"]["e750-lab"]
    assert router["arch"] == "mips_24kc"
    assert router["openwrtTarget"] == "ath79/nand"
    assert router["openwrtProfile"] == "glinet_gl-e750"
    assert router["capabilities"]["wifi_5g"] is False
    assert router["capabilities"]["cellular"] is False

    caps = yaml.safe_load((ROOT / "config" / "router_capabilities.yaml").read_text())
    e750 = caps["e750-lab"]
    assert e750["package_formats"] == ["apk", "ipk"]
    assert e750["sysupgrade_default"] is False
    assert "serial U-Boot" in (ROOT / "docs" / "E750-LAB-PROFILE.md").read_text()


def test_openbao_is_test_router_only():
    policy = (ROOT / "config" / "openbao-policy.hcl").read_text()
    assert "secret/data/fleet/test-routers-*" in policy
    assert 'path "secret/data/fleet/mt6000' not in policy.lower()

    loader = (ROOT / "scripts" / "openbao-env.sh").read_text()
    assert "secret/fleet/test-routers" in loader
    assert "TOLLGATE_SSH_PASSWORD" in loader
    assert "TOLLGATE_LUCI_PASSWORD" in loader


def test_entrypoints_require_protection_lock_and_quiesce():
    test_pr = (ROOT / "scripts" / "test-pr.sh").read_text()
    assert "check-protected.py" in test_pr
    assert "require_hardware_lock" in test_pr
    assert "quiesce-router.py" in test_pr

    conftest = (ROOT / "tests" / "conftest.py").read_text()
    assert "quiesce-router.py" in conftest
    assert "TOLLGATE_QUIESCE_ON_EXIT" in conftest

    makefile = (ROOT / "Makefile").read_text()
    for target in ("quiesce:", "quiesce-all:", "fleet-keys:", "check-protected:"):
        assert target in makefile
