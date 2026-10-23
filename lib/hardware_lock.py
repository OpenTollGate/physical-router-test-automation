"""Hardware lock — delegates to tollgate_lab.

Backward compatible: existing imports (from lib.hardware_lock import ...)
continue to work.

PRTA-REVIVE note: when tollgate_lab is NOT installed, this module now
delegates to the ONE machine-global bench lease (``lib.bench_lock.BenchLock``
via the ``scripts/hw-bench-lease`` CLI surface) instead of writing its own
/tmp JSON lock. The old /tmp lock was one of three inconsistent mechanisms
that could not stop two agents on two worktrees from driving the same router;
the bench lease is a single flock every surface shares.
"""

try:
    from tollgate_lab.hardware.lock import (
        acquire_hardware_lock,
        release_hardware_lock,
        is_hardware_locked,
        require_hardware_lock,
        read_hardware_lock,
    )
except ImportError:
    # Delegate to the machine-global bench lease (lib.bench_lock). BenchBusy
    # and BenchStale are RuntimeError subclasses, so callers that catch
    # RuntimeError (tests/conftest.py does) keep working unchanged.
    import json

    from lib.bench_lock import BenchLock, Holder, bench_state, read_holder

    _held_lock: BenchLock | None = None

    def _holder_dict(holder: Holder) -> dict:
        return {
            "session_id": holder.profile or "unknown",
            "git_branch": "",
            "timestamp": holder.since or "",
            "phase": holder.purpose or "",
            "hostname": holder.host or "",
            "task": holder.task or "",
            "pid": holder.pid or "",
            "locked": bench_state() == "held",
        }

    def read_hardware_lock():
        holder = read_holder()
        if holder.is_empty:
            return None
        return _holder_dict(holder)

    def is_hardware_locked():
        return bench_state() == "held"

    def require_hardware_lock():
        if not is_hardware_locked():
            raise RuntimeError(
                "Hardware not locked. Take the machine-global bench lease first: "
                "`make lock PHASE=\"description\"` or "
                "`scripts/hw-bench-lease exec --purpose <p> -- <cmd>`."
            )

    def acquire_hardware_lock(phase="acquired"):
        global _held_lock
        if _held_lock is not None:
            return
        lock = BenchLock(purpose=phase)
        lock.acquire()
        _held_lock = lock

    def release_hardware_lock():
        global _held_lock
        if _held_lock is None:
            return
        _held_lock.release()
        _held_lock = None

    def _dump_holder_json() -> str:
        holder = read_holder()
        return json.dumps(_holder_dict(holder), indent=2)
