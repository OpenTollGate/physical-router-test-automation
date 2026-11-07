"""Host-local authoritative admission for fleet lanes.

Slice 2 of the multi-contributor fleet plan. Source of truth for every rule
here is ``reports/prta-fleet-capacity-audit.md`` (2026-10-30), sections
"Critical existing race" and "Smallest safe implementation contract".

What this module is
-------------------
The **single authority** for one host's lanes. It answers exactly one
question, atomically: *may this run claim this lane plus this resource
vector plus these conflict domains right now?* — and when the answer is yes it
issues a run id, an ownership token, an expiry and a **fencing generation**.

Why it is a database and not a file
-----------------------------------
The audit's requirement is a *serialized transaction per lane*, and capacity
counters that can never be exceeded under concurrency. A read-then-write
sequence in Python cannot promise that: two scheduler processes can both read
the same one-slot snapshot and both decide they fit. So the decision and the
write happen inside one ``BEGIN IMMEDIATE`` transaction in SQLite, which takes
a write lock before the first read. Two processes racing for the last slot
therefore serialize at the storage engine, not at a mock lock, and the loser
sees the winner's row inside its own transaction.

That is also why the store is *host-local*. A central file on a submitting
laptop is what the audit rules out: it would be a copy of the truth, not the
truth. The production form is this store behind a protected service on the
host that owns the lanes; the semantics tested here are the same either way
because every decision is one transaction over the shared store.

Honesty rules encoded in the state machine
------------------------------------------
* ``RESERVED`` and ``ACTIVE`` are different states on purpose: host admission
  and labgrid acquisition are **not** one distributed transaction, so a run
  holds a reservation first and only becomes ``ACTIVE`` when the coordinator
  side has also committed. Tests run in neither state; they run only after
  ``ACTIVE``.
* An **expired** allocation is never handed to the next run. TTL expiry moves
  the lane to ``QUARANTINED``: the old processes may still be mutating a DUT,
  and reassignment on the strength of a timestamp alone is exactly the bug the
  audit calls out. The lane returns to service only after the previous holder
  is fenced and its cleanup is confirmed.
* A failed cleanup is **durable** (``CLEANUP_PENDING``) and keeps excluding the
  lane. It is not cleared by the next successful run.
* Every grant increments a **fencing generation**. A stale holder's token no
  longer authenticates any state transition, so a zombie cannot renew, cannot
  activate and cannot release its successor's claim.
* An authority **epoch** is invalidated on restart; admission refuses until
  ``reconcile()`` has run, and old-epoch evidence is refused rather than
  trusted (see ``lib.fleet_capacity`` freshness rules).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "AUTHORITY_SCHEMA_VERSION",
    "ENV_AUTHORITY_PATH",
    "AuthorityError",
    "NotGranted",
    "Fenced",
    "UnknownRun",
    "Demand",
    "LaneSpec",
    "Allocation",
    "Decision",
    "LaneHeadroom",
    "FleetAuthority",
    "default_authority_path",
]

#: Bumped whenever the on-disk schema changes incompatibly. An older store is
#: refused instead of migrated in place: half-migrated capacity state is worse
#: than no capacity state.
AUTHORITY_SCHEMA_VERSION = 1

#: Explicit path to the authority store. There is no implicit default that
#: points at somebody else's bench — see ``default_authority_path``.
ENV_AUTHORITY_PATH = "TOLLGATE_FLEET_AUTHORITY"


class AuthorityError(RuntimeError):
    """Admission store unusable — message describes the problem, not the data."""


class NotGranted(AuthorityError):
    """Admission refused. ``reason`` is a stable machine code."""

    def __init__(self, reason: str, detail: str = "", lane_id: str = "", run_id: str = ""):
        self.reason = reason
        self.detail = detail
        self.lane_id = lane_id
        self.run_id = run_id
        super().__init__(f"admission refused ({reason})" + (f": {detail}" if detail else ""))


class Fenced(AuthorityError):
    """The caller's fencing generation is stale — another grant superseded it."""


class UnknownRun(AuthorityError):
    """No such allocation (already released, or never granted on this store)."""


class State(str, Enum):
    """Allocation lifecycle.

    ``RESERVED`` holds the slot but forbids running tests. ``ACTIVE`` means
    host admission **and** coordinator acquisition both committed.
    """

    RESERVED = "reserved"
    ACTIVE = "active"
    RELEASING = "releasing"
    RELEASED = "released"
    ABORTED = "aborted"
    CLEANUP_PENDING = "cleanup_pending"
    FENCED = "fenced"
    QUARANTINED = "quarantined"


#: States that still consume their lane's budget and conflict domains. A
#: released/aborted run frees the lane; a fenced run does not, because fencing
#: says "that token is dead", not "the DUT is clean" — cleanup does that.
OCCUPYING = frozenset(
    {State.RESERVED, State.ACTIVE, State.RELEASING, State.CLEANUP_PENDING, State.QUARANTINED}
)

#: States a run may renew or activate from.
_LIVE = frozenset({State.RESERVED, State.ACTIVE})

#: Dimensions a demand occupies. Order is fixed so error messages and
#: comparisons are stable.
DIMENSIONS = ("vcpus", "ram_mib", "disk_mib", "inodes")


@dataclass(frozen=True)
class Demand:
    """One whole-topology lane's resource vector.

    Every field is required and positive: an unknown dimension is a refusal,
    never a zero. ``vcpus`` is an int because fractional vCPU accounting is a
    fiction this authority does not want to model.
    """

    vcpus: int
    ram_mib: int
    disk_mib: int
    inodes: int = 1

    def __post_init__(self) -> None:
        for name in DIMENSIONS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"Demand.{name} must be a positive integer (got {value!r})")

    def as_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in DIMENSIONS}

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> Demand:
        missing = [name for name in DIMENSIONS if name not in raw]
        if missing:
            raise ValueError(f"Demand is missing dimension(s): {', '.join(missing)}")
        return cls(**{name: int(raw[name]) for name in DIMENSIONS})

    @classmethod
    def from_host_demand(cls, host_demand: Any) -> Demand:
        """Adapter for ``lib.fleet_capacity.HostDemand`` (same vector, no import)."""
        return cls(
            vcpus=int(host_demand.vcpus),
            ram_mib=int(host_demand.ram_mib),
            disk_mib=int(host_demand.disk_mib),
            inodes=int(host_demand.disk_inodes),
        )


@dataclass(frozen=True)
class LaneSpec:
    """A lane as the authority knows it: budget, baseline, conflicts.

    ``*_total`` is the budget the owner authorized for this lane, and
    ``baseline_*`` is consumption by things this authority does NOT manage — a
    pre-existing idle guest, another user's processes, the hypervisor itself.
    The ledger accounts for managed allocations; the baseline accounts for
    everything else, so a running allocation is never subtracted twice.
    """

    lane_id: str
    fleet_id: str
    resource_id: str
    coordinator: str = ""
    vcpus_total: int = 1
    ram_mib_total: int = 1024
    ram_mib_reserve: int = 0
    disk_mib_total: int = 4096
    inodes_total: int = 1024
    baseline_vcpus: int = 0
    baseline_ram_mib: int = 0
    baseline_disk_mib: int = 0
    baseline_inodes: int = 0
    domains: tuple[str, ...] = ()
    max_concurrent: int = 1

    def __post_init__(self) -> None:
        if not self.lane_id or not self.lane_id.strip():
            raise ValueError("LaneSpec.lane_id is required")
        if not self.resource_id:
            raise ValueError("LaneSpec.resource_id is required")
        for name in (
            "vcpus_total",
            "ram_mib_total",
            "disk_mib_total",
            "inodes_total",
            "max_concurrent",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"LaneSpec.{name} must be a positive integer (got {value!r})")
        for name in ("ram_mib_reserve", "baseline_vcpus", "baseline_ram_mib", "baseline_disk_mib", "baseline_inodes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"LaneSpec.{name} must be a non-negative integer (got {value!r})")


@dataclass(frozen=True)
class Allocation:
    """A decided claim. ``token`` and ``fence`` together authenticate it."""

    run_id: str
    lane_id: str
    token: str
    state: State
    fence: int
    demand: Demand
    domains: tuple[str, ...]
    expires_at: float
    created_at: float
    owner_host: str
    owner_pid: int | None = None
    owner_start_ticks: int | None = None
    cleanup_pending: bool = False
    note: str = ""

    @property
    def live(self) -> bool:
        return self.state in _LIVE

    @property
    def occupies(self) -> bool:
        return self.state in OCCUPYING

    def renewed_until(self, ttl_s: float) -> float:
        return self.expires_at + ttl_s


@dataclass(frozen=True)
class Decision:
    """Grant or refusal, plus what the caller needs to queue and explain."""

    granted: bool
    allocation: Allocation | None = None
    reason: str = ""
    detail: str = ""
    headroom: Mapping[str, float] = field(default_factory=dict)
    queue_position: int = 0
    lane_id: str = ""

    def __bool__(self) -> bool:  # a refusal is not a result
        return self.granted


@dataclass(frozen=True)
class LaneHeadroom:
    lane_id: str
    free: Mapping[str, float]
    used: Mapping[str, float]
    baseline: Mapping[str, float]
    allocations: tuple[str, ...] = ()
    quarantined: bool = False
    quarantine_reason: str = ""


def default_authority_path(state_dir: Path | str | None = None) -> Path:
    """Resolve the store path — explicit only.

    ``TOLLGATE_FLEET_AUTHORITY`` wins. Otherwise the caller must pass a state
    directory. There is deliberately no fallback to a global temp file: a
    silent shared path is how two benches end up believing they own each
    other's lanes.
    """
    explicit = os.environ.get(ENV_AUTHORITY_PATH, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if state_dir is None:
        raise AuthorityError(
            "no fleet authority store configured — pass state_dir= or set "
            f"{ENV_AUTHORITY_PATH} to this host's store path"
        )
    return Path(state_dir).expanduser() / "fleet-authority.sqlite3"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lanes (
    lane_id        TEXT PRIMARY KEY,
    fleet_id       TEXT NOT NULL,
    resource_id    TEXT NOT NULL,
    coordinator    TEXT NOT NULL,
    vcpus_total    INTEGER NOT NULL,
    ram_mib_total  INTEGER NOT NULL,
    ram_mib_reserve INTEGER NOT NULL,
    disk_mib_total INTEGER NOT NULL,
    inodes_total   INTEGER NOT NULL,
    baseline_vcpus INTEGER NOT NULL,
    baseline_ram_mib INTEGER NOT NULL,
    baseline_disk_mib INTEGER NOT NULL,
    baseline_inodes INTEGER NOT NULL,
    domains        TEXT NOT NULL,
    max_concurrent INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS allocations (
    run_id         TEXT PRIMARY KEY,
    lane_id        TEXT NOT NULL,
    token          TEXT NOT NULL UNIQUE,
    state          TEXT NOT NULL,
    fence          INTEGER NOT NULL,
    demand         TEXT NOT NULL,
    domains        TEXT NOT NULL,
    expires_at     REAL NOT NULL,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    owner_host     TEXT NOT NULL,
    owner_pid      INTEGER,
    owner_start_ticks INTEGER,
    cleanup_pending INTEGER NOT NULL DEFAULT 0,
    note           TEXT NOT NULL DEFAULT '',
    released_clean INTEGER
);
CREATE INDEX IF NOT EXISTS allocations_lane ON allocations (lane_id, state);
CREATE TABLE IF NOT EXISTS waiters (
    run_id      TEXT PRIMARY KEY,
    lane_id     TEXT NOT NULL,
    demand      TEXT NOT NULL,
    domains     TEXT NOT NULL,
    owner_host  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    seq         INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS quarantine (
    lane_id  TEXT PRIMARY KEY,
    reason   TEXT NOT NULL,
    detail   TEXT NOT NULL DEFAULT '',
    since    REAL NOT NULL,
    run_id   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    at      REAL NOT NULL,
    kind    TEXT NOT NULL,
    lane_id TEXT NOT NULL DEFAULT '',
    run_id  TEXT NOT NULL DEFAULT '',
    detail  TEXT NOT NULL DEFAULT ''
);
"""

_META_EPOCH = "epoch"
_META_FENCE = "fence_gen"
_META_NEEDS_RECONCILE = "needs_reconcile"
_META_SCHEMA = "schema_version"
_META_SEQ = "waiter_seq"


class FleetAuthority:
    """Serialized admission for lanes on ONE host.

    All mutating methods take ``BEGIN IMMEDIATE`` first, so the read-decide-write
    sequence cannot interleave with another process. Reads (``headroom``,
    ``allocation``, ``snapshot``) are plain queries and are safe concurrently.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        owner_host: str | None = None,
        busy_timeout_ms: int = 30_000,
    ):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._owner_host = owner_host or _hostname()
        self._busy_timeout_ms = busy_timeout_ms
        self._init_store()

    # -- plumbing ----------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.path),
            timeout=self._busy_timeout_ms / 1000.0,
            isolation_level=None,  # explicit transactions only
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(f"PRAGMA busy_timeout={int(self._busy_timeout_ms)}")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_store(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            cur = conn.execute("SELECT value FROM meta WHERE key = ?", (_META_SCHEMA,))
            row = cur.fetchone()
            if row is None:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for key, value in (
                        (_META_SCHEMA, str(AUTHORITY_SCHEMA_VERSION)),
                        (_META_EPOCH, "1"),
                        (_META_FENCE, "0"),
                        (_META_NEEDS_RECONCILE, "1"),  # fresh store: reconcile first
                        (_META_SEQ, "0"),
                    ):
                        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (key, value))
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            elif int(row["value"]) != AUTHORITY_SCHEMA_VERSION:
                raise AuthorityError(
                    f"fleet authority store {self.path} has schema_version {row['value']}, "
                    f"this code expects {AUTHORITY_SCHEMA_VERSION} — refusing to migrate capacity "
                    "state in place; archive the store and register the lanes again"
                )
        finally:
            conn.close()

    def _meta(self, conn: sqlite3.Connection, key: str) -> str:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise AuthorityError(f"fleet authority store is missing meta key {key!r}")
        return str(row["value"])

    def _set_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def _next_fence(self, conn: sqlite3.Connection) -> int:
        fence = int(self._meta(conn, _META_FENCE)) + 1
        self._set_meta(conn, _META_FENCE, str(fence))
        return fence

    def _next_seq(self, conn: sqlite3.Connection) -> int:
        seq = int(self._meta(conn, _META_SEQ)) + 1
        self._set_meta(conn, _META_SEQ, str(seq))
        return seq

    def _event(self, conn: sqlite3.Connection, kind: str, *, lane_id: str = "", run_id: str = "", detail: str = "") -> None:
        conn.execute(
            "INSERT INTO events (at, kind, lane_id, run_id, detail) VALUES (?, ?, ?, ?, ?)",
            (self._clock(), kind, lane_id, run_id, detail),
        )

    # -- lane registration -------------------------------------------------
    def register_lane(self, spec: LaneSpec) -> None:
        """Declare or re-declare a lane's budget and conflict domains.

        Re-registering refreshes the budget and baseline but never touches a
        live allocation: shrinking a budget under a running job cannot revoke
        it, it only binds the *next* admission.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO lanes (
                    lane_id, fleet_id, resource_id, coordinator,
                    vcpus_total, ram_mib_total, ram_mib_reserve, disk_mib_total, inodes_total,
                    baseline_vcpus, baseline_ram_mib, baseline_disk_mib, baseline_inodes,
                    domains, max_concurrent
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lane_id) DO UPDATE SET
                    fleet_id = excluded.fleet_id,
                    resource_id = excluded.resource_id,
                    coordinator = excluded.coordinator,
                    vcpus_total = excluded.vcpus_total,
                    ram_mib_total = excluded.ram_mib_total,
                    ram_mib_reserve = excluded.ram_mib_reserve,
                    disk_mib_total = excluded.disk_mib_total,
                    inodes_total = excluded.inodes_total,
                    baseline_vcpus = excluded.baseline_vcpus,
                    baseline_ram_mib = excluded.baseline_ram_mib,
                    baseline_disk_mib = excluded.baseline_disk_mib,
                    baseline_inodes = excluded.baseline_inodes,
                    domains = excluded.domains,
                    max_concurrent = excluded.max_concurrent
                """,
                (
                    spec.lane_id,
                    spec.fleet_id,
                    spec.resource_id,
                    spec.coordinator,
                    spec.vcpus_total,
                    spec.ram_mib_total,
                    spec.ram_mib_reserve,
                    spec.disk_mib_total,
                    spec.inodes_total,
                    spec.baseline_vcpus,
                    spec.baseline_ram_mib,
                    spec.baseline_disk_mib,
                    spec.baseline_inodes,
                    ",".join(sorted(dict.fromkeys(spec.domains))),
                    spec.max_concurrent,
                ),
            )
            self._event(conn, "lane_registered", lane_id=spec.lane_id, detail=spec.resource_id)
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _lane_row(self, conn: sqlite3.Connection, lane_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM lanes WHERE lane_id = ?", (lane_id,)).fetchone()
        if row is None:
            raise NotGranted("lane_unknown", f"lane {lane_id!r} is not registered on this authority", lane_id=lane_id)
        return row

    # -- quarantine --------------------------------------------------------
    def quarantine(self, lane_id: str, reason: str, detail: str = "", run_id: str = "") -> None:
        """Exclude a lane from admission until a human or supervisor clears it.

        Quarantine is the honest answer to "we do not know whether the old
        holder is gone": the lane is not stolen, it is taken out of service.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO quarantine (lane_id, reason, detail, since, run_id) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(lane_id) DO UPDATE SET reason = excluded.reason, detail = excluded.detail, "
                "since = excluded.since, run_id = excluded.run_id",
                (lane_id, reason, detail, self._clock(), run_id),
            )
            self._event(conn, "lane_quarantined", lane_id=lane_id, run_id=run_id, detail=f"{reason}: {detail}")
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def clear_quarantine(self, lane_id: str, *, note: str = "") -> None:
        """Return a quarantined lane to service — only after verified cleanup."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM quarantine WHERE lane_id = ?", (lane_id,))
            self._event(conn, "lane_unquarantined", lane_id=lane_id, detail=note)
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def quarantined(self) -> dict[str, tuple[str, str, float]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM quarantine").fetchall()
        finally:
            conn.close()
        return {str(r["lane_id"]): (str(r["reason"]), str(r["detail"]), float(r["since"])) for r in rows}

    # -- epoch / reconcile -------------------------------------------------
    @property
    def epoch(self) -> int:
        conn = self._connect()
        try:
            return int(self._meta(conn, _META_EPOCH))
        finally:
            conn.close()

    @property
    def fence_generation(self) -> int:
        conn = self._connect()
        try:
            return int(self._meta(conn, _META_FENCE))
        finally:
            conn.close()

    def needs_reconcile(self) -> bool:
        conn = self._connect()
        try:
            return self._meta(conn, _META_NEEDS_RECONCILE) == "1"
        finally:
            conn.close()

    def invalidate_epoch(self, reason: str = "authority_restart") -> int:
        """Bump the epoch and distrust every in-flight allocation.

        Called on host or coordinator restart. Old-epoch evidence is refused by
        the capacity layer; here we make sure no old reservation can be
        activated or renewed, and that admission is blocked until something has
        actually looked at the leftovers.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            epoch = int(self._meta(conn, _META_EPOCH)) + 1
            self._set_meta(conn, _META_EPOCH, str(epoch))
            self._set_meta(conn, _META_NEEDS_RECONCILE, "1")
            rows = conn.execute(
                "SELECT run_id, lane_id FROM allocations WHERE state IN (?, ?)",
                (State.RESERVED.value, State.ACTIVE.value),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE allocations SET state = ?, fence = ?, updated_at = ? WHERE run_id = ?",
                    (State.QUARANTINED.value, self._next_fence(conn), self._clock(), row["run_id"]),
                )
                conn.execute(
                    "INSERT INTO quarantine (lane_id, reason, detail, since, run_id) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(lane_id) DO UPDATE SET reason = excluded.reason, detail = excluded.detail, "
                    "since = excluded.since, run_id = excluded.run_id",
                    (row["lane_id"], "epoch_invalidated", reason, self._clock(), row["run_id"]),
                )
            self._event(conn, "epoch_invalidated", detail=f"{reason} -> epoch {epoch}, {len(rows)} allocation(s) quarantined")
            conn.execute("COMMIT")
            return epoch
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def reconcile(self) -> tuple[Allocation, ...]:
        """Mark the authority usable again and hand back everything it distrusts.

        Returns the allocations the caller must verify against the coordinator
        and the process table. Completing reconciliation is the caller's job —
        the store only promises not to schedule until it has been asked for.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._set_meta(conn, _META_NEEDS_RECONCILE, "0")
            rows = conn.execute(
                "SELECT * FROM allocations WHERE state IN (?, ?, ?, ?) ORDER BY created_at",
                (
                    State.RESERVED.value,
                    State.ACTIVE.value,
                    State.QUARANTINED.value,
                    State.CLEANUP_PENDING.value,
                ),
            ).fetchall()
            self._event(conn, "reconciled", detail=f"{len(rows)} allocation(s) outstanding")
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return tuple(_row_to_allocation(r) for r in rows)

    # -- headroom ----------------------------------------------------------
    def headroom(self, lane_id: str) -> LaneHeadroom:
        conn = self._connect()
        try:
            return self._headroom(conn, lane_id)
        finally:
            conn.close()

    def _headroom(self, conn: sqlite3.Connection, lane_id: str) -> LaneHeadroom:
        lane = self._lane_row(conn, lane_id)
        rows = conn.execute(
            "SELECT run_id, state, demand, domains FROM allocations WHERE lane_id = ? AND state IN (%s)"
            % ",".join("?" * len(OCCUPYING)),
            (lane_id, *[s.value for s in OCCUPYING]),
        ).fetchall()
        used = dict.fromkeys(DIMENSIONS, 0)
        for row in rows:
            demand = json.loads(row["demand"])
            for dim in DIMENSIONS:
                used[dim] += int(demand[dim])
        baseline = {
            "vcpus": int(lane["baseline_vcpus"]),
            "ram_mib": int(lane["baseline_ram_mib"]),
            "disk_mib": int(lane["baseline_disk_mib"]),
            "inodes": int(lane["baseline_inodes"]),
        }
        totals = {
            "vcpus": int(lane["vcpus_total"]),
            "ram_mib": int(lane["ram_mib_total"]) - int(lane["ram_mib_reserve"]),
            "disk_mib": int(lane["disk_mib_total"]),
            "inodes": int(lane["inodes_total"]),
        }
        free = {dim: float(totals[dim] - baseline[dim] - used[dim]) for dim in DIMENSIONS}
        q = conn.execute("SELECT reason, detail FROM quarantine WHERE lane_id = ?", (lane_id,)).fetchone()
        return LaneHeadroom(
            lane_id=lane_id,
            free=free,
            used={k: float(v) for k, v in used.items()},
            baseline={k: float(v) for k, v in baseline.items()},
            allocations=tuple(str(r["run_id"]) for r in rows),
            quarantined=q is not None,
            quarantine_reason=str(q["reason"]) if q is not None else "",
        )

    # -- the transaction ---------------------------------------------------
    def try_reserve(
        self,
        *,
        lane_id: str,
        demand: Demand,
        run_id: str | None = None,
        domains: Iterable[str] = (),
        ttl_s: float = 900.0,
        owner_pid: int | None = None,
        owner_start_ticks: int | None = None,
        note: str = "",
    ) -> Decision:
        """The one atomic admission decision.

        Every reason code below is a refusal to dispatch. There is no
        "best effort" branch: a caller that gets ``granted=False`` must not
        touch the lane.
        """
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = self._clock()
            self._expire_locked(conn, now)

            if self._meta(conn, _META_NEEDS_RECONCILE) == "1":
                conn.execute("ROLLBACK")
                return Decision(
                    False,
                    reason="authority_needs_reconcile",
                    detail="previous allocations have not been reconciled since the last epoch invalidation",
                    lane_id=lane_id,
                )

            try:
                lane = self._lane_row(conn, lane_id)
            except NotGranted as exc:
                conn.execute("ROLLBACK")
                return Decision(False, reason=exc.reason, detail=exc.detail, lane_id=lane_id)

            # Idempotency key: a caller that lost the grant's reply must be able
            # to retry the SAME run id and get the SAME claim back. Only an
            # identical request replays; a different vector under a used run id
            # is a conflict, not a retry.
            if run_id:
                existing = conn.execute("SELECT * FROM allocations WHERE run_id = ?", (run_id,)).fetchone()
                if existing is not None:
                    prior = _row_to_allocation(existing)
                    same_request = (
                        prior.lane_id == lane_id
                        and prior.demand == demand
                        and set(prior.domains) == set((*_split(lane["domains"]), *domains))
                    )
                    conn.execute("ROLLBACK")
                    if prior.state in _LIVE and same_request:
                        return Decision(
                            True,
                            allocation=prior,
                            detail="idempotent replay of an existing live reservation",
                            headroom=dict(self.headroom(lane_id).free),
                            lane_id=lane_id,
                        )
                    return Decision(
                        False,
                        reason="duplicate_run_id",
                        detail=(
                            f"run id {run_id!r} already exists in state {prior.state.value!r}"
                            + ("" if same_request else " with a different request")
                        ),
                        lane_id=lane_id,
                    )

            q = conn.execute("SELECT reason FROM quarantine WHERE lane_id = ?", (lane_id,)).fetchone()
            if q is not None:
                conn.execute("ROLLBACK")
                return Decision(
                    False,
                    reason="lane_quarantined",
                    detail=f"lane {lane_id!r} is quarantined ({q['reason']}) until its previous holder is fenced",
                    lane_id=lane_id,
                )

            lanes_domains = _split(lane["domains"])
            requested_domains = tuple(sorted(dict.fromkeys((*lanes_domains, *domains))))

            # Domain exclusion spans lanes: one power strip, one RF band or one
            # shared client radio serializes runs even in different places.
            conflict = self._domain_conflict_locked(conn, lane_id, requested_domains)
            if conflict is not None:
                position = self._enqueue_locked(conn, lane_id, demand, requested_domains, now, run_id)
                conn.execute("COMMIT")
                return Decision(
                    False,
                    reason="domain_conflict",
                    detail=f"conflict domain(s) {', '.join(conflict)} held by another allocation",
                    queue_position=position,
                    lane_id=lane_id,
                )

            room = self._headroom(conn, lane_id)
            if room.quarantined:
                conn.execute("ROLLBACK")
                return Decision(False, reason="lane_quarantined", detail=room.quarantine_reason, lane_id=lane_id)

            busy = self._occupying_count_locked(conn, lane_id)
            if busy >= int(lane["max_concurrent"]):
                position = self._enqueue_locked(conn, lane_id, demand, requested_domains, now, run_id)
                conn.execute("COMMIT")
                return Decision(
                    False,
                    reason="lane_busy",
                    detail=f"lane concurrency {busy}/{int(lane['max_concurrent'])}",
                    headroom=dict(room.free),
                    queue_position=position,
                    lane_id=lane_id,
                )

            budget = demand.as_dict()
            short = [dim for dim in DIMENSIONS if room.free[dim] < budget[dim]]
            if short:
                position = self._enqueue_locked(conn, lane_id, demand, requested_domains, now, run_id)
                conn.execute("COMMIT")
                return Decision(
                    False,
                    reason="resource_vector_insufficient",
                    detail="does not fit: " + ", ".join(f"{d} free={int(room.free[d])} need={budget[d]}" for d in short),
                    headroom=dict(room.free),
                    queue_position=position,
                    lane_id=lane_id,
                )

            granted_run_id = run_id or f"run-{uuid.uuid4().hex[:16]}"
            token = f"{self._owner_host.split('.')[0]}-{uuid.uuid4().hex[:12]}"
            fence = self._next_fence(conn)
            try:
                conn.execute(
                    """
                    INSERT INTO allocations (
                        run_id, lane_id, token, state, fence, demand, domains,
                        expires_at, created_at, updated_at, owner_host, owner_pid,
                        owner_start_ticks, cleanup_pending, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        granted_run_id,
                        lane_id,
                        token,
                        State.RESERVED.value,
                        fence,
                        json.dumps(budget, sort_keys=True),
                        ",".join(requested_domains),
                        now + float(ttl_s),
                        now,
                        now,
                        self._owner_host,
                        owner_pid,
                        owner_start_ticks,
                        note,
                    ),
                )
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                return Decision(
                    False,
                    reason="duplicate_run_id",
                    detail=f"run id {granted_run_id!r} already exists",
                    lane_id=lane_id,
                )
            conn.execute("DELETE FROM waiters WHERE run_id = ?", (granted_run_id,))
            self._event(
                conn,
                "reserved",
                lane_id=lane_id,
                run_id=granted_run_id,
                detail=f"fence={fence} domains={','.join(requested_domains) or '-'}",
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

        allocation = Allocation(
            run_id=granted_run_id,
            lane_id=lane_id,
            token=token,
            state=State.RESERVED,
            fence=fence,
            demand=demand,
            domains=requested_domains,
            expires_at=now + float(ttl_s),
            created_at=now,
            owner_host=self._owner_host,
            owner_pid=owner_pid,
            owner_start_ticks=owner_start_ticks,
            note=note,
        )
        return Decision(True, allocation=allocation, headroom={k: v for k, v in room.free.items()}, lane_id=lane_id)

    def _occupying_count_locked(self, conn: sqlite3.Connection, lane_id: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM allocations WHERE lane_id = ? AND state IN (%s)"
            % ",".join("?" * len(OCCUPYING)),
            (lane_id, *[s.value for s in OCCUPYING]),
        ).fetchone()
        return int(row["n"])

    def _domain_conflict_locked(
        self, conn: sqlite3.Connection, lane_id: str, domains: Sequence[str]
    ) -> tuple[str, ...] | None:
        if not domains:
            return None
        rows = conn.execute(
            "SELECT run_id, lane_id, domains FROM allocations WHERE state IN (%s)"
            % ",".join("?" * len(OCCUPYING)),
            tuple(s.value for s in OCCUPYING),
        ).fetchall()
        for row in rows:
            if str(row["lane_id"]) == lane_id:
                continue  # same lane is governed by the resource vector + concurrency
            shared = set(domains) & set(_split(row["domains"]))
            if shared:
                return tuple(sorted(shared))
        return None

    def _enqueue_locked(
        self,
        conn: sqlite3.Connection,
        lane_id: str,
        demand: Demand,
        domains: Sequence[str],
        now: float,
        run_id: str | None,
    ) -> int:
        """Record the loser as a waiter and report its FIFO position.

        The loser stays queued rather than being silently dropped: that is what
        makes "the loser remains queued/busy" observable instead of a retry
        storm.
        """
        waiter_id = run_id or f"wait-{uuid.uuid4().hex[:16]}"
        existing = conn.execute("SELECT seq FROM waiters WHERE run_id = ?", (waiter_id,)).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO waiters (run_id, lane_id, demand, domains, owner_host, created_at, seq) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    waiter_id,
                    lane_id,
                    json.dumps(demand.as_dict(), sort_keys=True),
                    ",".join(domains),
                    self._owner_host,
                    now,
                    self._next_seq(conn),
                ),
            )
        row = conn.execute("SELECT seq FROM waiters WHERE run_id = ?", (waiter_id,)).fetchone()
        assert row is not None
        ahead = conn.execute("SELECT COUNT(*) AS n FROM waiters WHERE lane_id = ? AND seq < ?", (lane_id, row["seq"])).fetchone()
        return int(ahead["n"]) + 1

    def queue(self, lane_id: str | None = None) -> tuple[tuple[str, str, int], ...]:
        """Waiters in FIFO order as (run_id, lane_id, position)."""
        conn = self._connect()
        try:
            if lane_id is None:
                rows = conn.execute("SELECT * FROM waiters ORDER BY lane_id, seq").fetchall()
            else:
                rows = conn.execute("SELECT * FROM waiters WHERE lane_id = ? ORDER BY seq", (lane_id,)).fetchall()
        finally:
            conn.close()
        per_lane: dict[str, int] = {}
        out: list[tuple[str, str, int]] = []
        for row in rows:
            lid = str(row["lane_id"])
            per_lane[lid] = per_lane.get(lid, 0) + 1
            out.append((str(row["run_id"]), lid, per_lane[lid]))
        return tuple(out)

    def cancel_waiter(self, run_id: str) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM waiters WHERE run_id = ?", (run_id,))
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # -- lifecycle transitions --------------------------------------------
    def allocation(self, run_id: str) -> Allocation:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM allocations WHERE run_id = ?", (run_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise UnknownRun(f"no allocation {run_id!r} on this authority")
        return _row_to_allocation(row)

    def allocations(self, *, lane_id: str | None = None, states: Iterable[State] | None = None) -> tuple[Allocation, ...]:
        sql = "SELECT * FROM allocations"
        clauses: list[str] = []
        params: list[Any] = []
        if lane_id is not None:
            clauses.append("lane_id = ?")
            params.append(lane_id)
        if states is not None:
            wanted = list(states)
            clauses.append("state IN (%s)" % ",".join("?" * len(wanted)))
            params.extend(s.value for s in wanted)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at, run_id"
        conn = self._connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
        return tuple(_row_to_allocation(r) for r in rows)

    def _authenticate_locked(self, conn: sqlite3.Connection, run_id: str, fence: int) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM allocations WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise UnknownRun(f"no allocation {run_id!r} on this authority")
        if int(row["fence"]) != int(fence):
            raise Fenced(
                f"allocation {run_id!r} has been fenced (caller holds fence {fence}, "
                f"store holds {row['fence']}) — this claim is superseded"
            )
        return row

    def activate(self, run_id: str, fence: int, *, note: str = "") -> Allocation:
        """Second phase committed: the coordinator granted the lease too.

        Only from ``RESERVED``. A stale fence, an expired claim or a
        quarantined lane all refuse — a test may not run on a partial
        allocation.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = self._clock()
            row = self._authenticate_locked(conn, run_id, fence)
            if str(row["state"]) != State.RESERVED.value:
                conn.execute("ROLLBACK")
                raise NotGranted(
                    "not_reservable",
                    f"allocation {run_id!r} is in state {row['state']!r}, not 'reserved'",
                    lane_id=str(row["lane_id"]),
                    run_id=run_id,
                )
            if float(row["expires_at"]) <= now:
                conn.execute("ROLLBACK")
                raise NotGranted("lease_expired", f"allocation {run_id!r} expired before activation", run_id=run_id)
            q = conn.execute("SELECT reason FROM quarantine WHERE lane_id = ?", (str(row["lane_id"]),)).fetchone()
            if q is not None:
                conn.execute("ROLLBACK")
                raise NotGranted("lane_quarantined", str(q["reason"]), lane_id=str(row["lane_id"]), run_id=run_id)
            conn.execute(
                "UPDATE allocations SET state = ?, updated_at = ?, note = ? WHERE run_id = ?",
                (State.ACTIVE.value, now, note or str(row["note"]), run_id),
            )
            self._event(conn, "activated", lane_id=str(row["lane_id"]), run_id=run_id, detail=note)
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self.allocation(run_id)

    def renew(self, run_id: str, fence: int, *, ttl_s: float) -> Allocation:
        """Extend a live lease. Refuses once the claim has been fenced."""
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = self._clock()
            row = self._authenticate_locked(conn, run_id, fence)
            if str(row["state"]) not in {s.value for s in _LIVE}:
                conn.execute("ROLLBACK")
                raise NotGranted("not_renewable", f"allocation {run_id!r} is {row['state']!r}", run_id=run_id)
            new_expiry = max(float(row["expires_at"]), now) + float(ttl_s)
            conn.execute(
                "UPDATE allocations SET expires_at = ?, updated_at = ? WHERE run_id = ?",
                (new_expiry, now, run_id),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self.allocation(run_id)

    def expire(self, now: float | None = None) -> tuple[Allocation, ...]:
        """Expire overdue allocations into quarantine — never into free capacity.

        A TTL says "nobody renewed", not "the old processes are gone". Handing
        an expired lane straight to the next run is the destructive
        reassignment the audit forbids, so expiry quarantines the lane and the
        allocation keeps occupying budget until it is fenced and cleaned.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            expired = self._expire_locked(conn, self._clock() if now is None else float(now))
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return tuple(expired)

    def _expire_locked(self, conn: sqlite3.Connection, now: float) -> list[Allocation]:
        rows = conn.execute(
            "SELECT * FROM allocations WHERE state IN (?, ?) AND expires_at <= ?",
            (State.RESERVED.value, State.ACTIVE.value, now),
        ).fetchall()
        out: list[Allocation] = []
        for row in rows:
            lane_id = str(row["lane_id"])
            conn.execute(
                "UPDATE allocations SET state = ?, fence = ?, updated_at = ? WHERE run_id = ?",
                (State.QUARANTINED.value, self._next_fence(conn), now, row["run_id"]),
            )
            conn.execute(
                "INSERT INTO quarantine (lane_id, reason, detail, since, run_id) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(lane_id) DO UPDATE SET reason = excluded.reason, detail = excluded.detail, "
                "since = excluded.since, run_id = excluded.run_id",
                (
                    lane_id,
                    "lease_expired",
                    f"run {row['run_id']} stopped renewing; fence before reuse",
                    now,
                    str(row["run_id"]),
                ),
            )
            self._event(conn, "lease_expired", lane_id=lane_id, run_id=str(row["run_id"]), detail="quarantined")
            out.append(_row_to_allocation(row))
        return out

    def fence(self, run_id: str, *, note: str = "") -> int:
        """Kill a claim's authority without claiming its cleanup succeeded.

        The holder's ``(token, fence)`` stops authenticating immediately, so a
        zombie that wakes up later cannot renew, activate or release anything.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state FROM allocations WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise UnknownRun(f"no allocation {run_id!r} on this authority")
            fence = self._next_fence(conn)
            conn.execute(
                "UPDATE allocations SET state = ?, fence = ?, updated_at = ?, note = ? WHERE run_id = ?",
                (State.FENCED.value, fence, self._clock(), note, run_id),
            )
            self._event(conn, "fenced", run_id=run_id, detail=f"fence={fence} {note}".strip())
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return fence

    def complete(
        self,
        run_id: str,
        fence: int,
        *,
        clean: bool,
        detail: str = "",
    ) -> Allocation:
        """Close out a run, recording release cleanliness **separately**.

        ``clean=False`` is sticky: the lane stays excluded (``CLEANUP_PENDING``)
        until a later ``complete`` with ``clean=True`` or an explicit
        ``clear_cleanup_pending`` says the cleanup actually happened. The test
        verdict never gets to write "released" over an unverified teardown.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = self._clock()
            row = self._authenticate_locked(conn, run_id, fence)
            lane_id = str(row["lane_id"])
            state = State.RELEASED if clean else State.CLEANUP_PENDING
            conn.execute(
                "UPDATE allocations SET state = ?, updated_at = ?, cleanup_pending = ?, "
                "released_clean = ?, note = ? WHERE run_id = ?",
                (state.value, now, 0 if clean else 1, 1 if clean else 0, detail or str(row["note"]), run_id),
            )
            if clean:
                # A verified clean release is what clears our own quarantine.
                conn.execute("DELETE FROM quarantine WHERE lane_id = ? AND run_id = ?", (lane_id, run_id))
            else:
                conn.execute(
                    "INSERT INTO quarantine (lane_id, reason, detail, since, run_id) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(lane_id) DO UPDATE SET reason = excluded.reason, detail = excluded.detail, "
                    "since = excluded.since, run_id = excluded.run_id",
                    (lane_id, "cleanup_failed", detail or "release did not confirm", now, run_id),
                )
            self._event(
                conn,
                "released" if clean else "cleanup_pending",
                lane_id=lane_id,
                run_id=run_id,
                detail=detail,
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self.allocation(run_id)

    def abort(self, run_id: str, fence: int, *, detail: str = "") -> Allocation:
        """Compensate a reservation that never reached the coordinator.

        Frees the lane immediately: nothing was acquired, so nothing can be
        mutating a DUT. This is the only transition allowed to free capacity
        without an explicit clean release, and it is reachable only from
        ``RESERVED``.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = self._authenticate_locked(conn, run_id, fence)
            if str(row["state"]) != State.RESERVED.value:
                conn.execute("ROLLBACK")
                raise NotGranted(
                    "not_abortable",
                    f"allocation {run_id!r} is {row['state']!r}; only an unstarted reservation "
                    "may be compensated without a verified release",
                    run_id=run_id,
                )
            conn.execute(
                "UPDATE allocations SET state = ?, updated_at = ?, note = ? WHERE run_id = ?",
                (State.ABORTED.value, self._clock(), detail or str(row["note"]), run_id),
            )
            self._event(conn, "aborted", lane_id=str(row["lane_id"]), run_id=run_id, detail=detail)
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self.allocation(run_id)

    def clear_cleanup_pending(self, run_id: str, *, detail: str = "") -> Allocation:
        """Record that a previously failed cleanup was retried and verified."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM allocations WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise UnknownRun(f"no allocation {run_id!r} on this authority")
            if str(row["state"]) != State.CLEANUP_PENDING.value:
                conn.execute("ROLLBACK")
                raise NotGranted(
                    "no_cleanup_pending",
                    f"allocation {run_id!r} is {row['state']!r}, not cleanup_pending",
                    run_id=run_id,
                )
            conn.execute(
                "UPDATE allocations SET state = ?, updated_at = ?, cleanup_pending = 0, "
                "released_clean = 1, note = ? WHERE run_id = ?",
                (State.RELEASED.value, self._clock(), detail or str(row["note"]), run_id),
            )
            conn.execute(
                "DELETE FROM quarantine WHERE lane_id = ? AND reason = ?",
                (str(row["lane_id"]), "cleanup_failed"),
            )
            self._event(conn, "cleanup_confirmed", lane_id=str(row["lane_id"]), run_id=run_id, detail=detail)
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self.allocation(run_id)

    # -- diagnostics -------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """Everything an operator or a reconcile pass needs, in one read."""
        conn = self._connect()
        try:
            lanes = [self._headroom(conn, str(r["lane_id"])) for r in conn.execute("SELECT lane_id FROM lanes ORDER BY lane_id")]
            allocations = [
                _row_to_allocation(r)
                for r in conn.execute("SELECT * FROM allocations ORDER BY created_at, run_id").fetchall()
            ]
            waiters = [
                {"run_id": str(r["run_id"]), "lane_id": str(r["lane_id"]), "seq": int(r["seq"])}
                for r in conn.execute("SELECT * FROM waiters ORDER BY lane_id, seq").fetchall()
            ]
            quarantine = {
                str(r["lane_id"]): {"reason": str(r["reason"]), "detail": str(r["detail"]), "run_id": str(r["run_id"])}
                for r in conn.execute("SELECT * FROM quarantine").fetchall()
            }
            events = [
                {
                    "seq": int(r["seq"]),
                    "at": float(r["at"]),
                    "kind": str(r["kind"]),
                    "lane_id": str(r["lane_id"]),
                    "run_id": str(r["run_id"]),
                    "detail": str(r["detail"]),
                }
                for r in conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
            ]
            return {
                "path": str(self.path),
                "epoch": int(self._meta(conn, _META_EPOCH)),
                "fence_generation": int(self._meta(conn, _META_FENCE)),
                "needs_reconcile": self._meta(conn, _META_NEEDS_RECONCILE) == "1",
                "lanes": lanes,
                "allocations": allocations,
                "waiters": waiters,
                "quarantine": quarantine,
                "events": events,
            }
        finally:
            conn.close()

    def cleanup_pending(self) -> tuple[Allocation, ...]:
        return self.allocations(states=[State.CLEANUP_PENDING])


# ===========================================================================
# helpers
# ===========================================================================
def _hostname() -> str:
    import socket

    return socket.gethostname()


def _split(raw: str | None) -> tuple[str, ...]:
    return tuple(part for part in (raw or "").split(",") if part)


def _row_to_allocation(row: sqlite3.Row) -> Allocation:
    return Allocation(
        run_id=str(row["run_id"]),
        lane_id=str(row["lane_id"]),
        token=str(row["token"]),
        state=State(str(row["state"])),
        fence=int(row["fence"]),
        demand=Demand.from_mapping(json.loads(row["demand"])),
        domains=_split(row["domains"]),
        expires_at=float(row["expires_at"]),
        created_at=float(row["created_at"]),
        owner_host=str(row["owner_host"]),
        owner_pid=int(row["owner_pid"]) if row["owner_pid"] is not None else None,
        owner_start_ticks=int(row["owner_start_ticks"]) if row["owner_start_ticks"] is not None else None,
        cleanup_pending=bool(row["cleanup_pending"]),
        note=str(row["note"] or ""),
    )
