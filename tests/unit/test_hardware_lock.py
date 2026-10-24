"""Unit tests for lib/hardware_lock.py — bench-lease delegation fallback.

PRTA-REVIVE: when tollgate_lab is not installed, the module delegates to the
ONE machine-global bench lease instead of writing /tmp/tollgate_hardware.lock.
These tests exercise the fallback path against a temp lease file.

The fallback is loaded DETERMINISTICALLY: CI installs tollgate-lab
(requirements.txt), which flips lib.hardware_lock onto its delegating branch
and leaves the fallback branch (and its module global) undefined on that
host. Blocking the tollgate_lab import for one private module instance keeps
the code under test being THIS repo's fallback contract everywhere, without
mutating sys.modules for the rest of the process or touching the real
machine lease (each test pins TOLLGATE_BENCH_LOCK to a tmp path).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

import lib.hardware_lock as _hl_on_disk
from lib.bench_lock import bench_state


def _load_fallback_instance():
    spec = importlib.util.spec_from_file_location(
        "hardware_lock_fallback_under_test", Path(_hl_on_disk.__file__)
    )
    module = importlib.util.module_from_spec(spec)
    # None in sys.modules makes `import tollgate_lab` raise ImportError,
    # which is exactly the branch these tests exist for.
    tollgate_lab_modules = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "tollgate_lab" or name.startswith("tollgate_lab.")
    }
    blocked = {"tollgate_lab": None}
    blocked.update({name: None for name in tollgate_lab_modules})
    saved = dict(tollgate_lab_modules)
    for name in tollgate_lab_modules:
        del sys.modules[name]
    sys.modules.update(blocked)
    try:
        spec.loader.exec_module(module)
    finally:
        for name in blocked:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
    return module


hl = _load_fallback_instance()


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
