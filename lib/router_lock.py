"""File-based advisory locking for multi-router test coordination.

PRTA-REVIVE: this module now DELEGATES to the ONE machine-global bench lease
(``lib.bench_lock.BenchLock``) instead of maintaining its own
``routers.lock`` file. Rationale (card PRTA-REVIVE, 2026-10-01): the repo had
three inconsistent lock mechanisms — the Makefile's repo-local
``hardware.lock`` presence check, ``lib/hardware_lock.py``'s /tmp JSON and
this module's ``routers.lock`` — none machine-global, none flock-based, so
two agents in two worktrees could drive the same router at the same time.

The ``router_id`` / ``phase`` / ``branch`` metadata is preserved in the bench
lease holder line's ``purpose`` field, so ``bench-lock status`` still names
what owns the bench. ``BenchBusy``/``BenchStale`` are ``RuntimeError``
subclasses, so existing ``except RuntimeError`` call sites are unchanged.
"""

import logging
from typing import Any

from lib.bench_lock import BenchLock, bench_state, read_holder

log = logging.getLogger("tollgate.router_lock")

# Kept for backwards compatibility with anything importing the constant.
_STALE_THRESHOLD = None  # the flock is the authority now; no age heuristic


class RouterLock:
    """Per-router view over the machine-global bench lease.

    Usage as context manager::

        with RouterLock(router_id="upstream", phase="mint-health-test") as lock:
            # bench is leased for this session
            ...
    """

    def __init__(self, lock_path: str | None = None) -> None:
        # lock_path is accepted for API compatibility; the bench lease is
        # machine-global by design and cannot be per-worktree.
        self._lock: BenchLock | None = None
        self._router_id: str = ""

    @property
    def lock_path(self) -> str:
        from lib.bench_lock import lock_path as _lp

        return _lp()

    @property
    def _held(self) -> bool:
        return self._lock is not None and self._lock.held

    def acquire(self, router_id: str, phase: str, branch: str = "unknown") -> None:
        """Acquire the machine-global bench lease for *router_id*.

        Raises ``RuntimeError`` (BenchBusy) if another live window holds it,
        and ``RuntimeError`` (BenchStale) if a holder line is present with no
        flock behind it — recovery is explicit via ``force_release()``.
        """
        if self._held:
            raise RuntimeError(
                f"Lock already held by this RouterLock instance ({self.lock_path})"
            )
        lock = BenchLock(purpose=f"{router_id}:{phase}")
        lock.acquire()
        self._lock = lock
        self._router_id = router_id
        log.info(
            "Acquired bench lease for router %s (phase=%s, branch=%s)",
            router_id,
            phase,
            branch,
        )

    def release(self) -> None:
        """Release the bench lease (drops the flock and clears the holder line)."""
        if not self._held:
            log.debug("release() called but lock not held")
            return
        assert self._lock is not None
        self._lock.release()
        self._lock = None
        log.info("Released bench lease (%s)", self.lock_path)

    def is_locked(self) -> bool:
        """Whether the machine-global bench lease is held (by anyone)."""
        return bench_state() == "held"

    def status(self) -> dict[str, Any]:
        """Current holder line as a dict (empty when free)."""
        holder = read_holder()
        if holder.is_empty:
            return {}
        data: dict[str, Any] = {
            "locked": self.is_locked(),
            "session": holder.profile or "?",
            "timestamp": holder.since or "",
            "phase": holder.purpose or "",
            "router_id": self._router_id or "",
            "task": holder.task or "",
        }
        if holder.pid:
            data["pid"] = holder.pid
        return data

    def force_release(self) -> None:
        """Clear a STALE holder line (no flock behind it).

        A live lease cannot be stolen: the flock drops when its holder's
        process exits, by design.
        """
        lock = BenchLock(purpose="router-lock-force-release")
        lock.acquire(reclaim_stale=True)
        lock.release()
        log.warning("Cleared stale bench holder line (%s)", self.lock_path)

    # -- context manager --

    def __enter__(self) -> "RouterLock":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if self._held:
            self.release()
        return None
