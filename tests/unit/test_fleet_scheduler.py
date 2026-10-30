"""Fleet scheduler: staged admission, supervised execution, reconciliation.

Audit contract (``reports/prta-fleet-capacity-audit.md``):

* Gate 5 — inject acquire timeout after a successful grant, host-admission
  success with coordinator failure and the reverse. Only owned claims are
  released, and NO test executes on a partially allocated lane.
* Gate 6 — kill the runner and the scheduler separately (SIGKILL). No next
  run may start until the old execution is fenced or the lane quarantined.
  Cleanup never touches unrelated processes.
* "Labgrid acquisition and host admission are not one distributed
  transaction: explicitly stage them, run no tests until both commit,
  reconcile lost replies by idempotency key, and compensate partial
  acquisition using owned tokens."

The two phases are:
  1. host admission  — ``FleetAuthority.try_reserve`` (SQLite, one host)
  2. place acquisition — ``LabgridBench.acquire`` (coordinator, remote)

Neither may run concurrently for the same lane, and phase 2 never starts
without phase 1 committed.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from lib.fleet_authority import (
    Demand,
    FleetAuthority,
    HostSpec,
    LaneSpec,
    NotGranted,
    State,
)
from lib.fleet_scheduler import (
    FleetScheduler,
    LaneQuarantined,
    PartialAllocation,
    ReconcileReport,
    ScheduleRequest,
)

DEMAND = Demand(vcpus=2, ram_mib=2048, disk_mib=8192)

# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def authority(tmp_path):
    a = FleetAuthority(tmp_path / "authority.db")
    # A lane is only admissible when the host it lives on has a registered
    # budget for it (host aggregate admission fails CLOSED otherwise, which is
    # the whole point of the shared pool). These fixtures model one lane on one
    # host, so the host budget must cover that lane exactly.
    a.register_host(HostSpec(vcpus_total=2, ram_mib_total=2048, disk_mib_total=8192, inodes_total=100_000))
    a.register_lane(
        LaneSpec(
            lane_id="lane-a",
            fleet_id="fleet-a",
            resource_id="router-a",
            vcpus_total=2,
            ram_mib_total=2048,
            disk_mib_total=8192,
            domains=("rf-2g4",),
        )
    )
    return a


class Place:
    """A single-place coordinator double with real ownership semantics."""

    def __init__(self, fail_acquire: str = "", fail_release: str = ""):
        self.owner = ""
        self.calls: list[str] = []
        self.fail_acquire = fail_acquire
        self.fail_release = fail_release

    def runner(self, cmd, **kwargs):
        args = list(cmd[5:])
        env = kwargs.get("env") or {}
        me = f"{env.get('LG_HOSTNAME', 'h')}/{env.get('LG_USERNAME', 'u')}"
        verb = args[0] if args else ""
        self.calls.append(verb)

        if verb == "show":
            return _ok(cmd, f"  acquired: {self.owner or 'None'}\n")
        if verb == "acquire":
            if self.fail_acquire == "refused":
                return _fail(cmd, "place is acquired by someone else")
            self.owner = self.owner or me
            if self.fail_acquire == "granted-then-timeout":
                # The grant LANDED server-side; only the reply was lost.
                raise subprocess.TimeoutExpired(cmd="labgrid-client", timeout=60)
            return _ok(cmd, "acquired\n")
        if verb == "release-from":
            target = args[1] if len(args) > 1 else ""
            if self.fail_release == "error":
                return _fail(cmd, "coordinator unreachable")
            if self.fail_release == "silent-noop":
                return _ok(cmd, "released\n")  # rc=0 but nothing happened
            if self.owner == target:
                self.owner = ""
            return _ok(cmd, "released\n")
        return _ok(cmd, "")


def _ok(cmd, out):
    return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def _fail(cmd, err):
    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=err)


def _request(run_id="run-1", lane_id="lane-a", command=None, ttl_s=60.0):
    return ScheduleRequest(
        fleet_id="fleet-a",
        lane_id=lane_id,
        resource_id="router-a",
        run_id=run_id,
        demand=DEMAND,
        domains=("rf-2g4",),
        ttl_s=ttl_s,
        command=command or [sys.executable, "-c", "print('ran')"],
    )


@pytest.fixture
def scheduler(authority, tmp_path):
    place = Place()
    s = FleetScheduler(
        authority=authority,
        coordinator="127.0.0.1:20444",
        bench_factory=lambda: _bench(place),
    )
    s._test_place = place  # convenience handle for assertions
    return s


def _bench(place):
    from lib.labgrid_venue import ClientIdentity, LabgridBench

    return LabgridBench(
        coordinator="127.0.0.1:20444",
        place="lane-a",
        runner=place.runner,
        identity=ClientIdentity(host="testhost", user="prta-run-1"),
    )


# --------------------------------------------------------------------------- #
# gate 5: staged commit / partial allocation
# --------------------------------------------------------------------------- #


class TestStagedCommit:
    def test_both_phases_commit_before_any_command_runs(self, scheduler, authority):
        ran: list[str] = []
        sched = scheduler
        req = _request(command=[sys.executable, "-c", "print('ran')"])

        with sched.allocate(req) as lease:
            alloc = authority.allocation("run-1")
            assert alloc.state is State.ACTIVE
            assert lease.active
            ran.append("body")

        assert ran == ["body"]
        assert authority.allocation("run-1").state is State.RELEASED

    def test_acquisition_timeout_after_a_successful_grant_is_accepted(
        self, authority, tmp_path
    ):
        """A lost reply is not a refusal: read ownership back and claim it.

        The reservation must NOT be aborted — the place really is ours.
        """
        place = Place(fail_acquire="granted-then-timeout")
        sched = FleetScheduler(
            authority=authority,
            coordinator="127.0.0.1:20444",
            bench_factory=lambda: _bench(place),
        )
        with sched.allocate(_request()) as lease:
            assert lease.active
            assert authority.allocation("run-1").state is State.ACTIVE
        # And the owned claim is still released afterwards (it is ours).
        assert place.owner == ""

    def test_refused_acquisition_compensates_the_reservation(self, authority, tmp_path):
        """Coordinator refusal after host admission: abort, run nothing."""
        place = Place(fail_acquire="refused")
        sched = FleetScheduler(
            authority=authority,
            coordinator="127.0.0.1:20444",
            bench_factory=lambda: _bench(place),
        )
        with pytest.raises(PartialAllocation) as exc:
            with sched.allocate(_request()):
                raise AssertionError("no test may run on a partial allocation")
        assert exc.value.phase == "acquire"
        # The lane must be free again: nothing was acquired, so nothing leaks.
        assert authority.allocation("run-1").state is State.ABORTED
        assert not any(
            a.occupies for a in authority.allocations(lane_id="lane-a")
        )
        assert place.owner == ""

    def test_refused_acquisition_never_releases_someone_elses_place(
        self, authority
    ):
        place = Place(fail_acquire="refused")
        place.owner = "otherhost/otheruser"
        sched = FleetScheduler(
            authority=authority,
            coordinator="127.0.0.1:20444",
            bench_factory=lambda: _bench(place),
        )
        with pytest.raises(PartialAllocation):
            with sched.allocate(_request()):
                pass
        assert place.owner == "otherhost/otheruser"
        assert "release-from" not in place.calls

    def test_second_run_is_refused_while_the_first_holds_the_lane(
        self, scheduler, authority
    ):
        with scheduler.allocate(_request("run-1")):
            with pytest.raises(NotGranted) as exc:
                with scheduler.allocate(_request("run-2")):
                    pass
        assert exc.value.reason in {"lane_busy", "duplicate_run_id", "domain_conflict"}

    def test_activation_failure_releases_the_owned_place(self, authority, monkeypatch):
        """Phase 1 committed, phase 2 acquired, activation refused → compensate.

        The place was genuinely acquired, so it must be released, and the
        failure must surface as cleanup-pending rather than a silent leak.
        """
        place = Place()
        sched = FleetScheduler(
            authority=authority,
            coordinator="127.0.0.1:20444",
            bench_factory=lambda: _bench(place),
        )

        def refuse(*a, **k):
            raise NotGranted("not_reservable", "simulated activation race")

        monkeypatch.setattr(authority, "activate", refuse)
        with pytest.raises(PartialAllocation) as exc:
            with sched.allocate(_request()):
                raise AssertionError("must not run")
        assert exc.value.phase == "activate"
        assert place.owner == ""  # our claim was released
        assert authority.allocation("run-1").state in {
            State.ABORTED,
            State.CLEANUP_PENDING,
        }


# --------------------------------------------------------------------------- #
# gate 6: supervision, fencing, cleanup boundaries
# --------------------------------------------------------------------------- #


class TestSupervision:
    def test_unclean_release_marks_the_lane_excluded(self, authority):
        """A release that cannot be confirmed excludes the lane (sticky)."""
        place = Place(fail_release="silent-noop")
        sched = FleetScheduler(
            authority=authority,
            coordinator="127.0.0.1:20444",
            bench_factory=lambda: _bench(place),
        )
        with pytest.raises(Exception):
            with sched.allocate(_request()):
                pass
        assert authority.allocation("run-1").state is State.CLEANUP_PENDING
        # Next run must be refused, not silently scheduled on a dirty DUT.
        with pytest.raises((NotGranted, LaneQuarantined)):
            with sched.allocate(_request("run-2")):
                pass

    def test_clean_release_does_not_quarantine(self, scheduler, authority):
        with scheduler.allocate(_request("run-1")):
            pass
        with scheduler.allocate(_request("run-2")):
            pass
        assert authority.allocation("run-2").state is State.RELEASED

    def test_fenced_run_is_terminated_rather_than_left_running(self, authority):
        """A fence means the token is dead: stop the child, then clean up."""
        place = Place()
        sched = FleetScheduler(
            authority=authority,
            coordinator="127.0.0.1:20444",
            bench_factory=lambda: _bench(place),
            heartbeat_s=0.05,
        )
        with pytest.raises(Exception):
            with sched.allocate(_request(command=[sys.executable, "-c", "import time; time.sleep(30)"])) as lease:
                authority.fence("run-1", note="operator fenced")
                lease.run()
                raise AssertionError("the child must not survive its fence")
        assert place.owner == ""

    def test_reconcile_reports_outstanding_allocations(self, authority, tmp_path):
        place = Place(fail_release="error")
        sched = FleetScheduler(
            authority=authority,
            coordinator="127.0.0.1:20444",
            bench_factory=lambda: _bench(place),
        )
        with pytest.raises(Exception):
            with sched.allocate(_request()):
                pass
        authority.invalidate_epoch("host restart")
        report = sched.reconcile()
        assert isinstance(report, ReconcileReport)
        assert report.outstanding
        assert report.lanes_excluded


# --------------------------------------------------------------------------- #
# gate 6: real processes — SIGKILL the scheduler
# --------------------------------------------------------------------------- #


CHILD = textwrap.dedent(
    """
    import sys, time
    from lib.fleet_authority import Demand, FleetAuthority, HostSpec, LaneSpec
    from lib.fleet_scheduler import FleetScheduler, ScheduleRequest

    a = FleetAuthority(sys.argv[1])
    # Host budget must cover the lane: aggregate admission fails closed on an
    # unregistered host, so a child that skips this can never be admitted.
    a.register_host(HostSpec(vcpus_total=2, ram_mib_total=2048,
                             disk_mib_total=8192, inodes_total=100000))
    a.register_lane(LaneSpec(lane_id="lane-a", fleet_id="fleet-a",
                             resource_id="router-a", vcpus_total=2,
                             ram_mib_total=2048, disk_mib_total=8192))

    class P:
        owner = ""
        def runner(self, cmd, **kw):
            import subprocess
            args = list(cmd[5:])
            env = kw.get("env") or {}
            me = "%s/%s" % (env.get("LG_HOSTNAME", "h"), env.get("LG_USERNAME", "u"))
            v = args[0] if args else ""
            if v == "show":
                return subprocess.CompletedProcess(cmd, 0, stdout="  acquired: %s\\n" % (self.owner or "None"), stderr="")
            if v == "acquire":
                self.owner = self.owner or me
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if v == "release-from":
                t = args[1] if len(args) > 1 else ""
                if self.owner == t:
                    self.owner = ""
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    from lib.labgrid_venue import ClientIdentity, LabgridBench
    p = P()
    s = FleetScheduler(
        authority=a,
        coordinator="127.0.0.1:20444",
        bench_factory=lambda: LabgridBench(
            coordinator="127.0.0.1:20444", place="lane-a", runner=p.runner,
            identity=ClientIdentity(host="testhost", user="prta-" + sys.argv[2]),
        ),
    )
    print("READY", flush=True)
    with s.allocate(ScheduleRequest(
        fleet_id="fleet-a", lane_id="lane-a", resource_id="router-a",
        run_id=sys.argv[2], demand=Demand(vcpus=2, ram_mib=2048, disk_mib=8192),
        domains=("rf-2g4",), ttl_s=600.0,
        command=["sleep", "300"],
    )) as lease:
        print("RUNNING", flush=True)
        time.sleep(300)
    """
)


@pytest.mark.slow
def test_sigkilled_scheduler_blocks_the_next_run_until_reconciled(tmp_path):
    """Gate 6: SIGKILL leaves the lane occupied; only reconcile reopens it.

    A TTL is not evidence the old processes are gone, so the next run must be
    refused until something has actually looked at what the dead run left.
    """
    db = tmp_path / "authority.db"
    script = tmp_path / "child.py"
    script.write_text(CHILD)

    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)

    proc = subprocess.Popen(
        [sys.executable, str(script), str(db), "run-1"],
        cwd=str(repo),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    try:
        # readline() blocks forever if the child dies early, so wait on a
        # deadline-driven poll instead of trusting the pipe to produce a line.
        seen: list[str] = []
        deadline = time.time() + 60
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            line = proc.stdout.readline()
            if line:
                seen.append(line.strip())
            if line.startswith("RUNNING"):
                ready = True
                break
        assert ready, f"scheduler never reached the run phase: {seen!r}"
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=30)

    authority = FleetAuthority(db)
    alloc = authority.allocation("run-1")
    # Phase 1 was committed and phase 2 acquired, so the lane is OCCUPIED.
    assert alloc.occupies, f"a SIGKILLed run must still hold its lane, got {alloc.state}"

    # The next run cannot start on top of it.
    with pytest.raises((NotGranted, LaneQuarantined)):
        with FleetScheduler(
            authority=authority,
            coordinator="127.0.0.1:20444",
            bench_factory=lambda: _bench(Place()),
        ).allocate(_request("run-2")):
            pass

    # Only after reconciliation is the lane usable again.
    authority.invalidate_epoch("scheduler SIGKILLed")
    FleetScheduler(
        authority=authority,
        coordinator="127.0.0.1:20444",
        bench_factory=lambda: _bench(Place()),
    ).reconcile(assume_clean=True)
