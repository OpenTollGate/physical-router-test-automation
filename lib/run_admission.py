"""Admission control for the production test runner.

``run-profile.py`` used to go straight from "profile parsed" to "run pytest".
That is how a second run starts on top of a first one and two lanes fight over
one host. This module is the seam: the runner must obtain a granted, owned
reservation *before* it spawns anything, and must give it back exactly once
afterwards.

Design rules, all of which are refusals rather than defaults:

* **No manifest, no admission.** The caller states explicitly whether fleet
  admission is required. When it is required and anything is missing, the run
  does not start.
* **Unknown demand is a refusal.** A dimension the profile did not declare is
  never assumed to be zero; the caller must pass a complete vector.
* **A lost claim is not a licence to run.** If the authority no longer
  recognises our ``(run_id, fence)`` we raise instead of running unaccounted.
* **A failed release stays occupied.** Cleanup that cannot be confirmed leaves
  the lane occupied (``CLEANUP_PENDING``), because a lane that looks free but
  still holds a DUT is worse than one that is visibly stuck.

The pure decision helpers (``demand_from_mapping``, ``resolve_target``) do no
I/O so the refusal paths are unit-testable; ``Admission`` wraps the store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from lib.fleet_authority import (
    Allocation,
    AuthorityError,
    Demand,
    FleetAuthority,
    State,
)

#: Dimensions a lane demand must declare. Absent != zero.
DIMENSIONS = ("vcpus", "ram_mib", "disk_mib", "inodes")

#: Defaults mirror ``fleet_observe.resolve_host_demand`` so the CLI's preview
#: and the runner's actual request describe the same lane.
DEFAULT_DEMAND = {"vcpus": 2, "ram_mib": 1024, "disk_mib": 4096, "inodes": 1}


class AdmissionRefused(Exception):
    """The runner must not start. Carries a machine-readable reason."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class Target:
    """Which declared lane a run intends to occupy."""

    fleet_id: str
    lane_id: str
    resource_id: str
    domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdmissionRequest:
    run_id: str
    target: Target
    demand: Demand
    ttl_s: float = 900.0


@dataclass
class AdmissionOutcome:
    """What the runner must record, whether or not it ran."""

    granted: bool
    reason: str = ""
    detail: str = ""
    allocation: Allocation | None = None
    cleanup_pending: bool = False
    notes: list[str] = field(default_factory=list)


def demand_from_mapping(values: Mapping[str, Any]) -> Demand:
    """Build a ``Demand`` from declared values.

    **Every dimension must be declared.** A dimension the caller did not declare
    is never assumed to be a number: "I forgot to say how much disk this needs"
    must not silently mean "no disk", and it must not silently mean "the default
    disk" either. This function used to take a ``require_all=False`` flag that
    substituted ``DEFAULT_DEMAND`` for absent dimensions — measured,
    ``demand_from_mapping({"ram_mib": 32768}, require_all=False)`` returned
    ``vcpus=2, ram_mib=32768, disk_mib=4096, inodes=1``, i.e. an undeclared
    vector bought admission while charging less than a declared one. The flag is
    gone: there is no permissive path to select.
    """
    provided: dict[str, Any] = {}
    missing: list[str] = []
    for name in DIMENSIONS:
        raw = values.get(name)
        if raw is None:
            missing.append(name)
            continue
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise AdmissionRefused(
                "demand_invalid",
                f"demand.{name} must be an integer (got {type(raw).__name__})",
            )
        provided[name] = raw
    if missing:
        raise AdmissionRefused(
            "demand_incomplete",
            "lane demand must declare every dimension; missing "
            + ", ".join(sorted(missing)),
        )
    try:
        return Demand(**provided)
    except (TypeError, ValueError) as exc:  # positive-value validation
        raise AdmissionRefused("demand_invalid", str(exc)) from None


def resolve_target(
    *,
    fleet_id: str | None,
    lane_id: str | None,
    resource_id: str | None,
    domains: Iterable[str] = (),
    manifest: Any | None = None,
) -> Target:
    """Resolve exactly one declared lane.

    Ambiguity is refused. If the manifest declares a single lane we may infer
    it; if it declares several we will not guess which DUT a test run gets.
    """
    if manifest is not None and fleet_id is None:
        fleets = getattr(manifest, "fleets", {}) or {}
        if len(fleets) != 1:
            raise AdmissionRefused(
                "fleet_ambiguous",
                f"manifest declares {len(fleets)} fleets; pass --fleet explicitly",
            )
        fleet_id = next(iter(fleets))

    if lane_id is None and manifest is not None and fleet_id is not None:
        entry = (getattr(manifest, "fleets", {}) or {}).get(fleet_id)
        places = tuple(getattr(entry, "places", ()) or ())
        if len(places) != 1:
            raise AdmissionRefused(
                "lane_ambiguous",
                f"fleet {fleet_id!r} declares {len(places)} places; pass --lane explicitly",
            )
        lane_id = getattr(places[0], "resource_id", None) or places[0]

    if not fleet_id or not lane_id:
        raise AdmissionRefused(
            "target_incomplete", "fleet and lane must both be named to reserve capacity"
        )

    return Target(
        fleet_id=str(fleet_id),
        lane_id=str(lane_id),
        resource_id=str(resource_id or lane_id),
        domains=tuple(domains),
    )


class Admission:
    """One run's owned reservation, released exactly once.

    Used as a context manager: enter reserves, exit completes or aborts. Any
    exit that cannot be confirmed clean leaves the lane occupied.
    """

    def __init__(self, authority: FleetAuthority, request: AdmissionRequest):
        self.authority = authority
        self.request = request
        self.allocation: Allocation | None = None
        self.cleanup_pending = False

    # -- lifecycle ---------------------------------------------------------
    def acquire(self) -> Allocation:
        decision = self.authority.try_reserve(
            lane_id=self.request.target.lane_id,
            demand=self.request.demand,
            run_id=self.request.run_id,
            domains=self.request.target.domains,
            ttl_s=self.request.ttl_s,
        )
        if not decision.granted:
            raise AdmissionRefused(
                decision.reason or "refused",
                decision.detail or "no capacity granted for this lane",
            )
        assert decision.allocation is not None
        self.allocation = decision.allocation
        return decision.allocation

    def mark_started(self) -> None:
        """Record that work actually began; only then is the slot in use."""
        if self.allocation is None:
            raise AdmissionRefused("not_acquired", "cannot mark an unacquired run as started")
        self.allocation = self.authority.activate(
            self.request.run_id, self.allocation.fence, note="runner started"
        )

    def release(self, *, clean: bool = True, detail: str = "") -> bool:
        """Give the slot back. Returns False when the release is unconfirmed."""
        if self.allocation is None:
            return True
        allocation = self.allocation
        try:
            if allocation.state is State.RESERVED and clean:
                self.authority.abort(
                    self.request.run_id, allocation.fence, detail=detail or "run aborted before use"
                )
            else:
                self.authority.complete(
                    self.request.run_id, allocation.fence, clean=clean, detail=detail
                )
        except AuthorityError as exc:
            # Fenced or expired: the authority no longer lets us transition it.
            # Do not claim the lane is free — it is not ours to free.
            self.allocation = None
            raise AdmissionRefused("release_unconfirmed", str(exc)) from None
        self.allocation = None
        self.cleanup_pending = not clean
        return clean

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "Admission":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # A body exception is not a dirty release: the run may have failed its
        # assertions while leaving the DUT perfectly clean. Only a release that
        # cannot be confirmed marks the lane dirty.
        try:
            self.release(clean=True, detail="run finished" if exc is None else "run raised")
        except AdmissionRefused:
            self.cleanup_pending = True
        return False  # never swallow the body's exception
