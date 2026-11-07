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
    """A one-place coordinator: acquire/release-from/show with real ownership.

    ``show`` must answer with the ACTUAL holder, because release verifies the
    lease afterwards instead of trusting ``release-from``'s exit code.
    """
    state = {"owner": ""}

    def run(cmd, **kwargs):
        args = cmd[5:]  # strip <bin> -x <coordinator> -p <place>
        calls.append(" ".join(args))
        env = kwargs.get("env") or {}
        me = f"{env.get('LG_HOSTNAME', 'fake-host')}/{env.get('LG_USERNAME', 'fake-user')}"
        verb = args[0] if args else ""
        if verb == "show":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"  acquired: {state['owner'] or 'None'}\n", stderr=""
            )
        if fail_on and fail_on in args:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"boom: {fail_on}")
        if verb == "acquire":
            state["owner"] = state["owner"] or me
            return subprocess.CompletedProcess(cmd, 0, stdout="acquired\n", stderr="")
        if verb == "release-from":
            target = args[1] if len(args) > 1 else ""
            if state["owner"] == target:
                state["owner"] = ""
            # Real CLI exits 0 whether or not it matched.
            return subprocess.CompletedProcess(cmd, 0, stdout="released\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

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


def _verbs(calls: list[str]) -> list[str]:
    return [c.split()[0] for c in calls if c]


def test_acquires_file_lock_then_place_releases_reverse(lock_calls):
    calls: list[str] = []
    with venue_mutex(_binding(calls), phase="venu-test"):
        assert lock_calls == ["lock+ venu-test"]
        assert _verbs(calls) == ["acquire"]
    assert lock_calls == ["lock+ venu-test", "lock-"]
    # Conditional release, verified by re-reading ownership.
    assert _verbs(calls) == ["acquire", "show", "release-from", "show"]


def test_place_acquire_failure_releases_file_lock(lock_calls):
    calls: list[str] = []
    with pytest.raises(LabgridBenchError, match="x1860-1"):
        with venue_mutex(_binding(calls, fail_on="acquire"), phase="p"):
            pass
    assert lock_calls == ["lock+ p", "lock-"]
    # REGRESSION (audit 2026-10-30): a failed acquire must NEVER release. The
    # old best-effort undo could release a colleague's live acquisition; the
    # only recovery is reconciliation (read ownership back).
    assert "release" not in _verbs(calls)
    assert "release-from" not in _verbs(calls)


def test_place_release_failure_still_releases_file_lock(lock_calls):
    """A failed handback must not leak the repo lock, and must be reported.

    The teardown error is recorded on the bench rather than raised, so it can
    never overwrite the test verdict — see venue_mutex's docstring.
    """
    calls: list[str] = []
    binding = _binding(calls, fail_on="release-from")
    with venue_mutex(binding, phase="p"):
        pass
    assert lock_calls == ["lock+ p", "lock-"]
    assert binding.bench.cleanup_pending is True
    assert binding.bench.acquired is True


def test_body_exception_releases_both(lock_calls):
    calls: list[str] = []
    with pytest.raises(RuntimeError, match="scenario blew up"):
        with venue_mutex(_binding(calls), phase="p"):
            raise RuntimeError("scenario blew up")
    assert lock_calls == ["lock+ p", "lock-"]
    assert _verbs(calls) == ["acquire", "show", "release-from", "show"]


def test_teardown_failure_does_not_mask_the_body_exception(lock_calls):
    """Release status is captured separately from the test verdict."""
    calls: list[str] = []
    binding = _binding(calls, fail_on="release-from")
    with pytest.raises(RuntimeError, match="scenario blew up"):
        with venue_mutex(binding, phase="p"):
            raise RuntimeError("scenario blew up")
    assert binding.bench.cleanup_pending is True
    assert binding.bench.acquired is True


def test_teardown_failure_does_not_invent_a_failure_when_body_passed(lock_calls):
    calls: list[str] = []
    binding = _binding(calls, fail_on="release-from")
    with venue_mutex(binding, phase="p"):
        pass
    # The body succeeded, so the context exits cleanly; the unverified release
    # is reported as its own fact rather than as a fake test failure.
    assert binding.bench.cleanup_pending is True
    assert lock_calls == ["lock+ p", "lock-"]


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
        assert _verbs(calls) == ["acquire"]
    assert lock_calls == []
    assert _verbs(calls) == ["acquire", "show", "release-from", "show"]


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

    def test_failed_acquire_never_releases(self):
        """REGRESSION (audit 2026-10-30): no undo on the acquire error path.

        Releasing here would free whichever run actually holds the place when
        the failure was contention or an ambiguous timeout.
        """
        import subprocess as sp
        calls: list[str] = []
        def behavior(args):
            if "acquire" in args:
                return sp.CompletedProcess([], 1, stdout="", stderr="held")
            return sp.CompletedProcess([], 0, stdout="  acquired: None\n", stderr="")
        bench = self._bench(calls, behavior)
        with pytest.raises(LabgridBenchError):
            bench.acquire()
        verbs = [c.split()[0] for c in calls]
        assert "release" not in verbs
        assert "release-from" not in verbs
        # It reconciled instead: read ownership, saw it was not ours, refused.
        assert verbs == ["acquire", "show"]
        assert bench.acquired is False

    def test_who_returns_holder(self):
        import subprocess as sp
        def behavior(args):
            return sp.CompletedProcess([], 0, stdout="other@host\n", stderr="")
        assert self._bench([], behavior).who() == "other@host"
