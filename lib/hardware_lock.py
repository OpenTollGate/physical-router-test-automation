"""Hardware lock — the repo-root ``hardware.lock`` shared with the Makefile.

The Makefile's ``make lock`` targets and every Python runner (pymake,
release-matrix, pytest ``--lock-phase``) must gate on the SAME file, or the
two lock families silently stop excluding each other (the split-brain this
module had before: Make checked ``./hardware.lock`` while the imported
functions wrote into their install location).

tollgate_lab.hardware.lock is the canonical implementation when installed,
but it derives its lock path from its own ``__file__`` (site-packages when
not an editable install) — so we re-target its module-global
``HARDWARE_LOCK`` at this repo's ``hardware.lock`` before exporting its
functions. The standalone fallback mirrors the same contract: repo-root
path, Makefile key-value format, same-user refresh, 1h staleness.
"""

from __future__ import annotations

import contextlib
import os
import platform
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARDWARE_LOCK = _PROJECT_ROOT / "hardware.lock"
_STALE_THRESHOLD = timedelta(hours=1)


def _is_stale(data):
    try:
        ts = datetime.fromisoformat(str(data.get("timestamp", "")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    return datetime.now(timezone.utc) - ts > _STALE_THRESHOLD


def _read_kv(path: Path):
    if not path.is_file():
        return None
    data = {}
    for line in path.read_text().splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        data[key.strip()] = val.strip()
    return data or None


def _git_branch() -> str:
    try:
        out = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5, cwd=_PROJECT_ROOT,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _load_canonical():
    try:
        import tollgate_lab.hardware.lock as tl
    except ImportError:
        return None
    # Re-target the canonical implementation at this repo's Makefile-visible
    # lock file (it would otherwise use its own install location).
    tl.HARDWARE_LOCK = HARDWARE_LOCK
    tl._PROJECT_ROOT = _PROJECT_ROOT  # cosmetic: the worktree field it writes
    return tl


_tl = _load_canonical()

if _tl is not None:
    acquire_hardware_lock = _tl.acquire_hardware_lock
    release_hardware_lock = _tl.release_hardware_lock
    require_hardware_lock = _tl.require_hardware_lock
    is_hardware_locked = _tl.is_hardware_locked
    read_hardware_lock = _tl.read_hardware_lock
else:
    def read_hardware_lock():
        return _read_kv(HARDWARE_LOCK)

    def is_hardware_locked():
        data = read_hardware_lock()
        return bool(data) and data.get("locked", "false").lower() == "true"

    def _lock_user(data):
        session = data.get("session", "")
        return session.split("@")[0] if "@" in session else ""

    def require_hardware_lock():
        data = read_hardware_lock()
        if not data or data.get("locked", "false").lower() != "true":
            raise RuntimeError('Hardware not locked — run \'make lock PHASE="description"\' first')
        user = _lock_user(data)
        if user and user != os.getenv("USER", "") and not _is_stale(data):
            raise RuntimeError(
                f"Hardware locked by {data.get('session')} (phase: {data.get('phase', '?')}). "
                "Use 'make force-unlock' with caution."
            )

    def acquire_hardware_lock(phase="acquired"):
        data = read_hardware_lock()
        if data and data.get("locked", "false").lower() == "true":
            user = _lock_user(data)
            if user and user != os.getenv("USER", "") and not _is_stale(data):
                raise RuntimeError(f"Cannot acquire lock — held by {data.get('session')}")
        content = (
            "locked: true\n"
            f"branch: {_git_branch()}\n"
            f"worktree: {_PROJECT_ROOT}\n"
            f"session: {os.getenv('USER', 'unknown')}@{platform.node()}\n"
            f"timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"phase: {phase}\n"
        )
        fd, tmp = tempfile.mkstemp(dir=HARDWARE_LOCK.parent, prefix=".hardware-lock-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp, HARDWARE_LOCK)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            raise

    def release_hardware_lock():
        with contextlib.suppress(FileNotFoundError):
            HARDWARE_LOCK.unlink()
