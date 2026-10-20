"""Unit tests for lib/hardware_lock.py — the repo-root hardware mutex.

Two contracts are pinned:

1. ALWAYS: the lock file is the repo-root ``hardware.lock`` the Makefile
   gates on — Python runners (pymake, release-matrix, pytest --lock-phase)
   and ``make lock`` must exclude each other through the SAME file (the
   split-brain regression).
2. Fallback-only: when tollgate_lab is NOT installed, the standalone
   implementation (Makefile key-value format, same-user refresh, 1h
   staleness) honors the same contract.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

try:
    import tollgate_lab  # noqa: F401
    _HAS_TOLLGATE_LAB = True
except ImportError:
    _HAS_TOLLGATE_LAB = False

from lib.hardware_lock import (
    _is_stale,
    read_hardware_lock,
    is_hardware_locked,
    acquire_hardware_lock,
    release_hardware_lock,
    require_hardware_lock,
)

_fallback_only = pytest.mark.skipif(
    _HAS_TOLLGATE_LAB,
    reason="tollgate_lab installed — canonical implementation active, fallback not exercised",
)


def _write_lock(path, locked="true", session="user@host", ts=None):
    ts = ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(
        f"locked: {locked}\n"
        f"branch: test\n"
        f"worktree: /tmp\n"
        f"session: {session}\n"
        f"timestamp: {ts}\n"
        f"phase: test\n"
    )


class TestMakefileInteropContract:
    """The lock file must be the one the root Makefile checks."""

    def test_lock_file_is_repo_root_hardware_lock(self):
        import lib.hardware_lock as hl
        assert hl.HARDWARE_LOCK == Path(__file__).resolve().parents[2] / "hardware.lock"
        assert (hl.HARDWARE_LOCK.parent / "Makefile").is_file()

    @pytest.mark.skipif(not _HAS_TOLLGATE_LAB,
                        reason="tollgate_lab not installed")
    def test_canonical_module_retargeted_to_repo_lock(self):
        import tollgate_lab.hardware.lock as tl
        import lib.hardware_lock as hl
        assert tl.HARDWARE_LOCK == hl.HARDWARE_LOCK

    def test_make_lock_satisfies_python_require(self, tmp_path, monkeypatch):
        import lib.hardware_lock as hl
        lock = tmp_path / "hardware.lock"
        _write_lock(lock, session=f"{hl.os.getenv('USER', 'unknown')}@host")
        monkeypatch.setattr(hl, "HARDWARE_LOCK", lock)
        if _HAS_TOLLGATE_LAB:
            import tollgate_lab.hardware.lock as tl
            monkeypatch.setattr(tl, "HARDWARE_LOCK", lock, raising=False)
        hl.require_hardware_lock()  # must not raise


@_fallback_only
@pytest.mark.story("P11")
class TestReadHardwareLock:
    def test_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lib.hardware_lock.HARDWARE_LOCK", tmp_path / "no.lock")
        assert read_hardware_lock() is None

    def test_valid(self, tmp_path, monkeypatch):
        lock = tmp_path / "hw.lock"
        _write_lock(lock, session="alice@host1")
        monkeypatch.setattr("lib.hardware_lock.HARDWARE_LOCK", lock)
        result = read_hardware_lock()
        assert result["session"] == "alice@host1"
        assert result["locked"] == "true"

    def test_kv_lines_without_colon_ignored(self, tmp_path, monkeypatch):
        lock = tmp_path / "hw.lock"
        lock.write_text("locked: true\nnoise line\nsession: a@b\n")
        monkeypatch.setattr("lib.hardware_lock.HARDWARE_LOCK", lock)
        assert read_hardware_lock()["session"] == "a@b"


@_fallback_only
@pytest.mark.story("P11")
class TestIsStale:
    def test_fresh(self):
        ts = datetime.now(timezone.utc).isoformat()
        assert _is_stale({"timestamp": ts}) is False

    def test_old(self):
        ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        assert _is_stale({"timestamp": ts}) is True

    def test_missing(self):
        assert _is_stale({}) is True

    def test_bad_format_is_stale_not_crash(self):
        # a malformed timestamp must fail CLOSED (stale), never raise —
        # lock checks run in test setup paths where a crash is worse
        # than a stale verdict
        assert _is_stale({"timestamp": "garbage"}) is True
        assert _is_stale({"timestamp": None}) is True


@_fallback_only
@pytest.mark.story("P11")
class TestRequireSemantics:
    def test_no_lock_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lib.hardware_lock.HARDWARE_LOCK", tmp_path / "no.lock")
        with pytest.raises(RuntimeError, match="make lock"):
            require_hardware_lock()

    def test_other_user_held_raises(self, tmp_path, monkeypatch):
        lock = tmp_path / "hw.lock"
        _write_lock(lock, session="other@host")
        monkeypatch.setattr("lib.hardware_lock.HARDWARE_LOCK", lock)
        with pytest.raises(RuntimeError, match="locked by other@host"):
            require_hardware_lock()

    def test_stale_other_user_lock_passes(self, tmp_path, monkeypatch):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        lock = tmp_path / "hw.lock"
        _write_lock(lock, session="old@host", ts=old_ts)
        monkeypatch.setattr("lib.hardware_lock.HARDWARE_LOCK", lock)
        require_hardware_lock()

    def test_unlocked_flag_raises(self, tmp_path, monkeypatch):
        lock = tmp_path / "hw.lock"
        _write_lock(lock, locked="false")
        monkeypatch.setattr("lib.hardware_lock.HARDWARE_LOCK", lock)
        with pytest.raises(RuntimeError):
            require_hardware_lock()


@_fallback_only
@pytest.mark.story("P11")
class TestAcquireRelease:
    def test_acquire_creates_makefile_format(self, tmp_path, monkeypatch):
        lock = tmp_path / "hw.lock"
        monkeypatch.setattr("lib.hardware_lock.HARDWARE_LOCK", lock)
        acquire_hardware_lock("test-phase")
        assert lock.exists()
        data = read_hardware_lock()
        assert data["phase"] == "test-phase"
        assert data["locked"] == "true"
        assert "@" in data["session"]  # user@host, the format require() parses
        assert is_hardware_locked() is True

    def test_acquire_refuses_other_user(self, tmp_path, monkeypatch):
        lock = tmp_path / "hw.lock"
        _write_lock(lock, session="other@host")
        monkeypatch.setattr("lib.hardware_lock.HARDWARE_LOCK", lock)
        with pytest.raises(RuntimeError, match="held by other@host"):
            acquire_hardware_lock("competing")

    def test_acquire_same_user_refreshes(self, tmp_path, monkeypatch):
        lock = tmp_path / "hw.lock"
        monkeypatch.setattr("lib.hardware_lock.HARDWARE_LOCK", lock)
        acquire_hardware_lock("first")
        acquire_hardware_lock("second")  # same user — refresh, no raise
        assert read_hardware_lock()["phase"] == "second"

    def test_release_removes(self, tmp_path, monkeypatch):
        lock = tmp_path / "hw.lock"
        _write_lock(lock)
        monkeypatch.setattr("lib.hardware_lock.HARDWARE_LOCK", lock)
        release_hardware_lock()
        assert not lock.exists()

    def test_release_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lib.hardware_lock.HARDWARE_LOCK", tmp_path / "no.lock")
        release_hardware_lock()
