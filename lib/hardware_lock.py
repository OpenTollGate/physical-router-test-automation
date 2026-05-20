"""Hardware mutex compatible with root Makefile hardware.lock."""

from __future__ import annotations

import os
import platform
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from lib.router_lock import _STALE_THRESHOLD

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARDWARE_LOCK = _PROJECT_ROOT / "hardware.lock"


def _session_id() -> str:
    return f"{os.getenv('USER', 'unknown')}@{platform.node()}"


def _git_branch() -> str:
    try:
        out = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=_PROJECT_ROOT,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def read_hardware_lock() -> dict[str, str] | None:
    if not HARDWARE_LOCK.is_file():
        return None
    data: dict[str, str] = {}
    for line in HARDWARE_LOCK.read_text().splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        data[key.strip()] = val.strip()
    return data


def is_hardware_locked() -> bool:
    data = read_hardware_lock()
    if not data:
        return False
    return data.get("locked", "false").lower() == "true"


def _is_stale(data: dict[str, str]) -> bool:
    ts_str = data.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - ts > _STALE_THRESHOLD
    except (ValueError, TypeError):
        return True


def require_hardware_lock() -> None:
    """Raise if hardware.lock is missing or held by another user."""
    data = read_hardware_lock()
    if not data or data.get("locked", "false").lower() != "true":
        raise RuntimeError(
            "Hardware not locked — run 'make lock PHASE=\"description\"' first"
        )
    session = data.get("session", "")
    user = session.split("@")[0] if "@" in session else ""
    if user and user != os.getenv("USER", "") and not _is_stale(data):
        raise RuntimeError(
            f"Hardware locked by {session} (phase: {data.get('phase', '?')}). "
            "Use 'make force-unlock' with caution."
        )


def acquire_hardware_lock(phase: str) -> None:
    """Acquire or refresh hardware.lock (same user may refresh)."""
    data = read_hardware_lock()
    if data and data.get("locked", "false").lower() == "true":
        session = data.get("session", "")
        user = session.split("@")[0] if "@" in session else ""
        if user and user != os.getenv("USER", "") and not _is_stale(data):
            raise RuntimeError(f"Cannot acquire lock — held by {session}")

    content = (
        "locked: true\n"
        f"branch: {_git_branch()}\n"
        f"worktree: {_PROJECT_ROOT}\n"
        f"session: {_session_id()}\n"
        f"timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"phase: {phase}\n"
    )
    parent = HARDWARE_LOCK.parent
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".hardware-lock-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, HARDWARE_LOCK)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def release_hardware_lock() -> None:
    try:
        HARDWARE_LOCK.unlink()
    except FileNotFoundError:
        pass
