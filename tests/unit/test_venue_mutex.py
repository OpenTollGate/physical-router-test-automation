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
    assert calls == ["acquire"]  # release never issued on a failed acquire


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
