"""Bench lock — the shared GL-MT3000 bench is a single-owner resource.

Convention (kanban ``t_aa94ad3b``): ``~/.hermes/state/bench-mt3000.lock`` is held
with ``flock`` for the whole duration of a router-touching run, and carries a
human-readable holder identity line so the *next* run can say who to talk to.

Why: a stray deploy loop on the shared bench re-installed an old build three
times in one evening and silently reverted the #566 admin-board nft guard
mid-smoke-test.  An advisory lock plus a named holder is the cheap defence.

The lock is advisory but mandatory by convention: every router-touching script
in this repo (scenario tests, ``scripts/fresh-flash.py``,
``scripts/install-path-e2e.py``) acquires it and fails loudly otherwise.

Usage::

    from lib.bench_lock import BenchLock, BenchBusy

    with BenchLock(purpose="prta install-path e2e", task_id="t_a05094ad"):
        ...  # router-touching work

or from a shell::

    flock -n ~/.hermes/state/bench-mt3000.lock -c './your-router-script.sh'
"""

from __future__ import annotations

import argparse
import fcntl
import os
import platform
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_LOCK_PATH = os.path.expanduser("~/.hermes/state/bench-mt3000.lock")
ENV_LOCK_PATH = "TOLLGATE_BENCH_LOCK"
RELEASED_MARKER = "released"

#: Acquired locks keep themselves alive here.  A one-liner
#: ``BenchLock(purpose=...).acquire()`` would otherwise be garbage-collected
#: (closing the fd and silently dropping the flock) — exactly the class of
#: silent-release bug this module exists to prevent.  Entries are removed on
#: :meth:`BenchLock.release`.
_LIVE_LOCKS: list[BenchLock] = []


class BenchBusy(RuntimeError):
    """The bench lock is held by someone else; the message names the holder."""


@dataclass(frozen=True)
class Holder:
    """Parsed holder identity line."""

    raw: str = ""
    profile: str = ""
    pid: str = ""
    task: str = ""
    purpose: str = ""
    since: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.raw.strip()


def lock_path(path: str | None = None) -> str:
    return os.path.expanduser(path or os.environ.get(ENV_LOCK_PATH) or DEFAULT_LOCK_PATH)


def parse_holder(text: str) -> Holder:
    """Parse ``key=value`` tokens out of a holder line (tolerant of free text)."""
    fields: dict[str, str] = {}
    for token in (text or "").replace("\n", " ").split():
        if "=" in token:
            key, _, value = token.partition("=")
            fields[key.strip().lower()] = value.strip()
    return Holder(
        raw=(text or "").strip(),
        profile=fields.get("profile", ""),
        pid=fields.get("pid", ""),
        task=fields.get("task", ""),
        purpose=fields.get("purpose", ""),
        since=fields.get("since", ""),
    )


def holder_line(purpose: str, *, task_id: str | None = None, profile: str | None = None) -> str:
    """Build the identity line written into the lock file while held."""
    who = profile or os.environ.get("HERMES_PROFILE") or os.environ.get("USER", "unknown")
    task = task_id or os.environ.get("HERMES_TASK_ID", "")
    pieces = [
        f"profile={who}",
        f"pid={os.getpid()}",
        f"host={platform.node()}",
    ]
    if task:
        pieces.append(f"task={task}")
    pieces.append(f"purpose={purpose}")
    pieces.append(f"since={datetime.now().astimezone().isoformat(timespec='seconds')}")
    return " ".join(pieces)


def read_holder(path: str | None = None) -> Holder:
    try:
        with open(lock_path(path), encoding="utf-8") as handle:
            return parse_holder(handle.read())
    except FileNotFoundError:
        return Holder()


class BenchLock:
    """flock-based single-owner lock for the bench router.

    The flock IS the authority (it is released automatically when the process
    dies, so a dead worker can never wedge the bench); the identity line is
    written for humans reading ``cat ~/.hermes/state/bench-mt3000.lock``.
    """

    def __init__(
        self,
        *,
        purpose: str,
        task_id: str | None = None,
        profile: str | None = None,
        path: str | None = None,
    ) -> None:
        self.path = lock_path(path)
        self.purpose = purpose
        self.task_id = task_id
        self.profile = profile
        self._handle = None
        self._holder = Holder()

    # -- api ---------------------------------------------------------------

    @property
    def held(self) -> bool:
        return self._handle is not None

    def _open_locked(self):
        """Open the lock file and take the flock, or raise BenchBusy."""
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")  # kept open while held
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.seek(0)
            current = parse_holder(handle.read())
            handle.close()
            raise BenchBusy(
                "BENCH BUSY: the MT3000 bench is locked by "
                f"{current.raw or '<unknown holder>'} ({self.path}). "
                "Wait for that run to finish, or "
                f"kill its tree (pgrep -af {current.task or current.pid or '<task-id>'}) and retry. "
                f"[{'/'.join(str(exc).splitlines())}]"
            ) from exc
        return handle

    def acquire(self, *, write_holder: bool = True) -> BenchLock:
        """Take the lock or raise :class:`BenchBusy` naming the current holder."""
        if self.held:
            return self
        handle = self._open_locked()
        if not write_holder:
            handle.close()
            return self
        handle.seek(0)
        handle.truncate()
        handle.write(holder_line(self.purpose, task_id=self.task_id, profile=self.profile) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        self._holder = read_holder(self.path)
        _LIVE_LOCKS.append(self)
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            self._handle.truncate()
            self._handle.write(
                f"{RELEASED_MARKER} by {holder_line(self.purpose, task_id=self.task_id)}\n"
            )
            self._handle.flush()
        finally:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
            if self in _LIVE_LOCKS:
                _LIVE_LOCKS.remove(self)

    @property
    def holder(self) -> Holder:
        return self._holder if self.held else read_holder(self.path)

    def status(self) -> tuple[bool, Holder]:
        """Return ``(free, holder)`` without stealing the lock."""
        probe = BenchLock(purpose="status probe", path=self.path)
        try:
            probe.acquire(write_holder=False)
        except BenchBusy:
            return False, read_holder(self.path)
        else:
            return True, read_holder(self.path)

    def __enter__(self) -> BenchLock:
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bench lock helper for the shared MT3000")
    parser.add_argument("action", choices=["status", "check", "acquire"], nargs="?", default="status")
    parser.add_argument("--path", default=None, help="lock file (default ~/.hermes/state/bench-mt3000.lock)")
    parser.add_argument("--purpose", default="manual", help="purpose recorded in the holder line")
    parser.add_argument("--task", default=None, help="kanban task id recorded in the holder line")
    args = parser.parse_args(argv)

    lock = BenchLock(purpose=args.purpose, task_id=args.task, path=args.path)
    if args.action == "acquire":
        with lock:
            print(f"holding {lock.path} as: {lock.holder.raw}")
            return 0
    free, holder = lock.status()
    print(f"path   : {lock.path}")
    print(f"free   : {free}")
    print(f"holder : {holder.raw or '<none>'}")
    if args.action == "check" and not free:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
