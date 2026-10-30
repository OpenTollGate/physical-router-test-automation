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

Ownership (the 2026-10-30 audit's "critical existing race")
-----------------------------------------------------------
A place has exactly ONE owner string on the coordinator: ``<host>/<user>``,
taken from ``LG_HOSTNAME``/``LG_USERNAME`` (labgrid 26.0.1,
``labgrid/remote/client.py:102-106``). Everything about safe concurrency
follows from that:

* Every run gets its OWN identity (``ClientIdentity.for_run``), so two runs on
  one host are two owners, not one shared ``user`` that can trip over itself.
* ``acquire()`` NEVER releases on an error path. The old implementation did a
  best-effort ``release`` on any failure, which — under a shared identity —
  released whichever run actually held the place. An ambiguous failure (a
  timeout after the grant landed) is instead **reconciled**: read ownership
  back and accept the grant if it is ours.
* ``release()`` is conditional: ``release-from <our host/user>``, which the
  coordinator honours only for the matching owner
  (``labgrid/remote/coordinator.py:909-914``). We never ``--kick``.
* ``release-from`` exits 0 even when it released nothing, so success is
  re-verified by reading ownership back. If the release did not take effect,
  local state is KEPT and ``cleanup_pending`` is set — that flag is the durable
  "this lane owes a cleanup" signal, and it is what keeps the lane out of
  service instead of being handed to the next run.
"""

from __future__ import annotations

import contextlib
import hashlib
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

#: The coordinator stores one owner string per place; labgrid splits it into
#: ``host``/``user`` on a single ``/`` and its CLI help/limits assume a login
#: name, so a run identity must stay inside the usual 63-character budget.
_MAX_USER_LEN = 63
_RUN_PREFIX = "prta-"
#: Characters of the ORIGINAL run id hashed into every per-run identity. 16 hex
#: chars (64 bits) is far beyond collision range for the handful of runs that
#: share one coordinator, and it survives both sanitization and truncation.
_RUN_DIGEST_LEN = 16


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


@dataclass(frozen=True)
class ClientIdentity:
    """Who the coordinator thinks we are: one ``<host>/<user>`` string.

    The coordinator stores ownership as a single opaque string, so the only way
    two concurrent runs stay distinguishable is to give them different
    identities. ``host`` stays real — it is how operators and ``who`` identify
    the machine — while ``user`` carries the run.
    """

    host: str
    user: str

    @property
    def owner(self) -> str:
        return f"{self.host}/{self.user}"

    def env(self) -> dict[str, str]:
        return {"LG_HOSTNAME": self.host, "LG_USERNAME": self.user}

    @classmethod
    def current(cls) -> ClientIdentity:
        """This host and this OS user — the legacy shared-identity behaviour."""
        import getpass
        import socket

        host = os.environ.get("LG_HOSTNAME", "").strip() or socket.gethostname()
        user = os.environ.get("LG_USERNAME", "").strip()
        if not user:
            try:
                user = getpass.getuser()
            except Exception:  # noqa: BLE001 - no passwd entry (containers)
                user = f"uid{os.getuid()}"
        return cls(host=host, user=user)

    @classmethod
    def for_run(cls, run_id: str, *, host: str | None = None) -> ClientIdentity:
        """A unique identity for one run, derived from its run id.

        Deterministic in the run id (so a retry of the same run reconciles to
        the same owner) and unique per run (so two runs never share an owner).

        Sanitization alone is NOT injective and the coordinator's owner field is
        truncated, so the identity carries a digest of the ORIGINAL run id:

        * ``"run/1"`` and ``"run-1"`` both sanitize to ``"run-1"``;
        * two run ids sharing a 60-character prefix but differing at the end
          both truncate to the same 63-character owner.

        Either case would collapse two runs onto one coordinator owner — the
        exact collision that lets one run release the other's place. The digest
        is computed from the full, untruncated run id, so it cannot be fooled by
        normalization or by the length cap.
        """
        raw = (run_id or "").strip()
        token = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw)
        token = token.strip("-")
        if not token:
            raise ValueError("run_id is required for a per-run client identity")
        digest = hashlib.sha256(raw.encode()).hexdigest()[:_RUN_DIGEST_LEN]
        budget = max(1, _MAX_USER_LEN - len(_RUN_PREFIX) - 1 - _RUN_DIGEST_LEN)
        base_host = host or os.environ.get("LG_HOSTNAME", "").strip()
        if not base_host:
            import socket

            base_host = socket.gethostname()
        return cls(host=base_host, user=f"{_RUN_PREFIX}{token[:budget]}-{digest}")

    @classmethod
    def parse(cls, owner: str) -> ClientIdentity | None:
        owner = (owner or "").strip()
        if not owner or "/" not in owner:
            return None
        host, user = owner.split("/", 1)
        if not host or not user:
            return None
        return cls(host=host, user=user)


class LabgridBench:
    """Acquire/power/release one labgrid place via the client binary.

    ``identity`` is the run's ownership claim. Defaulting it to
    ``ClientIdentity.current()`` keeps single-run behaviour identical to before;
    concurrent callers pass ``ClientIdentity.for_run(run_id)``.
    """

    def __init__(
        self,
        coordinator: str,
        place: str,
        runner: Runner | None = None,
        identity: ClientIdentity | None = None,
    ):
        if not coordinator or ":" not in coordinator:
            raise LabgridBenchError(
                "coordinator address must be host:port (inventory 'coordinator.address')"
            )
        self.coordinator = coordinator
        self.place = place
        self.identity = identity or ClientIdentity.current()
        self._runner = runner or subprocess.run
        self._bin = _default_client_bin() if runner is None else "labgrid-client"
        #: True only while WE hold a verified lease.
        self._acquired = False
        #: Durable "this place still owes a release" flag. Set when a release
        #: could not be confirmed; only a confirmed release clears it.
        self._cleanup_pending = False

    # -- state (read-only, so a caller cannot fake ownership) --------------
    @property
    def acquired(self) -> bool:
        return self._acquired

    @property
    def cleanup_pending(self) -> bool:
        return self._cleanup_pending

    def _client(self, *args: str) -> str:
        cmd = [self._bin, "-x", self.coordinator, "-p", self.place, *args]
        env = {**os.environ, **self.identity.env()}
        try:
            proc = self._run(cmd, env=env)
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

    def _run(self, cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return self._runner(cmd, capture_output=True, text=True, timeout=_RUN_TIMEOUT, env=env)

    # -- ownership ---------------------------------------------------------
    def owner(self) -> str:
        """The coordinator's owner string for this place, or "" when free.

        ``show`` prints ``acquired: <host>/<user>`` or ``acquired: None``. The
        parse is deliberately strict: if we cannot read it, we return "" and
        the caller must treat ownership as UNKNOWN rather than assume either
        way — guessing "free" is how a run tramples a colleague's place.
        """
        out = self._client("show")
        for raw in out.splitlines():
            line = raw.strip()
            if not line.startswith("acquired:"):
                continue
            value = line.split(":", 1)[1].strip()
            if not value or value.lower() in {"none", "null"}:
                return ""
            return value
        raise LabgridBenchError(
            f"could not read ownership of place {self.place!r} from 'labgrid-client show' — "
            "refusing to guess whether it is free"
        )

    def owns(self) -> bool:
        """True when the coordinator says OUR identity holds this place."""
        return self.owner() == self.identity.owner

    def verify(self) -> bool:
        """Re-read the lease: still ours?"""
        try:
            held = self.owns()
        except LabgridBenchError:
            return False
        if not held:
            self._acquired = False
        return held

    # -- lifecycle ---------------------------------------------------------
    def acquire(self) -> None:
        """Acquire the place, distinguishing "refused" from "unknown".

        A failure here NEVER issues a release: a release under contention would
        return whichever run actually holds the place. The only recovery for an
        ambiguous failure is reconciliation — read ownership back and accept the
        grant if it is ours.
        """
        try:
            self._client("acquire")
        except LabgridBenchError:
            # Did the grant land anyway (timeout after grant, lost reply)?
            # Only OUR identity counts: someone else's claim is their result.
            if self._reconciled_as_owner():
                self._acquired = True
                return
            raise
        self._acquired = True

    def _reconciled_as_owner(self) -> bool:
        try:
            return self.owns()
        except LabgridBenchError:
            # Ownership unreadable: we cannot prove the grant was ours, so we
            # do not claim it and we do not release it.
            return False

    def release(self) -> None:
        """Release only OUR lease, and only believe it after re-reading.

        ``release-from`` is conditional server-side and returns success even
        when the owner did not match, so the return code cannot be trusted.
        When the lease is still there afterwards, local state is kept and
        ``cleanup_pending`` stays set: the place is still ours to clean up.
        """
        if not self._acquired:
            return
        try:
            held_by = self.owner()
        except LabgridBenchError:
            held_by = None  # unknown: attempt the conditional release anyway
        if held_by is not None and held_by and held_by != self.identity.owner:
            # Somebody else holds it now (we were fenced or replaced). Kicking
            # them would be the cross-run destructive action this fixes.
            self._acquired = False
            self._cleanup_pending = False
            return
        try:
            self._client("release-from", self.identity.owner)
        except LabgridBenchError:
            self._cleanup_pending = True
            raise
        # release-from exits 0 whether or not it matched; verify.
        try:
            still_held_by = self.owner()
        except LabgridBenchError as e:
            self._cleanup_pending = True
            raise LabgridBenchError(
                f"release of place {self.place!r} could not be confirmed: {e}"
            ) from None
        if still_held_by == self.identity.owner:
            self._cleanup_pending = True
            raise LabgridBenchError(
                f"labgrid-client release-from on place {self.place!r} reported success but the "
                f"lease is still held by {still_held_by!r} — treating cleanup as pending"
            )
        self._acquired = False
        self._cleanup_pending = False

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

    Release status is recorded on the bench (``acquired``/
    ``cleanup_pending``) and NEVER replaces the body's outcome: a teardown
    that fails must not turn a test failure into a teardown error, nor a
    teardown success into a pass. The caller reads
    ``binding.bench.cleanup_pending`` afterwards and reports it as its own
    fact.
    """
    if file_lock:
        hardware_lock.acquire_hardware_lock(phase)
    try:
        binding.bench.acquire()
        try:
            yield binding.bench
        finally:
            try:
                binding.bench.release()
            except LabgridBenchError:
                # cleanup_pending is already set on the bench; do not mask the
                # body exception (or invent one when the body succeeded).
                pass
    finally:
        if file_lock:
            hardware_lock.release_hardware_lock()
