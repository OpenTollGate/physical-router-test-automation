"""Fleet capacity detection — authorized manifests + reason-coded observations.

Slice 1 of the multi-contributor fleet plan. Source of truth for every rule
here is ``reports/prta-fleet-capacity-audit.md`` (2026-10-30). This module
answers one question per resource — *is this usable capacity right now, and
if not, why not* — and it answers it from data it was handed, never from
something it went and asked for.

Two halves, deliberately separated:

* **Boundaries** — the only code here that touches the world:
  ``load_manifest`` reads one explicitly named YAML file, and ``run_probes``
  calls an injected callable for the endpoints a manifest authorizes.
* **Decision path (pure)** — ``router_capacity``, ``guest_capacity``,
  ``new_vm_slots``, ``summarize``. No filesystem, no network, no clock:
  ``now`` and ``authority_epoch`` are arguments, so every distinction below
  is testable without a coordinator, a VM or a router.

Honesty rules encoded in the types, not in prose:

* A reachable coordinator certifies the **control plane only**.
  ``COORDINATOR_REACHABLE`` carries capacity 0 and is never counted.
* Unknown, stale, epoch-mismatched or refused evidence yields capacity 0
  with a reason code. There is no optimistic default anywhere.
* An idle guest is **not** a new VM slot: the guest still holds host CPU,
  RAM and disk, and only host admission decides new slots.
* One canonical owner per physical resource. A place id or alias claimed by
  two fleets is REFUSED at load time rather than resolved by precedence.
* There is no global default coordinator or host. A fleet id that is not in
  the manifest raises; nothing is filled in from a fallback.

Manifest errors never echo file content: PyYAML's own message interpolates
source snippets, so ``load_manifest`` withholds it (see
``_yaml_error_message``) and every error string passes through ``redact``.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import yaml

SCHEMA_VERSION = 1

#: Actions a fleet may authorize. An unknown action is refused at load time —
#: there is no "permit everything" default.
KNOWN_ACTIONS = frozenset({"observe", "acquire", "release", "power"})

#: Resource kinds a place entry may declare.
KNOWN_KINDS = frozenset({"router", "guest"})

__all__ = [
    "SCHEMA_VERSION",
    "KNOWN_ACTIONS",
    "KNOWN_KINDS",
    "CapacityError",
    "ManifestError",
    "ManifestRefused",
    "redact",
    "CredentialRef",
    "CapacityLimits",
    "CoordinatorSpec",
    "HostControllerSpec",
    "ResourceEntry",
    "FleetEntry",
    "FleetManifest",
    "parse_manifest",
    "load_manifest",
    "ResourceKind",
    "State",
    "Reason",
    "Verdict",
    "Observations",
    "CoordinatorObservation",
    "ExporterObservation",
    "RouterHealthObservation",
    "GuestObservation",
    "HostObservation",
    "host_observation",
    "router_observation",
    "guest_observation",
    "coordinator_observation",
    "exporter_observation",
    "router_capacity",
    "guest_capacity",
    "configured_capacity",
    "coordinator_capacity",
    "resource_conflicts",
    "lanes_independent",
    "new_vm_slots",
    "SpawnVerdict",
    "HostDemand",
    "Reservation",
    "FleetSummary",
    "summarize",
    "ProbeTarget",
    "observation_plan",
    "run_probes",
]


# ===========================================================================
# Errors and redaction
# ===========================================================================
class CapacityError(RuntimeError):
    """Base error — messages describe the problem, never manifest values."""


class ManifestError(CapacityError):
    """Manifest missing, malformed, or carrying a placeholder."""


class ManifestRefused(CapacityError):
    """Manifest is well-formed but unsafe: duplicate owner, literal secret,
    unknown action/key. Never resolved by precedence."""


_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
        "[REDACTED:pem]",
    ),
    (re.compile(r"nsec1[0-9a-z]{20,}"), "[REDACTED:nsec]"),
    (
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
            r"|sk-[A-Za-z0-9]{20,}|xox[abps]-[A-Za-z0-9-]{10,})"
        ),
        "[REDACTED:token]",
    ),
    (re.compile(r"cashu[AB][A-Za-z0-9+/=]{20,}"), "[REDACTED:cashu]"),
    (
        re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|credential)s?\b\s*[:=]\s*\S+"),
        r"\1: [REDACTED]",
    ),
    (re.compile(r"(?<![0-9a-fA-F])[a-f0-9]{64}(?![0-9a-fA-F])"), "[REDACTED:hex64]"),
)


def redact(text: str) -> str:
    """Strip credential-shaped values from text bound for a log or an error.

    Applied to every string this module raises or records. Callers that
    handle their own diagnostics should run their text through it too.
    """
    out = text
    for pattern, replacement in _REDACTIONS:
        out = pattern.sub(replacement, out)
    return out


def _yaml_error_message(src: Path | str, exc: yaml.YAMLError) -> str:
    """Position-only YAML error.

    ``str(exc)`` is deliberately never interpolated: PyYAML echoes source
    text (scanner snippets, offending scalars) and that is the documented
    leak in ``lib/lab_inventory.py``. Marks are safe — they carry coordinates,
    not content.
    """
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    where = ""
    if mark is not None:
        where = f" at line {int(getattr(mark, 'line', 0)) + 1}, column {int(getattr(mark, 'column', 0)) + 1}"
    return redact(
        f"fleet manifest {src} is not valid YAML ({type(exc).__name__}{where}) — fix the syntax. "
        "The parser message is withheld because it can echo file content."
    )


# ===========================================================================
# Manifest
# ===========================================================================
_PLACEHOLDER_PATTERN = re.compile(
    r"(?:^|[^A-Z0-9])(?:"
    r"REPLACE[_-]?ME|PLACEHOLDER|CHANGE[_-]?ME|TODO|FIXME"
    r"|EXAMPLE[_-](?:HOST|FLEET|PLACE|LANE|NAME|VALUE)"
    r")(?:[^A-Z0-9]|$)"
)


def _looks_like_placeholder(value: str) -> bool:
    """True for an unfinished manifest value.

    Deliberately token-based rather than substring-based: real lab hostnames
    legitimately contain ``example`` (RFC 2606) and ``example.com`` must not
    be mistaken for an unfinished value.
    """
    stripped = (value or "").strip()
    if not stripped:
        return True
    if "<" in stripped or ">" in stripped:
        return True
    return bool(_PLACEHOLDER_PATTERN.search(stripped.upper()))


@dataclass(frozen=True)
class CredentialRef:
    """A *reference* to a credential. Never a value."""

    source: str
    name: str


@dataclass(frozen=True)
class CoordinatorSpec:
    address: str
    credential_ref: CredentialRef | None = None


@dataclass(frozen=True)
class HostControllerSpec:
    endpoint: str
    credential_ref: CredentialRef | None = None


@dataclass(frozen=True)
class CapacityLimits:
    """Per-fleet self-limits. They can only ever tighten admission."""

    max_concurrent_runs: int = 1
    max_idle_guests: int = 0
    max_new_vm_slots: int = 0


@dataclass(frozen=True)
class ResourceEntry:
    """One physical topology/place and the fleet that canonically owns it."""

    resource_id: str
    fleet_id: str
    kind: str = "router"
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class FleetEntry:
    fleet_id: str
    coordinator: CoordinatorSpec
    host_controller: HostControllerSpec
    allowed_places: tuple[str, ...]
    permitted_actions: frozenset[str]
    capacity_limits: CapacityLimits
    shared_conflict_ids: tuple[str, ...] = ()
    owner_label: str = ""

    def permits(self, action: str) -> bool:
        return action in self.permitted_actions


@dataclass(frozen=True)
class FleetManifest:
    """Independently owned fleets — and no default anything.

    There is intentionally no ``coordinator``/``host`` attribute: a caller
    that has not named a fleet cannot get an endpoint out of this object.
    """

    schema_version: int
    fleets: Mapping[str, FleetEntry]
    resources: Mapping[str, ResourceEntry] = field(default_factory=dict)
    source: str = "<memory>"

    # -- lookups (never default) -------------------------------------------
    def fleet(self, fleet_id: str) -> FleetEntry:
        try:
            return self.fleets[fleet_id]
        except KeyError:
            known = sorted(self.fleets) or ["(none)"]
            raise ManifestError(
                f"fleet {fleet_id!r} is not configured in {self.source} (known: {known}) — "
                "fleets are explicit: there is no default fleet or default host"
            ) from None

    def resource(self, resource_id: str) -> ResourceEntry | None:
        """The canonical entry for an id or one of its aliases (None if unknown)."""
        return self.resources.get(resource_id)

    def owner_of(self, resource_id: str) -> str | None:
        entry = self.resources.get(resource_id)
        return entry.fleet_id if entry else None

    def resources_of(self, fleet_id: str) -> tuple[ResourceEntry, ...]:
        self.fleet(fleet_id)  # raises for an unknown fleet
        seen: dict[str, ResourceEntry] = {}
        for entry in self.resources.values():
            if entry.fleet_id == fleet_id:
                seen.setdefault(entry.resource_id, entry)
        return tuple(seen.values())


_FLEET_KEYS = frozenset(
    {
        "id",
        "owner",
        "coordinator",
        "host_controller",
        "allowed_places",
        "permitted_actions",
        "capacity_limits",
        "shared_conflict_ids",
    }
)
_CREDENTIAL_KEYS = frozenset({"source", "name"})


def _require_mapping(value: Any, what: str, source: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestError(f"fleet manifest {source}: {what} must be a mapping")
    return value


def _require_text(value: Any, what: str, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"fleet manifest {source}: {what} must be a non-empty string")
    return value.strip()


def _parse_credential_ref(value: Any, what: str, source: str) -> CredentialRef:
    ref = _require_mapping(value, what, source)
    extra = sorted(set(ref) - _CREDENTIAL_KEYS)
    if extra:
        # A literal value under any other key is exactly what must not live
        # in a manifest; refuse without echoing it.
        raise ManifestRefused(
            redact(
                f"fleet manifest {source}: {what} carries unexpected key(s) {extra} — "
                "credentials are referenced by (source, name), never embedded"
            )
        )
    return CredentialRef(
        source=_require_text(ref.get("source"), f"{what}.source", source),
        name=_require_text(ref.get("name"), f"{what}.name", source),
    )


def _parse_place(raw: Any, fleet_id: str, source: str) -> ResourceEntry:
    if isinstance(raw, str):
        resource_id = raw.strip()
        aliases: tuple[str, ...] = ()
        kind = "router"
    elif isinstance(raw, Mapping):
        extra = sorted(set(raw) - {"id", "aliases", "kind"})
        if extra:
            raise ManifestRefused(
                f"fleet manifest {source}: fleet {fleet_id!r} allowed_places entry has "
                f"unexpected key(s) {extra}"
            )
        resource_id = _require_text(raw.get("id"), f"fleet {fleet_id!r} allowed_places[].id", source)
        raw_aliases = raw.get("aliases") or ()
        if not isinstance(raw_aliases, Iterable) or isinstance(raw_aliases, (str, bytes)):
            raise ManifestError(
                f"fleet manifest {source}: fleet {fleet_id!r} allowed_places[].aliases must be a list"
            )
        aliases = tuple(str(a).strip() for a in raw_aliases if str(a).strip())
        kind = str(raw.get("kind") or "router").strip()
    else:
        raise ManifestError(
            f"fleet manifest {source}: fleet {fleet_id!r} allowed_places entries must be a "
            "string or a mapping with 'id'"
        )
    if not resource_id:
        raise ManifestError(f"fleet manifest {source}: fleet {fleet_id!r} has an empty place id")
    if _looks_like_placeholder(resource_id):
        raise ManifestError(
            f"fleet manifest {source}: fleet {fleet_id!r} place id {resource_id!r} is still a placeholder"
        )
    if kind not in KNOWN_KINDS:
        raise ManifestError(
            f"fleet manifest {source}: fleet {fleet_id!r} place {resource_id!r} has kind {kind!r} "
            f"(known: {sorted(KNOWN_KINDS)})"
        )
    return ResourceEntry(
        resource_id=resource_id, fleet_id=fleet_id, kind=kind, aliases=aliases
    )


def _parse_fleet(raw: Any, index: int, source: str) -> tuple[FleetEntry, tuple[ResourceEntry, ...]]:
    doc = _require_mapping(raw, f"fleets[{index}]", source)
    unknown = sorted(set(doc) - _FLEET_KEYS)
    if unknown:
        raise ManifestRefused(
            redact(
                f"fleet manifest {source}: fleets[{index}] has unknown key(s) {unknown} — "
                "a manifest carries references and limits only"
            )
        )

    fleet_id = _require_text(doc.get("id"), f"fleets[{index}].id", source)
    if _looks_like_placeholder(fleet_id):
        raise ManifestError(
            f"fleet manifest {source}: fleets[{index}].id {fleet_id!r} is still a placeholder"
        )

    coord_doc = _require_mapping(doc.get("coordinator"), f"fleet {fleet_id!r} coordinator", source)
    coord_unknown = sorted(set(coord_doc) - {"address", "credential_ref"})
    if coord_unknown:
        raise ManifestRefused(
            f"fleet manifest {source}: fleet {fleet_id!r} coordinator has unknown key(s) {coord_unknown}"
        )
    address = _require_text(coord_doc.get("address"), f"fleet {fleet_id!r} coordinator.address", source)
    if _looks_like_placeholder(address):
        raise ManifestError(
            f"fleet manifest {source}: fleet {fleet_id!r} coordinator.address {address!r} is still a "
            "placeholder — each fleet names its own coordinator"
        )
    if ":" not in address:
        raise ManifestError(
            f"fleet manifest {source}: fleet {fleet_id!r} coordinator.address must be host:port"
        )
    coord_ref = (
        _parse_credential_ref(coord_doc["credential_ref"], f"fleet {fleet_id!r} coordinator.credential_ref", source)
        if "credential_ref" in coord_doc
        else None
    )

    host_doc = _require_mapping(doc.get("host_controller"), f"fleet {fleet_id!r} host_controller", source)
    host_unknown = sorted(set(host_doc) - {"endpoint", "credential_ref"})
    if host_unknown:
        raise ManifestRefused(
            f"fleet manifest {source}: fleet {fleet_id!r} host_controller has unknown key(s) {host_unknown}"
        )
    endpoint = _require_text(host_doc.get("endpoint"), f"fleet {fleet_id!r} host_controller.endpoint", source)
    if _looks_like_placeholder(endpoint):
        raise ManifestError(
            f"fleet manifest {source}: fleet {fleet_id!r} host_controller.endpoint is still a placeholder"
        )
    host_ref = (
        _parse_credential_ref(
            host_doc["credential_ref"], f"fleet {fleet_id!r} host_controller.credential_ref", source
        )
        if "credential_ref" in host_doc
        else None
    )

    raw_places = doc.get("allowed_places")
    if not isinstance(raw_places, list) or not raw_places:
        raise ManifestError(
            f"fleet manifest {source}: fleet {fleet_id!r} needs a non-empty allowed_places list — "
            "capacity is never discovered by scanning"
        )
    places = tuple(_parse_place(p, fleet_id, source) for p in raw_places)
    if not places:
        raise ManifestError(f"fleet manifest {source}: fleet {fleet_id!r} allowed_places is empty")

    raw_actions = doc.get("permitted_actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ManifestError(
            f"fleet manifest {source}: fleet {fleet_id!r} needs permitted_actions (known: "
            f"{sorted(KNOWN_ACTIONS)})"
        )
    actions = frozenset(str(a).strip() for a in raw_actions)
    unknown_actions = sorted(actions - KNOWN_ACTIONS)
    if unknown_actions:
        raise ManifestError(
            f"fleet manifest {source}: fleet {fleet_id!r} permits unknown action(s) {unknown_actions} "
            f"(known: {sorted(KNOWN_ACTIONS)}) — permissions are an allowlist, never a wildcard"
        )
    # Capacity detection itself must be authorized; a fleet that cannot be
    # observed contributes nothing rather than being assumed available.
    if "observe" not in actions:
        raise ManifestError(
            f"fleet manifest {source}: fleet {fleet_id!r} does not permit 'observe' — it can never "
            "contribute detected capacity"
        )

    limits_doc = doc.get("capacity_limits") or {}
    limits_doc = _require_mapping(limits_doc, f"fleet {fleet_id!r} capacity_limits", source)
    limits_unknown = sorted(set(limits_doc) - {"max_concurrent_runs", "max_idle_guests", "max_new_vm_slots"})
    if limits_unknown:
        raise ManifestRefused(
            f"fleet manifest {source}: fleet {fleet_id!r} capacity_limits has unknown key(s) {limits_unknown}"
        )
    limits = CapacityLimits(
        max_concurrent_runs=_non_negative_int(
            limits_doc.get("max_concurrent_runs", 1), f"fleet {fleet_id!r} max_concurrent_runs", source
        ),
        max_idle_guests=_non_negative_int(
            limits_doc.get("max_idle_guests", 0), f"fleet {fleet_id!r} max_idle_guests", source
        ),
        max_new_vm_slots=_non_negative_int(
            limits_doc.get("max_new_vm_slots", 0), f"fleet {fleet_id!r} max_new_vm_slots", source
        ),
    )

    raw_conflicts = doc.get("shared_conflict_ids") or []
    if not isinstance(raw_conflicts, list):
        raise ManifestError(f"fleet manifest {source}: fleet {fleet_id!r} shared_conflict_ids must be a list")
    conflicts = tuple(str(c).strip() for c in raw_conflicts if str(c).strip())

    entry = FleetEntry(
        fleet_id=fleet_id,
        coordinator=CoordinatorSpec(address=address, credential_ref=coord_ref),
        host_controller=HostControllerSpec(endpoint=endpoint, credential_ref=host_ref),
        allowed_places=tuple(p.resource_id for p in places),
        permitted_actions=actions,
        capacity_limits=limits,
        shared_conflict_ids=conflicts,
        owner_label=str(doc.get("owner") or "").strip(),
    )
    return entry, places


def _non_negative_int(value: Any, what: str, source: str) -> int:
    if value is None:
        raise ManifestError(f"fleet manifest {source}: {what} must be an integer")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"fleet manifest {source}: {what} must be an integer (got {type(value).__name__})")
    if value < 0:
        raise ManifestError(f"fleet manifest {source}: {what} must be >= 0")
    return value


def parse_manifest(doc: Any, *, source: str = "<memory>") -> FleetManifest:
    """Validate an already-parsed manifest document (pure — no file access)."""
    root = _require_mapping(doc, "the document root", source)
    version = root.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ManifestError(
            f"fleet manifest {source}: schema_version must be {SCHEMA_VERSION} (got {version!r})"
        )
    raw_fleets = root.get("fleets")
    if not isinstance(raw_fleets, list) or not raw_fleets:
        raise ManifestError(f"fleet manifest {source}: needs a non-empty 'fleets' list")

    fleets: dict[str, FleetEntry] = {}
    resources: dict[str, ResourceEntry] = {}

    for index, raw in enumerate(raw_fleets):
        entry, places = _parse_fleet(raw, index, source)
        if entry.fleet_id in fleets:
            raise ManifestRefused(
                f"fleet manifest {source}: fleet id {entry.fleet_id!r} is declared twice — "
                "each fleet is an independent authority and must be unique"
            )
        fleets[entry.fleet_id] = entry

        for place in places:
            for identity in (place.resource_id, *place.aliases):
                existing = resources.get(identity)
                if existing is not None:
                    if existing.fleet_id == place.fleet_id:
                        raise ManifestRefused(
                            f"fleet manifest {source}: fleet {place.fleet_id!r} claims resource "
                            f"{identity!r} twice (canonical {existing.resource_id!r}) — one canonical "
                            "entry per physical resource"
                        )
                    raise ManifestRefused(
                        f"fleet manifest {source}: resource {identity!r} is claimed by both "
                        f"{existing.fleet_id!r} (as {existing.resource_id!r}) and {place.fleet_id!r} "
                        f"(as {place.resource_id!r}) — every physical resource needs exactly one "
                        "authoritative fleet; duplicate exports are unsafe until reconciled"
                    )
                resources[identity] = place

    return FleetManifest(
        schema_version=version, fleets=fleets, resources=resources, source=source
    )


def load_manifest(path: Path | str) -> FleetManifest:
    """Load one explicitly named manifest file.

    Contributors select their own path; the environment never supplies one
    implicitly, so a stray process cannot be pointed at somebody else's
    fleet by accident.
    """
    src = Path(path).expanduser()
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raise ManifestError(
            f"fleet manifest {src} is unreadable — pass an explicit manifest path "
            "(see config/fleets.example.yaml for the schema)"
        ) from None
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(_yaml_error_message(src, exc)) from None
    return parse_manifest(doc, source=str(src))


# ===========================================================================
# Observations
# ===========================================================================
class ResourceKind(str, enum.Enum):
    COORDINATOR = "coordinator"
    EXPORTER = "exporter"
    ROUTER = "router"
    GUEST = "guest"
    HOST = "host"


class State(str, enum.Enum):
    """Reason-coded per-resource state.

    The first block can carry capacity; the second block never does.
    """

    CONFIGURED = "configured"
    COORDINATOR_REACHABLE = "coordinator_reachable"
    EXPORTED_AVAILABLE = "exported_available"
    HEALTHY_IDLE = "healthy_idle"
    IDLE_GUEST = "idle_guest"
    NEW_VM_SLOTS_AVAILABLE = "new_vm_slots_available"

    UNCONFIGURED = "unconfigured"
    UNKNOWN = "unknown"
    STALE = "stale"
    REFUSED = "refused"


#: States that mean "this many units may be dispatched", by construction.
COUNTABLE_STATES = frozenset(
    {State.HEALTHY_IDLE, State.IDLE_GUEST, State.NEW_VM_SLOTS_AVAILABLE}
)


class Reason(str, enum.Enum):
    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    KIND_MISMATCH = "kind_mismatch"
    WRONG_FLEET = "wrong_fleet"
    NO_TELEMETRY = "no_telemetry"
    STALE_TELEMETRY = "stale_telemetry"
    EPOCH_MISMATCH = "epoch_mismatch"
    CLOCK_SKEW = "clock_skew"
    COORDINATOR_UNREACHABLE = "coordinator_unreachable"
    CONTROL_PLANE_ONLY = "control_plane_only"
    EXPORTER_DISCONNECTED = "exporter_disconnected"
    RESOURCE_MISSING = "resource_missing"
    PLACE_ACQUIRED = "place_acquired"
    HEALTH_FAILED = "health_failed"
    TELEMETRY_UNREADABLE = "telemetry_unreadable"
    IDENTITY_MISMATCH = "identity_mismatch"
    SESSION_ACTIVE = "session_active"
    VANTAGE_UNAVAILABLE = "vantage_unavailable"
    PROTECTED = "protected"
    GUEST_PID_REUSED = "guest_pid_reused"
    BRIDGE_ONLY = "bridge_only"
    TOPOLOGY_BROKEN = "topology_broken"
    LEASE_HELD = "lease_held"
    LIMIT_REACHED = "limit_reached"
    UNKNOWN_DIMENSION = "unknown_dimension"
    KVM_UNAVAILABLE = "kvm_unavailable"
    ARCH_MISMATCH = "arch_mismatch"
    RAM_INSUFFICIENT = "ram_insufficient"
    CPU_INSUFFICIENT = "cpu_insufficient"
    DISK_INSUFFICIENT = "disk_insufficient"
    INODE_INSUFFICIENT = "inode_insufficient"
    #: The reservation set is known to be *incomplete* (an occupying allocation
    #: could not be sized), so headroom cannot be computed. Never zero-as-free.
    RESERVATIONS_UNSIZED = "reservations_unsized"


@dataclass(frozen=True)
class _Timed:
    """Common evidence envelope: when it was taken, how long it lives, and
    which authority epoch produced it. Anything else is not evidence."""

    observed_at: float
    ttl_s: float
    authority_epoch: int


def _freshness(obs: _Timed, now: float, authority_epoch: int) -> Reason | None:
    """None when the observation is usable, else the disqualifying reason."""
    if obs.authority_epoch != authority_epoch:
        return Reason.EPOCH_MISMATCH
    if now < obs.observed_at:
        return Reason.CLOCK_SKEW
    if obs.ttl_s <= 0:
        return Reason.STALE_TELEMETRY
    if now - obs.observed_at > obs.ttl_s:
        return Reason.STALE_TELEMETRY
    return None


_STALE_REASONS = frozenset({Reason.STALE_TELEMETRY, Reason.EPOCH_MISMATCH, Reason.CLOCK_SKEW})


@dataclass(frozen=True)
class CoordinatorObservation(_Timed):
    fleet_id: str
    reachable: bool


@dataclass(frozen=True)
class ExporterObservation(_Timed):
    fleet_id: str
    resource_id: str
    exporter_connected: bool
    exported: bool = True
    acquired: bool = False
    missing: bool = False


@dataclass(frozen=True)
class RouterHealthObservation(_Timed):
    """Owner-authorized telemetry about one exported router/place."""

    fleet_id: str
    resource_id: str
    identity: str = ""
    expected_identity: str = ""
    health_ok: bool | None = None
    session_active: bool | None = None
    vantage_ok: bool | None = None
    telemetry_read_ok: bool | None = None
    protected: bool = False


@dataclass(frozen=True)
class GuestObservation(_Timed):
    """One existing QEMU lane. Bridge/pid presence is *identification*, not
    readiness: health, topology, lease and client reach must all be proven."""

    fleet_id: str
    resource_id: str
    pid: int | None = None
    pid_start_ticks: int | None = None
    expected_pid_start_ticks: int | None = None
    bridge_present: bool | None = None
    health_ok: bool | None = None
    topology_ok: bool | None = None
    lease_free: bool | None = None
    client_reachable: bool | None = None


@dataclass(frozen=True)
class HostObservation(_Timed):
    """Host admission telemetry.

    ``*_committed`` values already include every *running* allocation (idle
    guests included), so reservations for started runs must not be subtracted
    again — see ``new_vm_slots``. ``disk_free_mib``/``inode_free`` are
    post-commit free values for the same reason.
    """

    fleet_id: str
    resource_id: str
    cpu_total: float = 0.0
    cpu_committed: float = 0.0
    ram_total_mib: int = 0
    ram_committed_mib: int = 0
    ram_reserve_mib: int = 0
    disk_free_mib: int = 0
    inode_free: int = 0
    kvm_usable: bool | None = None
    architecture: str = ""
    unknown_dimensions: frozenset[str] = frozenset()


# -- constructors -----------------------------------------------------------
# Small helpers so tests and callers build evidence in one line each. They do
# not read anything: every value is the caller's measurement.
def coordinator_observation(
    *, fleet_id: str, reachable: bool, now: float, ttl_s: float, authority_epoch: int
) -> CoordinatorObservation:
    return CoordinatorObservation(
        fleet_id=fleet_id, reachable=reachable, observed_at=now, ttl_s=ttl_s, authority_epoch=authority_epoch
    )


def exporter_observation(
    *,
    fleet_id: str,
    resource_id: str,
    exporter_connected: bool,
    now: float,
    ttl_s: float,
    authority_epoch: int,
    exported: bool = True,
    acquired: bool = False,
    missing: bool = False,
) -> ExporterObservation:
    return ExporterObservation(
        fleet_id=fleet_id,
        resource_id=resource_id,
        exporter_connected=exporter_connected,
        exported=exported,
        acquired=acquired,
        missing=missing,
        observed_at=now,
        ttl_s=ttl_s,
        authority_epoch=authority_epoch,
    )


def router_observation(
    *,
    fleet_id: str,
    resource_id: str,
    now: float,
    ttl_s: float,
    authority_epoch: int,
    identity: str = "",
    expected_identity: str = "",
    health_ok: bool | None = True,
    session_active: bool | None = False,
    vantage_ok: bool | None = True,
    telemetry_read_ok: bool | None = True,
    protected: bool = False,
) -> RouterHealthObservation:
    return RouterHealthObservation(
        fleet_id=fleet_id,
        resource_id=resource_id,
        identity=identity,
        expected_identity=expected_identity,
        health_ok=health_ok,
        session_active=session_active,
        vantage_ok=vantage_ok,
        telemetry_read_ok=telemetry_read_ok,
        protected=protected,
        observed_at=now,
        ttl_s=ttl_s,
        authority_epoch=authority_epoch,
    )


def guest_observation(
    *,
    fleet_id: str,
    resource_id: str,
    now: float,
    ttl_s: float,
    authority_epoch: int,
    pid: int | None = None,
    pid_start_ticks: int | None = None,
    expected_pid_start_ticks: int | None = None,
    bridge_present: bool | None = None,
    health_ok: bool | None = None,
    topology_ok: bool | None = None,
    lease_free: bool | None = None,
    client_reachable: bool | None = None,
) -> GuestObservation:
    return GuestObservation(
        fleet_id=fleet_id,
        resource_id=resource_id,
        pid=pid,
        pid_start_ticks=pid_start_ticks,
        expected_pid_start_ticks=expected_pid_start_ticks,
        bridge_present=bridge_present,
        health_ok=health_ok,
        topology_ok=topology_ok,
        lease_free=lease_free,
        client_reachable=client_reachable,
        observed_at=now,
        ttl_s=ttl_s,
        authority_epoch=authority_epoch,
    )


def host_observation(
    *, fleet_id: str, resource_id: str, now: float, ttl_s: float, authority_epoch: int, **dimensions: Any
) -> HostObservation:
    """Host admission evidence. ``observed_at`` may be overridden through
    ``dimensions`` so a test can age the sample instead of moving the clock."""
    return HostObservation(
        fleet_id=fleet_id,
        resource_id=resource_id,
        observed_at=dimensions.pop("observed_at", now),
        ttl_s=ttl_s,
        authority_epoch=authority_epoch,
        **dimensions,
    )


# ===========================================================================
# Verdicts
# ===========================================================================
@dataclass(frozen=True)
class Verdict:
    fleet_id: str
    resource_id: str
    kind: ResourceKind
    state: State
    capacity: int
    reasons: tuple[Reason, ...] = ()
    detail: str = ""

    @property
    def countable(self) -> bool:
        return self.state in COUNTABLE_STATES and self.capacity > 0

    def explain(self) -> str:
        codes = ",".join(r.value for r in self.reasons) or Reason.OK.value
        return f"{self.fleet_id}/{self.resource_id}: {self.state.value} capacity={self.capacity} ({codes})"


def _verdict(
    *,
    fleet_id: str,
    resource_id: str,
    kind: ResourceKind,
    state: State,
    capacity: int = 0,
    reasons: Sequence[Reason] = (),
    detail: str = "",
) -> Verdict:
    # Capacity is only ever nonzero for a countable state; this keeps a
    # future edit from quietly turning "unknown" into a number.
    if state not in COUNTABLE_STATES:
        capacity = 0
    return Verdict(
        fleet_id=fleet_id,
        resource_id=resource_id,
        kind=kind,
        state=state,
        capacity=capacity,
        reasons=tuple(reasons),
        detail=redact(detail),
    )


def _state_for(reason: Reason) -> State:
    return State.STALE if reason in _STALE_REASONS else State.UNKNOWN


def _check_ownership(
    manifest: FleetManifest, resource_id: str, resource_kind: str
) -> tuple[ResourceEntry | None, Verdict | None]:
    """Configured/authorized gate — everything else depends on it."""
    entry = manifest.resource(resource_id)
    if entry is None:
        return None, _verdict(
            fleet_id="",
            resource_id=resource_id,
            kind=ResourceKind.ROUTER if resource_kind != "guest" else ResourceKind.GUEST,
            state=State.UNCONFIGURED,
            reasons=(Reason.NOT_CONFIGURED,),
            detail="not in any configured fleet — an unconfigured resource is never contacted or counted",
        )
    if entry.kind != resource_kind:
        return entry, _verdict(
            fleet_id=entry.fleet_id,
            resource_id=resource_id,
            kind=ResourceKind.ROUTER if resource_kind != "guest" else ResourceKind.GUEST,
            state=State.REFUSED,
            reasons=(Reason.KIND_MISMATCH,),
            detail=f"configured as kind {entry.kind!r}, assessed as {resource_kind!r}",
        )
    return entry, None


def _wrong_fleet(
    manifest: FleetManifest,
    entry: ResourceEntry,
    observations: Sequence[tuple[ResourceKind, object, str]],
) -> Verdict | None:
    """Cross-fleet evidence can never be reused for another fleet's resource.

    An observation may name the resource by any configured alias; what it may
    not do is describe a *different* physical resource or name another fleet.
    """

    def canonical(name: str) -> str | None:
        found = manifest.resource(name)
        return found.resource_id if found else None

    for kind, obs, name in observations:
        if obs is None:
            continue
        if getattr(obs, "fleet_id", entry.fleet_id) != entry.fleet_id:
            return _verdict(
                fleet_id=entry.fleet_id,
                resource_id=entry.resource_id,
                kind=kind,
                state=State.REFUSED,
                reasons=(Reason.WRONG_FLEET,),
                detail=f"{name} evidence belongs to another fleet",
            )
        if canonical(getattr(obs, "resource_id", entry.resource_id)) != entry.resource_id:
            return _verdict(
                fleet_id=entry.fleet_id,
                resource_id=entry.resource_id,
                kind=kind,
                state=State.REFUSED,
                reasons=(Reason.WRONG_FLEET,),
                detail=f"{name} evidence describes a different resource",
            )
    return None


@dataclass(frozen=True)
class Observations:
    """Everything known about one resource, or nothing at all."""

    coordinator: CoordinatorObservation | None = None
    exporter: ExporterObservation | None = None
    health: RouterHealthObservation | None = None
    guest: GuestObservation | None = None


def router_capacity(
    manifest: FleetManifest,
    resource_id: str,
    *,
    now: float,
    authority_epoch: int,
    observations: Observations | None = None,
) -> Verdict:
    """Physical/exported place: HEALTHY_IDLE(1) only with complete fresh proof."""
    entry, refused = _check_ownership(manifest, resource_id, "router")
    if refused is not None:
        return refused
    assert entry is not None
    obs = observations or Observations()

    mismatch = _wrong_fleet(
        manifest,
        entry,
        [
            (ResourceKind.COORDINATOR, obs.coordinator, "coordinator"),
            (ResourceKind.EXPORTER, obs.exporter, "exporter"),
            (ResourceKind.ROUTER, obs.health, "health"),
        ],
    )
    if mismatch is not None:
        return mismatch

    base: dict[str, Any] = dict(fleet_id=entry.fleet_id, resource_id=entry.resource_id, kind=ResourceKind.ROUTER)

    # 1. Coordinator — control plane only, never capacity by itself.
    if obs.coordinator is None:
        return _verdict(**base, state=State.UNKNOWN, reasons=(Reason.NO_TELEMETRY,), detail="no coordinator observation")
    why = _freshness(obs.coordinator, now, authority_epoch)
    if why is not None:
        return _verdict(**base, state=_state_for(why), reasons=(why,), detail="coordinator evidence")
    if not obs.coordinator.reachable:
        return _verdict(
            **base, state=State.UNKNOWN, reasons=(Reason.COORDINATOR_UNREACHABLE,), detail="coordinator did not answer"
        )
    if obs.exporter is None:
        return _verdict(
            **base,
            state=State.UNKNOWN,
            reasons=(Reason.CONTROL_PLANE_ONLY, Reason.NO_TELEMETRY),
            detail="coordinator answered but nothing exported this place — control plane only",
        )

    # 2. Exporter — connected *and* exporting *and* free.
    why = _freshness(obs.exporter, now, authority_epoch)
    if why is not None:
        return _verdict(**base, state=_state_for(why), reasons=(why,), detail="exporter evidence")
    if obs.exporter.missing:
        return _verdict(**base, state=State.UNKNOWN, reasons=(Reason.RESOURCE_MISSING,), detail="resource not exported")
    if not obs.exporter.exporter_connected:
        return _verdict(
            **base,
            state=State.UNKNOWN,
            reasons=(Reason.EXPORTER_DISCONNECTED,),
            detail="coordinator answered, exporter is disconnected — not capacity",
        )
    if not obs.exporter.exported:
        return _verdict(**base, state=State.UNKNOWN, reasons=(Reason.RESOURCE_MISSING,), detail="place not exported")
    if obs.exporter.acquired:
        return _verdict(**base, state=State.REFUSED, reasons=(Reason.PLACE_ACQUIRED,), detail="place is acquired")

    # 3. Exporter is fine but hardware health was not reported: EXPORTED_AVAILABLE
    #    is a real state and still worth ZERO capacity — exported is not idle.
    if obs.health is None:
        return _verdict(
            **base,
            state=State.EXPORTED_AVAILABLE,
            reasons=(Reason.NO_TELEMETRY,),
            detail="exported and available, but no owner-authorized health telemetry — exported is not idle",
        )
    why = _freshness(obs.health, now, authority_epoch)
    if why is not None:
        return _verdict(**base, state=_state_for(why), reasons=(why,), detail="health evidence")
    h = obs.health
    if h.protected:
        return _verdict(**base, state=State.REFUSED, reasons=(Reason.PROTECTED,), detail="router is protected")
    if not h.telemetry_read_ok:
        return _verdict(
            **base, state=State.REFUSED, reasons=(Reason.TELEMETRY_UNREADABLE,), detail="telemetry read failed"
        )
    if h.expected_identity and h.identity != h.expected_identity:
        return _verdict(**base, state=State.REFUSED, reasons=(Reason.IDENTITY_MISMATCH,), detail="identity mismatch")
    if h.health_ok is not True:
        return _verdict(
            **base,
            state=State.UNKNOWN if h.health_ok is None else State.REFUSED,
            reasons=(Reason.UNKNOWN_DIMENSION if h.health_ok is None else Reason.HEALTH_FAILED,),
            detail="health not proven",
        )
    if h.session_active is True:
        return _verdict(**base, state=State.REFUSED, reasons=(Reason.SESSION_ACTIVE,), detail="paid session active")
    if h.session_active is None:
        return _verdict(**base, state=State.UNKNOWN, reasons=(Reason.UNKNOWN_DIMENSION,), detail="session state unknown")
    if h.vantage_ok is not True:
        return _verdict(
            **base,
            state=State.UNKNOWN if h.vantage_ok is None else State.REFUSED,
            reasons=(Reason.UNKNOWN_DIMENSION if h.vantage_ok is None else Reason.VANTAGE_UNAVAILABLE,),
            detail="client vantage not proven",
        )

    return _verdict(
        **base, state=State.HEALTHY_IDLE, capacity=1, reasons=(Reason.OK,), detail="healthy, idle, owner-authorized"
    )


def guest_capacity(
    manifest: FleetManifest,
    resource_id: str,
    *,
    now: float,
    authority_epoch: int,
    observations: Observations | None = None,
) -> Verdict:
    """Existing QEMU lane: idle only when identity, health, topology, lease and
    client reach are all proven. A pidfile or a bridge is not readiness."""
    entry, refused = _check_ownership(manifest, resource_id, "guest")
    if refused is not None:
        return refused
    assert entry is not None
    obs = observations or Observations()
    mismatch = _wrong_fleet(manifest, entry, [(ResourceKind.GUEST, obs.guest, "guest")])
    if mismatch is not None:
        return mismatch

    base: dict[str, Any] = dict(fleet_id=entry.fleet_id, resource_id=entry.resource_id, kind=ResourceKind.GUEST)
    g = obs.guest
    if g is None:
        return _verdict(**base, state=State.UNKNOWN, reasons=(Reason.NO_TELEMETRY,), detail="no guest observation")
    why = _freshness(g, now, authority_epoch)
    if why is not None:
        return _verdict(**base, state=_state_for(why), reasons=(why,), detail="guest evidence")

    if g.pid is None:
        # Bridge-up/pidfile-missing (or nothing at all): identification failed.
        return _verdict(
            **base,
            state=State.UNKNOWN,
            reasons=(Reason.BRIDGE_ONLY,),
            detail="no live guest process — bridge/pidfile presence alone is not readiness",
        )
    if g.pid_start_ticks is None or g.expected_pid_start_ticks is None:
        return _verdict(
            **base,
            state=State.UNKNOWN,
            reasons=(Reason.UNKNOWN_DIMENSION, Reason.BRIDGE_ONLY),
            detail="pid seen without a start-time match — cannot rule out pid reuse",
        )
    if g.pid_start_ticks != g.expected_pid_start_ticks:
        return _verdict(
            **base,
            state=State.REFUSED,
            reasons=(Reason.GUEST_PID_REUSED,),
            detail="pid start time differs from the recorded guest — treat as a different process",
        )

    unproven = [
        (Reason.HEALTH_FAILED, g.health_ok, "guest health"),
        (Reason.TOPOLOGY_BROKEN, g.topology_ok, "topology integrity"),
        (Reason.LEASE_HELD, g.lease_free, "authoritative lease"),
        (Reason.VANTAGE_UNAVAILABLE, g.client_reachable, "client reachability"),
    ]
    unknown = tuple(r for r, v, _ in unproven if v is None)
    if unknown:
        return _verdict(
            **base,
            state=State.UNKNOWN,
            reasons=(*unknown, Reason.UNKNOWN_DIMENSION),
            detail="guest dimensions unproven: " + ", ".join(n for _, v, n in unproven if v is None),
        )
    refused_dims = tuple(r for r, v, _ in unproven if v is False)
    if refused_dims:
        failed = ", ".join(n for _, v, n in unproven if v is False)
        return _verdict(
            **base, state=State.REFUSED, reasons=refused_dims, detail=f"guest not idle: {failed}"
        )

    return _verdict(
        **base, state=State.IDLE_GUEST, capacity=1, reasons=(Reason.OK,), detail="identified, healthy, lease-free"
    )


# ---------------------------------------------------------------------------
# Host admission / new VM slots
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HostDemand:
    """What one whole-topology lane needs from its host."""

    vcpus: int
    ram_mib: int
    disk_mib: int
    disk_inodes: int = 1

    def __post_init__(self) -> None:
        for name in ("vcpus", "ram_mib", "disk_mib", "disk_inodes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"HostDemand.{name} must be a positive integer (got {value!r})")


@dataclass(frozen=True)
class Reservation:
    """An outstanding admission claim.

    ``started=True`` means the claim is already reflected in the host's
    committed counters, so it must NOT be subtracted a second time.
    """

    run_id: str
    demand: HostDemand
    expires_at: float
    started: bool = False


@dataclass(frozen=True)
class SpawnVerdict:
    fleet_id: str
    resource_id: str
    state: State
    slots: int
    reasons: tuple[Reason, ...] = ()
    detail: str = ""
    headroom: Mapping[str, float] = field(default_factory=dict)

    @property
    def kind(self) -> ResourceKind:
        """Host admission is a property of the host, not of a place."""
        return ResourceKind.HOST

    @property
    def capacity(self) -> int:
        """Same value as ``slots`` — no caller gets to read a slot count off
        an attribute that means something else."""
        return self.slots

    @property
    def countable(self) -> bool:
        return self.state in COUNTABLE_STATES and self.slots > 0


def new_vm_slots(
    host: HostObservation | None,
    *,
    demand: HostDemand,
    now: float,
    authority_epoch: int,
    reservations: Sequence[Reservation] = (),
    reservations_incomplete: bool = False,
    limit: int | None = None,
    required_architecture: str | None = None,
) -> SpawnVerdict:
    """How many *new* lanes this host may admit — a separate number from idle
    guests, because an idle guest still consumes the host it runs on.

    Every dimension must be known and must fit. Unknown yields zero.

    ``reservations_incomplete`` means the caller knows at least one *occupying*
    allocation could not be sized (a corrupt ``demand`` column, an unreadable
    store). Sub-tracting only the rows it could parse would silently price that
    claim at zero, so the answer is refused outright rather than under-counted.
    """
    if host is None:
        return SpawnVerdict(
            fleet_id="",
            resource_id="",
            state=State.UNKNOWN,
            slots=0,
            reasons=(Reason.NO_TELEMETRY,),
            detail="no host admission telemetry",
        )

    base: dict[str, Any] = dict(fleet_id=host.fleet_id, resource_id=host.resource_id)
    why = _freshness(host, now, authority_epoch)
    if why is not None:
        return SpawnVerdict(**base, state=_state_for(why), slots=0, reasons=(why,), detail="host admission evidence")

    if host.unknown_dimensions:
        return SpawnVerdict(
            **base,
            state=State.UNKNOWN,
            slots=0,
            reasons=(Reason.UNKNOWN_DIMENSION,),
            detail="unknown dimension(s): " + ", ".join(sorted(host.unknown_dimensions)),
        )
    if host.kvm_usable is None:
        return SpawnVerdict(
            **base, state=State.UNKNOWN, slots=0, reasons=(Reason.UNKNOWN_DIMENSION,), detail="KVM usability unknown"
        )
    if host.kvm_usable is False:
        return SpawnVerdict(
            **base,
            state=State.REFUSED,
            slots=0,
            reasons=(Reason.KVM_UNAVAILABLE,),
            detail="KVM unavailable to the execution identity (no implicit TCG substitution)",
        )
    if required_architecture and host.architecture != required_architecture:
        return SpawnVerdict(
            **base,
            state=State.REFUSED,
            slots=0,
            reasons=(Reason.ARCH_MISMATCH,),
            detail=f"host architecture {host.architecture!r} != required {required_architecture!r}",
        )
    if not host.architecture:
        return SpawnVerdict(
            **base, state=State.UNKNOWN, slots=0, reasons=(Reason.UNKNOWN_DIMENSION,), detail="host architecture unknown"
        )

    # Outstanding claims only: unstarted and not expired. Started claims are
    # already inside *_committed and subtracting them again would over-report
    # headroom in the safe direction — but it would also make an idle guest
    # look free, which is the bug this model exists to prevent.
    if reservations_incomplete:
        # An occupier we cannot size is not a zero-cost occupier. Refuse before
        # any arithmetic: a number derived from a partial ledger is a guess.
        return SpawnVerdict(
            **base,
            state=State.UNKNOWN,
            slots=0,
            reasons=(Reason.RESERVATIONS_UNSIZED,),
            detail="reservation set is incomplete: at least one occupying allocation could not be sized",
        )
    outstanding = [r for r in reservations if not r.started and r.expires_at > now]
    claimed_cpu = sum(r.demand.vcpus for r in outstanding)
    claimed_ram = sum(r.demand.ram_mib for r in outstanding)
    claimed_disk = sum(r.demand.disk_mib for r in outstanding)
    claimed_inodes = sum(r.demand.disk_inodes for r in outstanding)

    headroom = {
        "cpu": host.cpu_total - host.cpu_committed - claimed_cpu,
        "ram_mib": host.ram_total_mib - host.ram_committed_mib - host.ram_reserve_mib - claimed_ram,
        "disk_mib": host.disk_free_mib - claimed_disk,
        "disk_inodes": host.inode_free - claimed_inodes,
    }

    fits = {
        "cpu": headroom["cpu"] // demand.vcpus,
        "ram_mib": headroom["ram_mib"] // demand.ram_mib,
        "disk_mib": headroom["disk_mib"] // demand.disk_mib,
        "disk_inodes": headroom["disk_inodes"] // demand.disk_inodes,
    }
    slots = max(0, min(int(v) for v in fits.values()))

    # Only the dimensions that actually bind are reported, so a full host
    # says WHY instead of "0".
    reason_for = {
        "cpu": Reason.CPU_INSUFFICIENT,
        "ram_mib": Reason.RAM_INSUFFICIENT,
        "disk_mib": Reason.DISK_INSUFFICIENT,
        "disk_inodes": Reason.INODE_INSUFFICIENT,
    }
    if slots <= 0:
        binding = tuple(reason_for[dim] for dim, count in fits.items() if count <= 0)
        return SpawnVerdict(
            **base,
            state=State.REFUSED,
            slots=0,
            reasons=binding or (Reason.RAM_INSUFFICIENT,),
            detail="resource vector does not fit: " + ", ".join(f"{d}={int(h)}" for d, h in headroom.items()),
            headroom=dict(headroom),
        )

    if limit is not None:
        slots = max(0, min(slots, limit))
        if slots == 0:
            return SpawnVerdict(
                **base,
                state=State.REFUSED,
                slots=0,
                reasons=(Reason.LIMIT_REACHED,),
                detail="fleet capacity limit allows no new VM slots",
                headroom=dict(headroom),
            )

    return SpawnVerdict(
        **base,
        state=State.NEW_VM_SLOTS_AVAILABLE,
        slots=slots,
        reasons=(Reason.OK,),
        detail="host admission satisfied on every dimension",
        headroom=dict(headroom),
    )


# ===========================================================================
# Snapshot aggregation
# ===========================================================================
@dataclass(frozen=True)
class FleetSummary:
    """Observed per-fleet capacity.

    Counts are what the evidence supports, *not* what the fleet allows:
    ``capacity_limits`` never rewrites an observation. ``admissible_*`` apply
    those limits on top, so a limit and a measurement can never be confused
    for one another.
    """

    fleet_id: str
    configured_places: int
    coordinator_reachable: bool
    exported_available: int
    idle_routers: int
    idle_guests: int
    new_vm_slots: int
    max_concurrent_runs: int = 1
    max_idle_guests: int = 0
    max_new_vm_slots: int = 0
    excluded: tuple[Verdict | SpawnVerdict, ...] = ()

    @property
    def lanes_ready(self) -> int:
        """Observed ready predeclared lanes. Deliberately excludes
        ``new_vm_slots``: new slots are host-admission output and must not be
        added to a lane count without re-running admission."""
        return self.idle_routers + self.idle_guests

    @property
    def admissible_lanes(self) -> int:
        """Lanes this fleet is willing to run now (observed, capped by its
        own declared limit)."""
        return min(self.lanes_ready, self.max_concurrent_runs)

    @property
    def admissible_idle_guests(self) -> int:
        return min(self.idle_guests, self.max_idle_guests)

    @property
    def admissible_new_vm_slots(self) -> int:
        return min(self.new_vm_slots, self.max_new_vm_slots)


def summarize(manifest: FleetManifest, verdicts: Iterable[Verdict | SpawnVerdict]) -> tuple[FleetSummary, ...]:
    """Per-fleet counts. Only configured resources may contribute, and only
    countable states count — a verdict for an unconfigured resource is a bug,
    not a zero."""
    per_fleet: dict[str, list[Verdict | SpawnVerdict]] = {fid: [] for fid in manifest.fleets}
    coordinators: dict[str, list[Verdict]] = {fid: [] for fid in manifest.fleets}
    hosts: dict[str, list[Verdict | SpawnVerdict]] = {fid: [] for fid in manifest.fleets}
    for verdict in verdicts:
        if verdict.kind is ResourceKind.COORDINATOR:
            # Control-plane evidence is keyed by fleet, not by a physical
            # resource: it is never counted as a place.
            if verdict.fleet_id not in per_fleet:
                raise CapacityError(
                    redact(f"coordinator verdict names unconfigured fleet {verdict.fleet_id!r}")
                )
            coordinators[verdict.fleet_id].append(verdict)  # type: ignore[arg-type]
            continue
        if verdict.kind is ResourceKind.HOST:
            # Host admission belongs to a fleet's declared host controller,
            # not to a place; it can never be counted as an idle lane.
            if verdict.fleet_id not in per_fleet:
                raise CapacityError(redact(f"host verdict names unconfigured fleet {verdict.fleet_id!r}"))
            hosts[verdict.fleet_id].append(verdict)
            continue
        owner = manifest.owner_of(verdict.resource_id)
        if owner is None:
            raise CapacityError(
                redact(
                    f"verdict for resource {verdict.resource_id!r} is not configured in {manifest.source} — "
                    "an unconfigured resource can never be counted"
                )
            )
        if verdict.fleet_id and verdict.fleet_id != owner:
            raise CapacityError(
                redact(
                    f"verdict claims fleet {verdict.fleet_id!r} but {verdict.resource_id!r} belongs to {owner!r}"
                )
            )
        per_fleet[owner].append(verdict)

    summaries: list[FleetSummary] = []
    for fleet_id in sorted(per_fleet):
        entry = manifest.fleets[fleet_id]
        items = per_fleet[fleet_id]
        routers = [v for v in items if v.kind is ResourceKind.ROUTER]
        guests = [v for v in items if v.kind is ResourceKind.GUEST]
        summaries.append(
            FleetSummary(
                fleet_id=fleet_id,
                configured_places=len(manifest.resources_of(fleet_id)),
                coordinator_reachable=any(v.state is State.COORDINATOR_REACHABLE for v in coordinators[fleet_id]),
                exported_available=sum(1 for v in items if v.state is State.EXPORTED_AVAILABLE),
                idle_routers=sum(v.capacity for v in routers if v.state is State.HEALTHY_IDLE),
                idle_guests=sum(v.capacity for v in guests if v.state is State.IDLE_GUEST),
                new_vm_slots=sum(
                    getattr(v, "slots", 0) for v in hosts[fleet_id] if v.state is State.NEW_VM_SLOTS_AVAILABLE
                ),
                max_concurrent_runs=entry.capacity_limits.max_concurrent_runs,
                max_idle_guests=entry.capacity_limits.max_idle_guests,
                max_new_vm_slots=entry.capacity_limits.max_new_vm_slots,
                excluded=tuple(v for v in items if not v.countable),
            )
        )
    return tuple(summaries)


def resource_conflicts(manifest: FleetManifest, resource_id: str) -> frozenset[str]:
    """The conflict domains one resource participates in.

    A resource's domains are its fleet's declared ``shared_conflict_ids`` —
    that is what makes two lanes in different fleets mutually exclusive.
    An unconfigured resource has no domains, and that is *not* permission:
    callers must have proven the resource configured first.
    """
    entry = manifest.resource(resource_id)
    if entry is None:
        return frozenset()
    return frozenset(manifest.fleets[entry.fleet_id].shared_conflict_ids)


def lanes_independent(manifest: FleetManifest, left: str, right: str) -> tuple[bool, tuple[str, ...]]:
    """True only when two lanes share no declared conflict domain.

    Distinct fleets are NOT sufficient: an explicitly shared power strip or
    RF band serializes them. Same resource is never independent of itself.
    """
    left_entry = manifest.resource(left)
    right_entry = manifest.resource(right)
    if left_entry is None or right_entry is None:
        raise ManifestError(
            f"cannot judge independence of unconfigured resource(s): "
            f"{left if left_entry is None else ''} {right if right_entry is None else ''}".strip()
        )
    if left_entry.fleet_id == right_entry.fleet_id:
        # Within one fleet, only the declared conflicts decide — but two
        # names for the same physical resource always collide.
        if left_entry.resource_id == right_entry.resource_id:
            return False, ("same_resource",)
    shared = resource_conflicts(manifest, left) & resource_conflicts(manifest, right)
    if shared:
        return False, tuple(sorted(shared))
    return True, ()


def configured_capacity(manifest: FleetManifest, resource_id: str) -> Verdict:
    """The *authorization* state on its own: configured and allowlisted, but
    nothing observed. Capacity is zero — being written down is not being free.

    Callers use this before any probe so an unconfigured resource is refused
    without a network call ever being considered.
    """
    kind = manifest.resource(resource_id)
    resource_kind = kind.kind if kind else "router"
    entry, refused = _check_ownership(manifest, resource_id, resource_kind)
    if refused is not None:
        return refused
    assert entry is not None
    return _verdict(
        fleet_id=entry.fleet_id,
        resource_id=entry.resource_id,
        kind=ResourceKind.GUEST if entry.kind == "guest" else ResourceKind.ROUTER,
        state=State.CONFIGURED,
        reasons=(Reason.OK,),
        detail="configured and authorized — no capacity claimed until observed fresh",
    )


def coordinator_capacity(
    manifest: FleetManifest,
    fleet_id: str,
    *,
    now: float,
    authority_epoch: int,
    observation: CoordinatorObservation | None = None,
) -> Verdict:
    """Control-plane reachability as its own verdict — capacity 0, always.

    Kept separate so no caller can mistake "the coordinator answered" for
    "a router is free".
    """
    entry = manifest.fleet(fleet_id)
    base: dict[str, Any] = dict(fleet_id=entry.fleet_id, resource_id=f"coordinator:{fleet_id}", kind=ResourceKind.COORDINATOR)
    if observation is None:
        return _verdict(**base, state=State.UNKNOWN, reasons=(Reason.NO_TELEMETRY,), detail="no coordinator observation")
    if observation.fleet_id != fleet_id:
        return _verdict(**base, state=State.REFUSED, reasons=(Reason.WRONG_FLEET,), detail="evidence for another fleet")
    why = _freshness(observation, now, authority_epoch)
    if why is not None:
        return _verdict(**base, state=_state_for(why), reasons=(why,), detail="coordinator evidence")
    if not observation.reachable:
        return _verdict(
            **base, state=State.UNKNOWN, reasons=(Reason.COORDINATOR_UNREACHABLE,), detail="coordinator did not answer"
        )
    return _verdict(
        **base,
        state=State.COORDINATOR_REACHABLE,
        reasons=(Reason.CONTROL_PLANE_ONLY,),
        detail="control plane only — certifies nothing about exported resources",
    )


# ===========================================================================
# Probe planning (the only place that names endpoints)
# ===========================================================================
@dataclass(frozen=True)
class ProbeTarget:
    fleet_id: str
    role: str
    endpoint: str

    @property
    def key(self) -> str:
        return f"{self.fleet_id}:{self.role}"


def observation_plan(
    manifest: FleetManifest, *, fleets: Sequence[str] | None = None, action: str = "observe"
) -> tuple[ProbeTarget, ...]:
    """Exactly the endpoints the manifest authorizes, and nothing else.

    Unknown fleet ids raise instead of falling back: discovery by scanning,
    guessing an SSH alias or sweeping a home directory is not authorization.
    """
    if action not in KNOWN_ACTIONS:
        raise ManifestError(f"unknown action {action!r} (known: {sorted(KNOWN_ACTIONS)})")
    if fleets is None:
        selected = sorted(manifest.fleets)
    else:
        selected = list(fleets)
        for fleet_id in selected:  # raises for anything unconfigured
            manifest.fleet(fleet_id)

    targets: list[ProbeTarget] = []
    for fleet_id in selected:
        entry = manifest.fleets[fleet_id]
        if not entry.permits(action):
            continue
        targets.append(ProbeTarget(fleet_id=fleet_id, role="coordinator", endpoint=entry.coordinator.address))
        targets.append(ProbeTarget(fleet_id=fleet_id, role="host_controller", endpoint=entry.host_controller.endpoint))
    return tuple(targets)


def run_probes(
    manifest: FleetManifest, probe: Callable[[ProbeTarget], Any], *, fleets: Sequence[str] | None = None
) -> dict[str, Any]:
    """Call ``probe`` once per authorized endpoint.

    Authorization is re-derived from the MANIFEST for each target, not from
    the plan list being iterated: if the plan ever gains an endpoint the
    manifest does not authorize for this fleet and action, the probe is not
    called at all.
    """
    plan = observation_plan(manifest, fleets=fleets)
    authorized = {
        (entry.fleet_id, entry.coordinator.address)
        for entry in manifest.fleets.values()
        if entry.permits("observe")
    } | {
        (entry.fleet_id, entry.host_controller.endpoint)
        for entry in manifest.fleets.values()
        if entry.permits("observe")
    }
    results: dict[str, Any] = {}
    for target in plan:
        if (target.fleet_id, target.endpoint) not in authorized:
            raise CapacityError(
                f"refusing to probe {target.role!r} for fleet {target.fleet_id!r}: "
                "the endpoint is not authorized by the manifest"
            )
        results[target.key] = probe(target)
    return results
