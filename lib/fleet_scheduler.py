"""Fleet scheduler: staged admission, supervised execution, reconciliation.

Two phases, staged deliberately (``prta-fleet-capacity-audit.md``):

1. **host admission** — :class:`~lib.fleet_authority.FleetAuthority` reserves the
   lane, its RAM/CPU/disk vector and its conflict domains in one serialized
   SQLite transaction. Cheap, local, and the only thing that can refuse
   concurrency.
2. **place acquisition** — :class:`~lib.labgrid_venue.LabgridBench` acquires the
   physical place from the coordinator.

They are *not* one distributed transaction, and this module never pretends
otherwise. What it does instead:

* no command runs until BOTH phases have committed (``RESERVED`` → ``ACTIVE``);
* a lost acquisition reply is reconciled by identity, not assumed lost;
* a partial allocation is compensated using **our own** tokens only — the
  scheduler never releases a place it did not acquire;
* a release that cannot be confirmed is recorded as ``CLEANUP_PENDING`` and
  excludes the lane, because a TTL proves nobody renewed, not that nothing is
  still mutating the DUT;
* a fenced run is terminated (its whole process group) before anything else
  happens to the lane.

Nothing here announces cross-coordinator all-or-nothing atomicity.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, Mapping, Sequence

from lib.fleet_authority import (
    Allocation,
    AuthorityError,
    Demand,
    Fenced,
    FleetAuthority,
    NotGranted,
    State,
)
from lib.labgrid_venue import LabgridBench, LabgridBenchError

__all__ = [
    "FleetScheduler",
    "LaneQuarantined",
    "Lease",
    "PartialAllocation",
    "ReconcileReport",
    "ScheduleRequest",
]


class LaneQuarantined(AuthorityError):
    """The lane is excluded until its previous holder is proven cleaned up."""

    def __init__(self, lane_id: str, reason: str = ""):
        super().__init__(f"lane {lane_id!r} is quarantined: {reason or 'previous holder not cleaned up'}")
        self.lane_id = lane_id
        self.reason = reason


class PartialAllocation(AuthorityError):
    """One phase committed and the other did not.

    ``phase`` names the phase that failed: ``"acquire"`` (host admission was
    granted, the coordinator refused) or ``"activate"`` (the place was
    acquired, the authority refused to promote the claim). In both cases no
    test ran.
    """

    def __init__(self, phase: str, detail: str, *, cleanup_pending: bool = False):
        super().__init__(f"{phase} phase failed after partial allocation: {detail}")
        self.phase = phase
        self.detail = detail
        self.cleanup_pending = cleanup_pending


@dataclass(frozen=True)
class ScheduleRequest:
    """One predeclared topology asking for a lane."""

    fleet_id: str
    lane_id: str
    resource_id: str
    run_id: str
    demand: Demand
    domains: tuple[str, ...] = ()
    ttl_s: float = 900.0
    command: Sequence[str] = ()
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReconcileReport:
    """What a reconcile pass found, and what it was able to settle."""

    outstanding: tuple[Allocation, ...] = ()
    recycled: tuple[str, ...] = ()
    lanes_excluded: tuple[str, ...] = ()
    detail: str = ""


class Lease:
    """A committed allocation: authority slot + verified place.

    ``run()`` executes the command under supervision. The process group is
    separate, so killing a fenced or expired run can never reach unrelated
    processes.
    """

    def __init__(
        self,
        scheduler: "FleetScheduler",
        request: ScheduleRequest,
        allocation: Allocation,
        bench: LabgridBench,
    ):
        self.scheduler = scheduler
        self.request = request
        self.allocation = allocation
        self.bench = bench
        self._child: subprocess.Popen | None = None

    @property
    def active(self) -> bool:
        return self.bench.acquired

    @property
    def cleanup_pending(self) -> bool:
        return self.bench.cleanup_pending

    # -- supervision -------------------------------------------------------
    def _still_ours(self) -> bool:
        """True while our (run_id, fence) still authenticates."""
        try:
            current = self.scheduler.authority.allocation(self.request.run_id)
        except AuthorityError:
            return False
        return current.fence == self.allocation.fence and current.live

    def _terminate(self) -> None:
        child, self._child = self._child, None
        if child is None or child.poll() is not None:
            return
        # Own session → kill the group, never the process tree we share with
        # the caller (the audit's "cleanup never kills unrelated processes").
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(child.pid), signal.SIGTERM)
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(child.pid), signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                child.wait(timeout=10)

    def run(self, *, poll_s: float | None = None) -> int:
        """Run the command, refusing to outlive our authority.

        Any loss of authority (fence, expiry, quarantine) terminates the
        process group and raises — a zombie run must not keep mutating a DUT
        that the authority believes is free.
        """
        command = list(self.request.command)
        if not command:
            raise ValueError("ScheduleRequest.command is empty")
        if not self._still_ours():
            raise Fenced(f"allocation {self.request.run_id!r} lost its authority before start")

        interval = poll_s if poll_s is not None else self.scheduler.heartbeat_s
        self._child = subprocess.Popen(
            command,
            cwd=self.request.cwd,
            env={**os.environ, **dict(self.request.env)},
            start_new_session=True,
        )
        child = self._child
        try:
            while True:
                try:
                    rc = child.wait(timeout=interval)
                    return rc
                except subprocess.TimeoutExpired:
                    pass
                if not self._still_ours():
                    self._terminate()
                    raise Fenced(
                        f"allocation {self.request.run_id!r} was fenced while running — "
                        "child process group terminated"
                    )
                with contextlib.suppress(AuthorityError):
                    self.scheduler.authority.renew(
                        self.request.run_id, self.allocation.fence, ttl_s=self.request.ttl_s
                    )
        finally:
            if self._child is not None:
                self._terminate()


class FleetScheduler:
    """Admission + supervision for one host's lanes."""

    def __init__(
        self,
        *,
        authority: FleetAuthority,
        coordinator: str,
        bench_factory: Callable[[], LabgridBench],
        heartbeat_s: float = 30.0,
    ):
        self.authority = authority
        self.coordinator = coordinator
        self._bench_factory = bench_factory
        self.heartbeat_s = heartbeat_s

    # -- admission ---------------------------------------------------------
    def _reserve(self, request: ScheduleRequest) -> Allocation:
        decision = self.authority.try_reserve(
            lane_id=request.lane_id,
            demand=request.demand,
            run_id=request.run_id,
            domains=request.domains,
            ttl_s=request.ttl_s,
            owner_pid=os.getpid(),
        )
        if not decision.granted and decision.reason == "authority_needs_reconcile":
            # A fresh store, or one whose epoch was invalidated, refuses until
            # something has looked at what is outstanding. With nothing in
            # flight there is nothing to verify, so reconcile and retry once —
            # this is a startup step, not a bypass of the gate.
            self.authority.reconcile()
            if not any(a.occupies for a in self.authority.allocations()):
                decision = self.authority.try_reserve(
                    lane_id=request.lane_id,
                    demand=request.demand,
                    run_id=request.run_id,
                    domains=request.domains,
                    ttl_s=request.ttl_s,
                    owner_pid=os.getpid(),
                )
        if decision.granted:
            assert decision.allocation is not None
            return decision.allocation
        if decision.reason == "lane_quarantined":
            raise LaneQuarantined(decision.lane_id or request.lane_id, decision.detail)
        raise NotGranted(
            decision.reason or "refused",
            decision.detail,
            lane_id=decision.lane_id or request.lane_id,
            run_id=request.run_id,
        )

    def _compensate(self, request: ScheduleRequest, allocation: Allocation, bench: LabgridBench) -> bool:
        """Undo a partial allocation. Returns True if cleanup is pending.

        Only OUR claim is compensated: an unstarted reservation is aborted, and
        a place we actually acquired is released. Anything we cannot confirm
        becomes ``CLEANUP_PENDING`` (sticky) instead of free capacity.
        """
        clean = True
        if bench.acquired or bench.cleanup_pending:
            try:
                bench.release()
            except LabgridBenchError:
                clean = False
        else:
            clean = False if bench.cleanup_pending else True
        try:
            if clean:
                if self.authority.allocation(request.run_id).state is State.RESERVED:
                    self.authority.abort(request.run_id, allocation.fence, detail="partial allocation")
                else:
                    self.authority.complete(
                        request.run_id, allocation.fence, clean=True, detail="partial allocation released"
                    )
            else:
                self.authority.complete(
                    request.run_id,
                    allocation.fence,
                    clean=False,
                    detail="release could not be confirmed during compensation",
                )
        except AuthorityError:
            # The claim is no longer ours to transition (fenced, expired).
            # The lane stays excluded by the quarantine it already carries.
            clean = False
        return not clean

    @contextlib.contextmanager
    def allocate(self, request: ScheduleRequest) -> Iterator[Lease]:
        """Stage both phases, then yield a lease that is safe to run under."""
        allocation = self._reserve(request)

        bench = self._bench_factory()
        try:
            bench.acquire()
        except LabgridBenchError as exc:
            pending = self._compensate(request, allocation, bench)
            raise PartialAllocation("acquire", str(exc), cleanup_pending=pending) from None

        try:
            allocation = self.authority.activate(
                request.run_id, allocation.fence, note=f"place {request.resource_id} acquired"
            )
        except AuthorityError as exc:
            pending = self._compensate(request, allocation, bench)
            raise PartialAllocation("activate", str(exc), cleanup_pending=pending) from None

        lease = Lease(self, request, allocation, bench)
        body_error: BaseException | None = None
        try:
            yield lease
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            body_error = exc

        # Clean release is captured separately from the test verdict: a failed
        # teardown must not overwrite the body's outcome, nor invent one.
        clean = True
        release_error: LabgridBenchError | None = None
        try:
            bench.release()
        except LabgridBenchError as exc:
            clean = False
            release_error = exc

        with contextlib.suppress(AuthorityError, Fenced):
            current = self.authority.allocation(request.run_id)
            if current.fence == allocation.fence:
                self.authority.complete(
                    request.run_id,
                    allocation.fence,
                    clean=clean,
                    detail="" if clean else f"release unconfirmed: {release_error}",
                )

        if body_error is not None:
            raise body_error
        if release_error is not None:
            raise release_error

    # -- reconciliation ----------------------------------------------------
    def reconcile(self, *, assume_clean: bool = False) -> ReconcileReport:
        """Verify leftovers against the coordinator, then reopen or exclude.

        ``assume_clean`` is for a caller that has already proven the leftovers
        are gone (an explicit operator decision); the default leaves unproven
        lanes excluded.
        """
        self.authority.reconcile()
        outstanding = tuple(a for a in self.authority.allocations() if a.occupies)
        recycled: list[str] = []
        excluded: list[str] = []

        for alloc in outstanding:
            bench = self._bench_factory()
            held_by = ""
            with contextlib.suppress(LabgridBenchError):
                held_by = bench.owner() or ""

            free = not held_by
            if free and assume_clean:
                with contextlib.suppress(AuthorityError):
                    self.authority.complete(
                        alloc.run_id, alloc.fence, clean=True, detail="reconciled: place free"
                    )
                recycled.append(alloc.run_id)
                continue
            if not free and held_by and allocated_to_us(held_by, alloc):
                with contextlib.suppress(LabgridBenchError):
                    bench.release()
            excluded.append(alloc.lane_id)

        for lane_id in dict.fromkeys(excluded):
            self.authority.quarantine(lane_id, "reconcile_unresolved", "leftover allocation not proven clean")

        return ReconcileReport(
            outstanding=outstanding,
            recycled=tuple(recycled),
            lanes_excluded=tuple(dict.fromkeys(excluded)),
            detail=f"{len(outstanding)} outstanding, {len(recycled)} recycled, {len(set(excluded))} excluded",
        )


def allocated_to_us(held_by: str, alloc: Allocation) -> bool:
    """Does this coordinator owner string belong to that allocation's run?"""
    token = f"prta-{alloc.run_id}"[:63]
    host, _, user = (held_by or "").partition("/")
    return bool(host) and user == token
