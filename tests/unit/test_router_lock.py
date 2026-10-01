"""Unit tests for lib/router_lock.py — RouterLock as a bench-lease delegate.

PRTA-REVIVE: RouterLock no longer writes its own ``routers.lock``; it takes
the ONE machine-global bench lease (``lib.bench_lock.BenchLock``). These tests
pin the delegation contract: same user-facing behaviours (acquire/release,
double-acquire, concurrent refusal, status, stale recovery, context manager)
under flock semantics — there is no age-based staleness any more, a dead
holder's flock is dropped by the kernel.
"""

import os

import pytest

from lib.bench_lock import bench_state
from lib.router_lock import RouterLock


@pytest.fixture(autouse=True)
def isolated_bench_lock(tmp_path, monkeypatch):
    """Point the machine-global bench lease at a temp file for this test."""
    path = tmp_path / "bench-lease.lock"
    monkeypatch.setenv("TOLLGATE_BENCH_LOCK", str(path))
    yield path


@pytest.fixture
def lock_file(isolated_bench_lock):
    """Back-compat name: the bench lease file the RouterLock delegates to."""
    return str(isolated_bench_lock)


@pytest.fixture
def lock():
    return RouterLock()


class TestAcquireRelease:
    def test_acquire_writes_holder_line(self, lock, lock_file):
        lock.acquire(router_id="upstream", phase="deploy", branch="main")
        assert os.path.isfile(lock_file)
        assert lock._held
        with open(lock_file) as f:
            text = f.read()
        # purpose carries the router metadata: "<router_id>:<phase>"
        assert "upstream:deploy" in text
        assert "pid=" in text

    def test_release_clears_holder(self, lock, lock_file):
        lock.acquire(router_id="upstream", phase="deploy")
        lock.release()
        assert not lock._held
        with open(lock_file) as f:
            assert f.read().strip() == ""
        assert bench_state() == "free"

    def test_release_without_acquire_is_safe(self, lock):
        lock.release()  # should not raise

    def test_lock_is_machine_global(self, lock):
        """Two RouterLock instances (e.g. two worktrees) contend on ONE lease."""
        lock.acquire(router_id="alpha", phase="first")
        second = RouterLock()
        with pytest.raises(RuntimeError, match="BENCH BUSY"):
            second.acquire(router_id="alpha", phase="second")
        lock.release()

    def test_double_acquire_raises(self, lock):
        lock.acquire(router_id="alpha", phase="one")
        with pytest.raises(RuntimeError):
            lock.acquire(router_id="alpha", phase="two")
        lock.release()


class TestStatus:
    def test_status_while_held(self, lock):
        lock.acquire(router_id="alpha", phase="mint-health-test")
        status = lock.status()
        assert status["locked"] is True
        assert "alpha:mint-health-test" in status["phase"]
        lock.release()

    def test_status_empty_when_free(self, lock):
        assert lock.status() == {}

    def test_is_locked(self, lock):
        assert lock.is_locked() is False
        lock.acquire(router_id="alpha", phase="p")
        assert lock.is_locked() is True
        lock.release()
        assert lock.is_locked() is False


class TestStaleRecovery:
    def test_stale_holder_refuses_then_force_release(self, lock_file):
        with open(lock_file, "w") as f:
            f.write(
                "ghost pid=4128000 purpose=alpha:dead since=2026-06-01T00:00:00"
                " task=- host=bench\n"
            )
        lock = RouterLock()
        # flock is free but the holder line is live metadata -> explicit refusal
        with pytest.raises(RuntimeError, match="BENCH STALE"):
            lock.acquire(router_id="alpha", phase="next")
        lock.force_release()
        with open(lock_file) as f:
            assert f.read().strip() == ""
        # and the next acquire succeeds
        lock.acquire(router_id="alpha", phase="next")
        lock.release()

    def test_force_release_no_file_is_safe(self, lock):
        lock.force_release()  # should not raise

    def test_live_lock_cannot_be_stolen_by_force_release(self, lock):
        lock.acquire(router_id="alpha", phase="live")
        other = RouterLock()
        # force_release may only clear STALE lines; a live flock is refused
        with pytest.raises(RuntimeError, match="BENCH BUSY"):
            other.force_release()
        assert lock.is_locked() is True
        lock.release()


class TestContextManager:
    def test_context_manager_acquires_and_releases(self):
        with RouterLock() as lock:
            lock.acquire(router_id="alpha", phase="cm")
            assert lock.is_locked() is True
        assert lock.is_locked() is False

    def test_context_manager_releases_on_exception(self):
        lock = RouterLock()
        with pytest.raises(ValueError):
            with lock:
                lock.acquire(router_id="alpha", phase="boom")
                raise ValueError("x")
        assert not lock._held
