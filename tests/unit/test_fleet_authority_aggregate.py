"""Aggregate host admission across INDEPENDENT lanes — the gap slice 2 leaves open.

**What this is, precisely:** an UNSUPPORTED CONFIGURATION, not a violated
configured budget. ``FleetAuthority`` has no host-pool concept at all — there is
no field, argument or row in which a operator could declare "this host has
512 MiB to give". So nothing was ignored; the capability does not exist. The
consequence is still real and load-bearing for the throughput claim, because a
runner that admits per-lane will overcommit a host.

Slice 2's ``FleetAuthority`` budgets **per lane**: ``LaneSpec.ram_mib_total`` is a
per-lane ceiling, and ``max_concurrent`` is a per-lane slot count. Two lanes each
declaring 256 MiB are each admitted 256 MiB of a host that only has 512 MiB to
give, and a third lane declaring another 256 MiB is admitted too — 768 MiB of
demand against 512 MiB of host. Nothing in the store computes the aggregate, so
"no oversubscription beyond the admitted budget" holds per lane and fails across
lanes.

That matters for the card's headline claim: a scheduler that admits two lanes on
a host sized for one has produced a busier machine, not a faster one, and the
refusal that should have happened never appears.

These tests encode the invariant the throughput benchmark needs:

    Before ANY guest process is spawned, the summed demand of all admitted
    lanes must fit the host's declared pool.

The ``xfail(strict=True)`` on the failing cases is deliberate documentation of an
owed capability, and it is deliberately NOT the qualification verdict: pytest
exits 0 on expected failures, so the qualification command in
``docs/FLEET-CAP-3-QUALIFICATION.md`` re-runs this file with ``--runxfail`` and
requires a nonzero exit. Strict xfail means that when a shared pool IS
implemented the tests turn XPASS and the suite breaks until someone flips the
marker — the gap cannot quietly disappear, and cannot quietly be claimed fixed.
"""
from __future__ import annotations

import pytest

from lib.fleet_authority import Demand, FleetAuthority, LaneSpec

#: 512 MiB of host RAM is the whole pool; each lane *claims* 256 MiB.
HOST_RAM_MIB = 512
LANE_RAM_MIB = 256


def _authority(tmp_path, lanes: int = 3) -> FleetAuthority:
    auth = FleetAuthority(tmp_path / "authority.db")
    # A fresh store refuses until something has looked at what is outstanding
    # (slice 2's startup gate); with nothing in flight that is a no-op pass.
    auth.reconcile()
    for i in range(lanes):
        auth.register_lane(
            LaneSpec(
                lane_id=f"lane-{i + 1}",
                fleet_id="test-fleet",
                resource_id=f"test-fleet/lane-{i + 1}",
                # Every lane gets the same per-lane budget: this is what slice 2
                # models, and it is the only thing it models.
                vcpus_total=1,
                ram_mib_total=LANE_RAM_MIB,
                disk_mib_total=1024,
                inodes_total=1000,
                max_concurrent=1,
            )
        )
    return auth


def _demand() -> Demand:
    return Demand(vcpus=1, ram_mib=LANE_RAM_MIB, disk_mib=1024, inodes=1)


def _admit(auth: FleetAuthority, lane_id: str, run_id: str):
    return auth.try_reserve(lane_id=lane_id, demand=_demand(), run_id=run_id, ttl_s=600)


class TestPerLaneBudgetIsWhatExists:
    """What slice 2 DOES enforce — asserted so the report cannot overstate it."""

    def test_one_lane_cannot_exceed_its_own_budget(self, tmp_path):
        auth = _authority(tmp_path)
        assert _admit(auth, "lane-1", "r1").granted is True
        # A second claim on the SAME lane is refused by that lane's slot count.
        second = _admit(auth, "lane-1", "r2")
        assert second.granted is False
        assert second.reason == "lane_busy"

    def test_a_single_lane_refuses_demand_above_its_budget(self, tmp_path):
        auth = _authority(tmp_path)
        decision = auth.try_reserve(
            lane_id="lane-1",
            demand=Demand(vcpus=1, ram_mib=LANE_RAM_MIB + 1, disk_mib=1024, inodes=1),
            run_id="too-big",
            ttl_s=600,
        )
        assert decision.granted is False
        assert decision.reason == "resource_vector_insufficient"


class TestAggregateHostBudgetIsMissing:
    def test_two_lanes_each_within_budget_are_both_admitted(self, tmp_path):
        """The intended case: 2 x 256 = 512 fits the pool. Must stay allowed."""
        auth = _authority(tmp_path)
        assert _admit(auth, "lane-1", "r1").granted is True
        assert _admit(auth, "lane-2", "r2").granted is True

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "UNIMPLEMENTED: FleetAuthority has no host-level pool. Three lanes of "
            "256 MiB are admitted on a 512 MiB host because each lane only sees "
            "its own budget. Slice 2 gap found during slice 3 qualification."
        ),
    )
    def test_third_lane_is_refused_once_the_host_pool_is_exhausted(self, tmp_path):
        auth = _authority(tmp_path)
        assert _admit(auth, "lane-1", "r1").granted is True
        assert _admit(auth, "lane-2", "r2").granted is True
        # 2 x 256 = 512 = the whole pool. This MUST be refused.
        third = _admit(auth, "lane-3", "r3")
        assert third.granted is False, (
            "three 256 MiB lanes were admitted on a 512 MiB host — the authority "
            "enforces per-lane budgets only, so nothing stops aggregate overcommit"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="UNIMPLEMENTED: no aggregate headroom view across lanes.",
    )
    def test_headroom_is_reported_for_the_host_not_only_per_lane(self, tmp_path):
        auth = _authority(tmp_path)
        assert _admit(auth, "lane-1", "r1").granted is True
        assert _admit(auth, "lane-2", "r2").granted is True
        lane3 = auth.headroom("lane-3")
        # Per-lane view says "256 MiB free" — true for the lane, false for the host.
        assert lane3.free["ram_mib"] >= LANE_RAM_MIB
        # What a scheduler actually needs: the host pool, minus everything admitted.
        assert hasattr(auth, "host_headroom"), (
            "no host-level headroom accessor: a runner cannot know the host is full"
        )
