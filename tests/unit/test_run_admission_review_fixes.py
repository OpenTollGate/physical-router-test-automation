"""Regression tests for the *runner-side* findings of the PR #334 cold review.

The review's other findings were fixed in ``test_fleet_observe_review_fixes.py``
(observation slice). These are the ones in the admission seam and the
production runner, which the same PR carries:

* HIGH  ``scripts/run-profile.py`` called ``admission.mark_started()`` outside
        the ``try/finally`` that releases the lease, so anything it raised
        leaked the lane for the whole lease TTL and exited with a traceback
        instead of ``ADMISSION_EXIT``.
* HIGH  ``demand_from_mapping(..., require_all=False)`` filled absent
        dimensions from ``DEFAULT_DEMAND``: undeclared demand silently became a
        declared number, in the over-admission direction.
* MEDIUM ``try_reserve(ttl_s=0)`` raised ``ValueError`` while every other
        refusal is a ``Decision(False, reason=...)``; the runner only catches
        ``AdmissionRefused``, so ``--lease-ttl 0`` bypassed ``ADMISSION_EXIT``.
* MEDIUM ``try_reserve`` compared an idempotent retry against the CURRENT lane
        domains instead of the grant's own domains, so a lost-reply retry after
        any ``register_lane`` became ``duplicate_run_id``.
* MEDIUM ``run-profile.py`` took (and completed) a real, host-debited
        reservation on ``--dry-run``: nothing executed, but a concurrent run
        with an overlapping domain was refused for the lease duration.

Every test below is RED against 8dee66a.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from lib.fleet_authority import (
    Demand,
    FleetAuthority,
    HostSpec,
    LaneSpec,
    State,
)
from lib.run_admission import (
    Admission,
    AdmissionRefused,
    AdmissionRequest,
    demand_from_mapping,
)

REPO = Path(__file__).resolve().parents[2]
RUN_PROFILE = REPO / "scripts" / "run-profile.py"

DEMAND = Demand(vcpus=1, ram_mib=128, disk_mib=256, inodes=1)


def _authority(tmp_path, *, max_concurrent=1) -> FleetAuthority:
    auth = FleetAuthority(tmp_path / "authority.db")
    auth.reconcile()
    auth.register_host(
        HostSpec(vcpus_total=8, ram_mib_total=16384, disk_mib_total=200_000, inodes_total=1_000_000)
    )
    auth.register_lane(
        LaneSpec(
            lane_id="lane-a",
            fleet_id="fleet-a",
            resource_id="router-a",
            vcpus_total=8,
            ram_mib_total=16384,
            disk_mib_total=200_000,
            inodes_total=1_000_000,
            max_concurrent=max_concurrent,
        )
    )
    return auth


# ---------------------------------------------------------------------------
# HIGH: undeclared demand must never become a default
# ---------------------------------------------------------------------------
def test_partial_demand_can_never_be_defaulted_by_the_helper():
    """``demand_from_mapping`` must have no permissive branch at all.

    It once offered ``require_all=False``, which returned ``vcpus=2,
    disk_mib=4096, inodes=1`` for an input that declared only RAM. That is the
    C2 failure (undeclared demand treated as declared), pointing at
    over-admission: a permissive caller is charged LESS than a declared one, so
    it wins capacity races it should lose.
    """
    with pytest.raises(AdmissionRefused) as exc:
        demand_from_mapping({"ram_mib": 32768})
    assert exc.value.reason == "demand_incomplete"
    assert "vcpus" in exc.value.detail


def test_the_permissive_keyword_is_gone_not_merely_defaulted():
    """Dropping the default is not enough: the branch must not be selectable."""
    with pytest.raises(TypeError):
        demand_from_mapping({"ram_mib": 32768}, require_all=False)


def test_partial_demand_is_refused_without_the_flag_too():
    with pytest.raises(AdmissionRefused) as exc:
        demand_from_mapping({"ram_mib": 32768})
    assert exc.value.reason == "demand_incomplete"


# ---------------------------------------------------------------------------
# MEDIUM: a bad TTL is a refusal, not an exception
# ---------------------------------------------------------------------------
def test_zero_ttl_is_a_refusal_not_a_traceback(tmp_path):
    """Every other ``try_reserve`` rejection is a ``Decision``; this one was not."""
    auth = _authority(tmp_path)
    decision = auth.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1", ttl_s=0)
    assert decision.granted is False
    assert decision.reason == "ttl_invalid"
    assert "ttl" in decision.detail.lower()


def test_negative_ttl_is_a_refusal_not_a_traceback(tmp_path):
    auth = _authority(tmp_path)
    decision = auth.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1", ttl_s=-5)
    assert decision.granted is False
    assert decision.reason == "ttl_invalid"


def test_refused_ttl_does_not_occupy_the_lane(tmp_path):
    auth = _authority(tmp_path)
    auth.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1", ttl_s=0)
    # A refusal must leave nothing behind for the next caller to trip over.
    assert auth.allocations(lane_id="lane-a") == ()


# ---------------------------------------------------------------------------
# MEDIUM: a lost-reply retry is still the same request after re-registration
# ---------------------------------------------------------------------------
def test_idempotent_replay_survives_lane_reregistration(tmp_path):
    """The grant's own domains are what identifies the grant.

    ``register_lane`` may add a conflict domain under a live allocation and is
    explicitly documented never to touch it. The retry path compared against the
    *current* lane row, so the same (run_id, demand, domains=()) request was
    answered ``duplicate_run_id ... with a different request`` — refusing the one
    case (a lost grant reply) idempotency exists for.
    """
    auth = _authority(tmp_path)
    first = auth.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1", domains=())
    assert first.granted, first.detail

    auth.register_lane(
        LaneSpec(
            lane_id="lane-a",
            fleet_id="fleet-a",
            resource_id="router-a",
            vcpus_total=8,
            ram_mib_total=16384,
            disk_mib_total=200_000,
            inodes_total=1_000_000,
            max_concurrent=1,
            domains=("rf-a",),
        )
    )

    replay = auth.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1", domains=())
    assert replay.granted, replay.detail
    assert replay.allocation is not None
    assert replay.allocation.fence == first.allocation.fence
    assert "idempotent" in replay.detail


def test_a_genuinely_different_request_is_still_a_conflict(tmp_path):
    """The idempotent path must not swallow a real second request."""
    auth = _authority(tmp_path)
    assert auth.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").granted
    other = auth.try_reserve(
        lane_id="lane-a", demand=Demand(vcpus=2, ram_mib=256, disk_mib=256, inodes=1), run_id="run-1"
    )
    assert other.granted is False
    assert other.reason == "duplicate_run_id"
    assert "different request" in other.detail


# ---------------------------------------------------------------------------
# HIGH: mark_started must not be able to leak the lease
# ---------------------------------------------------------------------------
def test_mark_started_is_inside_the_releasing_finally():
    """Structural pin on the production runner, via its real AST.

    A lease acquired but only released inside a ``finally`` that begins *after*
    ``mark_started()`` is not released when ``mark_started()`` raises. The
    behaviour is asserted in ``test_a_failing_mark_started_still_releases_the_
    lease``; this test pins the shape, so an edit cannot quietly move the call
    back out from under the release.
    """
    tree = ast.parse(RUN_PROFILE.read_text())

    def calls(node, attr):
        return [
            n
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr
        ]

    # Every `try` whose finally releases the admission lease.
    releasing = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Try)
        and any(calls(stmt, "release") for stmt in n.finalbody)
    ]
    assert releasing, "no try/finally in run-profile.py releases the admission lease"

    mark_started_lineno = [c.lineno for c in calls(tree, "mark_started")]
    assert mark_started_lineno, "run-profile.py never calls mark_started()"

    guarded = False
    for node in releasing:
        body_lines = {
            n.lineno for stmt in node.body for n in ast.walk(stmt) if hasattr(n, "lineno")
        }
        for lineno in mark_started_lineno:
            if lineno in body_lines:
                guarded = True
    assert guarded, (
        "mark_started() is called outside the try whose finally releases the "
        f"lease — a raise from it (line {mark_started_lineno}) leaks the lane "
        "for the whole TTL and exits with a traceback instead of ADMISSION_EXIT"
    )


def test_a_failing_mark_started_still_releases_the_lease(tmp_path):
    """Behavioural version of the same invariant, against the real Admission.

    The lease is acquired, ``activate`` is forced to fail, and the lane must be
    free afterwards. Before the fix the caller raised out of the function with
    the lease still ``RESERVED``.
    """
    from lib.run_admission import Target

    auth = _authority(tmp_path)
    admission = Admission(
        auth,
        AdmissionRequest(
            run_id="run-1",
            target=Target(fleet_id="fleet-a", lane_id="lane-a", resource_id="router-a"),
            demand=DEMAND,
            ttl_s=900.0,
        ),
    )
    admission.acquire()
    assert [a.run_id for a in auth.allocations(lane_id="lane-a")] == ["run-1"]

    def boom(*_a, **_kw):
        raise RuntimeError("activate exploded")

    auth.activate = boom  # type: ignore[method-assign]
    try:
        admission.mark_started()
    except RuntimeError:
        pass
    else:  # pragma: no cover - the fake must raise
        pytest.fail("the fake activate did not raise")

    # The runner's contract: whatever mark_started does, the lane is released.
    # `run-profile.py` releases with `clean=True`, and an un-activated RESERVED
    # lease must be ABORTED (never completed) — nothing ran, so nothing is owed.
    released = admission.release(clean=True, detail="mark_started failed")
    assert released is True
    allocations = auth.allocations(lane_id="lane-a")
    assert allocations, "the allocation row must still exist for audit"
    assert allocations[0].state is not State.RESERVED
    assert allocations[0].state is State.ABORTED


# ---------------------------------------------------------------------------
# MEDIUM: a dry run must not take a reservation
# ---------------------------------------------------------------------------
def test_dry_run_does_not_reserve():
    """``--dry-run`` executes nothing, so it must debit nothing.

    It previously acquired, activated and *completed* a real reservation: the
    authority recorded a clean run that never happened, and any concurrent run
    sharing a conflict domain was refused for the lease TTL.
    """
    source = RUN_PROFILE.read_text()
    start = source.index("--- admission: reserve the lane BEFORE anything executes")
    block = source[start : source.index("finally:", start)]
    assert "args.dry_run" in block, (
        "the admission block acquires unconditionally — a dry run takes a real, "
        "host-debited lease for work that will not happen"
    )
    # Order matters: the dry-run branch must come before the acquire, and
    # mark_started must sit inside the `try` whose `finally` releases.
    assert block.index("args.dry_run") < block.index("admission.acquire()")
    body = source[source.index("    try:\n        if admission is not None:", start) : source.index("finally:", start)]
    assert "mark_started()" in body, "mark_started() is not inside the releasing try"
    # `admission_from_args` constructs FleetAuthority, whose constructor mints
    # the store — a dry run must not reach it either. Check executable lines
    # only; the comment above the branch legitimately names the function.
    dry_branch = block[: block.index("else:")]
    code = "\n".join(
        line for line in dry_branch.splitlines() if not line.lstrip().startswith("#")
    )
    assert "admission_from_args" not in code, (
        "a dry run still builds the authority (which creates the store)"
    )
