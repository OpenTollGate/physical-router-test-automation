"""File-based advisory locking for multi-router test coordination.

Prevents concurrent test sessions from modifying the same router simultaneously.
Lock file uses simple key-value format. Stale locks (>2 hours) are auto-detected.
"""

import os
import platform
import tempfile
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger("tollgate.router_lock")

_STALE_THRESHOLD = timedelta(hours=2)


def _default_lock_path() -> str:
    """Return project-root relative default lock path."""
    # Walk up from this file to find project root (has config/ dir)
    here = Path(__file__).resolve().parent
    for parent in [here] + list(here.parents):
        if (parent / "config").is_dir():
            return str(parent / "routers.lock")
    return str(here / "routers.lock")


def _session_id() -> str:
    """Return user@hostname identifier for the current session."""
    return f"{os.getenv('USER', 'unknown')}@{platform.node()}"


class RouterLock:
    """File-based advisory lock for a single router.

    Usage as context manager::

        with RouterLock(router_id="upstream", phase="mint-health-test") as lock:
            # router is locked for this session
            ...
        # lock released automatically

    Manual usage::

        lock = RouterLock(lock_path="/tmp/test.lock")
        lock.acquire(router_id="alpha", phase="deploy", branch="main")
        try:
            ...
        finally:
            lock.release()
    """

    def __init__(self, lock_path: str | None = None) -> None:
        self._lock_path: str = lock_path or _default_lock_path()
        self._held: bool = False

    @property
    def lock_path(self) -> str:
        return self._lock_path

    def acquire(self, router_id: str, phase: str, branch: str = "unknown") -> None:
        """Acquire the lock for *router_id*.

        Raises ``RuntimeError`` if the lock is already held by another session
        (and is not stale). Stale locks (>2 hours old) emit a warning but are
        overwritten.
        """
        if self._held:
            raise RuntimeError(f"Lock already held by this RouterLock instance ({self._lock_path})")

        existing = self._read_lock()
        if existing is not None:
            locked = existing.get("locked", "false").lower() == "true"
            if locked:
                ts_str = existing.get("timestamp", "")
                stale = False
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) - ts > _STALE_THRESHOLD:
                        stale = True
                except (ValueError, TypeError):
                    stale = True  # unreadable timestamp => treat as stale

                if stale:
                    log.warning(
                        "Overwriting stale lock on router %s (held by %s since %s)",
                        existing.get("router_id", "?"),
                        existing.get("session", "?"),
                        ts_str,
                    )
                else:
                    raise RuntimeError(
                        "Router '"
                        + existing.get("router_id", "?")
                        + "' is locked by "
                        + existing.get("session", "?")
                        + " since "
                        + ts_str
                        + " (phase: "
                        + existing.get("phase", "?")
                        + ", branch: "
                        + existing.get("branch", "?")
                        + "). Use force_release() or wait for the lock to expire."
                    )

        content = (
            f"locked: true\n"
            f"branch: {branch}\n"
            f"session: {_session_id()}\n"
            f"timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"phase: {phase}\n"
            f"router_id: {router_id}\n"
        )
        self._atomic_write(content)
        self._held = True
        log.info("Acquired lock on router %s (phase=%s, branch=%s)", router_id, phase, branch)

    def release(self) -> None:
        """Release the lock by removing the lock file."""
        if not self._held:
            log.debug("release() called but lock not held")
            return
        try:
            os.remove(self._lock_path)
            log.info("Released lock (%s)", self._lock_path)
        except FileNotFoundError:
            log.debug("Lock file already removed: %s", self._lock_path)
        self._held = False

    def is_locked(self) -> bool:
        """Check whether the lock file exists and indicates a held lock."""
        data = self._read_lock()
        if data is None:
            return False
        return data.get("locked", "false").lower() == "true"

    def status(self) -> dict[str, str]:
        """Return the current lock file contents as a dict.

        Returns an empty dict if no lock file exists.
        """
        data = self._read_lock()
        return data if data is not None else {}

    def force_release(self) -> None:
        """Force-remove the lock file regardless of ownership."""
        try:
            os.remove(self._lock_path)
            log.warning("Force-released lock (%s)", self._lock_path)
        except FileNotFoundError:
            log.debug("No lock file to force-release: %s", self._lock_path)
        self._held = False

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

    # -- internals --

    def _read_lock(self) -> dict[str, str] | None:
        """Parse the lock file into a dict. Returns None if file missing."""
        try:
            with open(self._lock_path) as f:
                text = f.read().strip()
        except FileNotFoundError:
            return None
        data: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            key, _, val = line.partition(":")
            data[key.strip()] = val.strip()
        return data

    def _atomic_write(self, content: str) -> None:
        """Write *content* to the lock path atomically (write temp, rename)."""
        parent = os.path.dirname(self._lock_path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".routers-lock-")
        try:
            with os.fdopen(fd, "w") as f:
                _ = f.write(content)
            os.replace(tmp_path, self._lock_path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
