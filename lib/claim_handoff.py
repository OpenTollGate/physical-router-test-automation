"""Fleet claim handoff (requirement 2) and release evidence (requirement 5).

Two halves, both **pure logic over injected inputs**, both deliberately NOT
wired into ``scripts/run-profile.py`` (whose fleet gate stays closed):

**Part A — the handoff.** ``lib/fleet_binding`` proves that a manifest lane and
the venue resolution name one DUT, and exports the claim as
``TOLLGATE_FLEET_CLAIM_*``. Until now nothing consumed it: the runner could
reserve host budget for place A while the pytest fixture acquired place B, or
acquired *nothing* and fell back to the legacy/direct path underneath the
reservation. :func:`evaluate_handoff` is the consumer — a decision the fixture
seam (``tests/conftest.py:_claim_handoff_guard``) takes *before*
``venue_mutex`` acquires, so disagreement exits ``rc=4`` with the place still
untouched.

The rule is: **no claim env, no new behaviour.** When none of the claim
variables is set, :func:`evaluate_handoff` returns
:attr:`ClaimCode.NO_CLAIM` and the caller does nothing at all — every existing
run path is unchanged. When a claim *is* present, every uncertainty is a
refusal (partial env, a venue that would own no place, a different place, a
run identity that disagrees, a foreign claim on the same place). There is no
"warn and continue" and no fallback to the legacy path: a reservation that the
fixture does not honour is worse than a run that refuses to start.

**Part B — release evidence.** ``LabgridBench.release()`` already re-reads
ownership and raises when the release is unconfirmed, leaving
``cleanup_pending`` set (``lib/labgrid_venue.py:303-343``). What was missing is
the *recording* rule: given what the release call did, may this allocation be
marked clean-released, or must it become ``CLEANUP_PENDING``?
:func:`release_verdict` is that rule, as a total function, with the fail-closed
default baked in — **no re-read, no "clean"**.

Nothing here acquires, releases, reads a file or contacts a coordinator; the
one project import is :class:`lib.fleet_binding.PlaceClaim`, because overlap
attribution is exactly what requirement 2 means by "not already held".
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from lib.fleet_binding import PlaceClaim

# ---------------------------------------------------------------------------
# The wire format: exactly these variables, exported by the runner
# ---------------------------------------------------------------------------
#: The place the runner reserved host budget for (``Binding.place``).
ENV_PLACE = "TOLLGATE_FLEET_CLAIM_PLACE"
#: The attributed owner of that reservation (``ClientIdentity.for_run(...).owner``,
#: i.e. ``<host>/<user>``) — the string the coordinator stores.
ENV_OWNER = "TOLLGATE_FLEET_CLAIM_OWNER"
#: The run identity the claim belongs to (``Binding.owner`` / runner run id).
ENV_RUN_ID = "TOLLGATE_FLEET_RUN_ID"
#: Optional ``place=owner;place=owner`` list of claims this host already knows
#: are live (other lanes). Feeds :meth:`PlaceClaim.overlaps`.
ENV_HELD = "TOLLGATE_FLEET_CLAIM_HELD"
#: The runner's pre-existing run id. When BOTH it and ``ENV_RUN_ID`` are set
#: they must agree: a claim attributed to a different run is not this run's.
ENV_RUN_ID_LEGACY = "TOLLGATE_RUN_ID"

#: Any one of these being non-empty means "a claim was exported".
CLAIM_ENV = (ENV_PLACE, ENV_OWNER, ENV_RUN_ID)


class ClaimEnvError(ValueError):
    """The exported claim env cannot be read as a claim at all."""

    def __init__(self, code: "ClaimCode", detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


class ClaimCode(str, enum.Enum):
    """Stable machine-readable handoff outcomes.

    Renaming a member changes what a log scraper or CI assertion sees;
    ``tests/unit/test_claim_handoff.py`` pins the exact string values, the same
    way ``lib.fleet_binding.Refusal`` is pinned.
    """

    #: No claim env at all — the fixture behaves exactly as it did before.
    NO_CLAIM = "no_claim"
    #: A claim was exported and the fixture will honour it.
    CLAIM_AGREES = "claim_agrees"
    #: Some claim env is set but place/owner/run id is missing.
    CLAIM_INCOMPLETE = "claim_incomplete"
    #: The venue decision owns no place (direct/legacy/auto-skip/xdist worker).
    VENUE_TAKES_NO_PLACE = "venue_takes_no_place"
    #: The claim names place A; the venue resolution targets place B.
    PLACE_MISMATCH = "place_mismatch"
    #: The claim is attributed to a different run than this one.
    CLAIM_RUN_ID_MISMATCH = "claim_run_id_mismatch"
    #: The claim's owner is not the identity this session would acquire as.
    CLAIM_OWNER_MISMATCH = "claim_owner_mismatch"
    #: Another attributed owner already claims this place.
    CLAIM_ALREADY_HELD = "claim_already_held"
    #: ``TOLLGATE_FLEET_CLAIM_HELD`` cannot be parsed.
    CLAIM_HELD_UNPARSABLE = "claim_held_unparsable"


#: Every code a caller may see, as plain strings.
CLAIM_CODES: frozenset[str] = frozenset(member.value for member in ClaimCode)

#: Codes that mean "proceed" (no refusal).
PROCEED_CODES = frozenset({ClaimCode.NO_CLAIM, ClaimCode.CLAIM_AGREES})


@dataclass(frozen=True)
class ClaimHandoff:
    """A run claim read from the environment, attributed to one run."""

    place: str
    owner: str
    run_id: str

    @property
    def claim(self) -> PlaceClaim:
        return PlaceClaim(place=self.place, owner=self.owner)


def _get(environ: Mapping[str, str], name: str) -> str:
    return (environ.get(name) or "").strip()


def read_claim(environ: Mapping[str, str]) -> ClaimHandoff | None:
    """Read the exported claim, or ``None`` when no claim env is present.

    Partial env is NOT "no claim": a runner that exports only
    ``TOLLGATE_FLEET_CLAIM_PLACE`` has still reserved host budget, so the
    handoff must refuse rather than quietly revert to the unclaimed path
    (:class:`ClaimEnvError` with :attr:`ClaimCode.CLAIM_INCOMPLETE`).
    """
    present = [name for name in CLAIM_ENV if _get(environ, name)]
    if not present:
        return None
    place = _get(environ, ENV_PLACE)
    owner = _get(environ, ENV_OWNER)
    run_id = _get(environ, ENV_RUN_ID)
    missing = [
        name
        for name, value in ((ENV_PLACE, place), (ENV_OWNER, owner), (ENV_RUN_ID, run_id))
        if not value
    ]
    if missing:
        raise ClaimEnvError(
            ClaimCode.CLAIM_INCOMPLETE,
            "a fleet claim must be fully attributed — set "
            + ", ".join(missing)
            + " (a partial claim cannot be honoured and must not fall back)",
        )
    return ClaimHandoff(place=place, owner=owner, run_id=run_id)


def parse_claims(raw: str) -> tuple[PlaceClaim, ...]:
    """Parse ``place=owner;place=owner`` into claims.

    A malformed entry is an error, not something to skip: an unreadable
    "already held" list means overlap cannot be ruled out, and silently
    dropping half of it would turn a contention check into a no-op.
    """
    claims: list[PlaceClaim] = []
    for chunk in (raw or "").split(";"):
        entry = chunk.strip()
        if not entry:
            continue
        place, sep, owner = entry.partition("=")
        place, owner = place.strip(), owner.strip()
        if not sep or not place or not owner:
            raise ClaimEnvError(
                ClaimCode.CLAIM_HELD_UNPARSABLE,
                f"held-claim entry {entry!r} is not 'place=owner' — refusing to "
                "treat an unreadable contention list as empty",
            )
        claims.append(PlaceClaim(place=place, owner=owner))
    return tuple(claims)


@dataclass(frozen=True)
class Handoff:
    """What the fixture seam should do about the exported claim."""

    code: ClaimCode
    detail: str = ""
    place: str = ""
    owner: str = ""
    run_id: str = ""
    #: The foreign claim that blocks us, when ``CLAIM_ALREADY_HELD``.
    conflict: PlaceClaim | None = None

    @property
    def refused(self) -> bool:
        """True unless the claim agrees (or there is no claim at all)."""
        return self.code not in PROCEED_CODES

    @property
    def message(self) -> str:
        return f"{self.code.value}: {self.detail}" if self.detail else self.code.value


def evaluate_handoff(
    *,
    environ: Mapping[str, str],
    resolved_place: str = "",
    owns_place: bool = True,
    skip_reason: str = "",
    held: Iterable[PlaceClaim] = (),
    identity_owner: str = "",
) -> Handoff:
    """Decide whether this session may proceed under the exported run claim.

    Total: every input problem becomes a refused :class:`Handoff`, never an
    exception, because the caller's only safe reaction to all of them is the
    same one (``pytest.exit(rc=4)`` before any acquisition).

    Precedence, fixed and pinned by tests:

    1. no claim env → :attr:`ClaimCode.NO_CLAIM` (zero new behaviour);
    2. partial claim, or an unreadable held list → refusal;
    3. the venue decision owns no place → refusal (a reservation with no
       owner of the place cannot be honoured, and falling back to the legacy
       path is exactly the silent retarget this prevents);
    4. claim place ≠ resolved place → refusal;
    5. claim run id ≠ this run's id → refusal;
    6. claim owner ≠ the identity this session would acquire as (only checked
       when the caller knows that identity) → refusal;
    7. a foreign attributed claim on the same place → refusal;
    8. otherwise the claim agrees.

    ``held`` is the caller's extra knowledge (claims already recorded in this
    process); ``ENV_HELD`` is the runner-exported list. Both feed
    :meth:`PlaceClaim.overlaps`, so "same owner restating its claim" is a
    retry, not contention.
    """
    try:
        claim = read_claim(environ)
    except ClaimEnvError as exc:
        return Handoff(exc.code, exc.detail)
    if claim is None:
        # No claim exported: nothing below may change this session's behaviour,
        # so the held list is not even parsed (a stale one must not refuse a
        # run that never made a reservation).
        return Handoff(ClaimCode.NO_CLAIM)
    try:
        exported_held = parse_claims(_get(environ, ENV_HELD))
    except ClaimEnvError as exc:
        return Handoff(exc.code, exc.detail)

    if not owns_place:
        return Handoff(
            ClaimCode.VENUE_TAKES_NO_PLACE,
            f"a fleet claim for place {claim.place!r} (run {claim.run_id!r}) is "
            f"exported, but this session's venue decision owns no place"
            + (f" ({skip_reason})" if skip_reason else "")
            + " — the fixture would drive a DUT it never acquired, or none at all",
            place=claim.place,
            owner=claim.owner,
            run_id=claim.run_id,
        )

    resolved = (resolved_place or "").strip()
    if (claim.place or "").strip() != resolved:
        return Handoff(
            ClaimCode.PLACE_MISMATCH,
            f"the run claim is for place {claim.place!r} but the venue "
            f"resolution targets {resolved or '(no place)'!r} — refusing to "
            "acquire a place the reservation does not name",
            place=claim.place,
            owner=claim.owner,
            run_id=claim.run_id,
        )

    legacy_run_id = _get(environ, ENV_RUN_ID_LEGACY)
    if legacy_run_id and legacy_run_id != claim.run_id:
        return Handoff(
            ClaimCode.CLAIM_RUN_ID_MISMATCH,
            f"the claim is attributed to run {claim.run_id!r} but this process "
            f"is run {legacy_run_id!r} — another lane's reservation",
            place=claim.place,
            owner=claim.owner,
            run_id=claim.run_id,
        )

    mine = (identity_owner or "").strip()
    if mine and mine != claim.owner:
        # The coordinator stores ONE owner string per place. If the bench in
        # this session would acquire as a different identity, the reservation
        # and the acquisition are two different claims on one DUT.
        return Handoff(
            ClaimCode.CLAIM_OWNER_MISMATCH,
            f"the claim is attributed to owner {claim.owner!r} but this session "
            f"would acquire as {mine!r} — the place would be held by an "
            "identity the reservation does not name",
            place=claim.place,
            owner=claim.owner,
            run_id=claim.run_id,
        )

    ours = claim.claim
    for other in (*exported_held, *tuple(held)):
        if ours.overlaps(other):
            return Handoff(
                ClaimCode.CLAIM_ALREADY_HELD,
                f"place {claim.place!r} is already claimed by run "
                f"{other.owner!r} (this claim is {claim.owner!r})",
                place=claim.place,
                owner=claim.owner,
                run_id=claim.run_id,
                conflict=other,
            )

    return Handoff(
        ClaimCode.CLAIM_AGREES,
        f"place {claim.place!r} claimed by {claim.owner!r} (run {claim.run_id!r})",
        place=claim.place,
        owner=claim.owner,
        run_id=claim.run_id,
    )


# ---------------------------------------------------------------------------
# Part B — release evidence
# ---------------------------------------------------------------------------
class ReleaseState(str, enum.Enum):
    """The only two states an allocation may be recorded in."""

    CLEAN_RELEASED = "clean_released"
    CLEANUP_PENDING = "cleanup_pending"


class ReleaseReason(str, enum.Enum):
    """Why the state was chosen — the evidence itself, not a comment."""

    #: Release re-verified: the place is free.
    RELEASED_CONFIRMED = "released_confirmed"
    #: This session never held the place, so nothing is owed.
    NEVER_ACQUIRED = "never_acquired"
    #: Another owner holds the place now (fenced/replaced): ours is gone.
    LEASE_TAKEN_BY_OTHER = "lease_taken_by_other"
    #: Our own lease is STILL on the coordinator.
    LEASE_STILL_HELD = "lease_still_held"
    #: Ownership could not be read back, so the release is unproven.
    OWNERSHIP_UNREADABLE = "ownership_unreadable"
    #: We do not know our own owner string, so nothing can be attributed.
    OWNER_UNKNOWN = "owner_unknown"
    #: ``LabgridBench.release()`` raised (it also sets ``cleanup_pending``).
    RELEASE_RAISED = "release_raised"
    #: The bench's durable ``cleanup_pending`` flag is set.
    CLEANUP_FLAG_SET = "cleanup_flag_set"


#: Reasons that permit :attr:`ReleaseState.CLEAN_RELEASED`.
CLEAN_REASONS: frozenset[ReleaseReason] = frozenset(
    {
        ReleaseReason.RELEASED_CONFIRMED,
        ReleaseReason.NEVER_ACQUIRED,
        ReleaseReason.LEASE_TAKEN_BY_OTHER,
    }
)


@dataclass(frozen=True)
class ReleaseVerdict:
    """One allocation's release outcome, ready to be recorded as evidence."""

    state: ReleaseState
    reason: ReleaseReason
    detail: str = ""
    place: str = ""
    owner_after: str | None = None

    @property
    def clean(self) -> bool:
        return self.state is ReleaseState.CLEAN_RELEASED

    @property
    def review(self) -> bool:
        """True when a human should look even though the run may continue."""
        return not self.clean or self.reason is ReleaseReason.LEASE_TAKEN_BY_OTHER

    def to_dict(self) -> dict[str, object]:
        """JSON-safe record for the run's evidence file."""
        return {
            "state": self.state.value,
            "reason": self.reason.value,
            "detail": self.detail,
            "place": self.place,
            "owner_after": self.owner_after,
            "clean": self.clean,
            "review": self.review,
        }


def release_verdict(
    *,
    acquired: bool,
    cleanup_pending: bool,
    error: BaseException | None = None,
    owner_after: str | None = None,
    our_owner: str = "",
    place: str = "",
) -> ReleaseVerdict:
    """Decide clean-released vs ``CLEANUP_PENDING`` for one allocation.

    ``owner_after`` is the ownership string re-read AFTER the release
    (``LabgridBench.owner()``), or ``None`` when it was not read. ``None`` can
    never be clean for a session that held the lease: an unverified release is
    an unproven release, and the flag exists for exactly that case.

    Fail-closed ordering, pinned by tests:

    1. ``error`` — the release raised (:attr:`ReleaseReason.RELEASE_RAISED`);
    2. the re-read says the place is still OURS → pending, even if ``acquired``
       is False (incoherent state is not evidence of a clean release) and even
       if ``cleanup_pending`` is also set (still-held is the more specific
       fact, and it is the one an operator acts on);
    3. ``cleanup_pending`` — the bench's durable flag;
    4. ``not acquired`` — nothing was taken, nothing is owed;
    5. no re-read available → pending;
    6. our own owner string unknown → pending (cannot be attributed);
    7. place free → clean, confirmed;
    8. someone else holds it → clean, but flagged for review (we were
       fenced/replaced; the lease left our ownership without our release).
    """
    if error is not None:
        return ReleaseVerdict(
            ReleaseState.CLEANUP_PENDING,
            ReleaseReason.RELEASE_RAISED,
            f"release raised {type(error).__name__}: {error}",
            place=place,
            owner_after=owner_after,
        )

    ours = (our_owner or "").strip()
    after = None if owner_after is None else (owner_after or "").strip()

    if after is not None and ours and after == ours:
        return ReleaseVerdict(
            ReleaseState.CLEANUP_PENDING,
            ReleaseReason.LEASE_STILL_HELD,
            f"place {place!r} is still held by {after!r} after release",
            place=place,
            owner_after=owner_after,
        )
    if cleanup_pending:
        return ReleaseVerdict(
            ReleaseState.CLEANUP_PENDING,
            ReleaseReason.CLEANUP_FLAG_SET,
            "bench reports cleanup_pending — the place still owes a release",
            place=place,
            owner_after=owner_after,
        )

    if not acquired:
        return ReleaseVerdict(
            ReleaseState.CLEAN_RELEASED,
            ReleaseReason.NEVER_ACQUIRED,
            "this session never held the place",
            place=place,
            owner_after=owner_after,
        )
    if after is None:
        return ReleaseVerdict(
            ReleaseState.CLEANUP_PENDING,
            ReleaseReason.OWNERSHIP_UNREADABLE,
            "no ownership re-read after release — an unverified release is unproven",
            place=place,
            owner_after=owner_after,
        )
    if not ours:
        return ReleaseVerdict(
            ReleaseState.CLEANUP_PENDING,
            ReleaseReason.OWNER_UNKNOWN,
            "this session's own owner string is unknown, so the re-read cannot "
            "be attributed",
            place=place,
            owner_after=owner_after,
        )
    if not after:
        return ReleaseVerdict(
            ReleaseState.CLEAN_RELEASED,
            ReleaseReason.RELEASED_CONFIRMED,
            f"place {place!r} is free after release",
            place=place,
            owner_after=owner_after,
        )
    return ReleaseVerdict(
        ReleaseState.CLEAN_RELEASED,
        ReleaseReason.LEASE_TAKEN_BY_OTHER,
        f"place {place!r} is held by {after!r}, not by us ({ours!r}) — "
        "our lease is gone but we did not release it",
        place=place,
        owner_after=owner_after,
    )


def record_release(
    bench,
    *,
    error: BaseException | None = None,
    owner_after: str | None = None,
    ever_acquired: bool | None = None,
) -> ReleaseVerdict:
    """Record a release for a bench-like object WITHOUT contacting anything.

    Reads only local, already-known state (``acquired``, ``cleanup_pending``,
    ``identity.owner``, ``place``); the ownership re-read is passed in by the
    caller, because reading it is I/O and this function must stay pure. A
    caller that does not re-read gets ``CLEANUP_PENDING`` — deliberately.

    ``ever_acquired=True`` is how a caller records the outcome of a release it
    just performed: ``LabgridBench.release()`` clears ``acquired`` on success,
    so without it the record would read "never acquired" instead of "released
    and confirmed". Pass ``False`` only when the session provably never took
    the place.
    """
    identity = getattr(bench, "identity", None)
    acquired = bool(getattr(bench, "acquired", False))
    if ever_acquired is not None:
        acquired = bool(ever_acquired)
    return release_verdict(
        acquired=acquired,
        cleanup_pending=bool(getattr(bench, "cleanup_pending", False)),
        error=error,
        owner_after=owner_after,
        our_owner=getattr(identity, "owner", "") if identity is not None else "",
        place=getattr(bench, "place", ""),
    )
