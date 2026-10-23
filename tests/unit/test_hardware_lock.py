"""Unit tests for lib/hardware_lock.py — bench-lease delegation fallback.

PRTA-REVIVE: when tollgate_lab is not installed, the module delegates to the
ONE machine-global bench lease instead of writing /tmp/tollgate_hardware.lock.
These tests exercise the fallback path against a temp lease file.
"""

import os

import pytest

from lib.bench_lock import bench_state

# Import the fallback implementation directly: the tollgate_lab path (when
# installed) is that package's own contract, not this repo's.
import lib.hardware_lock as hl


@pytest.fixture(autouse=True)
def isolated_bench_lock(tmp_path, monkeypatch):
    path = tmp_path / "bench-lease.lock"
    monkeypatch.setenv("TOLLGATE_BENCH_LOCK", str(path))
    # reset any lock held by a previous test in this process
    if hl._held_lock is not None:  # noqa: SLF001 - test-only introspection
        hl._held_lock.release()
        hl._held_lock = None
    yield path


class TestReadLock:
    def test_no_file_returns_none(self):
        assert hl.read_hardware_lock() is None

    def test_valid_holder_line(self, isolated_bench_lock):
        with open(isolated_bench_lock, "w") as f:
            f.write(
                "worker-a pid=1234 purpose=smoke since=2026-10-01T10:00:00"
                " task=t_x host=bench\n"
            )
        data = hl.read_hardware_lock()
        assert data is not None
        assert data["session_id"] == "worker-a"
        assert data["phase"] == "smoke"
        assert data["pid"] == "1234"


class TestIsLocked:
    def test_free_when_no_window(self):
        assert hl.is_hardware_locked() is False

    def test_locked_while_held(self):
        hl.acquire_hardware_lock("unit-test")
        assert hl.is_hardware_locked() is True
        hl.release_hardware_lock()
        assert hl.is_hardware_locked() is False


class TestRequire:
    def test_requires_when_free(self):
        with pytest.raises(RuntimeError, match="make lock"):
            hl.require_hardware_lock()

    def test_passes_when_held(self):
        hl.acquire_hardware_lock("unit-test")
        hl.require_hardware_lock()  # no raise
        hl.release_hardware_lock()


class TestAcquireRelease:
    def test_acquire_then_release_round_trip(self, isolated_bench_lock):
        hl.acquire_hardware_lock("deploy")
        with open(isolated_bench_lock) as f:
            assert "purpose=deploy" in f.read()
        assert bench_state() == "held"
        hl.release_hardware_lock()
        assert bench_state() == "free"

    def test_acquire_busy_raises_runtime_error(self):
        import lib.bench_lock as bl

        other = bl.BenchLock(purpose="someone-else")
        other.acquire()
        try:
            with pytest.raises(RuntimeError, match="BENCH BUSY"):
                hl.acquire_hardware_lock("second-agent")
        finally:
            other.release()

    def test_release_without_acquire_is_safe(self):
        hl.release_hardware_lock()  # no raise
