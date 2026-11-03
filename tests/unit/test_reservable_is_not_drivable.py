"""A lane you can reserve but cannot drive is not a lane.

Measured on `archy-vm` (2026-11-03): labgrid `acquire` succeeds and `release`
succeeds, the guest answers ping, and its MAC matches a running qemu process —
yet every SSH key on this host is rejected. A scheduler that trusted "the
reservation succeeded" would hand that lane to a test run that then cannot reach
its device, and the failure would surface as a test error rather than a capacity
mistake.

The same shape exists one level up: `reachable_unclaimed` counts lanes the
coordinator can hand out. That is a claim about *reservability*. It must never be
promoted into `idle_routers`, `idle_guests`, or `capacity`, because those are
claims about a device we can actually operate.

These tests pin the separation, using the real numbers measured on the bench:
3 unclaimed routers, 0 idle, and one guest that reserves cleanly but is not
drivable.
"""

from __future__ import annotations

from lib.fleet_capacity import FleetSummary, ResourceKind, State, Verdict


def _verdict(state: State, *, kind=ResourceKind.ROUTER, resource_id="r", capacity=0) -> Verdict:
    return Verdict(
        fleet_id="x280-bench",
        resource_id=resource_id,
        kind=kind,
        state=state,
        capacity=capacity,
        reasons=(),
        detail="",
    )


def _summary(**overrides) -> FleetSummary:
    fields = dict(
        fleet_id="x280-bench",
        configured_places=4,
        coordinator_reachable=True,
        exported_available=3,
        idle_routers=0,
        idle_guests=0,
        new_vm_slots=0,
        excluded=(),
    )
    fields.update(overrides)
    return FleetSummary(**fields)


def test_the_measured_bench_split_is_three_unclaimed_zero_idle():
    """The exact state measured on the live coordinator."""
    summary = _summary(
        excluded=(
            _verdict(State.EXPORTED_AVAILABLE, resource_id="gl-mt3000"),
            _verdict(State.EXPORTED_AVAILABLE, resource_id="comfast"),
            _verdict(State.EXPORTED_AVAILABLE, resource_id="tollgate-af92"),
            # archy-vm: reserves cleanly, but no guest observation -> unknown.
            _verdict(State.UNKNOWN, kind=ResourceKind.GUEST, resource_id="archy-vm"),
        )
    )
    assert summary.reachable_unclaimed == 3
    assert summary.idle_routers == 0
    assert summary.idle_guests == 0


def test_a_reservable_guest_is_not_an_idle_guest():
    """`acquire` succeeding is not evidence the device can be driven."""
    summary = _summary(
        excluded=(_verdict(State.UNKNOWN, kind=ResourceKind.GUEST, resource_id="archy-vm"),)
    )
    assert summary.idle_guests == 0
    assert summary.reachable_unclaimed == 0, (
        "a guest with no telemetry must not be counted by the router-lane signal"
    )


def test_unclaimed_never_exceeds_the_lanes_the_coordinator_offers():
    """Guard against the count drifting above what was actually observed."""
    summary = _summary(
        excluded=(
            _verdict(State.EXPORTED_AVAILABLE, resource_id="gl-mt3000"),
            _verdict(State.EXPORTED_AVAILABLE, resource_id="comfast"),
        )
    )
    assert summary.reachable_unclaimed == 2
    assert summary.reachable_unclaimed <= summary.configured_places
