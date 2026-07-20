"""Hardware lock — delegates to tollgate_lab.

Backward compatible: existing imports continue to work.
"""

try:
    from tollgate_lab.hardware.lock import (
        acquire_hardware_lock,
        release_hardware_lock,
        is_hardware_locked,
        require_hardware_lock,
        read_hardware_lock,
        _session_id,
        _git_branch,
        _is_stale,
        HARDWARE_LOCK,
        _PROJECT_ROOT,
        _STALE_THRESHOLD,
    )
    import os
    _SESSION_ID = os.environ.get("GITHUB_RUN_ID", os.environ.get("USER", "unknown"))

except ImportError:
    import os, json, platform, subprocess, tempfile
    from datetime import datetime, timezone, timedelta
    from pathlib import Path

    _PROJECT_ROOT = Path(__file__).resolve().parents[1]
    _SESSION_ID = os.environ.get("GITHUB_RUN_ID", os.environ.get("USER", "unknown"))
    HARDWARE_LOCK = Path(tempfile.gettempdir()) / "tollgate_hardware.lock"
    _STALE_THRESHOLD = timedelta(hours=1)

    def _session_id():
        return _SESSION_ID

    def _git_branch():
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=_PROJECT_ROOT, text=True, timeout=5
            ).strip()
        except Exception:
            return "unknown"

    def read_hardware_lock():
        if not HARDWARE_LOCK.exists():
            return None
        try:
            return json.loads(HARDWARE_LOCK.read_text())
        except Exception:
            return None

    def is_hardware_locked():
        data = read_hardware_lock()
        if not data:
            return False
        return not _is_stale(data)

    def _is_stale(data):
        ts = datetime.fromisoformat(data.get("timestamp", "2000-01-01T00:00:00+00:00"))
        return datetime.now(timezone.utc) - ts > _STALE_THRESHOLD

    def require_hardware_lock():
        if not is_hardware_locked():
            raise RuntimeError("Hardware not locked. Run acquire_hardware_lock first.")

    def acquire_hardware_lock(phase="acquired"):
        data = {
            "session_id": _session_id(),
            "git_branch": _git_branch(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "hostname": platform.node(),
        }
        HARDWARE_LOCK.write_text(json.dumps(data, indent=2))

    def release_hardware_lock():
        if HARDWARE_LOCK.exists():
            HARDWARE_LOCK.unlink()

__all__ = [
    "acquire_hardware_lock",
    "release_hardware_lock",
    "is_hardware_locked",
    "require_hardware_lock",
    "read_hardware_lock",
    "HARDWARE_LOCK",
    "_SESSION_ID",
]
