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
        if proc.returncode != 0:
            raise LabgridBenchError(
                f"labgrid-client {' '.join(args)} on place {self.place!r} failed "
                f"(rc={proc.returncode}): {(getattr(proc, 'stderr', '') or '').strip()}"
            )
        return proc.stdout or ""

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        return self._runner(cmd, capture_output=True, text=True, timeout=_RUN_TIMEOUT)

    def acquire(self) -> None:
        self._client("acquire")
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


@dataclass(frozen=True)
class VenueBinding:
    bench: LabgridBench
    entry: RouterEntry


def bench_for_router(inv: LabInventory, name: str) -> VenueBinding:
    """Guarded place binding: refuses protected/unmapped routers BEFORE any
    coordinator interaction (the labgrid path itself has no PRTA-side guard)."""
    entry = inv.router(name)
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


def apply_venue_env(entry: RouterEntry, environ: Mapping[str, str] = os.environ) -> None:
    for key, value in venue_env(entry).items():
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


def auto_place_name(inv: LabInventory, environ: Mapping[str, str] = os.environ) -> str | None:
    """Which place an auto session means: an explicit TOLLGATE_LABGRID_PLACE,
    else the dossier whose address equals TOLLGATE_SSH_HOST, else None."""
    explicit = (environ.get("TOLLGATE_LABGRID_PLACE") or "").strip()
    if explicit:
        return explicit
    host = (environ.get("TOLLGATE_SSH_HOST") or "").strip()
    if host:
        for entry in inv.routers.values():
            if entry.address and entry.address == host and entry.place:
                return entry.name
    return None


def plan_venue(environ: Mapping[str, str] = os.environ) -> tuple[str, "VenueBinding | None", str]:
    """Decide the router-access venue for this session.

    Returns ("venue", binding, place-name) — acquire through venue_mutex —
    or ("legacy", None, reason) — env path, no place acquisition.

    Auto falls back to legacy ONLY on unmet preconditions (missing inventory
    or client, non-physical lab, no place mapping). A venue that resolves but
    then fails — unknown/protected/undressed place, coordinator or acquire
    error — raises InventoryError/LabgridBenchError and is the CALLER's
    fatal case: falling back to direct while a place is held would put two
    sessions on one DUT.
    """
    mode = resolve_venue_mode(environ)
    if mode == "direct":
        return "legacy", None, "TOLLGATE_VENUE=direct"
    if mode == "legacy":
        raw = (environ.get("TOLLGATE_VENUE") or "").strip()
        return "legacy", None, f"TOLLGATE_VENUE={raw!r} keeps the legacy path"
    if mode == "labgrid":
        inv = load_inventory()
        place = (environ.get("TOLLGATE_LABGRID_PLACE") or "").strip() or "ap-lan2"
    else:
        ok, why = venue_preconditions_met(environ)
        if not ok:
            return "legacy", None, f"venue auto: {why}"
        try:
            inv = load_inventory()
        except InventoryError as e:
            return "legacy", None, f"venue auto: inventory unusable ({e})"
        place = auto_place_name(inv, environ)
        if not place:
            return "legacy", None, "venue auto: no place maps to this target"
    return "venue", bench_for_router(inv, place), place


@contextlib.contextmanager
def venue_mutex(binding: "VenueBinding", phase: str) -> Iterator[LabgridBench]:
    """Transition invariant (#232): a venue session holds BOTH mutexes.

    Legacy participants (Make targets, pymake, release-matrix) take only the
    repo file lock; foreign labgrid sessions may take only the place. Taking
    both — file lock first, then place, released in reverse — makes venue
    and legacy sessions exclude each other, and the fixed order means two
    PRTA sessions can never wait on each other circularly (they serialize on
    the cheap local lock before any coordinator round-trip). Shed the
    file-lock half when #232's exit criteria hold; until then removing it
    reopens the venue-vs-legacy race on shared DUTs.
    """
    hardware_lock.acquire_hardware_lock(phase)
    try:
        binding.bench.acquire()
        try:
            yield binding.bench
        finally:
            binding.bench.release()
    finally:
        hardware_lock.release_hardware_lock()
