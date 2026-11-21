"""Endpoint binding between a manifest lane and the place pytest will own.

The runner reserves **host budget** for a lane; the pytest fixture in
``tests/conftest.py`` acquires the **place**. Both are statements about one
physical DUT, made by two different components from two different sources: the
fleet manifest (lane/coordinator) and the venue resolution (inventory +
``TOLLGATE_*`` env). Nothing in the repo currently checks that they name the
same DUT, so a run can reserve budget for fleet A's router while the fixture
drives fleet B's — or while the fixture acquires nothing at all and the
reservation means nothing.

This module is the check, as **pure functions over injected inputs**:

* no coordinator contact, no ``labgrid-client``, no file reads, no env reads
  (every input is a parameter — ``environ`` is a mapping the caller passes);
* no acquisition of any kind. The place owner is, and stays,
  ``tests/conftest.py`` (``plan_venue`` → ``venue_mutex``). This module only
  *predicts* what that resolution will target and refuses when the prediction
  cannot be shown to agree with the manifest;
* refusals, never warnings. Each refusal carries a stable machine-readable
  code from :class:`Refusal`, so an operator log line and a CI assertion can
  key off the same token.

Why a prediction rather than a call
-----------------------------------
``plan_venue`` is not pure: it loads the gitignored inventory, probes for the
client binary and (via ``bench_for_router``) constructs the bench object that
later acquires. Calling it here would make this layer a *participant* in the
venue decision — and an extra resolution is exactly the overlap it is supposed
to detect. :func:`venue_intent` therefore mirrors the decision *rules* of
``plan_venue``/``conftest`` (mode, auto-skip, xdist, explicit place, default
place) over injected data. ``tests/unit/test_fleet_binding.py`` pins that
mirror against the real ``plan_venue`` with a monkeypatched inventory, so the
two cannot drift silently.

The seam for a later change is one call: run-profile.py, in
``require_supported_fleet_lifecycle`` (after the gate is relaxed), would build
the inputs it already has (manifest, ``--fleet``/``--lane``/``--resource``,
``owner=run_id``), load the inventory that ``plan_venue`` would load, pass its
own merged profile env as ``environ``/``profile_endpoint``, and call
:func:`check_binding`; a raised :class:`BindingRefused` becomes an
``AdmissionRefused(decision.code, decision.detail)`` before any lane is
reserved.
"""

from __future__ import annotations

import ast
import enum
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.labgrid_venue import resolve_venue_mode

#: labgrid's default coordinator port. Used ONLY to normalise a missing port
#: on either side of the comparison — never to invent an endpoint.
DEFAULT_COORDINATOR_PORT = 20408

#: The place ``plan_venue`` falls back to when a forced venue has no better
#: information (``lib/labgrid_venue.py:plan_venue``). Mirrored here so the
#: prediction cannot disagree with it.
DEFAULT_PLACE = "ap-lan2"

#: Session scopes that ``tests/conftest.py`` auto-skips in auto venue mode
#: (``_session_args_only(config, "unit"/"parity")``).
AUTO_SKIP_SCOPES = ("unit", "parity")


class Refusal(str, enum.Enum):
    """Stable machine-readable refusal codes.

    Renaming a member is a behaviour change for every log scraper and CI
    assertion keyed on it; ``tests/unit/test_fleet_binding.py`` pins the exact
    string values.

    ``str``-mixin deliberately, matching ``lib.fleet_capacity.Reason`` and
    ``State``: a code is compared, logged and JSON-encoded as its plain value.
    """

    # -- manifest side ----------------------------------------------------
    MANIFEST_MISSING = "manifest_missing"
    OWNER_MISSING = "owner_missing"
    FLEET_AMBIGUOUS = "fleet_ambiguous"
    FLEET_UNKNOWN = "fleet_unknown"
    LANE_AMBIGUOUS = "lane_ambiguous"
    RESOURCE_NOT_AUTHORIZED = "resource_not_authorized"
    RESOURCE_KIND_UNSUPPORTED = "resource_kind_unsupported"

    # -- venue side ------------------------------------------------------
    VENUE_PLACE_UNRESOLVABLE = "venue_place_unresolvable"
    PLACE_AMBIGUOUS = "place_ambiguous"
    VENUE_TAKES_NO_PLACE = "venue_takes_no_place"
    FIXTURE_WOULD_SKIP_PLACE = "fixture_would_skip_place"
    RESOURCE_PROTECTED = "resource_protected"
    VENUE_ADDRESS_MISSING = "venue_address_missing"

    # -- cross-checks ----------------------------------------------------
    COORDINATOR_ADDRESS_MISSING = "coordinator_address_missing"
    COORDINATOR_ADDRESS_MISMATCH = "coordinator_address_mismatch"
    PLACE_OVERRIDE_CONFLICT = "place_override_conflict"
    PLACE_MISMATCH = "place_mismatch"
    PROFILE_ENDPOINT_MISMATCH = "profile_endpoint_mismatch"
    PROFILE_ENDPOINT_UNKNOWN = "profile_endpoint_unknown"

    # -- claims ----------------------------------------------------------
    PLACE_ALREADY_CLAIMED = "place_already_claimed"


#: Every code a caller may see, as plain strings.
CODES: frozenset[str] = frozenset(member.value for member in Refusal)


class BindingRefused(RuntimeError):
    """The manifest lane and the venue resolution do not name one DUT.

    ``code`` is the stable token (also on ``.reason`` for symmetry with
    ``lib.run_admission.AdmissionRefused``); ``detail`` is operator-facing and
    never carries a DUT address or any credential.
    """

    def __init__(self, code: Refusal | str, detail: str = "") -> None:
        self.code = code.value if isinstance(code, Refusal) else str(code)
        self.reason = self.code
        self.detail = detail
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


def normalize_coordinator(address: str) -> str:
    """Canonical ``host:port`` for comparison.

    Lower-cases the host, drops a trailing dot, keeps an IPv6 literal in
    brackets, and fills labgrid's default port when the port is absent — a
    missing port is a *normalisation* difference, not a different coordinator.
    An empty address is NOT normalisable: an unknown coordinator can never be
    shown to be the right one.
    """
    raw = (address or "").strip()
    if not raw:
        raise ValueError("coordinator address is empty")
    if raw.startswith("["):
        close = raw.find("]")
        if close == -1:
            raise ValueError(f"unbalanced IPv6 literal in coordinator address {raw!r}")
        host = raw[1:close]
        rest = raw[close + 1 :]
        port = rest[1:] if rest.startswith(":") else rest
        canonical_host = f"[{host.lower()}]"
    elif raw.count(":") > 1:  # bare IPv6 literal without brackets/port
        canonical_host = f"[{raw.lower()}]"
        port = ""
    else:
        host, _, maybe_port = raw.partition(":")
        canonical_host = host.lower().rstrip(".")
        port = maybe_port
    port = port.strip()
    if not canonical_host.strip("[]"):
        raise ValueError("coordinator address has an empty host")
    if not port:
        port = str(DEFAULT_COORDINATOR_PORT)
    if not port.isdigit() or not (0 < int(port) < 65536):
        raise ValueError(f"coordinator port {port!r} is not a TCP port")
    return f"{canonical_host}:{int(port)}"


# ---------------------------------------------------------------------------
# What the venue resolution will target (pure prediction)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlaceCandidate:
    """One inventory dossier that claims a labgrid place."""

    router_name: str
    place: str
    address: str = ""
    protected: bool = False


@dataclass(frozen=True)
class VenueIntent:
    """What ``plan_venue`` + the ``router`` fixture will do, without doing it.

    ``owns_place`` is False for every path where the fixture touches no
    coordinator: direct/legacy mode, xdist workers, the unit-only/parity
    auto-skip, unmet preconditions, and auto mode finding no place for the
    target. ``skip_reason`` says which (empty when ``owns_place``).
    """

    mode: str
    owns_place: bool
    place: str = ""
    origin: str = ""
    skip_reason: str = ""
    candidate: PlaceCandidate | None = None

    @property
    def address(self) -> str:
        return self.candidate.address if self.candidate else ""

    @property
    def protected(self) -> bool:
        return bool(self.candidate and self.candidate.protected)


def place_candidates(inventory) -> dict[str, tuple[PlaceCandidate, ...]]:
    """``place -> dossiers claiming it``, for ambiguity checks.

    ``labgrid_venue._resolve_entry`` accepts a dossier key OR a place name, so
    a place named by two dossiers is a real ambiguity there; here it is a
    refusal.
    """
    index: dict[str, list[PlaceCandidate]] = {}
    for name, entry in (getattr(inventory, "routers", {}) or {}).items():
        place = (getattr(entry, "place", "") or "").strip()
        if not place:
            continue
        index.setdefault(place, []).append(
            PlaceCandidate(
                router_name=str(name),
                place=place,
                address=(getattr(entry, "address", "") or "").strip(),
                protected=bool(getattr(entry, "protected", False)),
            )
        )
    return {place: tuple(items) for place, items in index.items()}


def _match_target(inventory, environ: Mapping[str, str], operator_host: str | None) -> tuple[str, str]:
    """Mirror ``labgrid_venue._resolve_target`` (dossier key + origin).

    The operator's host — captured by conftest BEFORE preflight could
    reassign it — wins absolutely when it maps to ANY dossier, even one with
    no place. Otherwise the post-preflight ``TOLLGATE_SSH_HOST`` is honoured
    only when it maps to a dossier that HAS a place.
    """
    routers = getattr(inventory, "routers", {}) or {}
    if operator_host:
        for name, entry in routers.items():
            if getattr(entry, "address", "") and entry.address == operator_host:
                return str(name), "address-match"
        return "", ""
    host = (environ.get("TOLLGATE_SSH_HOST") or "").strip()
    if host:
        for name, entry in routers.items():
            if getattr(entry, "address", "") == host and getattr(entry, "place", ""):
                return str(name), "address-match"
    return "", ""


def _candidate_for(inventory, name: str) -> PlaceCandidate:
    """Resolve a dossier key or place name to exactly one candidate."""
    routers = getattr(inventory, "routers", {}) or {}
    if name in routers:
        entry = routers[name]
        place = (getattr(entry, "place", "") or "").strip()
        if not place:
            raise BindingRefused(
                Refusal.VENUE_PLACE_UNRESOLVABLE,
                f"dossier {name!r} has no labgrid place mapping",
            )
        # Indexing every dossier with a place means this key is always present,
        # so a claimant exists unless two dossiers share the place.
        claimants = place_candidates(inventory).get(place, ())
        if len(claimants) > 1:
            raise BindingRefused(
                Refusal.PLACE_AMBIGUOUS,
                f"place {place!r} is claimed by {len(claimants)} dossiers "
                f"({', '.join(sorted(c.router_name for c in claimants))}) — "
                "a lane cannot name an ambiguous DUT",
            )
        return next(iter(claimants))
    claimants = place_candidates(inventory).get(name, ())
    if not claimants:
        raise BindingRefused(
            Refusal.VENUE_PLACE_UNRESOLVABLE,
            f"no inventory dossier maps to {name!r}",
        )
    if len(claimants) > 1:
        raise BindingRefused(
            Refusal.PLACE_AMBIGUOUS,
            f"place {name!r} is claimed by {len(claimants)} dossiers "
            f"({', '.join(sorted(c.router_name for c in claimants))})",
        )
    return claimants[0]


def venue_intent(
    *,
    environ: Mapping[str, str],
    inventory=None,
    preconditions_met: bool = True,
    session_scope: str = "full",
    xdist_worker: bool = False,
    operator_host: str | None = None,
) -> VenueIntent:
    """Predict the place the ``router`` fixture will own, or that it won't.

    Mirrors, in conftest order: xdist worker → legacy; auto + unit/parity
    session → auto-skip; ``TOLLGATE_VENUE`` direct/legacy → legacy; auto with
    unmet preconditions → legacy; explicit place → that place; forced →
    address match else :data:`DEFAULT_PLACE`; auto → address match else
    legacy.

    Protection is deliberately NOT refused here: ``plan_venue`` raises for a
    protected dossier, but the binding layer must report it as its own code
    (:attr:`Refusal.RESOURCE_PROTECTED`) with the place named, so this returns
    the candidate and lets :func:`check_binding` refuse.
    """
    mode = resolve_venue_mode(environ)
    label = (environ.get("TOLLGATE_VENUE") or "").strip()

    if xdist_worker:
        return VenueIntent(mode, False, skip_reason="xdist-worker")
    if mode == "auto" and session_scope in AUTO_SKIP_SCOPES:
        return VenueIntent(mode, False, skip_reason="auto-skip")
    if mode == "direct":
        return VenueIntent(mode, False, skip_reason="mode-direct")
    if mode == "legacy":
        return VenueIntent(mode, False, skip_reason=f"mode-legacy:{label}")
    if mode == "auto" and not preconditions_met:
        return VenueIntent(mode, False, skip_reason="preconditions-unmet")
    if inventory is None:
        raise BindingRefused(
            Refusal.VENUE_PLACE_UNRESOLVABLE,
            f"venue mode {mode!r} needs a lab inventory and none was supplied",
        )

    explicit = (environ.get("TOLLGATE_LABGRID_PLACE") or "").strip()
    if explicit:
        name, origin = explicit, "explicit-place"
    else:
        name, origin = _match_target(inventory, environ, operator_host)
        if not name:
            if mode != "labgrid":
                return VenueIntent(mode, False, skip_reason="no-place-match")
            if operator_host or (environ.get("TOLLGATE_SSH_HOST") or "").strip():
                raise BindingRefused(
                    Refusal.VENUE_PLACE_UNRESOLVABLE,
                    "forced venue: no inventory dossier maps to the configured "
                    "target — the fixture would refuse to run",
                )
            name, origin = DEFAULT_PLACE, "forced"

    candidate = _candidate_for(inventory, name)
    return VenueIntent(
        mode,
        True,
        place=candidate.place,
        origin=origin,
        candidate=candidate,
    )


# ---------------------------------------------------------------------------
# Claims — the non-overlap observable
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlaceClaim:
    """A stated intent to own one place, attributed to one run identity."""

    place: str
    owner: str

    def normalized_place(self) -> str:
        return (self.place or "").strip()

    def overlaps(self, other: PlaceClaim) -> bool:
        """Same place, two *attributed* and different owners.

        ``check_binding`` never emits an owner-less claim (``OWNER_MISSING``),
        so an empty owner here means the caller injected a claim that does not
        attribute ownership at all — that is not evidence of a competing run,
        and treating it as contention would refuse every retry. Same place +
        same owner is the same claim restated (a retry), not an overlap.
        """
        if not self.normalized_place() or not other.normalized_place():
            return False
        mine = (self.owner or "").strip()
        theirs = (other.owner or "").strip()
        if not mine or not theirs:
            return False
        return self.normalized_place() == other.normalized_place() and mine != theirs


def find_overlaps(claims: Iterable[PlaceClaim]) -> tuple[tuple[PlaceClaim, PlaceClaim], ...]:
    """Every pair of claims that collide. Pure, order-independent."""
    ordered = sorted(
        (c for c in claims if c.normalized_place()),
        key=lambda c: (c.normalized_place(), (c.owner or "").strip()),
    )
    found: list[tuple[PlaceClaim, PlaceClaim]] = []
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            if first.overlaps(second):
                found.append((first, second))
    return tuple(found)


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Binding:
    """Proven agreement: this manifest lane IS the DUT the fixture will own."""

    fleet_id: str
    resource_id: str
    lane_id: str
    coordinator: str
    place: str
    dut_address: str
    owner: str
    origin: str
    mode: str

    def as_env(self) -> dict[str, str]:
        """Env a later seam would export so conftest resolves this same DUT.

        ``TOLLGATE_VENUE=labgrid`` + ``TOLLGATE_LABGRID_PLACE`` make the venue
        decision *forced and explicit* rather than auto — a lane must not be
        able to fall back to the legacy direct path underneath a reservation.
        """
        return {
            "TOLLGATE_VENUE": "labgrid",
            "TOLLGATE_LABGRID_PLACE": self.place,
            "TOLLGATE_SSH_HOST": self.dut_address,
        }

    @property
    def claim(self) -> PlaceClaim:
        return PlaceClaim(place=self.place, owner=self.owner)


def _resolve_lane(manifest, fleet_id: str | None, lane_id: str | None, resource_id: str | None):
    """Exactly one fleet and one declared place, or a refusal."""
    if fleet_id is None:
        if len(manifest.fleets) != 1:
            raise BindingRefused(
                Refusal.FLEET_AMBIGUOUS,
                f"manifest declares {len(manifest.fleets)} fleets; a lane must name its fleet",
            )
        fleet_id = next(iter(manifest.fleets))
    entry = manifest.fleets.get(fleet_id)
    if entry is None:
        raise BindingRefused(Refusal.FLEET_UNKNOWN, f"fleet {fleet_id!r} is not declared")

    named = resource_id or lane_id
    if named is None:
        if len(entry.allowed_places) != 1:
            raise BindingRefused(
                Refusal.LANE_AMBIGUOUS,
                f"fleet {fleet_id!r} declares {len(entry.allowed_places)} places; "
                "a lane must name its resource",
            )
        named = entry.allowed_places[0]

    resource = manifest.resource(named)
    if resource is None or resource.fleet_id != fleet_id:
        raise BindingRefused(
            Refusal.RESOURCE_NOT_AUTHORIZED,
            f"resource {named!r} is not owned by fleet {fleet_id!r}",
        )
    kind = (getattr(resource, "kind", "") or "").strip()
    if kind and kind != "router":
        raise BindingRefused(
            Refusal.RESOURCE_KIND_UNSUPPORTED,
            f"resource {resource.resource_id!r} is kind {kind!r}; the pytest venue "
            "fixture owns a labgrid *place*, so only router places can be bound",
        )
    return entry, resource, str(lane_id or resource.resource_id)


def check_binding(
    *,
    manifest=None,
    inventory=None,
    environ: Mapping[str, str] | None = None,
    fleet_id: str | None = None,
    resource_id: str | None = None,
    lane_id: str | None = None,
    owner: str = "",
    profile_endpoint: str = "",
    session_scope: str = "full",
    xdist_worker: bool = False,
    operator_host: str | None = None,
    preconditions_met: bool = True,
    already_claimed: Sequence[PlaceClaim] = (),
) -> Binding:
    """Prove one lane and one venue resolution name the same DUT, or refuse.

    Precedence is fixed and documented by tests: manifest identity
    (fleet/lane/resource) → coordinator agreement → venue resolution →
    place agreement, including the env override → profile endpoint → claims.
    The first violated rule is the reported code, so a log line is
    deterministic when several things are wrong at once.

    ``already_claimed`` is how overlap is made observable without acquiring
    anything: pass the claims this process already knows about (other runs on
    this host, a retry of the same run) and a foreign claim on the same place
    is refused before a reservation is taken.
    """
    if manifest is None:
        raise BindingRefused(
            Refusal.MANIFEST_MISSING,
            "no fleet manifest: this check exists only for runs that take a lane",
        )
    if not (owner or "").strip():
        raise BindingRefused(
            Refusal.OWNER_MISSING,
            "a lane reservation must be attributable to a run identity",
        )

    entry, resource, resolved_lane = _resolve_lane(manifest, fleet_id, lane_id, resource_id)

    # -- coordinator agreement (a) ----------------------------------------
    manifest_coordinator = (getattr(getattr(entry, "coordinator", None), "address", "") or "").strip()
    inventory_coordinator = (
        getattr(getattr(inventory, "coordinator", None), "address", "") or ""
    ).strip()
    if not manifest_coordinator or not inventory_coordinator:
        raise BindingRefused(
            Refusal.COORDINATOR_ADDRESS_MISSING,
            "both the manifest fleet coordinator.address and the inventory "
            "coordinator.address must be set before a lane can be bound",
        )
    try:
        wanted = normalize_coordinator(manifest_coordinator)
        actual = normalize_coordinator(inventory_coordinator)
    except ValueError as exc:
        raise BindingRefused(Refusal.COORDINATOR_ADDRESS_MISSING, str(exc)) from None
    if wanted != actual:
        raise BindingRefused(
            Refusal.COORDINATOR_ADDRESS_MISMATCH,
            f"fleet {entry.fleet_id!r} is served by coordinator {wanted} but the "
            f"inventory resolves places on {actual} — the lane's DUT is not the "
            "fixture's DUT",
        )

    # -- what the fixture will do -----------------------------------------
    intent = venue_intent(
        environ=environ or {},
        inventory=inventory,
        preconditions_met=preconditions_met,
        session_scope=session_scope,
        xdist_worker=xdist_worker,
        operator_host=operator_host,
    )
    if intent.skip_reason == "auto-skip":
        raise BindingRefused(
            Refusal.FIXTURE_WOULD_SKIP_PLACE,
            f"a fleet manifest demands lane {resource.resource_id!r} but this "
            f"{session_scope}-only session would auto-skip the venue: the fixture "
            "would own no place, so the reservation could not be honoured",
        )
    if not intent.owns_place:
        raise BindingRefused(
            Refusal.VENUE_TAKES_NO_PLACE,
            f"venue mode {intent.mode!r} ({intent.skip_reason}) acquires no place — "
            "with a fleet manifest there must be a place owner, or the host-budget "
            "reservation is meaningless",
        )
    if intent.protected:
        raise BindingRefused(
            Refusal.RESOURCE_PROTECTED,
            f"place {intent.place!r} is flagged protected in the inventory — no run "
            "may acquire it",
        )
    if not intent.address:
        raise BindingRefused(
            Refusal.VENUE_ADDRESS_MISSING,
            f"place {intent.place!r} has no inventory address, so the DUT the run "
            "drives cannot be identified",
        )

    # -- place agreement (b) ----------------------------------------------
    manifest_place = (resource.resource_id or "").strip()
    if intent.origin == "explicit-place" and intent.place != manifest_place:
        raise BindingRefused(
            Refusal.PLACE_OVERRIDE_CONFLICT,
            f"TOLLGATE_LABGRID_PLACE={intent.place!r} overrides the manifest lane's "
            f"place {manifest_place!r} — an env override may not retarget a lane",
        )
    if intent.place != manifest_place:
        raise BindingRefused(
            Refusal.PLACE_MISMATCH,
            f"lane {resource.resource_id!r} is not the place the fixture would own "
            f"({intent.place!r}, origin {intent.origin!r})",
        )

    # -- profile endpoint (req 1: env must target exactly this place) -----
    endpoint = (profile_endpoint or "").strip()
    if endpoint:
        named = [
            c
            for c in place_candidates(inventory).get(intent.place, ())
            if c.address == endpoint
        ]
        if not named:
            owned_elsewhere = [
                (place, c)
                for place, items in place_candidates(inventory).items()
                for c in items
                if c.address == endpoint
            ]
            if owned_elsewhere:
                other_place = owned_elsewhere[0][0]
                raise BindingRefused(
                    Refusal.PROFILE_ENDPOINT_MISMATCH,
                    f"the profile's TOLLGATE_SSH_HOST names the DUT of place "
                    f"{other_place!r}, but the lane is place {intent.place!r}",
                )
            raise BindingRefused(
                Refusal.PROFILE_ENDPOINT_UNKNOWN,
                f"the profile's TOLLGATE_SSH_HOST matches no inventory dossier, so it "
                f"cannot be shown to be place {intent.place!r}'s DUT",
            )

    # -- claims ------------------------------------------------------------
    claim = PlaceClaim(place=intent.place, owner=owner)
    for other in already_claimed or ():
        if claim.overlaps(other):
            raise BindingRefused(
                Refusal.PLACE_ALREADY_CLAIMED,
                f"place {claim.place!r} is already claimed by run {other.owner!r}",
            )

    return Binding(
        fleet_id=entry.fleet_id,
        resource_id=resource.resource_id,
        lane_id=resolved_lane,
        coordinator=actual,
        place=intent.place,
        dut_address=intent.address,
        owner=owner.strip(),
        origin=intent.origin,
        mode=intent.mode,
    )


# ---------------------------------------------------------------------------
# Structural self-check (used by the tests, cheap enough to keep here)
# ---------------------------------------------------------------------------
#: Names this module must never import or call: the place belongs to conftest.
FORBIDDEN_NAMES = frozenset(
    {
        "subprocess",
        "socket",
        "yaml",
        "load_inventory",
        "LabgridBench",
        "venue_mutex",
        "bench_for_router",
        "hardware_lock",
        "acquire",
        "release",
        "acquire_hardware_lock",
        "release_hardware_lock",
        "open",
        "urlopen",
    }
)

#: Modules this layer may import. ``lib.labgrid_venue`` is the ONE project
#: import and only for ``resolve_venue_mode`` — a pure string→mode function.
#: Importing ``plan_venue`` would make this layer a participant in the venue
#: decision instead of a checker of it, so the name allowlist is explicit.
PERMITTED_MODULES = frozenset(
    {
        "__future__",
        "ast",
        "collections",
        "collections.abc",
        "dataclasses",
        "enum",
        "pathlib",
        "typing",
        "lib",
        "lib.labgrid_venue",
    }
)
PERMITTED_NAMES = frozenset({"resolve_venue_mode"})


def module_io_audit(path: str | Path | None = None) -> dict[str, tuple[str, ...]]:
    """Audit this module's own imports/calls for acquisition or I/O.

    Returns the offenders under three keys — ``modules`` (an import outside
    :data:`PERMITTED_MODULES`), ``names`` (an imported symbol that is not
    ``resolve_venue_mode`` or is in :data:`FORBIDDEN_NAMES`) and ``calls`` (a
    call/attribute to a :data:`FORBIDDEN_NAMES` member) — so a test can assert
    all three are empty. Parsing this module's own source is the one file
    access in the module and is kept out of every decision path.
    """
    source = Path(path or __file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules: list[str] = []
    names: list[str] = []
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(
                alias.name for alias in node.names if alias.name not in PERMITTED_MODULES
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module not in PERMITTED_MODULES:
                modules.append(module)
            # Project symbols are the risk: a name from outside this package
            # can never acquire a place, but `lib.*` can.
            project_import = module == "lib" or module.startswith("lib.")
            names.extend(
                alias.name
                for alias in node.names
                if alias.name in FORBIDDEN_NAMES
                or (project_import and alias.name not in PERMITTED_NAMES)
            )
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in FORBIDDEN_NAMES:
                calls.append(name)
    return {"modules": tuple(modules), "names": tuple(names), "calls": tuple(calls)}
