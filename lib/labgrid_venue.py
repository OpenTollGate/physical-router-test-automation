"""Labgrid physical venue adapter (Phase 3 of poe-venue-roadmap).

Wraps the shared coordinator's place lifecycle (acquire / power / release)
for the pytest ``router`` fixture. Power flows through the place's
NetworkPowerPort — the exporter's conwrt_poe backend post-verifies every
manage underneath, so its errors are surfaced, never retried blind. Shell
access stays on the existing env-driven ``Router`` path; this module only
feeds it (see ``venue_env``).

The labgrid python package is NOT imported here: the client binary is
driven as a subprocess, so unit tests inject a fake runner and CI needs no
coordinator at all.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping

from lib import hardware_lock
from lib.lab_inventory import (
    InventoryError,
    LabInventory,
    RouterEntry,
    inventory_exists,
    load_inventory,
)

_RUN_TIMEOUT = 60


class LabgridBenchError(RuntimeError):
    """Place operation failed — message carries stderr, never secrets."""


def _default_client_bin() -> str:
    override = os.environ.get("TOLLGATE_LABGRID_CLIENT", "").strip()
    if override:
        return override
    found = shutil.which("labgrid-client")
    if found:
        return found
    repo_local = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              ".venv", "bin", "labgrid-client")
    if os.path.isfile(repo_local) and os.access(repo_local, os.X_OK):
        return repo_local
    raise LabgridBenchError(
        "labgrid-client not found — pip install labgrid into the venv or set "
        "TOLLGATE_LABGRID_CLIENT"
    )


Runner = Callable[..., subprocess.CompletedProcess]


class LabgridBench:
    """Acquire/power/release one labgrid place via the client binary."""

    def __init__(self, coordinator: str, place: str, runner: Runner | None = None):
        if not coordinator or ":" not in coordinator:
            raise LabgridBenchError(
                "coordinator address must be host:port (inventory 'coordinator.address')"
            )
        self.coordinator = coordinator
        self.place = place
        self._runner = runner or subprocess.run
        self._bin = _default_client_bin() if runner is None else "labgrid-client"
        self._acquired = False

    def _client(self, *args: str) -> str:
        cmd = [self._bin, "-x", self.coordinator, "-p", self.place, *args]
        try:
            proc = self._run(cmd)
        except OSError as e:
            raise LabgridBenchError(f"labgrid-client failed to start: {e}") from None
        except subprocess.TimeoutExpired:
            # A timed-out acquire may still have been GRANTED server-side —
            # callers must see a LabgridBenchError, not a raw TimeoutExpired
            # that skips every RuntimeError handler upstream.
            raise LabgridBenchError(
                f"labgrid-client {' '.join(args)} on place {self.place!r} "
                f"timed out after {_RUN_TIMEOUT}s — the operation may have "
                "succeeded on the coordinator; check 'labgrid-client who'"
            ) from None
        if proc.returncode != 0:
            raise LabgridBenchError(
                f"labgrid-client {' '.join(args)} on place {self.place!r} failed "
                f"(rc={proc.returncode}): {(getattr(proc, 'stderr', '') or '').strip()}"
            )
        return proc.stdout or ""

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        return self._runner(cmd, capture_output=True, text=True, timeout=_RUN_TIMEOUT)

    def acquire(self) -> None:
        try:
            self._client("acquire")
        except LabgridBenchError:
            # An acquire that failed after the request was sent may have been
            # granted anyway; release() would no-op while _acquired is False,
            # leaking the place. Best-effort undo, then surface the failure.
            with contextlib.suppress(LabgridBenchError):
                self._client("release")
            raise
        self._acquired = True

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            self._client("release")
        finally:
            self._acquired = False

    def power(self, on: bool) -> None:
        self._client("power", "on" if on else "off")

    def who(self) -> str:
        """Current holder of the place — contention diagnostics in fatal paths."""
        return self._client("who").strip()


@dataclass(frozen=True)
class VenueBinding:
    bench: LabgridBench
    entry: RouterEntry


def bench_for_router(inv: LabInventory, name: str) -> VenueBinding:
    """Guarded place binding: refuses protected/unmapped routers BEFORE any
    coordinator interaction (the labgrid path itself has no PRTA-side guard).
    `name` accepts either the inventory dossier key or the dossier's labgrid
    place name (TOLLGATE_LABGRID_PLACE is ambiguous in operators' hands)."""
    entry = _resolve_entry(inv, name)
    if entry.protected:
        raise LabgridBenchError(
            f"router {name!r} is protected — labgrid venue refuses to acquire it"
        )
    if not entry.place:
        raise LabgridBenchError(
            f"router {name!r} has no labgrid place mapping — set 'place:' in the "
            "inventory (see inventory.local.yaml.example)"
        )
    if not entry.address:
        raise LabgridBenchError(
            f"router {name!r} has no address — the venue must be able to reach "
            "the DUT over SSH"
        )
    if not inv.coordinator.address:
        raise LabgridBenchError(
            "inventory 'coordinator.address' is empty — required for the labgrid venue"
        )
    return VenueBinding(
        bench=LabgridBench(inv.coordinator.address, entry.place), entry=entry
    )


def venue_env(entry: RouterEntry) -> dict[str, str]:
    """Env vars feeding the existing Router construction in conftest."""
    env: dict[str, str] = {"TOLLGATE_SSH_HOST": entry.address}
    if entry.keyfile:
        env["TOLLGATE_SSH_KEY"] = os.path.expanduser(entry.keyfile)
    if entry.jump_host:
        env["TOLLGATE_SSH_JUMP_HOST"] = entry.jump_host
    return env


def apply_venue_env(
    entry: RouterEntry,
    environ: Mapping[str, str] = os.environ,
    respect_existing: bool = False,
) -> None:
    """Feed the venue's connection env into the Router construction.

    respect_existing=True fills only UNSET vars (setdefault): used when the
    venue was chosen by ADDRESS MATCH — the operator already named this DUT
    with their own env, so their explicit keyfile/jump-host/port wins.
    Authoritative modes (forced/explicit-place) keep venue authority AND drop
    TOLLGATE_SSH_PORT: a leftover port from a previous target poisons the
    switched DUT's connection.
    """
    if not respect_existing:
        if isinstance(environ, dict):
            environ.pop("TOLLGATE_SSH_PORT", None)
    for key, value in venue_env(entry).items():
        if respect_existing and environ.get(key):
            continue
        environ[key] = value


def resolve_venue_mode(environ: Mapping[str, str] = os.environ) -> str:
    """TOLLGATE_VENUE values: 'labgrid' forces the venue, 'direct' forces the
    legacy env path, unset/'auto' prefers the venue on physical runs when it
    is configured on this host. Any other value keeps the legacy path —
    extensive-test.sh reuses this variable for its own lane selection
    ('virtual'/'physical'/'skip') and must not change meaning here.
    """
    mode = (environ.get("TOLLGATE_VENUE") or "").strip().lower()
    if mode == "labgrid":
        return "labgrid"
    if mode == "direct":
        return "direct"
    if mode in ("", "auto"):
        return "auto"
    return "legacy"


def venue_preconditions_met(environ: Mapping[str, str] = os.environ) -> tuple[bool, str]:
    """Coarse venue availability on THIS host, before any coordinator contact."""
    if (environ.get("TOLLGATE_LAB_TYPE") or "physical") != "physical":
        return False, "non-physical lab type"
    if not inventory_exists():
        return False, "no lab inventory on this host"
    try:
        _default_client_bin()
    except LabgridBenchError:
        return False, "labgrid-client not installed"
    return True, ""


@dataclass(frozen=True)
class VenuePlan:
    """action: "venue" — acquire through venue_mutex; "legacy" — env path.

    origin: how the venue was chosen — "forced" (TOLLGATE_VENUE=labgrid),
    "explicit-place" (TOLLGATE_LABGRID_PLACE named it), "address-match"
    (the dossier whose address equals TOLLGATE_SSH_HOST), "" for legacy.
    Address-match respects pre-set operator env (see apply_venue_env).
    """

    action: str
    binding: "VenueBinding | None" = None
    detail: str = ""
    origin: str = ""


def _resolve_entry(inv: LabInventory, name: str) -> RouterEntry:
    try:
        return inv.router(name)
    except InventoryError:
        by_place = [e for e in inv.routers.values() if e.place and e.place == name]
        if len(by_place) == 1:
            return by_place[0]
        raise


def _resolve_target(
    inv: LabInventory, environ: Mapping[str, str], operator_host: str | None
) -> tuple[str | None, str]:
    """Which dossier this session means, by address.

    The operator's host (captured BEFORE preflight could reassign it) wins
    absolutely when it maps to ANY dossier — even one without a place, so a
    preflight substitute (picked because the operator's DUT is unreachable,
    i.e. most likely busy in another session) can never silently redirect
    the session to a different DUT. The post-preflight env host is only
    honored when the operator set nothing (auto-discovery).
    """
    if operator_host:
        for name, entry in inv.routers.items():
            if entry.address and entry.address == operator_host:
                return name, "address-match"
        return None, ""
    host = (environ.get("TOLLGATE_SSH_HOST") or "").strip()
    if host:
        for name, entry in inv.routers.items():
            if entry.address and entry.address == host and entry.place:
                return name, "address-match"
    return None, ""


def plan_venue(environ: Mapping[str, str] = os.environ, operator_host: str | None = None) -> VenuePlan:
    """Decide the router-access venue for this session.

    Auto falls back to legacy ONLY on unmet preconditions (missing inventory
    or client, non-physical lab, no place mapping). A venue that resolves but
    then fails — unknown/protected place, coordinator or acquire error —
    raises InventoryError/LabgridBenchError and is the CALLER's fatal case:
    falling back to direct while a place is held would put two sessions on
    one DUT.
    """
    mode = resolve_venue_mode(environ)
    if mode == "direct":
        return VenuePlan("legacy", detail="TOLLGATE_VENUE=direct")
    if mode == "legacy":
        raw = (environ.get("TOLLGATE_VENUE") or "").strip()
        return VenuePlan("legacy", detail=f"TOLLGATE_VENUE={raw!r} keeps the legacy path")
    if mode == "labgrid":
        try:
            inv = load_inventory()
        except InventoryError:
            raise
        explicit = (environ.get("TOLLGATE_LABGRID_PLACE") or "").strip()
        if explicit:
            place, origin = explicit, "explicit-place"
        else:
            place, origin = _resolve_target(inv, environ, operator_host)
            if place is None:
                if operator_host or (environ.get("TOLLGATE_SSH_HOST") or "").strip():
                    # The operator named a DUT that maps to no dossier —
                    # refuse rather than silently retarget to the smoke default.
                    raise LabgridBenchError(
                        "forced venue: no inventory dossier maps to the configured "
                        "TOLLGATE_SSH_HOST — set TOLLGATE_LABGRID_PLACE or fix the inventory"
                    )
                place, origin = "ap-lan2", "forced"
    else:
        ok, why = venue_preconditions_met(environ)
        if not ok:
            return VenuePlan("legacy", detail=f"venue auto: {why}")
        try:
            inv = load_inventory()
        except InventoryError as e:
            return VenuePlan("legacy", detail=f"venue auto: inventory unusable ({e})")
        explicit = (environ.get("TOLLGATE_LABGRID_PLACE") or "").strip()
        if explicit:
            place, origin = explicit, "explicit-place"
        else:
            place, origin = _resolve_target(inv, environ, operator_host)
            if place is None:
                return VenuePlan("legacy", detail="venue auto: no place maps to this target")
            if not inv.router(place).place:
                return VenuePlan("legacy", detail=f"venue auto: {place!r} has no place mapping")
    return VenuePlan("venue", binding=bench_for_router(inv, place), detail=place, origin=origin)


@contextlib.contextmanager
def venue_mutex(binding: "VenueBinding", phase: str, *, file_lock: bool = True) -> Iterator[LabgridBench]:
    """Transition invariant (#232): a venue session holds BOTH mutexes.

    Legacy participants (Make targets, pymake, release-matrix) take only the
    repo file lock; foreign labgrid sessions may take only the place. Taking
    both — file lock first, then place, released in reverse — makes venue
    and legacy sessions exclude each other. The fixed order means two PRTA
    sessions cannot wait on each other circularly; note the file lock only
    serializes CROSS-USER (same-user re-acquire is allowed by design), so
    same-user concurrent sessions race the place and the loser fails loudly.
    Shed the file-lock half when #232's exit criteria hold; until then
    removing it reopens the venue-vs-legacy race on shared DUTs.

    file_lock=False for sessions that ALREADY hold the repo lock through
    --lock-phase or TOLLGATE_USE_HARDWARE_LOCK (sessionstart/pymake owns
    acquire and release there) — acquiring again here would double-release,
    and the second unconditional unlink can delete a FOREIGN session's lock.
    """
    if file_lock:
        hardware_lock.acquire_hardware_lock(phase)
    try:
        binding.bench.acquire()
        try:
            yield binding.bench
        finally:
            binding.bench.release()
    finally:
        if file_lock:
            hardware_lock.release_hardware_lock()
