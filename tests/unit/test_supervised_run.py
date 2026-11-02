"""Supervised execution must be provable, not asserted.

Every test here uses a **real** SQLite ``FleetAuthority`` and a harmless
Python child. Nothing acquires a labgrid place, and nothing touches a router:
the place belongs to the pytest venue fixture (``tests/conftest.py``), and
re-acquiring it here would recreate the double-ownership bug this milestone
exists to remove.

``test_plain_tee_run_renews_nothing_*`` is the characterisation test for the
defect: today's ``tee_run`` renews zero times, so a child that outlives the TTL
loses the reservation without anyone noticing. The remaining tests are the RED
half — they fail until ``lib.supervised_run`` exists.
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

from lib.fleet_authority import Demand, FleetAuthority, HostSpec, LaneSpec, State

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def _module():
    """Import the module under test per test, so RED failures are per-test."""
    return importlib.import_module("lib.supervised_run")


def _runner():
    spec = importlib.util.spec_from_file_location("supervised_runner", ROOT / "scripts" / "run-profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _authority(tmp_path, *, max_concurrent: int = 1) -> FleetAuthority:
    auth = FleetAuthority(tmp_path / "authority.db")
    auth.reconcile()
    auth.register_host(HostSpec(vcpus_total=4, ram_mib_total=8192, disk_mib_total=65536, inodes_total=100_000))
    auth.register_lane(
        LaneSpec(
            lane_id="lane-a",
            fleet_id="fleet-a",
            resource_id="router-a",
            vcpus_total=4,
            ram_mib_total=8192,
            disk_mib_total=65536,
            max_concurrent=max_concurrent,
        )
    )
    return auth


def _activate(auth: FleetAuthority, *, ttl_s: float, run_id: str = "run-1", fence: int | None = None):
    decision = auth.try_reserve(
        lane_id="lane-a",
        demand=Demand(vcpus=2, ram_mib=1024, disk_mib=4096, inodes=1),
        run_id=run_id,
        ttl_s=ttl_s,
    )
    assert decision.granted, decision.reason
    assert decision.allocation is not None
    return auth.activate(run_id, decision.allocation.fence)


def _child(script: str) -> list[str]:
    return [sys.executable, "-c", script]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for(path: Path, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.01)
    return False


# --------------------------------------------------------------------------- #
# characterisation of the defect: plain tee_run is unsupervised
# --------------------------------------------------------------------------- #
def test_plain_tee_run_renews_nothing_and_lets_the_lease_expire(tmp_path, monkeypatch):
    runner = _runner()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=0.2)
    before = auth.allocation("run-1").expires_at

    calls: list[str] = []
    real_renew = FleetAuthority.renew

    def counting_renew(self, run_id, fence, *, ttl_s):
        calls.append(run_id)
        return real_renew(self, run_id, fence, ttl_s=ttl_s)

    monkeypatch.setattr(FleetAuthority, "renew", counting_renew)

    rc = runner.tee_run(
        _child("import time; time.sleep(0.5); print('finished after TTL')"),
        cwd=tmp_path,
        env=dict(os.environ),
        log_path=tmp_path / "output.log",
        dry_run=False,
    )

    assert rc == 0
    assert calls == [], "plain tee_run renewed the lease — this test is the defect record"
    assert auth.allocation("run-1").expires_at == before
    # The child outlived the TTL and nobody renewed: the lane quarantines.
    expired = auth.expire()
    assert [a.run_id for a in expired] == ["run-1"]
    assert auth.allocation("run-1").state is State.QUARANTINED
    assert auth.quarantined()["lane-a"][0] == "lease_expired"
    assert allocation.state is State.ACTIVE  # stale snapshot: the runner never noticed


# --------------------------------------------------------------------------- #
# heartbeats across a TTL shorter than the child's lifetime
# --------------------------------------------------------------------------- #
def test_heartbeats_span_a_ttl_shorter_than_the_child(tmp_path, capsys):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=0.4)
    log = tmp_path / "nested" / "output.log"

    outcome = m.run_supervised(
        _child("import time; print('hello'); time.sleep(1.2); print('bye')"),
        cwd=tmp_path,
        env=dict(os.environ),
        log_path=log,
        dry_run=False,
        authority=auth,
        run_id="run-1",
        fence=allocation.fence,
        ttl_s=0.4,
        heartbeat_s=0.08,
    )

    assert outcome.exit_code == 0
    assert outcome.cleanup is m.Cleanup.CONFIRMED
    assert outcome.cleanup_confirmed is True
    assert outcome.renewals >= 5, outcome
    assert outcome.fence_checks >= outcome.renewals
    held = auth.allocation("run-1")
    assert held.state is State.ACTIVE, "the lease must still be ours after the child finished"
    assert held.expires_at > allocation.expires_at
    assert auth.expire() == (), "a renewed lease must not be quarantined"
    assert log.read_text(encoding="utf-8").splitlines() == ["hello", "bye"]
    assert "hello" in capsys.readouterr().out


def test_supervised_tee_run_returns_the_child_status_and_tees_the_log(tmp_path, capsys):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=30)
    log = tmp_path / "out.log"

    rc = m.supervised_tee_run(
        _child("print('line one'); raise SystemExit(7)"),
        cwd=tmp_path,
        env=dict(os.environ),
        log_path=log,
        dry_run=False,
        authority=auth,
        run_id="run-1",
        fence=allocation.fence,
        ttl_s=30,
        heartbeat_s=1.0,
    )

    assert rc == 7
    text = log.read_text(encoding="utf-8")
    assert text.strip() == "line one"
    out = capsys.readouterr().out
    assert "line one" in out and "==> Command:" in out
    assert auth.allocation("run-1").state is State.ACTIVE


def test_dry_run_spawns_nothing_and_renews_nothing(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    _activate(auth, ttl_s=30)
    renewals: list[int] = []

    class Recorder:
        def renew(self, run_id, fence, *, ttl_s):
            renewals.append(fence)
            raise AssertionError("dry run must not touch the authority")

        def allocation(self, run_id):
            raise AssertionError("dry run must not touch the authority")

    log = tmp_path / "dry.log"
    outcome = m.run_supervised(
        _child("raise SystemExit(9)"),
        cwd=tmp_path,
        env=dict(os.environ),
        log_path=log,
        dry_run=True,
        authority=Recorder(),
        run_id="run-1",
        fence=1,
        ttl_s=30,
        heartbeat_s=1.0,
    )

    assert outcome.exit_code == 0
    assert outcome.renewals == 0
    assert renewals == []
    assert not log.exists()


def test_missing_authority_is_refused_not_ignored(tmp_path):
    m = _module()
    with pytest.raises(m.MissingAuthority):
        m.run_supervised(
            _child("print('must not run')"),
            cwd=tmp_path,
            env=dict(os.environ),
            log_path=tmp_path / "x.log",
            dry_run=False,
            authority=None,
            run_id="run-1",
            fence=1,
            ttl_s=30,
            heartbeat_s=1.0,
        )


def test_heartbeat_must_be_shorter_than_the_ttl(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=1.0)
    with pytest.raises(ValueError):
        m.run_supervised(
            _child("print('x')"),
            cwd=tmp_path,
            env=dict(os.environ),
            log_path=tmp_path / "x.log",
            dry_run=False,
            authority=auth,
            run_id="run-1",
            fence=allocation.fence,
            ttl_s=1.0,
            heartbeat_s=1.0,
        )


# --------------------------------------------------------------------------- #
# renewal failure is fatal: kill the group, raise, never run on unaccounted
# --------------------------------------------------------------------------- #
def test_renewal_failure_kills_the_child_and_raises(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=0.3)
    pid_file = tmp_path / "child.pid"

    class FailingRenew:
        """Real store, but renew reports a broken authority after one success."""

        def __init__(self):
            self.calls = 0

        def renew(self, run_id, fence, *, ttl_s):
            self.calls += 1
            if self.calls >= 2:
                # Do not race the child's startup: the point of this test is
                # what happens to a *running* child when renewal dies.
                _wait_for(pid_file)
                raise sqlite3.OperationalError("disk I/O error")
            return auth.renew(run_id, fence, ttl_s=ttl_s)

        def allocation(self, run_id):
            return auth.allocation(run_id)

    authority = FailingRenew()
    start = time.monotonic()
    with pytest.raises(m.RenewalFailed) as excinfo:
        m.run_supervised(
            _child(
                f"import os, pathlib, time;pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));time.sleep(10)"
            ),
            cwd=tmp_path,
            env=dict(os.environ),
            log_path=tmp_path / "out.log",
            dry_run=False,
            authority=authority,
            run_id="run-1",
            fence=allocation.fence,
            ttl_s=0.3,
            heartbeat_s=0.05,
            terminate_grace_s=2.0,
        )

    elapsed = time.monotonic() - start
    assert elapsed < 8.0, "the supervisor waited for the child instead of killing it"
    assert excinfo.value.reason == "renewal_failed"
    assert excinfo.value.exit_code == m.EXIT_RENEWAL_FAILED
    assert _wait_for(pid_file), "child never started; the test would pass vacuously"
    pid = int(pid_file.read_text())
    assert not _pid_alive(pid), "the child survived a fatal renewal failure"
    outcome = excinfo.value.outcome
    assert outcome is not None
    assert outcome.cleanup is m.Cleanup.CONFIRMED
    assert authority.calls >= 2


def test_renewal_failure_escalates_to_sigkill_when_sigterm_is_ignored(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=0.3)
    pid_file = tmp_path / "child.pid"
    signals: list[int] = []

    real_killpg = os.killpg

    def recording_killpg(pgid, sig):
        signals.append(sig)
        return real_killpg(pgid, sig)

    class FailingRenew:
        calls = 0

        def renew(self, run_id, fence, *, ttl_s):
            type(self).calls += 1
            if not pid_file.exists():
                # The child has not installed its handler yet; a real authority
                # would still be healthy here. Only then does it "go bad".
                return auth.renew(run_id, fence, ttl_s=ttl_s)
            raise sqlite3.OperationalError("store unavailable")

        def allocation(self, run_id):
            return auth.allocation(run_id)

    with pytest.raises(m.RenewalFailed) as excinfo:
        m.run_supervised(
            _child(
                "import os, pathlib, signal, time;"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));"
                "print('ignoring SIGTERM', flush=True);"
                "time.sleep(10)"
            ),
            cwd=tmp_path,
            env=dict(os.environ),
            log_path=tmp_path / "out.log",
            dry_run=False,
            authority=FailingRenew(),
            run_id="run-1",
            fence=allocation.fence,
            ttl_s=0.3,
            heartbeat_s=0.05,
            terminate_grace_s=0.3,
            kill_group=recording_killpg,
        )

    pid = int(pid_file.read_text())
    assert not _pid_alive(pid)
    assert signals.count(15) >= 1 and signals.count(9) >= 1, signals
    assert excinfo.value.outcome.cleanup is m.Cleanup.CONFIRMED


# --------------------------------------------------------------------------- #
# fence validity: authority loss terminates the child
# --------------------------------------------------------------------------- #
def test_authority_loss_fences_the_child(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=0.6)
    pid_file = tmp_path / "child.pid"

    def steal():
        # Fence only once the child is provably running: the point is what
        # happens to a *live* child, not to a race with its startup.
        assert _wait_for(pid_file)
        auth.fence("run-1", note="operator took the lane")

    thief = threading.Thread(target=steal, daemon=True)
    thief.start()

    with pytest.raises(m.AuthorityLost) as excinfo:
        m.run_supervised(
            _child(
                f"import os, pathlib, time;pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));time.sleep(10)"
            ),
            cwd=tmp_path,
            env=dict(os.environ),
            log_path=tmp_path / "out.log",
            dry_run=False,
            authority=auth,
            run_id="run-1",
            fence=allocation.fence,
            ttl_s=0.6,
            heartbeat_s=0.05,
            terminate_grace_s=2.0,
        )
    thief.join(timeout=5)

    assert excinfo.value.reason == "authority_lost"
    assert excinfo.value.exit_code == m.EXIT_SUPERVISION_LOST
    pid = int(pid_file.read_text())
    assert not _pid_alive(pid), "a fenced run kept mutating the DUT"
    assert auth.allocation("run-1").state is State.FENCED
    assert excinfo.value.outcome.cleanup is m.Cleanup.CONFIRMED


def test_assert_authority_valid_refuses_a_superseded_fence(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=30)
    auth.fence("run-1", note="superseded")
    with pytest.raises(m.AuthorityLost) as excinfo:
        m.assert_authority_valid(auth, "run-1", allocation.fence)
    assert "finish" in str(excinfo.value) or "fenced" in str(excinfo.value).lower()


def test_assert_authority_valid_refuses_a_closed_allocation(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=30)
    auth.complete("run-1", allocation.fence, clean=True)
    with pytest.raises(m.AuthorityLost) as excinfo:
        m.assert_authority_valid(auth, "run-1", allocation.fence)
    assert "not live" in str(excinfo.value) or "released" in str(excinfo.value)


def test_assert_authority_valid_refuses_an_unknown_run(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    with pytest.raises(m.AuthorityLost) as excinfo:
        m.assert_authority_valid(auth, "never-granted", 1)
    assert "no live claim" in str(excinfo.value)


def test_assert_authority_valid_refuses_an_expired_lease(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=5)
    with pytest.raises(m.AuthorityLost) as excinfo:
        m.assert_authority_valid(auth, "run-1", allocation.fence, now=lambda: allocation.expires_at + 1)
    assert "expire" in str(excinfo.value).lower()


def test_a_lost_claim_is_detected_before_the_child_spawns(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=30)
    auth.fence("run-1", note="already gone")
    marker = tmp_path / "spawned"
    with pytest.raises(m.AuthorityLost):
        m.run_supervised(
            _child(f"import pathlib; pathlib.Path({str(marker)!r}).touch()"),
            cwd=tmp_path,
            env=dict(os.environ),
            log_path=tmp_path / "out.log",
            dry_run=False,
            authority=auth,
            run_id="run-1",
            fence=allocation.fence,
            ttl_s=30,
            heartbeat_s=1.0,
        )
    assert not marker.exists()


# --------------------------------------------------------------------------- #
# cleanup reporting: CONFIRMED vs unconfirmed (so the caller can mark pending)
# --------------------------------------------------------------------------- #
def test_unconfirmed_teardown_is_reported_not_claimed_clean(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=30)

    outcome = m.run_supervised(
        _child("print('done')"),
        cwd=tmp_path,
        env=dict(os.environ),
        log_path=tmp_path / "out.log",
        dry_run=False,
        authority=auth,
        run_id="run-1",
        fence=allocation.fence,
        ttl_s=30,
        heartbeat_s=1.0,
        terminate_grace_s=0.2,
        group_alive=lambda pgid: True,  # the probe says the group never dies
        kill_group=lambda pgid, sig: None,
    )

    assert outcome.exit_code == 0
    assert outcome.cleanup is m.Cleanup.UNCONFIRMED
    assert outcome.cleanup_confirmed is False


def test_fatal_loss_with_unconfirmed_teardown_reports_both(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=0.3)

    class FailingRenew:
        def renew(self, run_id, fence, *, ttl_s):
            raise sqlite3.OperationalError("gone")

        def allocation(self, run_id):
            return auth.allocation(run_id)

    with pytest.raises(m.RenewalFailed) as excinfo:
        m.run_supervised(
            _child("import time; time.sleep(5)"),
            cwd=tmp_path,
            env=dict(os.environ),
            log_path=tmp_path / "out.log",
            dry_run=False,
            authority=FailingRenew(),
            run_id="run-1",
            fence=allocation.fence,
            ttl_s=0.3,
            heartbeat_s=0.05,
            terminate_grace_s=0.2,
            group_alive=lambda pgid: True,
            kill_group=lambda pgid, sig: None,
        )
    assert excinfo.value.outcome.cleanup is m.Cleanup.UNCONFIRMED


# --------------------------------------------------------------------------- #
# scope: supervision is not a second place owner
# --------------------------------------------------------------------------- #
def test_supervision_does_not_import_labgrid_or_acquire_anything(tmp_path):
    """Supervision covers HOST BUDGET only; the place belongs to the fixture.

    Checked structurally (AST) rather than by prose, so the docstring may name
    the venue seam while the code is proven not to reach it.
    """
    import ast

    m = _module()
    source = Path(m.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not {name for name in imported if name.startswith("labgrid")}, imported

    called = {
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    forbidden_calls = {"try_reserve", "acquire", "activate", "release", "complete", "abort", "fence"}
    assert not (called & forbidden_calls), called & forbidden_calls

    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=30)

    forbidden: list[str] = []

    class Watcher:
        def renew(self, run_id, fence, *, ttl_s):
            return auth.renew(run_id, fence, ttl_s=ttl_s)

        def allocation(self, run_id):
            return auth.allocation(run_id)

        def __getattr__(self, name):
            forbidden.append(name)
            raise AssertionError(f"supervision called authority.{name}")

    m.run_supervised(
        _child("print('ok')"),
        cwd=tmp_path,
        env=dict(os.environ),
        log_path=tmp_path / "out.log",
        dry_run=False,
        authority=Watcher(),
        run_id="run-1",
        fence=allocation.fence,
        ttl_s=30,
        heartbeat_s=1.0,
    )
    assert forbidden == []
    assert auth.allocation("run-1").state is State.ACTIVE
    assert auth.allocations() == (auth.allocation("run-1"),)


def test_child_runs_in_its_own_session_so_the_group_can_be_killed(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=30)
    log = tmp_path / "out.log"

    rc = m.supervised_tee_run(
        _child("import os; print(os.getsid(0), os.getpgrp())"),
        cwd=tmp_path,
        env=dict(os.environ),
        log_path=log,
        dry_run=False,
        authority=auth,
        run_id="run-1",
        fence=allocation.fence,
        ttl_s=30,
        heartbeat_s=1.0,
    )

    assert rc == 0
    sid, pgid = (int(part) for part in log.read_text(encoding="utf-8").split())
    assert sid != os.getsid(0)
    assert pgid == sid


def test_runner_tee_run_is_unchanged_and_gate_still_fails_closed():
    """This milestone builds machinery; it must not relax the production gate."""
    runner = _runner()
    assert runner.FLEET_LIFECYCLE_UNAVAILABLE == "fleet_lifecycle_unavailable"
    assert not hasattr(runner, "supervised_tee_run")
    import inspect

    source = inspect.getsource(runner.require_supported_fleet_lifecycle)
    assert "FLEET_LIFECYCLE_UNAVAILABLE" in source


# --------------------------------------------------------------------------- #
# the remaining edges: an already-dead authority, a non-renewable store, a
# leftover grandchild, and bad heartbeat arithmetic
# --------------------------------------------------------------------------- #
def test_unreadable_store_before_spawn_is_reported_as_renewal_failed(tmp_path):
    m = _module()

    class Unreadable:
        def allocation(self, run_id):
            raise RuntimeError("database is locked")

        def renew(self, run_id, fence, *, ttl_s):
            raise AssertionError("must not reach renewal")

    marker = tmp_path / "spawned"
    with pytest.raises(m.RenewalFailed) as excinfo:
        m.run_supervised(
            _child(f"import pathlib; pathlib.Path({str(marker)!r}).touch()"),
            cwd=tmp_path,
            env=dict(os.environ),
            log_path=tmp_path / "out.log",
            dry_run=False,
            authority=Unreadable(),
            run_id="run-1",
            fence=1,
            ttl_s=30,
            heartbeat_s=1.0,
        )
    assert "unreadable" in str(excinfo.value)
    assert not marker.exists()


def test_missing_run_id_is_refused_before_spawn(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    _activate(auth, ttl_s=30)
    with pytest.raises(m.MissingAuthority):
        m.run_supervised(
            _child("print('never')"),
            cwd=tmp_path,
            env=dict(os.environ),
            log_path=tmp_path / "out.log",
            dry_run=False,
            authority=auth,
            run_id="",
            fence=1,
            ttl_s=30,
            heartbeat_s=1.0,
        )


def test_renew_returning_a_non_live_state_is_authority_lost(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=0.5)
    from dataclasses import replace

    class ClosedOnRenew:
        def renew(self, run_id, fence, *, ttl_s):
            state = State.QUARANTINED
            return replace(auth.allocation(run_id), state=state)

        def allocation(self, run_id):
            return auth.allocation(run_id)

    with pytest.raises(m.AuthorityLost) as excinfo:
        m.run_supervised(
            _child("import time; time.sleep(5)"),
            cwd=tmp_path,
            env=dict(os.environ),
            log_path=tmp_path / "out.log",
            dry_run=False,
            authority=ClosedOnRenew(),
            run_id="run-1",
            fence=allocation.fence,
            ttl_s=0.5,
            heartbeat_s=0.05,
            terminate_grace_s=0.3,
        )
    assert "quarantined" in str(excinfo.value)


def test_heartbeat_defaults_to_three_beats_per_ttl(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=0.3)
    outcome = m.run_supervised(
        _child("import time; time.sleep(0.7)"),
        cwd=tmp_path,
        env=dict(os.environ),
        log_path=tmp_path / "out.log",
        dry_run=False,
        authority=auth,
        run_id="run-1",
        fence=allocation.fence,
        ttl_s=0.3,
        heartbeat_s=None,
    )
    assert outcome.exit_code == 0
    assert outcome.renewals >= 4
    assert auth.allocation("run-1").state is State.ACTIVE


def test_non_positive_heartbeat_and_ttl_are_refused(tmp_path):
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=30)
    for kwargs in ({"ttl_s": 30, "heartbeat_s": 0}, {"ttl_s": 0, "heartbeat_s": 0.1}):
        with pytest.raises(ValueError):
            m.run_supervised(
                _child("print('never')"),
                cwd=tmp_path,
                env=dict(os.environ),
                log_path=tmp_path / "x.log",
                dry_run=False,
                authority=auth,
                run_id="run-1",
                fence=allocation.fence,
                **kwargs,
            )


def test_leftover_grandchild_makes_teardown_unconfirmed(tmp_path):
    """A child that daemonises leaves the group alive: report, do not claim."""
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=30)
    killed: list[int] = []

    outcome = m.run_supervised(
        _child("print('parent exits, grandchild stays')"),
        cwd=tmp_path,
        env=dict(os.environ),
        log_path=tmp_path / "out.log",
        dry_run=False,
        authority=auth,
        run_id="run-1",
        fence=allocation.fence,
        ttl_s=30,
        heartbeat_s=1.0,
        terminate_grace_s=0.2,
        group_alive=lambda pgid: True,
        kill_group=lambda pgid, sig: killed.append(sig),
    )

    assert outcome.exit_code == 0
    assert outcome.cleanup is m.Cleanup.UNCONFIRMED
    assert "still alive" in outcome.cleanup_detail.lower()
    assert 15 in killed and 9 in killed


def test_group_alive_probe_ignores_pgid_one_and_permission_errors():
    m = _module()
    assert m._group_alive(1) is False
    assert m._group_alive(0) is False

    def denied(pgid, sig=0):
        raise PermissionError("not ours")

    real = os.killpg
    os.killpg = denied
    try:
        assert m._group_alive(4242) is True
    finally:
        os.killpg = real


def test_supervision_errors_report_unconfirmed_without_an_outcome():
    m = _module()
    err = m.RenewalFailed("no outcome attached")
    assert err.cleanup is m.Cleanup.UNCONFIRMED
    assert err.cleanup_confirmed is False
    assert err.exit_code == m.EXIT_RENEWAL_FAILED
    assert m.AuthorityLost.exit_code == m.EXIT_SUPERVISION_LOST
    assert m.MissingAuthority("x").reason == "authority_missing"


def test_a_grandchild_holding_the_pipe_cannot_hang_the_runner(tmp_path):
    """Bounded drain: a leftover descendant must not hold the lane open."""
    m = _module()
    auth = _authority(tmp_path)
    allocation = _activate(auth, ttl_s=30)
    log = tmp_path / "out.log"

    started = time.monotonic()
    outcome = m.run_supervised(
        _child(
            "import os, subprocess, sys, time;"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']);"
            "print('parent done', flush=True)"
        ),
        cwd=tmp_path,
        env=dict(os.environ),
        log_path=log,
        dry_run=False,
        authority=auth,
        run_id="run-1",
        fence=allocation.fence,
        ttl_s=30,
        heartbeat_s=1.0,
        terminate_grace_s=0.3,
        drain_after_exit_s=0.2,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 10.0, "the runner waited on a grandchild's open pipe"
    assert outcome.exit_code == 0
    assert "parent done" in log.read_text(encoding="utf-8")
    # The leftover grandchild was still in our group, so the supervisor killed
    # it and then *proved* it gone — that is a confirmed teardown, not a claim.
    assert "left group" in outcome.cleanup_detail
    assert "group gone" in outcome.cleanup_detail
    assert outcome.cleanup is m.Cleanup.CONFIRMED


def test_outcome_clean_is_only_true_when_confirmed():
    m = _module()
    assert m.Outcome(exit_code=0).clean is True
    assert m.Outcome(exit_code=0, cleanup=m.Cleanup.UNCONFIRMED).clean is False
