"""Unit tests for the venue transition invariant (lib.labgrid_venue.venue_mutex).

The invariant: a venue session holds BOTH the repo file lock and the labgrid
place — file lock first, released in reverse — so venue sessions and legacy
Make/pymake participants (file lock only) exclude each other until #232's
exit criteria retire the lock layer. These tests run with a fake
labgrid-client runner and monkeypatched lock functions; no coordinator.
"""
from __future__ import annotations

import subprocess

import pytest

from lib.lab_inventory import RouterEntry
from lib.labgrid_venue import LabgridBench, LabgridBenchError, VenueBinding, venue_mutex


def _fake_runner(calls: list[str], fail_on: str = ""):
    def run(cmd, **kwargs):
        args = cmd[5:]  # strip <bin> -x <coordinator> -p <place>
        calls.append(" ".join(args))
        rc = 1 if fail_on and fail_on in args else 0
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr=f"boom: {fail_on}")
    return run


def _binding(calls: list[str], fail_on: str = "") -> VenueBinding:
    bench = LabgridBench(
        coordinator="fake:20408",
        place="x1860-1",
        runner=_fake_runner(calls, fail_on),
    )
    entry = RouterEntry(name="x1860-1", poe_port="3", address="192.0.2.10", place="x1860-1")
    return VenueBinding(bench=bench, entry=entry)


@pytest.fixture
def lock_calls(monkeypatch):
    calls: list[str] = []

    def fake_acquire(phase="acquired"):
        calls.append(f"lock+ {phase}")

    def fake_release():
        calls.append("lock-")

    monkeypatch.setattr("lib.labgrid_venue.hardware_lock.acquire_hardware_lock", fake_acquire)
    monkeypatch.setattr("lib.labgrid_venue.hardware_lock.release_hardware_lock", fake_release)
    return calls


def test_acquires_file_lock_then_place_releases_reverse(lock_calls):
    calls: list[str] = []
    with venue_mutex(_binding(calls), phase="venu-test"):
        assert lock_calls == ["lock+ venu-test"]
        assert calls == ["acquire"]
    assert lock_calls == ["lock+ venu-test", "lock-"]
    assert calls == ["acquire", "release"]


def test_place_acquire_failure_releases_file_lock(lock_calls):
    calls: list[str] = []
    with pytest.raises(LabgridBenchError, match="x1860-1"):
        with venue_mutex(_binding(calls, fail_on="acquire"), phase="p"):
            pass
    assert lock_calls == ["lock+ p", "lock-"]
    # best-effort undo: a possibly-granted acquire is released before failing
    assert calls == ["acquire", "release"]


def test_place_release_failure_still_releases_file_lock(lock_calls):
    calls: list[str] = []
    with pytest.raises(LabgridBenchError):
        with venue_mutex(_binding(calls, fail_on="release"), phase="p"):
            pass
    assert lock_calls == ["lock+ p", "lock-"]


def test_body_exception_releases_both(lock_calls):
    calls: list[str] = []
    with pytest.raises(RuntimeError, match="scenario blew up"):
        with venue_mutex(_binding(calls), phase="p"):
            raise RuntimeError("scenario blew up")
    assert lock_calls == ["lock+ p", "lock-"]
    assert calls == ["acquire", "release"]


def test_file_lock_failure_never_touches_coordinator(monkeypatch):
    def refused(phase="acquired"):
        raise RuntimeError("Cannot acquire lock — held by other@host")

    monkeypatch.setattr("lib.labgrid_venue.hardware_lock.acquire_hardware_lock", refused)
    calls: list[str] = []
    with pytest.raises(RuntimeError, match="held by other@host"):
        with venue_mutex(_binding(calls), phase="p"):
            pass
    assert calls == []  # no coordinator round-trip when the local mutex refuses


def test_file_lock_false_skips_repo_lock_entirely(lock_calls):
    # Sessions already holding the lock via --lock-phase/TOLLGATE_USE_HARDWARE_LOCK:
    # the venue takes only the place, so the existing holder keeps acquire/release.
    calls: list[str] = []
    with venue_mutex(_binding(calls), phase="p", file_lock=False):
        assert lock_calls == []
        assert calls == ["acquire"]
    assert lock_calls == []
    assert calls == ["acquire", "release"]


class TestClientFailureContracts:
    def _bench(self, calls, behavior):
        def run(cmd, **kwargs):
            args = cmd[5:]
            calls.append(" ".join(args))
            return behavior(args)
        return LabgridBench(coordinator="fake:20408", place="x1860-1", runner=run)

    def test_timeout_becomes_labgrid_error(self):
        import subprocess as sp
        def behavior(args):
            if "acquire" in args:
                raise sp.TimeoutExpired(cmd="labgrid-client", timeout=60)
            return sp.CompletedProcess([], 0, stdout="", stderr="")
        bench = self._bench([], behavior)
        with pytest.raises(LabgridBenchError, match="timed out"):
            bench.acquire()

    def test_failed_acquire_attempts_best_effort_release(self):
        import subprocess as sp
        calls: list[str] = []
        def behavior(args):
            if "acquire" in args:
                return sp.CompletedProcess([], 1, stdout="", stderr="held")
            return sp.CompletedProcess([], 0, stdout="", stderr="")
        bench = self._bench(calls, behavior)
        with pytest.raises(LabgridBenchError):
            bench.acquire()
        assert calls == ["acquire", "release"]  # the possibly-granted place is undone

    def test_who_returns_holder(self):
        import subprocess as sp
        def behavior(args):
            return sp.CompletedProcess([], 0, stdout="other@host\n", stderr="")
        assert self._bench([], behavior).who() == "other@host"
