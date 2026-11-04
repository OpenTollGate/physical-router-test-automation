"""A lane nobody holds is not the same claim as a proven-idle router.

The strict rule is right: ``HEALTHY_IDLE`` requires ``vantage_ok is True``, which
requires the observer's MAC to appear in the portal's client list as a live
customer. On an empty portal it never appears, so ``vantage_ok`` is ``None`` and
the verdict is UNKNOWN.

That rule exists because of a real incident (2026-10-21): a tester that was
bypassed by design reported "no paying customers", and the reading was retracted.
It must not be relaxed.

When idle evidence is missing, report a separate, clearly weaker observation
without weakening the idle rule or implying that the lane can be scheduled:

  ``reachable_unclaimed`` -- exported and observed unacquired at a reachable
  coordinator. It says nothing about fleet-authority leases or eligibility,
  health or paying customers. It never sets ``capacity``, never counts as
  ``idle_routers``, and never appears in a qualification decision.

These tests pin the separation so the two can never be conflated.
"""

from __future__ import annotations

from lib.fleet_capacity import FleetSummary, ResourceKind, State, Verdict


def _summary(excluded=(), coordinator_reachable=True, idle_routers=0) -> FleetSummary:
    return FleetSummary(
        fleet_id="bench",
        configured_places=3,
        coordinator_reachable=coordinator_reachable,
        exported_available=2,
        idle_routers=idle_routers,
        idle_guests=0,
        new_vm_slots=0,
        excluded=excluded,
    )


def _verdict(state: State, resource_id: str = "r", capacity: int = 0) -> Verdict:
    return Verdict(
        fleet_id="bench",
        resource_id=resource_id,
        kind=ResourceKind.ROUTER,
        state=state,
        capacity=capacity,
        reasons=(),
        detail="",
    )


# ---------------------------------------------------------------------------
# The new signal
# ---------------------------------------------------------------------------


def test_reachable_unclaimed_counts_exported_unacquired_lanes():
    """Coordinator observation only: exported + reachable + unacquired."""
    summary = _summary(
        excluded=(
            _verdict(State.EXPORTED_AVAILABLE, resource_id="a"),
            _verdict(State.EXPORTED_AVAILABLE, resource_id="b"),
            # Unknown for an unrelated reason must NOT count.
            _verdict(State.UNKNOWN, resource_id="c"),
            # A lane someone holds is not unclaimed.
            _verdict(State.REFUSED, resource_id="d"),
        )
    )
    assert summary.reachable_unclaimed == 2


def test_reachable_unclaimed_is_zero_when_the_coordinator_is_down():
    """No coordinator means no reachability claim, whatever the exporters say."""
    summary = _summary(
        coordinator_reachable=False,
        excluded=(_verdict(State.EXPORTED_AVAILABLE, resource_id="a"),),
    )
    assert summary.reachable_unclaimed == 0


def test_reachable_unclaimed_never_becomes_idle_capacity():
    """The separation this whole module exists for."""
    summary = _summary(
        excluded=(_verdict(State.EXPORTED_AVAILABLE, resource_id="a"),),
        idle_routers=0,
    )
    assert summary.reachable_unclaimed == 1
    assert summary.idle_routers == 0, "an unclaimed lane is not a proven-idle router"
    assert summary.lanes_ready == 0
    assert summary.admissible_lanes == 0


def test_a_vantage_refusal_is_not_counted_as_unclaimed():
    """Exported but refused for vantage is not schedulable evidence.

    This is the exact shape measured on the bench: gl-mt3000 reports
    ``unknown_dimension -- health not proven`` and must never be promoted to a
    scheduling input by this new field.
    """
    summary = _summary(excluded=(_verdict(State.UNKNOWN, resource_id="gl-mt3000"),))
    assert summary.reachable_unclaimed == 0
