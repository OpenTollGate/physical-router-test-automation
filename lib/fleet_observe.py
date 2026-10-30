"""Read-only observation adapters for authorized fleets — slice 4.

Slice 1 (``lib/fleet_capacity``) decided *what capacity a given piece of
evidence supports*. It deliberately shipped no adapter: ``run_probes`` took an
injected callable and nothing in the tree supplied one. This module is that
adapter layer, and it is **read-only by construction**:

* the only labgrid subcommands it can issue are ``version``, ``resources``,
  ``places`` and ``show``;
* the only host it measures is the one this process runs on, and only when the
  manifest's ``host_controller`` endpoint actually names it;
* the only guests it inspects are the pidfiles an operator names explicitly.

There is no power, no flash, no acquisition, no VM start, no subnet scan, no
SSH sweep and no home-directory inventory crawl anywhere in this file. Those
would all be *discovery*, and discovery is not authorization (audit, "Required
state distinctions" item 1).

Honesty rules this module refuses to soften
-------------------------------------------
* **Reachable is not capacious.** A coordinator that answers certifies the
  control plane. Every place it lists still needs a fresh exporter view before
  anything may be counted, and this adapter never invents router health: absent
  owner-authorized telemetry the state is ``EXPORTED_AVAILABLE`` at capacity 0
  (that IS a real state — exported is not idle).
* **Unknown is never free.** An unparseable ``show`` yields *no* observation
  rather than a guess, and a pidfile with a dead process yields ``pid=None`` —
  the bridge-only state — not "guest is up".
* **Raw headroom is not admission.** ``/proc`` can say how much RAM is
  available; it cannot say how much has been *reserved*, because that is
  authority state on the host, and the shared host-pool accounting that would
  make it trustworthy does not exist yet (FLEET-CAP-5). So the local host
  adapter reports real measured numbers **and** marks the committed-vCPU
  dimension unknown, which makes ``new_vm_slots`` answer ``UNKNOWN``/0. The
  module-level ``ADMISSION_STATUS`` says the same thing to an operator in one
  word: ``NOT_QUALIFIED``. No caller of this module may print a new-VM slot
  count as allocatable.
* **Secrets stay out of the output.** Every detail string passes through
  ``fleet_capacity.redact``; the adapters never echo a credential, and the
  manifest only ever carries credential *references*.

Evidence and decisions are separate on purpose: ``collect`` produces an
``Evidence`` value with timestamps, ``decide`` replays the pure slice-1 decision
path over it, and ``--from-snapshot`` re-decides a saved ``Evidence`` so an
operator can see a stale snapshot decay into refusals without any probe running.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.fleet_capacity import (
    CapacityError,
    CoordinatorObservation,
    ExporterObservation,
    FleetManifest,
    GuestObservation,
    HostDemand,
    HostObservation,
    Observations,
    ProbeTarget,
    Reason,
    Reservation,
    ResourceKind,
    SpawnVerdict,
    State,
    Verdict,
    coordinator_capacity,
    coordinator_observation,
    exporter_observation,
    guest_capacity,
    guest_observation,
    host_observation,
    new_vm_slots,
    redact,
    router_capacity,
    run_probes,
    summarize,
)

__all__ = [
    "ADMISSION_STATUS",
    "ADMISSION_DETAIL",
    "READ_ONLY_SUBCOMMANDS",
    "EVIDENCE_SCHEMA_VERSION",
    "ObservationRefused",
    "ProbeLimits",
    "CommandResult",
    "LabgridReadOnlyClient",
    "PlaceState",
    "CoordinatorRead",
    "HostMetrics",
    "GuestSpec",
    "ReservationRead",
    "Refusal",
    "Evidence",
    "parse_place_show",
    "read_coordinator",
    "read_local_host",
    "read_local_guest",
    "read_authority_reservations",
    "read_authority_epoch",
    "host_observation_from_metrics",
    "collect",
    "decide",
    "resolve_host_demand",
]

#: One word an operator cannot misread: this slice observes, it does not admit.
#: Two *independent, structural* reasons, and both must stay true:
#:   * ``cpu_committed`` is an unmeasured dimension here (``/proc`` cannot say how
#:     many vCPUs running guests have already claimed), and an unmeasured
#:     dimension makes any slot arithmetic a guess in the dangerous direction;
#:   * ``FleetAuthority.host_headroom()`` is only reachable through
#:     ``FleetAuthority.__init__``, which runs ``_init_store()`` and therefore
#:     CREATES a 64 KB store at whatever path it is given. An observation tool
#:     that used it would be a writer on a live bench. (Read-only-ness of the
#:     connection is not the obstacle — reading a store and reading a *pool*
#:     are both SELECTs; minting the store is the obstacle.)
#: Admission belongs to ``lib/run_admission.py`` inside the runner, which holds
#: a real owned lease. No number this module prints may be read as "you may
#: start this many VMs".
ADMISSION_STATUS = "NOT_QUALIFIED"

ADMISSION_DETAIL = (
    "read-only observation only: this report measures the host and the "
    "coordinators, it does not admit. Slot arithmetic is withheld because "
    "cpu_committed is unmeasured from /proc and because the only handle that "
    "can read the host pool (FleetAuthority) creates the authority store in its "
    "constructor, so using it here would make an observation command a writer. "
    "Admission (with an owned, fenced lease) happens in the runner via "
    "lib/run_admission.py; nothing here starts a VM or enables parallel execution."
)

EVIDENCE_SCHEMA_VERSION = 2

#: The complete set of coordinator operations this module may issue. Anything
#: else — including every mutating verb — is refused before a process starts.
READ_ONLY_SUBCOMMANDS = frozenset({"version", "resources", "places", "show"})

_SUBPROCESS_OUTPUT_CAP = 64 * 1024


class ObservationRefused(CapacityError):
    """A probe was not authorized, not possible, or not local.

    Refusal is a *result*, not a crash: ``collect`` records it in
    ``Evidence.refusals`` and the affected resource then reports zero capacity
    with a reason code.
    """


# ===========================================================================
# Limits and the client wrapper
# ===========================================================================
@dataclass(frozen=True)
class ProbeLimits:
    """Bounded probes: a timeout, a freshness budget and an output cap.

    Every one is required to be positive, because "0 means infinite" is exactly
    the kind of default that turns one dead exporter into a hung scheduler.
    """

    timeout_s: float = 10.0
    ttl_s: float = 60.0
    max_output_bytes: int = _SUBPROCESS_OUTPUT_CAP

    def __post_init__(self) -> None:
        for name in ("timeout_s", "ttl_s"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"ProbeLimits.{name} must be a positive number (got {value!r})")
        if isinstance(self.max_output_bytes, bool) or not isinstance(self.max_output_bytes, int):
            raise ValueError("ProbeLimits.max_output_bytes must be an integer")
        if self.max_output_bytes <= 0:
            raise ValueError("ProbeLimits.max_output_bytes must be positive")


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def detail(self) -> str:
        """Redacted one-line failure text (never the full argv of a password)."""
        if self.timed_out:
            return f"{self.argv[0]} {self.argv[-1]} timed out"
        return redact((self.stderr or self.stdout or "").strip())[:400]


def _default_client_binary() -> str:
    override = os.environ.get("TOLLGATE_LABGRID_CLIENT", "").strip()
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        raise ObservationRefused(
            "TOLLGATE_LABGRID_CLIENT does not name an executable — observation cannot run",
        )
    found = shutil.which("labgrid-client")
    if found:
        return found
    raise ObservationRefused(
        "labgrid-client is not installed — install the labgrid package or set "
        "TOLLGATE_LABGRID_CLIENT; observation cannot invent coordinator evidence",
    )


def _run_bounded(argv: Sequence[str], *, timeout_s: float, max_output_bytes: int) -> CommandResult:
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_s,
            env={**os.environ, "LC_ALL": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired:
        return CommandResult(argv=tuple(argv), returncode=-1, timed_out=True)
    except OSError as exc:
        return CommandResult(argv=tuple(argv), returncode=-1, stderr=f"cannot execute: {exc}")
    return CommandResult(
        argv=tuple(argv),
        returncode=int(proc.returncode),
        stdout=(proc.stdout or "")[:max_output_bytes],
        stderr=(proc.stderr or "")[:max_output_bytes],
    )


class LabgridReadOnlyClient:
    """A labgrid client that can only look.

    The allowlist is enforced *here*, above the process boundary: a caller that
    asks for ``acquire`` or ``power`` gets an ``ObservationRefused`` and no
    subprocess, which is a structural guarantee rather than a convention.
    """

    def __init__(
        self,
        limits: ProbeLimits | None = None,
        *,
        binary: str | None = None,
        runner: Callable[..., CommandResult] | None = None,
    ):
        self.limits = limits or ProbeLimits()
        self._binary = binary or _default_client_binary()
        self._runner = runner or (
            lambda argv: _run_bounded(
                argv, timeout_s=self.limits.timeout_s, max_output_bytes=self.limits.max_output_bytes
            )
        )

    @property
    def binary(self) -> str:
        return self._binary

    def run(self, coordinator: str, subcommand: str, *, place: str | None = None) -> CommandResult:
        """Issue exactly ``labgrid-client -x <addr> [-p <place>] <subcommand>``.

        There is no free-form argument passthrough on purpose: the shape of the
        command line is fixed here, so a caller cannot smuggle ``power``/``flash``
        in through an ``args`` sequence.

        ``coordinator`` and ``place`` are *values*, not flags: labgrid-client's
        own argparse would happily re-tokenize a leading ``-``, so a value
        starting with ``-`` is refused here rather than handed to it. Pinning
        only the subcommand token would leave option confusion open at the two
        positions that precede it.
        """
        if subcommand not in READ_ONLY_SUBCOMMANDS:
            raise ObservationRefused(
                f"labgrid subcommand {subcommand!r} is not read-only — this adapter may only "
                f"issue {sorted(READ_ONLY_SUBCOMMANDS)}",
            )
        if not coordinator or ":" not in coordinator:
            raise ObservationRefused(f"coordinator {coordinator!r} is not host:port")
        if coordinator.startswith("-"):
            raise ObservationRefused(f"coordinator {coordinator!r} looks like a flag, not an address")
        if place is not None and (not place or place.startswith("-")):
            raise ObservationRefused(f"place {place!r} looks like a flag, not a place name")
        argv = [self._binary, "-x", coordinator]
        if place is not None:
            argv += ["-p", place]
        argv.append(subcommand)
        return self._runner(argv)


# ===========================================================================
# Coordinator / place parsing
# ===========================================================================
@dataclass(frozen=True)
class PlaceState:
    """What ``show`` said about one place. ``None`` means "could not tell"."""

    name: str
    exists: bool
    acquired_by: str | None = None
    available: bool | None = None
    match_patterns: tuple[str, ...] = ()


_ACQUIRED_RE = re.compile(r"^\s*acquired:\s*(\S+)\s*$", re.MULTILINE)
_AVAIL_RE = re.compile(r"'avail':\s*(True|False)")
_RESOURCE_BLOCK_RE = re.compile(
    r"^(?:Matching|Acquired) resource '[^']*' \(([^)]+)\):", re.MULTILINE
)


def parse_place_show(text: str, name: str) -> PlaceState:
    """Parse ``labgrid-client show`` output without guessing.

    Measured against labgrid 26.0.1 (and asserted against a live coordinator in
    ``tests/integration/test_fleet_observe_coordinator_real.py``):

    * a free place prints ``acquired: None``;
    * a held place prints ``acquired: <host>/<user>``;
    * with its exporter down the block of resource detail is simply absent, so
      "no ``avail`` line" must mean *unproven*, not *unavailable*.
    """
    if f"Place '{name}'" not in text:
        return PlaceState(name=name, exists=False)
    acquired_match = _ACQUIRED_RE.search(text)
    acquired_by: str | None
    if acquired_match is None:
        acquired_by = None  # printed, still not readable: unknown, never "free"
    else:
        raw = acquired_match.group(1).strip()
        acquired_by = "" if raw.lower() in {"none", "null"} else raw
    flags = {value == "True" for value in _AVAIL_RE.findall(text)}
    # ``{False}`` is a truthy set: "available: False" must stay False.
    available: bool | None = True if flags == {True} else (False if flags == {False} else None)
    patterns = tuple(match.strip() for match in _RESOURCE_BLOCK_RE.findall(text) if match.strip())
    return PlaceState(
        name=name,
        exists=True,
        acquired_by=acquired_by,
        available=available,
        match_patterns=patterns,
    )


@dataclass(frozen=True)
class CoordinatorRead:
    """One coordinator's read-only view: reachability plus the allowlisted places."""

    fleet_id: str
    address: str
    reachable: bool
    version: str = ""
    published_resources: tuple[str, ...] = ()
    places: Mapping[str, PlaceState] = field(default_factory=dict)
    detail: str = ""


def read_coordinator(
    client: LabgridReadOnlyClient,
    *,
    fleet_id: str,
    address: str,
    place_ids: Sequence[str],
) -> CoordinatorRead:
    """Read one authorized coordinator and only the places the manifest names.

    ``resources`` is read as *narrative* — it proves an exporter is connected
    and publishing, nothing more. Places come from the manifest allowlist, never
    from the coordinator's place list, so this cannot become discovery.
    """
    version = client.run(address, "version")
    if not version.ok:
        return CoordinatorRead(
            fleet_id=fleet_id,
            address=address,
            reachable=False,
            detail=f"coordinator did not answer: {version.detail}",
        )
    resources = client.run(address, "resources")
    published: tuple[str, ...] = ()
    detail = ""
    if resources.ok:
        published = tuple(line.strip() for line in resources.stdout.splitlines() if line.strip())
    else:
        detail = f"resource listing failed: {resources.detail}"

    places: dict[str, PlaceState] = {}
    for place_id in place_ids:
        shown = client.run(address, "show", place=place_id)
        if not shown.ok:
            if "matches nothing" in (shown.stderr or "").lower():
                places[place_id] = PlaceState(name=place_id, exists=False)
            else:
                # Unreadable is unknown: record it as not-present-for-us and keep
                # the detail, so the verdict says missing/unknown. Guessing "free"
                # here is how a run tramples a colleague's place.
                places[place_id] = PlaceState(name=place_id, exists=False)
                detail = detail or f"place {place_id!r} could not be read: {shown.detail}"
            continue
        places[place_id] = parse_place_show(shown.stdout, place_id)
    return CoordinatorRead(
        fleet_id=fleet_id,
        address=address,
        reachable=True,
        version=version.stdout.strip(),
        published_resources=published,
        places=places,
        detail=detail,
    )


# ===========================================================================
# Local host metrics (this host only)
# ===========================================================================
_LOCAL_HOST_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

_ARCH_ALIASES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
    "armv7l": "armv7l",
    "mips": "mips",
    "mipsel": "mipsel",
}


def endpoint_host(endpoint: str) -> str:
    """Host part of a ``host:port`` endpoint (IPv6-aware enough for refusal)."""
    text = (endpoint or "").strip()
    if text.startswith("["):
        closing = text.find("]")
        return text[1:closing] if closing > 0 else text
    if text.count(":") == 1:
        return text.rsplit(":", 1)[0]
    return text


def is_local_endpoint(endpoint: str, *, hostname: str | None = None) -> bool:
    """True only when the endpoint names THIS host.

    A host controller on another machine cannot be measured from here — reading
    ``/proc`` would silently answer for the wrong box. That is a refusal
    (``host_controller_not_local``), not a fallback.
    """
    host = endpoint_host(endpoint).strip().lower()
    if not host:
        return False
    if host in _LOCAL_HOST_NAMES or host.startswith("127."):
        return True
    names = {(hostname or "").strip().lower(), _hostname().lower()}
    try:
        names.add(_fqdn().lower())
    except OSError:  # pragma: no cover - resolution failure is not a bug here
        pass
    return bool(host) and host in {n for n in names if n}


def _hostname() -> str:
    import socket

    return socket.gethostname()


def _fqdn() -> str:
    import socket

    return socket.getfqdn()


def _read_meminfo(path: Path = Path("/proc/meminfo")) -> dict[str, int]:
    """Meminfo in MiB. Raises ``ObservationRefused`` when it cannot be read."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        raise ObservationRefused(f"cannot read {path} — no host memory evidence") from None
    values: dict[str, int] = {}
    for line in lines:
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            kib = int(parts[0])
        except ValueError:
            continue
        values[key.strip()] = max(0, kib // 1024)
    for required in ("MemTotal", "MemAvailable"):
        if required not in values:
            raise ObservationRefused(f"{path} has no {required} line — refusing to guess memory")
    return values


@dataclass(frozen=True)
class HostMetrics:
    """Raw measurements from THIS host. Not admission — see ``ADMISSION_STATUS``."""

    endpoint: str
    observed_at: float
    hostname: str
    cpu_total: int
    load1: float
    ram_total_mib: int
    ram_available_mib: int
    swap_total_mib: int
    swap_free_mib: int
    ram_reserve_mib: int
    disk_path: str
    disk_total_mib: int
    disk_free_mib: int
    inode_total: int
    inode_free: int
    kvm_present: bool
    kvm_usable: bool | None
    architecture: str
    detail: str = ""

    @property
    def ram_committed_mib(self) -> int:
        return max(0, self.ram_total_mib - self.ram_available_mib)

    @property
    def swap_used_mib(self) -> int:
        return max(0, self.swap_total_mib - self.swap_free_mib)

    def as_report(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "observed_at": self.observed_at,
            "hostname": self.hostname,
            "cpu_total": self.cpu_total,
            "load1": self.load1,
            "ram_total_mib": self.ram_total_mib,
            "ram_available_mib": self.ram_available_mib,
            "ram_committed_mib": self.ram_committed_mib,
            "ram_reserve_mib": self.ram_reserve_mib,
            "swap_total_mib": self.swap_total_mib,
            "swap_free_mib": self.swap_free_mib,
            "swap_used_mib": self.swap_used_mib,
            "disk_path": self.disk_path,
            "disk_total_mib": self.disk_total_mib,
            "disk_free_mib": self.disk_free_mib,
            "inode_total": self.inode_total,
            "inode_free": self.inode_free,
            "kvm_present": self.kvm_present,
            "kvm_usable": self.kvm_usable,
            "architecture": self.architecture,
            "detail": self.detail,
        }


def read_local_host(
    endpoint: str,
    *,
    bandwidth: ProbeLimits,
    now: float,
    disk_path: Path | str = "/",
    ram_reserve_mib: int = 0,
    hostname: str | None = None,
    meminfo_path: Path = Path("/proc/meminfo"),
    host_root: Path = Path("/"),
) -> HostMetrics:
    """Measure this host. Refuses when the endpoint is not local.

    ``ram_reserve_mib`` is a safety reserve the operator declares; it is
    subtracted from headroom by ``new_vm_slots`` and never added back.
    """
    if not is_local_endpoint(endpoint, hostname=hostname):
        raise ObservationRefused(
            f"host controller {endpoint!r} is not this host — refusing to report this machine's "
            "metrics as another authority's state",
        )
    if ram_reserve_mib < 0:
        raise ObservationRefused("ram reserve must be >= 0")
    meminfo = _read_meminfo(meminfo_path)
    try:
        load1 = float(os.getloadavg()[0])
    except (OSError, AttributeError):  # pragma: no cover - platform dependent
        load1 = 0.0
    try:
        stat = os.statvfs(str(disk_path))
    except OSError:
        raise ObservationRefused(f"cannot statvfs {disk_path!r} — no disk/inode evidence") from None
    block = max(1, int(stat.f_frsize))
    disk_total_mib = (int(stat.f_blocks) * block) // (1024 * 1024)
    disk_free_mib = (int(stat.f_bavail) * block) // (1024 * 1024)
    kvm_path = Path(host_root) / "dev" / "kvm"
    kvm_present = kvm_path.exists()
    kvm_usable = (os.access(str(kvm_path), os.R_OK | os.W_OK) if kvm_present else False)
    machine = platform.machine().strip().lower()
    architecture = _ARCH_ALIASES.get(machine, "")  # unknown arch stays unknown
    return HostMetrics(
        endpoint=endpoint,
        observed_at=now,
        hostname=hostname or _hostname(),
        cpu_total=int(os.cpu_count() or 0),
        load1=load1,
        ram_total_mib=meminfo["MemTotal"],
        ram_available_mib=meminfo["MemAvailable"],
        swap_total_mib=meminfo.get("SwapTotal", 0),
        swap_free_mib=meminfo.get("SwapFree", 0),
        ram_reserve_mib=int(ram_reserve_mib),
        disk_path=str(disk_path),
        disk_total_mib=disk_total_mib,
        disk_free_mib=disk_free_mib,
        inode_total=int(stat.f_files),
        inode_free=int(stat.f_favail),
        kvm_present=kvm_present,
        kvm_usable=kvm_usable,
        architecture=architecture,
        detail="" if kvm_usable else ("KVM device present but not usable by this identity" if kvm_present else "no /dev/kvm"),
    )


#: Dimensions this adapter genuinely cannot measure from ``/proc``. Reporting
#: them as zero would be a guess in the dangerous direction (a full host would
#: look free), so they are named as unknown and the verdict becomes UNKNOWN/0.
UNMEASURED_HOST_DIMENSIONS = frozenset({"cpu_committed"})


def host_observation_from_metrics(
    metrics: HostMetrics,
    *,
    fleet_id: str,
    resource_id: str,
    ttl_s: float,
    authority_epoch: int,
) -> HostObservation:
    """Turn raw metrics into admission evidence that cannot over-report.

    ``cpu_committed`` is deliberately left at 0 with the dimension marked
    unknown, so ``new_vm_slots`` refuses to produce a slot count from it. RAM
    committed is real (total minus available) — that number is measured, not
    inferred from a laptop.
    """
    return host_observation(
        fleet_id=fleet_id,
        resource_id=resource_id,
        now=metrics.observed_at,
        ttl_s=ttl_s,
        authority_epoch=authority_epoch,
        cpu_total=float(metrics.cpu_total),
        cpu_committed=0.0,
        ram_total_mib=metrics.ram_total_mib,
        ram_committed_mib=metrics.ram_committed_mib,
        ram_reserve_mib=metrics.ram_reserve_mib,
        disk_free_mib=metrics.disk_free_mib,
        inode_free=metrics.inode_free,
        kvm_usable=metrics.kvm_usable,
        architecture=metrics.architecture,
        unknown_dimensions=UNMEASURED_HOST_DIMENSIONS,
    )


# ===========================================================================
# Local QEMU guest identification (read-only)
# ===========================================================================
@dataclass(frozen=True)
class GuestSpec:
    """One existing guest lane the operator declared, by pidfile.

    ``expected_pid_start_ticks`` is the recorded start time from when the guest
    was launched; without it a pid is only an identification attempt, because a
    recycled pid would otherwise look like a live lane.
    """

    resource_id: str
    pidfile: Path
    expected_pid_start_ticks: int | None = None


def _pid_start_ticks(pid: int, *, proc_root: Path = Path("/proc")) -> int | None:
    """Field 22 of ``/proc/<pid>/stat`` — the anti-pid-reuse identity."""
    try:
        raw = (Path(proc_root) / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # ``comm`` may contain spaces and parentheses; state starts after the last ')'.
    closing = raw.rfind(")")
    if closing < 0:
        return None
    fields = raw[closing + 1 :].split()
    if len(fields) < 20:
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def read_local_guest(
    spec: GuestSpec,
    *,
    fleet_id: str,
    now: float,
    bandwidth: ProbeLimits,
    authority_epoch: int,
    proc_root: Path = Path("/proc"),
) -> GuestObservation:
    """Identify an existing guest. Never claims readiness.

    Health, topology, lease and client reach are all left unproven (``None``) on
    purpose: this adapter reads a pidfile and a ``/proc`` entry, which is
    identification, not a readiness proof. ``guest_capacity`` therefore answers
    ``UNKNOWN`` — exactly the audit's "bridge or pidfile alone is not enough".
    """
    pid: int | None = None
    start_ticks: int | None = None
    try:
        raw = Path(spec.pidfile).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        raw = ""
    if raw:
        try:
            candidate = int(raw.split()[0])
        except (IndexError, ValueError):
            candidate = 0
        if candidate > 0:
            ticks = _pid_start_ticks(candidate, proc_root=proc_root)
            if ticks is not None:
                pid, start_ticks = candidate, ticks
    # No pid → ``pid=None`` → guest_capacity answers BRIDGE_ONLY/UNKNOWN. A dead
    # pidfile is never promoted to "idle guest": the file is a claim, /proc is
    # the evidence, and only the evidence counts.
    return guest_observation(
        fleet_id=fleet_id,
        resource_id=spec.resource_id,
        now=now,
        ttl_s=bandwidth.ttl_s,
        authority_epoch=authority_epoch,
        pid=pid,
        pid_start_ticks=start_ticks,
        expected_pid_start_ticks=spec.expected_pid_start_ticks,
        bridge_present=None,
        health_ok=None,
        topology_ok=None,
        lease_free=None,
        client_reachable=None,
    )


# ===========================================================================
# Outstanding reservations — read from the host authority, never guessed
# ===========================================================================
_AUTHORITY_OCCUPYING_STATES = (
    "reserved",
    "active",
    "releasing",
    "cleanup_pending",
    "quarantined",
)


def read_authority_epoch(path: Path | str | None) -> int | None:
    """Read the authority's epoch **without becoming a writer on its store**.

    ``FleetAuthority.__init__`` runs ``_init_store()``, which CREATES the file,
    creates every table and inserts the meta row. An observation tool that used
    it would silently mint a 64 KB authority store at whatever ``--authority``
    path it was pointed at — a side effect from a command whose contract is "I
    only look". Measured on this branch: the CLI created the store before this
    function existed.

    Returns ``None`` when there is no readable authority epoch (absent path, not
    a store, unreadable) so the caller can refuse explicitly instead of
    defaulting to a made-up epoch.
    """
    if path is None or not str(path).strip():
        return None
    src = Path(path).expanduser()
    if not src.is_file():
        return None
    try:
        conn = sqlite3.connect(_read_only_uri(src), uri=True, timeout=5.0)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", ("epoch",)).fetchone()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ReservationRead:
    """Outstanding claims as the *host authority* reports them.

    ``available`` is ``False`` whenever the claim set cannot be trusted as a
    *complete* account of what occupies the host — no store, unreadable store,
    or an occupying row whose demand cannot be sized. ``unsized`` names the
    rows that caused the last case, so an operator can fix the row instead of
    reading "no reservations".
    """

    source: str
    available: bool
    reservations: tuple[Reservation, ...] = ()
    unsized: tuple[str, ...] = ()
    detail: str = ""


def _read_only_uri(path: Path) -> str:
    """A ``mode=ro`` SQLite URI for an arbitrary filesystem path.

    ``f"file:{path}?mode=ro"`` is not a path — it is text interpolation into a
    URI. A store at ``/var/lib/prta/a?b#c.sqlite3`` opened ``/var/lib/prta/a``
    (measured), so the real store was reported as "not found" and something else
    could have been read in its place. ``Path.as_uri()`` percent-encodes ``?``,
    ``#`` and ``%``; ``mode=ro`` is then appended as the query string.
    """
    return Path(path).expanduser().resolve().as_uri() + "?mode=ro"


def read_authority_reservations(
    path: Path | str | None, *, now: float
) -> ReservationRead:
    """Read outstanding allocations from the host's authority store, read-only.

    No store, no rows, no claim: this returns ``available=False`` with a reason
    instead of "zero reservations", because an unreadable authority is not
    evidence that nothing is reserved. The connection is opened
    ``mode=ro`` — this adapter must never become a writer on a live bench.
    """
    if path is None or not str(path).strip():
        return ReservationRead(
            source="",
            available=False,
            detail="no host authority store configured (pass --authority or set TOLLGATE_FLEET_AUTHORITY)",
        )
    src = Path(path).expanduser()
    if not src.is_file():
        return ReservationRead(source=str(src), available=False, detail="host authority store not found")
    try:
        conn = sqlite3.connect(_read_only_uri(src), uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        return ReservationRead(source=str(src), available=False, detail=redact(f"cannot open store: {exc}"))
    try:
        conn.row_factory = sqlite3.Row
        try:
            placeholders = ",".join("?" * len(_AUTHORITY_OCCUPYING_STATES))
            rows = conn.execute(
                f"SELECT run_id, state, demand, expires_at FROM allocations WHERE state IN ({placeholders})",
                _AUTHORITY_OCCUPYING_STATES,
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            return ReservationRead(
                source=str(src), available=False, detail=redact(f"store is not an authority store: {exc}")
            )
    finally:
        conn.close()

    out: list[Reservation] = []
    unsized: list[str] = []
    for row in rows:
        run_id = str(row["run_id"])
        try:
            demand = json.loads(row["demand"])
            host_demand = HostDemand(
                vcpus=int(demand["vcpus"]),
                ram_mib=int(demand["ram_mib"]),
                disk_mib=int(demand["disk_mib"]),
                disk_inodes=int(demand["inodes"]),
            )
        except (ValueError, KeyError, TypeError):
            # This row occupies the host but cannot be priced. Dropping it would
            # make its demand look like zero — the dangerous direction. Record
            # it, keep the sized rows, and make the whole read unavailable.
            unsized.append(run_id)
            continue
        state = str(row["state"])
        out.append(
            Reservation(
                run_id=run_id,
                demand=host_demand,
                expires_at=float(row["expires_at"]),
                # Anything past RESERVED is already inside the host's committed
                # counters; treating it as outstanding would double-count.
                started=state != "reserved",
            )
        )
    if unsized:
        return ReservationRead(
            source=str(src),
            available=False,
            reservations=tuple(out),
            unsized=tuple(unsized),
            detail=redact(
                "occupying allocation(s) cannot be sized: "
                + ", ".join(unsized)
                + " — headroom is unknown, not free"
            ),
        )
    return ReservationRead(source=str(src), available=True, reservations=tuple(out))


# ===========================================================================
# Evidence
# ===========================================================================
@dataclass(frozen=True)
class Refusal:
    """Something this adapter would not touch, and why."""

    subject: str
    reason: str
    detail: str = ""

    def as_report(self) -> dict[str, str]:
        return {"subject": self.subject, "reason": self.reason, "detail": redact(self.detail)}


@dataclass(frozen=True)
class Evidence:
    """Everything observed, with the timestamps needed to distrust it later."""

    observed_at: float
    authority_epoch: int
    ttl_s: float
    coordinators: Mapping[str, CoordinatorObservation] = field(default_factory=dict)
    exporters: Mapping[str, ExporterObservation] = field(default_factory=dict)
    health: Mapping[str, Any] = field(default_factory=dict)
    guests: Mapping[str, GuestObservation] = field(default_factory=dict)
    hosts: Mapping[str, HostObservation] = field(default_factory=dict)
    host_metrics: Mapping[str, HostMetrics] = field(default_factory=dict)
    reservations: tuple[Reservation, ...] = ()
    reservation_source: str = ""
    reservation_detail: str = ""
    #: Occupying allocations that exist (per the store) but cannot be sized, so
    #: the reservation vector above is a *lower bound*, not the whole claim set.
    reservations_unsized: tuple[str, ...] = ()
    coordinators_read: Mapping[str, CoordinatorRead] = field(default_factory=dict)
    refusals: tuple[Refusal, ...] = ()
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    # -- serialization (snapshots are re-decided, never trusted blind) ------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observed_at": self.observed_at,
            "authority_epoch": self.authority_epoch,
            "ttl_s": self.ttl_s,
            "coordinators": {k: _coordinator_to_dict(v) for k, v in self.coordinators.items()},
            "exporters": {k: _exporter_to_dict(v) for k, v in self.exporters.items()},
            "health": {k: _router_health_to_dict(v) for k, v in self.health.items()},
            "guests": {k: _guest_to_dict(v) for k, v in self.guests.items()},
            "hosts": {k: _host_to_dict(v) for k, v in self.hosts.items()},
            "host_metrics": {k: v.as_report() for k, v in self.host_metrics.items()},
            "reservations": [
                {
                    "run_id": r.run_id,
                    "demand": {
                        "vcpus": r.demand.vcpus,
                        "ram_mib": r.demand.ram_mib,
                        "disk_mib": r.demand.disk_mib,
                        "disk_inodes": r.demand.disk_inodes,
                    },
                    "expires_at": r.expires_at,
                    "started": r.started,
                }
                for r in self.reservations
            ],
            "reservation_source": self.reservation_source,
            "reservation_detail": self.reservation_detail,
            "reservations_unsized": list(self.reservations_unsized),
            "coordinators_read": {
                k: {
                    "fleet_id": v.fleet_id,
                    "reachable": v.reachable,
                    "version": v.version,
                    "published_resources": list(v.published_resources),
                    "places": {
                        pid: {
                            "name": p.name,
                            "exists": p.exists,
                            "acquired_by": p.acquired_by,
                            "available": p.available,
                            "match_patterns": list(p.match_patterns),
                        }
                        for pid, p in v.places.items()
                    },
                    "detail": v.detail,
                }
                for k, v in self.coordinators_read.items()
            },
            "refusals": [r.as_report() for r in self.refusals],
        }

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> Evidence:
        if int(doc.get("schema_version", -1)) != EVIDENCE_SCHEMA_VERSION:
            raise ObservationRefused(
                f"evidence snapshot schema_version {doc.get('schema_version')!r} is not "
                f"{EVIDENCE_SCHEMA_VERSION} — refusing to interpret it"
            )
        hosts = {k: _host_from_dict(v) for k, v in (doc.get("hosts") or {}).items()}
        metrics = {
            k: HostMetrics(
                endpoint=str(v["endpoint"]),
                observed_at=float(v["observed_at"]),
                hostname=str(v["hostname"]),
                cpu_total=int(v["cpu_total"]),
                load1=float(v["load1"]),
                ram_total_mib=int(v["ram_total_mib"]),
                ram_available_mib=int(v["ram_available_mib"]),
                swap_total_mib=int(v["swap_total_mib"]),
                swap_free_mib=int(v["swap_free_mib"]),
                ram_reserve_mib=int(v["ram_reserve_mib"]),
                disk_path=str(v["disk_path"]),
                disk_total_mib=int(v["disk_total_mib"]),
                disk_free_mib=int(v["disk_free_mib"]),
                inode_total=int(v["inode_total"]),
                inode_free=int(v["inode_free"]),
                kvm_present=bool(v["kvm_present"]),
                kvm_usable=None if v["kvm_usable"] is None else bool(v["kvm_usable"]),
                architecture=str(v["architecture"]),
                detail=str(v.get("detail") or ""),
            )
            for k, v in (doc.get("host_metrics") or {}).items()
        }
        return cls(
            observed_at=float(doc["observed_at"]),
            authority_epoch=int(doc["authority_epoch"]),
            ttl_s=float(doc["ttl_s"]),
            coordinators={k: _coordinator_from_dict(v) for k, v in (doc.get("coordinators") or {}).items()},
            exporters={k: _exporter_from_dict(v) for k, v in (doc.get("exporters") or {}).items()},
            health={k: _router_health_from_dict(v) for k, v in (doc.get("health") or {}).items()},
            guests={k: _guest_from_dict(v) for k, v in (doc.get("guests") or {}).items()},
            hosts=hosts,
            host_metrics=metrics,
            reservations=tuple(
                Reservation(
                    run_id=str(r["run_id"]),
                    demand=HostDemand(
                        vcpus=int(r["demand"]["vcpus"]),
                        ram_mib=int(r["demand"]["ram_mib"]),
                        disk_mib=int(r["demand"]["disk_mib"]),
                        disk_inodes=int(r["demand"]["disk_inodes"]),
                    ),
                    expires_at=float(r["expires_at"]),
                    started=bool(r["started"]),
                )
                for r in (doc.get("reservations") or [])
            ),
            reservation_source=str(doc.get("reservation_source") or ""),
            reservation_detail=str(doc.get("reservation_detail") or ""),
            reservations_unsized=tuple(str(r) for r in (doc.get("reservations_unsized") or ())),
            coordinators_read={
                k: CoordinatorRead(
                    fleet_id=str(v["fleet_id"]),
                    address="",
                    reachable=bool(v["reachable"]),
                    version=str(v.get("version") or ""),
                    published_resources=tuple(v.get("published_resources") or ()),
                    places={
                        pid: PlaceState(
                            name=str(p["name"]),
                            exists=bool(p["exists"]),
                            acquired_by=p.get("acquired_by"),
                            available=p.get("available"),
                            match_patterns=tuple(p.get("match_patterns") or ()),
                        )
                        for pid, p in (v.get("places") or {}).items()
                    },
                    detail=str(v.get("detail") or ""),
                )
                for k, v in (doc.get("coordinators_read") or {}).items()
            },
            refusals=tuple(
                Refusal(subject=str(r["subject"]), reason=str(r["reason"]), detail=str(r.get("detail") or ""))
                for r in (doc.get("refusals") or [])
            ),
        )


def _coordinator_to_dict(obs: CoordinatorObservation) -> dict[str, Any]:
    return {
        "fleet_id": obs.fleet_id,
        "reachable": obs.reachable,
        "observed_at": obs.observed_at,
        "ttl_s": obs.ttl_s,
        "authority_epoch": obs.authority_epoch,
    }


def _coordinator_from_dict(raw: Mapping[str, Any]) -> CoordinatorObservation:
    return coordinator_observation(
        fleet_id=str(raw["fleet_id"]),
        reachable=bool(raw["reachable"]),
        now=float(raw["observed_at"]),
        ttl_s=float(raw["ttl_s"]),
        authority_epoch=int(raw["authority_epoch"]),
    )


def _exporter_to_dict(obs: ExporterObservation) -> dict[str, Any]:
    return {
        "fleet_id": obs.fleet_id,
        "resource_id": obs.resource_id,
        "exporter_connected": obs.exporter_connected,
        "exported": obs.exported,
        "acquired": obs.acquired,
        "missing": obs.missing,
        "observed_at": obs.observed_at,
        "ttl_s": obs.ttl_s,
        "authority_epoch": obs.authority_epoch,
    }


def _exporter_from_dict(raw: Mapping[str, Any]) -> ExporterObservation:
    return exporter_observation(
        fleet_id=str(raw["fleet_id"]),
        resource_id=str(raw["resource_id"]),
        exporter_connected=bool(raw["exporter_connected"]),
        exported=bool(raw["exported"]),
        acquired=bool(raw["acquired"]),
        missing=bool(raw["missing"]),
        now=float(raw["observed_at"]),
        ttl_s=float(raw["ttl_s"]),
        authority_epoch=int(raw["authority_epoch"]),
    )


def _router_health_to_dict(obs: Any) -> dict[str, Any]:
    return {
        "fleet_id": obs.fleet_id,
        "resource_id": obs.resource_id,
        "identity": obs.identity,
        "expected_identity": obs.expected_identity,
        "health_ok": obs.health_ok,
        "session_active": obs.session_active,
        "vantage_ok": obs.vantage_ok,
        "telemetry_read_ok": obs.telemetry_read_ok,
        "protected": obs.protected,
        "observed_at": obs.observed_at,
        "ttl_s": obs.ttl_s,
        "authority_epoch": obs.authority_epoch,
    }


def _router_health_from_dict(raw: Mapping[str, Any]) -> Any:
    from lib.fleet_capacity import router_observation

    return router_observation(
        fleet_id=str(raw["fleet_id"]),
        resource_id=str(raw["resource_id"]),
        identity=str(raw.get("identity") or ""),
        expected_identity=str(raw.get("expected_identity") or ""),
        health_ok=raw.get("health_ok"),
        session_active=raw.get("session_active"),
        vantage_ok=raw.get("vantage_ok"),
        telemetry_read_ok=raw.get("telemetry_read_ok"),
        protected=bool(raw.get("protected") or False),
        now=float(raw["observed_at"]),
        ttl_s=float(raw["ttl_s"]),
        authority_epoch=int(raw["authority_epoch"]),
    )


def _guest_to_dict(obs: GuestObservation) -> dict[str, Any]:
    return {
        "fleet_id": obs.fleet_id,
        "resource_id": obs.resource_id,
        "pid": obs.pid,
        "pid_start_ticks": obs.pid_start_ticks,
        "expected_pid_start_ticks": obs.expected_pid_start_ticks,
        "bridge_present": obs.bridge_present,
        "health_ok": obs.health_ok,
        "topology_ok": obs.topology_ok,
        "lease_free": obs.lease_free,
        "client_reachable": obs.client_reachable,
        "observed_at": obs.observed_at,
        "ttl_s": obs.ttl_s,
        "authority_epoch": obs.authority_epoch,
    }


def _guest_from_dict(raw: Mapping[str, Any]) -> GuestObservation:
    return guest_observation(
        fleet_id=str(raw["fleet_id"]),
        resource_id=str(raw["resource_id"]),
        pid=None if raw.get("pid") is None else int(raw["pid"]),
        pid_start_ticks=None if raw.get("pid_start_ticks") is None else int(raw["pid_start_ticks"]),
        expected_pid_start_ticks=(
            None if raw.get("expected_pid_start_ticks") is None else int(raw["expected_pid_start_ticks"])
        ),
        bridge_present=raw.get("bridge_present"),
        health_ok=raw.get("health_ok"),
        topology_ok=raw.get("topology_ok"),
        lease_free=raw.get("lease_free"),
        client_reachable=raw.get("client_reachable"),
        now=float(raw["observed_at"]),
        ttl_s=float(raw["ttl_s"]),
        authority_epoch=int(raw["authority_epoch"]),
    )


def _host_to_dict(obs: HostObservation) -> dict[str, Any]:
    return {
        "fleet_id": obs.fleet_id,
        "resource_id": obs.resource_id,
        "cpu_total": obs.cpu_total,
        "cpu_committed": obs.cpu_committed,
        "ram_total_mib": obs.ram_total_mib,
        "ram_committed_mib": obs.ram_committed_mib,
        "ram_reserve_mib": obs.ram_reserve_mib,
        "disk_free_mib": obs.disk_free_mib,
        "inode_free": obs.inode_free,
        "kvm_usable": obs.kvm_usable,
        "architecture": obs.architecture,
        "unknown_dimensions": sorted(obs.unknown_dimensions),
        "observed_at": obs.observed_at,
        "ttl_s": obs.ttl_s,
        "authority_epoch": obs.authority_epoch,
    }


def _host_from_dict(raw: Mapping[str, Any]) -> HostObservation:
    return host_observation(
        fleet_id=str(raw["fleet_id"]),
        resource_id=str(raw["resource_id"]),
        now=float(raw["observed_at"]),
        ttl_s=float(raw["ttl_s"]),
        authority_epoch=int(raw["authority_epoch"]),
        cpu_total=float(raw.get("cpu_total") or 0),
        cpu_committed=float(raw.get("cpu_committed") or 0),
        ram_total_mib=int(raw.get("ram_total_mib") or 0),
        ram_committed_mib=int(raw.get("ram_committed_mib") or 0),
        ram_reserve_mib=int(raw.get("ram_reserve_mib") or 0),
        disk_free_mib=int(raw.get("disk_free_mib") or 0),
        inode_free=int(raw.get("inode_free") or 0),
        kvm_usable=raw.get("kvm_usable"),
        architecture=str(raw.get("architecture") or ""),
        unknown_dimensions=frozenset(raw.get("unknown_dimensions") or ()),
    )


# ===========================================================================
# Collection
# ===========================================================================
def collect(
    manifest: FleetManifest,
    *,
    limits: ProbeLimits | None = None,
    authority_epoch: int,
    now: float,
    client: LabgridReadOnlyClient | None = None,
    fleets: Sequence[str] | None = None,
    disk_path: Path | str = "/",
    ram_reserve_mib: int = 0,
    guests: Iterable[GuestSpec] = (),
    authority_path: Path | str | None = None,
    hostname: str | None = None,
    meminfo_path: Path = Path("/proc/meminfo"),
    proc_root: Path = Path("/proc"),
    host_root: Path = Path("/"),
) -> Evidence:
    """Observe every fleet the manifest authorizes — and nothing else.

    Authorization comes from ``run_probes``/``observation_plan`` (slice 1), so a
    target the manifest has not authorized is never reached: if the plan ever
    grows an endpoint the manifest does not permit for ``observe``, the probe is
    refused before any subprocess or file read.
    """
    limits = limits or ProbeLimits()
    client = client or LabgridReadOnlyClient(limits)
    selected = list(fleets) if fleets is not None else sorted(manifest.fleets)
    for fleet_id in selected:  # raises for an unconfigured fleet: no fallback
        manifest.fleet(fleet_id)

    coordinators: dict[str, CoordinatorObservation] = {}
    exporters: dict[str, ExporterObservation] = {}
    hosts: dict[str, HostObservation] = {}
    host_metrics: dict[str, HostMetrics] = {}
    reads: dict[str, CoordinatorRead] = {}
    refusals: list[Refusal] = []
    probed_epoch = int(authority_epoch)

    def probe(target: ProbeTarget) -> Any:
        entry = manifest.fleet(target.fleet_id)
        try:
            if target.role == "coordinator":
                if not entry.permits("observe"):
                    raise ObservationRefused(
                        f"fleet {target.fleet_id!r} does not permit 'observe' — refusing to contact "
                        "its coordinator"
                    )
                place_ids = tuple(r.resource_id for r in manifest.resources_of(target.fleet_id))
                read = read_coordinator(
                    client, fleet_id=target.fleet_id, address=target.endpoint, place_ids=place_ids
                )
                reads[target.fleet_id] = read
                coordinators[target.fleet_id] = coordinator_observation(
                    fleet_id=target.fleet_id,
                    reachable=read.reachable,
                    now=now,
                    ttl_s=limits.ttl_s,
                    authority_epoch=probed_epoch,
                )
                _record_places(manifest, read, exporters=exporters, now=now, limits=limits, epoch=probed_epoch)
                return read
            if target.role == "host_controller":
                metrics = read_local_host(
                    target.endpoint,
                    bandwidth=limits,
                    now=now,
                    disk_path=disk_path,
                    ram_reserve_mib=ram_reserve_mib,
                    hostname=hostname,
                    meminfo_path=meminfo_path,
                    host_root=host_root,
                )
                host_metrics[target.fleet_id] = metrics
                hosts[target.fleet_id] = host_observation_from_metrics(
                    metrics,
                    fleet_id=target.fleet_id,
                    resource_id=f"host:{target.fleet_id}",
                    ttl_s=limits.ttl_s,
                    authority_epoch=probed_epoch,
                )
                return metrics
        except ObservationRefused as exc:
            # A refusal is per-target: one fleet's unmeasured host must not
            # discard another fleet's evidence. It is recorded, never silently
            # upgraded to "unknown means maybe free".
            refusals.append(
                Refusal(
                    subject=f"{target.fleet_id}:{target.role}",
                    reason="refused",
                    detail=str(exc),
                )
            )
            return None
        raise ObservationRefused(f"unknown probe role {target.role!r}")

    try:
        run_probes(manifest, probe, fleets=selected)
    except ObservationRefused as exc:
        # The plan itself was refused (nothing authorized). Record and continue
        # with whatever evidence exists — an empty plan is a result, not a crash.
        refusals.append(Refusal(subject="observation plan", reason="refused", detail=str(exc)))
    except CapacityError as exc:
        refusals.append(Refusal(subject="observation plan", reason="refused", detail=str(exc)))

    guest_obs: dict[str, GuestObservation] = {}
    for spec in guests:
        owner = manifest.resource(spec.resource_id)
        if owner is None:
            refusals.append(
                Refusal(
                    subject=f"guest:{spec.resource_id}",
                    reason="not_configured",
                    detail="guest pidfile is not an allowlisted place in this manifest",
                )
            )
            continue
        if owner.kind != "guest":
            refusals.append(
                Refusal(
                    subject=f"guest:{spec.resource_id}",
                    reason="kind_mismatch",
                    detail=f"configured as kind {owner.kind!r}, observed as a QEMU guest",
                )
            )
            continue
        guest_obs[owner.resource_id] = read_local_guest(
            spec,
            fleet_id=owner.fleet_id,
            now=now,
            bandwidth=limits,
            authority_epoch=probed_epoch,
            proc_root=proc_root,
        )

    reservation_read = read_authority_reservations(authority_path, now=now)
    if reservation_read.unsized:
        # Not a silent omission: an occupier we cannot price is a refusal with a
        # reason code, exactly like a dead exporter.
        refusals.append(
            Refusal(
                subject="host authority reservations",
                reason="reservations_unsized",
                detail=reservation_read.detail,
            )
        )
    return Evidence(
        observed_at=now,
        authority_epoch=probed_epoch,
        ttl_s=limits.ttl_s,
        coordinators=coordinators,
        exporters=exporters,
        guests=guest_obs,
        hosts=hosts,
        host_metrics=host_metrics,
        reservations=reservation_read.reservations,
        reservation_source=reservation_read.source,
        reservation_detail=reservation_read.detail,
        reservations_unsized=reservation_read.unsized,
        coordinators_read=reads,
        refusals=tuple(refusals),
    )


def _record_places(
    manifest: FleetManifest,
    read: CoordinatorRead,
    *,
    exporters: dict[str, ExporterObservation],
    now: float,
    limits: ProbeLimits,
    epoch: int,
) -> None:
    """Translate the place view into slice-1 exporter evidence.

    Mapping (all four facts are measured, none inferred):

    * nothing published at all → ``exporter_connected=False``: no exporter is
      serving this coordinator, so nothing here can be counted;
    * place absent → ``missing=True``;
    * place present without any matched resource block → ``exported=False``;
    * ``acquired: <owner>`` → ``acquired=True``.
    """
    any_published = bool(read.published_resources)
    for resource_id in (r.resource_id for r in manifest.resources_of(read.fleet_id)):
        entry = manifest.resource(resource_id)
        if entry is None or entry.kind != "router":
            continue
        place = read.places.get(resource_id)
        if place is None:
            continue
        exported = place.exists and bool(place.match_patterns)
        acquired = bool(place.acquired_by)
        exporters[resource_id] = exporter_observation(
            fleet_id=read.fleet_id,
            resource_id=resource_id,
            exporter_connected=any_published and (exported or place.exists),
            exported=exported,
            acquired=acquired,
            missing=not place.exists,
            now=now,
            ttl_s=limits.ttl_s,
            authority_epoch=epoch,
        )


# ===========================================================================
# Decisions (pure — replays slice 1 over the evidence)
# ===========================================================================
def resolve_host_demand(
    *, vcpus: int = 2, ram_mib: int = 1024, disk_mib: int = 4096, disk_inodes: int = 1
) -> HostDemand:
    """The declared resource vector of ONE whole-topology lane.

    It is a request, not a measurement: the CLI prints it next to the answer so
    an operator can see what the raw headroom was compared against.
    """
    return HostDemand(vcpus=vcpus, ram_mib=ram_mib, disk_mib=disk_mib, disk_inodes=disk_inodes)


def decide(
    manifest: FleetManifest,
    evidence: Evidence,
    *,
    now: float,
    demand: HostDemand | None = None,
) -> tuple[Verdict | SpawnVerdict, ...]:
    """Re-run slice-1's decision path over the evidence (no I/O at all)."""
    demand = demand or resolve_host_demand()
    verdicts: list[Verdict | SpawnVerdict] = []
    for fleet_id in sorted(manifest.fleets):
        verdicts.append(
            coordinator_capacity(
                manifest,
                fleet_id,
                now=now,
                authority_epoch=evidence.authority_epoch,
                observation=evidence.coordinators.get(fleet_id),
            )
        )
    for resource_id in sorted(manifest.resources):
        entry = manifest.resource(resource_id)
        if entry is None or entry.resource_id != resource_id:
            continue  # aliases are not separate resources
        fleet_id = entry.fleet_id
        observations = Observations(
            coordinator=evidence.coordinators.get(fleet_id),
            exporter=evidence.exporters.get(resource_id),
            health=evidence.health.get(resource_id),
            guest=evidence.guests.get(resource_id),
        )
        if entry.kind == "guest":
            verdicts.append(
                guest_capacity(
                    manifest,
                    resource_id,
                    now=now,
                    authority_epoch=evidence.authority_epoch,
                    observations=observations,
                )
            )
        else:
            verdicts.append(
                router_capacity(
                    manifest,
                    resource_id,
                    now=now,
                    authority_epoch=evidence.authority_epoch,
                    observations=observations,
                )
            )
    for fleet_id in sorted(manifest.fleets):
        host = evidence.hosts.get(fleet_id)
        if host is None:
            continue
        verdicts.append(
            new_vm_slots(
                host,
                demand=demand,
                now=now,
                authority_epoch=evidence.authority_epoch,
                reservations=evidence.reservations,
                # A partial ledger is not a complete one. Without this the rows
                # that could not be sized would be priced at zero.
                reservations_incomplete=bool(evidence.reservations_unsized),
                limit=manifest.fleets[fleet_id].capacity_limits.max_new_vm_slots,
            )
        )
    return tuple(verdicts)


# ===========================================================================
# Report
# ===========================================================================
def _explain(verdict: Verdict | SpawnVerdict) -> str:
    """One-line reason string for either verdict type (SpawnVerdict has no ``explain``)."""
    codes = ",".join(r.value for r in verdict.reasons) or Reason.OK.value
    capacity = verdict.capacity
    return f"{verdict.fleet_id}/{verdict.resource_id}: {verdict.state.value} capacity={capacity} ({codes})"


def build_report(
    manifest: FleetManifest,
    evidence: Evidence,
    verdicts: Sequence[Verdict | SpawnVerdict],
    *,
    generated_at: str,
    now: float,
    demand: HostDemand,
    admission_status: str = ADMISSION_STATUS,
) -> dict[str, Any]:
    """Machine-readable operator report. Text rendering lives in the CLI."""
    summaries = summarize(manifest, verdicts)
    per_fleet: dict[str, list[Verdict | SpawnVerdict]] = {fid: [] for fid in manifest.fleets}
    for verdict in verdicts:
        if verdict.kind is ResourceKind.COORDINATOR:
            per_fleet.setdefault(verdict.fleet_id, []).append(verdict)
            continue
        if verdict.kind is ResourceKind.HOST:
            per_fleet.setdefault(verdict.fleet_id, []).append(verdict)
            continue
        owner = manifest.owner_of(verdict.resource_id)
        if owner is None:
            continue
        per_fleet[owner].append(verdict)

    fleets: list[dict[str, Any]] = []
    for summary in summaries:
        items = per_fleet.get(summary.fleet_id, [])
        host_verdicts = [v for v in items if isinstance(v, SpawnVerdict)]
        metrics = evidence.host_metrics.get(summary.fleet_id)
        read = evidence.coordinators_read.get(summary.fleet_id)
        fleets.append(
            {
                "fleet_id": summary.fleet_id,
                "coordinator_reachable": summary.coordinator_reachable,
                "coordinator_status": [
                    _explain(v) for v in items if v.kind is ResourceKind.COORDINATOR
                ],
                "configured_places": summary.configured_places,
                "exported_available": summary.exported_available,
                "idle_physical_routers": summary.idle_routers,
                "idle_existing_guests": summary.idle_guests,
                "new_vm_slots_raw": summary.new_vm_slots,
                "admission": admission_status,
                "limits": {
                    "max_concurrent_runs": summary.max_concurrent_runs,
                    "max_idle_guests": summary.max_idle_guests,
                    "max_new_vm_slots": summary.max_new_vm_slots,
                },
                "published_resources": list(read.published_resources) if read else [],
                "host": metrics.as_report() if metrics else None,
                "host_admission": [
                    {
                        "state": v.state.value,
                        "slots": v.slots,
                        "reasons": [r.value for r in v.reasons],
                        "detail": redact(v.detail),
                        "raw_headroom": {k: float(val) for k, val in v.headroom.items()},
                    }
                    for v in host_verdicts
                ],
                "excluded": [
                    {
                        "resource_id": v.resource_id,
                        "kind": v.kind.value,
                        "state": v.state.value,
                        "capacity": v.capacity,
                        "reasons": [r.value for r in v.reasons],
                        "detail": redact(v.detail),
                    }
                    for v in summary.excluded
                ],
                "verdicts": [
                    {
                        "resource_id": v.resource_id,
                        "kind": v.kind.value,
                        "state": v.state.value,
                        "capacity": v.capacity,
                        "reasons": [r.value for r in v.reasons],
                        "detail": redact(v.detail),
                    }
                    for v in items
                ],
            }
        )
    return {
        "generated_at": generated_at,
        "observed_at": evidence.observed_at,
        "age_s": max(0.0, now - evidence.observed_at),
        "authority_epoch": evidence.authority_epoch,
        "ttl_s": evidence.ttl_s,
        "manifest": manifest.source,
        "admission": {"status": admission_status, "detail": ADMISSION_DETAIL},
        "declared_lane_demand": {
            "vcpus": demand.vcpus,
            "ram_mib": demand.ram_mib,
            "disk_mib": demand.disk_mib,
            "disk_inodes": demand.disk_inodes,
        },
        "reservations": {
            "source": evidence.reservation_source,
            "detail": evidence.reservation_detail,
            "complete": not evidence.reservations_unsized,
            "unsized": list(evidence.reservations_unsized),
            "outstanding": [
                {
                    "run_id": r.run_id,
                    "started": r.started,
                    "expires_at": r.expires_at,
                    "demand": {
                        "vcpus": r.demand.vcpus,
                        "ram_mib": r.demand.ram_mib,
                        "disk_mib": r.demand.disk_mib,
                        "disk_inodes": r.demand.disk_inodes,
                    },
                }
                for r in evidence.reservations
            ],
        },
        "refusals": [r.as_report() for r in evidence.refusals],
        "fleets": fleets,
        "totals": {
            "reachable_fleets": sum(1 for f in fleets if f["coordinator_reachable"]),
            "configured_fleets": len(fleets),
            "idle_physical_routers": sum(f["idle_physical_routers"] for f in fleets),
            "idle_existing_guests": sum(f["idle_existing_guests"] for f in fleets),
            "exported_available": sum(f["exported_available"] for f in fleets),
            # Never a promise: see admission.status.
            "new_vm_slots_raw": sum(f["new_vm_slots_raw"] for f in fleets),
        },
    }
