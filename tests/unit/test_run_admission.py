"""Runner admission seam — the refusals that stop an unaccounted test run.

These tests exist because the failure they prevent is expensive and silent:
two test runs starting on one host and fighting over a DUT. Everything here
asserts a *refusal* or an *exactly-once* property, not a happy path.
"""

from __future__ import annotations

import pytest

from lib.fleet_authority import (
    Demand,
    FleetAuthority,
    HostSpec,
    LaneSpec,
    State,
    UnknownRun,
)
from lib.run_admission import (
    Admission,
    AdmissionRefused,
    AdmissionRequest,
    demand_from_mapping,
    resolve_target,
)


def _authority(tmp_path, *, vcpus=4, ram=8192, disk=65536, max_concurrent=1) -> FleetAuthority:
    auth = FleetAuthority(tmp_path / "authority.db")
    auth.reconcile()
    auth.register_host(
        HostSpec(vcpus_total=vcpus, ram_mib_total=ram, disk_mib_total=disk, inodes_total=100_000)
    )
    auth.register_lane(
        LaneSpec(
            lane_id="lane-a",
            fleet_id="fleet-a",
            resource_id="router-a",
            vcpus_total=vcpus,
            ram_mib_total=ram,
            disk_mib_total=disk,
            max_concurrent=max_concurrent,
        )
    )
    return auth


def _request(**overrides) -> AdmissionRequest:
    from lib.run_admission import Target

    base = dict(
        run_id="run-1",
        target=Target(fleet_id="fleet-a", lane_id="lane-a", resource_id="router-a"),
        demand=Demand(vcpus=2, ram_mib=1024, disk_mib=4096, inodes=1),
    )
    base.update(overrides)
    return AdmissionRequest(**base)


# --------------------------------------------------------------------------- #
# demand declarations — absent is not zero
# --------------------------------------------------------------------------- #
def test_partial_demand_is_refused_not_defaulted():
    with pytest.raises(AdmissionRefused) as exc:
        demand_from_mapping({"vcpus": 2, "ram_mib": 1024})
    assert exc.value.reason == "demand_incomplete"
    assert "disk_mib" in exc.value.detail


def test_complete_demand_is_accepted():
    demand = demand_from_mapping({"vcpus": 2, "ram_mib": 1024, "disk_mib": 4096, "inodes": 1})
    assert (demand.vcpus, demand.ram_mib, demand.disk_mib) == (2, 1024, 4096)


def test_zero_demand_is_refused_by_the_authority():
    with pytest.raises(AdmissionRefused) as exc:
        demand_from_mapping({"vcpus": 0, "ram_mib": 1024, "disk_mib": 4096, "inodes": 1})
    assert exc.value.reason == "demand_invalid"


def test_non_integer_demand_is_refused():
    with pytest.raises(AdmissionRefused) as exc:
        demand_from_mapping({"vcpus": "2", "ram_mib": 1024, "disk_mib": 4096, "inodes": 1})
    assert exc.value.reason == "demand_invalid"


# --------------------------------------------------------------------------- #
# target resolution — never guess which DUT a run gets
# --------------------------------------------------------------------------- #
class _Place:
    def __init__(self, resource_id: str):
        self.resource_id = resource_id


class _FleetEntry:
    def __init__(self, places):
        self.places = places


class _Manifest:
    def __init__(self, fleets):
        self.fleets = fleets


def test_single_lane_can_be_inferred_from_the_manifest():
    manifest = _Manifest({"fleet-a": _FleetEntry([_Place("router-a")])})
    target = resolve_target(fleet_id=None, lane_id=None, resource_id=None, manifest=manifest)
    assert (target.fleet_id, target.lane_id, target.resource_id) == (
        "fleet-a",
        "router-a",
        "router-a",
    )


def test_multiple_fleets_are_ambiguous_and_refused():
    manifest = _Manifest(
        {"fleet-a": _FleetEntry([_Place("router-a")]), "fleet-b": _FleetEntry([_Place("b1")])}
    )
    with pytest.raises(AdmissionRefused) as exc:
        resolve_target(fleet_id=None, lane_id=None, resource_id=None, manifest=manifest)
    assert exc.value.reason == "fleet_ambiguous"


def test_multiple_places_are_ambiguous_and_refused():
    manifest = _Manifest({"fleet-a": _FleetEntry([_Place("r1"), _Place("r2")])})
    with pytest.raises(AdmissionRefused) as exc:
        resolve_target(fleet_id=None, lane_id=None, resource_id=None, manifest=manifest)
    assert exc.value.reason == "lane_ambiguous"


def test_missing_names_without_manifest_are_refused():
    with pytest.raises(AdmissionRefused) as exc:
        resolve_target(fleet_id=None, lane_id=None, resource_id=None, manifest=None)
    assert exc.value.reason == "target_incomplete"


# --------------------------------------------------------------------------- #
# reservation lifecycle
# --------------------------------------------------------------------------- #
def test_acquire_then_release_leaves_the_lane_free(tmp_path):
    auth = _authority(tmp_path)
    adm = Admission(auth, _request())
    adm.acquire()
    assert auth.allocation("run-1").state is State.RESERVED
    assert adm.release(clean=True) is True
    assert auth.allocation("run-1").state is State.ABORTED


def test_started_run_that_finishes_releases_the_lane(tmp_path):
    auth = _authority(tmp_path)
    adm = Admission(auth, _request())
    adm.acquire()
    adm.mark_started()
    assert auth.allocation("run-1").state is State.ACTIVE
    adm.release(clean=True)
    assert auth.allocation("run-1").state is State.RELEASED


def test_dirty_release_leaves_the_lane_occupied(tmp_path):
    """A run that cannot confirm teardown must not hand the DUT to the next one."""
    auth = _authority(tmp_path)
    adm = Admission(auth, _request())
    adm.acquire()
    adm.mark_started()
    assert adm.release(clean=False, detail="release unconfirmed") is False
    assert auth.allocation("run-1").state is State.CLEANUP_PENDING
    assert adm.cleanup_pending is True


def test_second_run_is_refused_while_the_first_holds_the_lane(tmp_path):
    auth = _authority(tmp_path)
    first = Admission(auth, _request())
    first.acquire()
    second = Admission(auth, _request(run_id="run-2"))
    with pytest.raises(AdmissionRefused) as exc:
        second.acquire()
    assert second.allocation is None
    assert exc.value.reason, "a refusal must carry a reason code"


def test_refused_run_does_not_touch_the_lane(tmp_path):
    auth = _authority(tmp_path)
    Admission(auth, _request()).acquire()
    second = Admission(auth, _request(run_id="run-2"))
    with pytest.raises(AdmissionRefused):
        second.acquire()
    # A refused run must leave NO trace: the strongest form of "did not touch
    # the lane" is that no allocation row exists for it at all.
    with pytest.raises(UnknownRun):
        auth.allocation("run-2")


def test_release_without_acquire_is_a_noop(tmp_path):
    auth = _authority(tmp_path)
    adm = Admission(auth, _request())
    assert adm.release(clean=True) is True


def test_unacquired_release_cannot_mark_started(tmp_path):
    auth = _authority(tmp_path)
    adm = Admission(auth, _request())
    with pytest.raises(AdmissionRefused) as exc:
        adm.mark_started()
    assert exc.value.reason == "not_acquired"


def test_context_manager_releases_on_success(tmp_path):
    auth = _authority(tmp_path)
    with Admission(auth, _request()) as adm:
        adm.mark_started()
    assert auth.allocation("run-1").state is State.RELEASED


def test_context_manager_does_not_swallow_the_body_error(tmp_path):
    auth = _authority(tmp_path)
    with pytest.raises(RuntimeError):
        with Admission(auth, _request()) as adm:
            adm.mark_started()
            raise RuntimeError("test failed")
    # A failing test is not a dirty lane.
    assert auth.allocation("run-1").state is State.RELEASED


def test_lane_is_reusable_after_a_clean_release(tmp_path):
    auth = _authority(tmp_path)
    first = Admission(auth, _request())
    first.acquire()
    first.release(clean=True)
    second = Admission(auth, _request(run_id="run-2"))
    second.acquire()
    assert auth.allocation("run-2").state is State.RESERVED
