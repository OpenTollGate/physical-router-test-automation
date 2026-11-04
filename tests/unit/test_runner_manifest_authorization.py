"""Real-schema runner authorization regressions; no coordinator or DUT I/O."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from lib.fleet_authority import FleetAuthority, HostSpec, LaneSpec
from lib.fleet_capacity import load_manifest
from lib.run_admission import AdmissionRefused, resolve_target

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("authorization_runner", ROOT / "scripts/run-profile.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def setup(tmp_path, *, actions=("observe", "acquire", "release"), registered_fleet="test_FLEET_A", registered_resource="test_PLACE_A1"):
    doc = yaml.safe_load((ROOT / "config/fleets.example.yaml").read_text().replace("REPLACE_ME_", "test_"))
    doc["fleets"][0]["permitted_actions"] = list(actions)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(doc))
    # Validate with the real loader, not a shape double.
    load_manifest(path)
    auth = FleetAuthority(tmp_path / "authority.db")
    auth.reconcile()
    budget = dict(vcpus_total=8, ram_mib_total=16384, disk_mib_total=200000, inodes_total=1000000)
    auth.register_host(HostSpec(**budget))
    auth.register_lane(LaneSpec(lane_id="lane-a", fleet_id=registered_fleet, resource_id=registered_resource, **budget))
    args = SimpleNamespace(fleet_manifest=str(path), fleet="test_FLEET_A", lane="lane-a", resource="test_PLACE_A1", domain=None, demand_vcpus=1, demand_ram_mib=128, demand_disk_mib=256, demand_inodes=1, authority_store=str(tmp_path / "authority.db"), lease_ttl=900)
    return args, auth, budget


@pytest.mark.parametrize("actions", [("observe",), ("observe", "acquire"), ("observe", "release")])
def test_execution_requires_acquire_and_release(tmp_path, actions):
    args, auth, _ = setup(tmp_path, actions=actions)
    with pytest.raises(AdmissionRefused, match="action_not_permitted"):
        runner.admission_from_args(args, "denied").acquire()
    assert auth.allocations() == ()


@pytest.mark.parametrize("field,value", [("fleet", "undeclared"), ("resource", "undeclared"), ("resource", "test_PLACE_B1")])
def test_explicit_target_cannot_escape_manifest(tmp_path, field, value):
    args, auth, _ = setup(tmp_path)
    setattr(args, field, value)
    with pytest.raises(AdmissionRefused, match="not_authorized"):
        runner.admission_from_args(args, "denied").acquire()
    assert auth.allocations() == ()


@pytest.mark.parametrize("overrides", [{"registered_fleet": "other"}, {"registered_resource": "other"}])
def test_registered_lane_identity_must_match(tmp_path, overrides):
    args, auth, _ = setup(tmp_path, **overrides)
    with pytest.raises(AdmissionRefused, match="lane_identity_mismatch"):
        runner.admission_from_args(args, "denied").acquire()
    assert auth.allocations() == ()


@pytest.mark.parametrize("domains", [None, ["extra"]])
def test_cli_cannot_omit_declared_shared_conflicts(tmp_path, domains):
    args, auth, budget = setup(tmp_path)
    args.domain = domains
    auth.register_lane(LaneSpec(lane_id="blocker", fleet_id="other", resource_id="other", **budget))
    from lib.fleet_authority import Demand
    decision = auth.try_reserve(lane_id="blocker", run_id="blocker", demand=Demand(vcpus=1, ram_mib=128, disk_mib=256, inodes=1), domains=("test_CONFLICT_RF_A",))
    assert decision.granted
    with pytest.raises(AdmissionRefused, match="domain_conflict"):
        runner.admission_from_args(args, "denied").acquire()
    assert len(auth.allocations()) == 1


def test_authorized_target_retains_manifest_and_extra_domains(tmp_path):
    args, auth, _ = setup(tmp_path)
    args.domain = ["extra", "extra"]
    admission = runner.admission_from_args(args, "allowed")
    allocation = admission.acquire()
    assert set(allocation.domains) == {"extra", "test_CONFLICT_RF_A"}
    admission.release()


def test_nonfleet_profile_remains_optional():
    assert runner.admission_from_args(SimpleNamespace(fleet_manifest=None), "legacy") is None
