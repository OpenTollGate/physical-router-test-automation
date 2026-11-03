"""Fail-closed production lifecycle; real SQLite and harmless child commands only.

These are the *refusal* guards. The blanket ``fleet_lifecycle_unavailable``
refusal is gone — a fully bound, authorized, supervised run is now allowed — but
every unsatisfied input must still refuse with ``ADMISSION_EXIT`` (75) and run
nothing. Each case below is one way for a fleet run to be unsatisfied; none of
them may spawn a child, take a reservation or mint a store.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from lib.fleet_authority import Demand
from test_runner_manifest_authorization import setup

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lifecycle_runner", ROOT / "scripts/run-profile.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def prepare(monkeypatch, tmp_path, kind, extra):
    marker = tmp_path / "child-started"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch(); print('harmless output'); raise SystemExit(7)"]
    profile = {"name": "offline", "runner": kind, "matrix": {"tier": "unit", "lab": "virtual", "scope": "test"}, "env": {}}
    monkeypatch.setattr(runner, "load_profile", lambda _: profile)
    monkeypatch.setattr(runner, "build_pytest_command", lambda *a: command)
    monkeypatch.setattr(runner, "build_playwright_command", lambda *a: (command, {}))
    monkeypatch.setattr(runner, "REPO_DIR", tmp_path)
    monkeypatch.setattr(runner, "TESTS_DIR", tmp_path)
    monkeypatch.setattr(runner, "git_short_sha", lambda: "offline")
    monkeypatch.setattr(runner, "git_full_sha", lambda: "offline")
    # No venue inventory on this host, and no venue env: the run can be
    # authorized but it cannot be bound to a place, so it must refuse.
    monkeypatch.delenv("TOLLGATE_LAB_INVENTORY", raising=False)
    monkeypatch.setenv("TOLLGATE_LAB_INVENTORY", str(tmp_path / "absent-inventory.yaml"))
    monkeypatch.setenv("TOLLGATE_VENUE", "labgrid")
    monkeypatch.delenv("TOLLGATE_LABGRID_PLACE", raising=False)
    monkeypatch.delenv("TOLLGATE_SSH_HOST", raising=False)
    monkeypatch.setattr(sys, "argv", ["run-profile.py", "--profile", "offline", "--run-dir", str(tmp_path / "results"), "--no-render", *extra])
    return marker


@pytest.mark.parametrize("kind", ["pytest", "playwright"])
@pytest.mark.parametrize("occupied", [False, True])
def test_an_unbindable_fleet_run_is_refused_before_reservation_or_child(monkeypatch, tmp_path, capsys, kind, occupied):
    """Authorized but unbound: no inventory means no place can be proved.

    Reservation state on the store is irrelevant to the outcome — a refused run
    must leave the store exactly as it found it and spawn nothing.
    """
    args, auth, _ = setup(tmp_path)
    if occupied:
        assert auth.try_reserve(lane_id="lane-a", run_id="existing", demand=Demand(1, 128, 256, 1)).granted
    before = auth.allocations()
    marker = prepare(monkeypatch, tmp_path, kind, ["--fleet-manifest", args.fleet_manifest, "--fleet", args.fleet, "--resource", args.resource, "--lane", args.lane, "--authority-store", args.authority_store, "--demand-vcpus", "1", "--demand-ram-mib", "128", "--demand-disk-mib", "256", "--demand-inodes", "1"])
    assert runner.main() == runner.ADMISSION_EXIT
    assert not marker.exists()
    assert auth.allocations() == before
    assert "inventory_unavailable" in capsys.readouterr().err


def test_refusal_does_not_construct_authority(monkeypatch, tmp_path, capsys):
    """A refused run creates no store: the SQLite file must not exist."""
    store = tmp_path / "never-created.db"
    marker = prepare(monkeypatch, tmp_path, "pytest", ["--fleet-manifest", "missing.yaml", "--authority-store", str(store)])
    assert runner.main() == runner.ADMISSION_EXIT
    assert not store.exists()
    assert not marker.exists()
    err = capsys.readouterr().err
    assert "ADMISSION REFUSED" in err
    assert "no store was created" in err


@pytest.mark.parametrize("kind", ["pytest", "playwright"])
def test_fleet_dry_run_remains_nonexecuting(monkeypatch, tmp_path, kind):
    store = tmp_path / "never-created.db"
    marker = prepare(monkeypatch, tmp_path, kind, ["--fleet-manifest", "missing.yaml", "--authority-store", str(store), "--dry-run"])
    assert runner.main() == 0
    assert not store.exists()
    assert not marker.exists()


def test_nonfleet_retains_log_and_child_exit(monkeypatch, tmp_path, capsys):
    marker = prepare(monkeypatch, tmp_path, "pytest", [])
    assert runner.main() == 7
    assert marker.exists()
    assert (tmp_path / "results/raw/unit/output.log").read_text() == "harmless output\n"
    assert "harmless output" in capsys.readouterr().out


def test_the_blanket_refusal_is_gone_but_a_refusal_still_exists():
    """The gate that refused *every* fleet run unconditionally must be gone.

    This is the positive half of the change: a satisfied run is now allowed.
    The negative half — that every unsatisfied run still refuses — is the rest
    of this file plus ``test_runner_fleet_lifecycle.py``.
    """
    assert not hasattr(runner, "require_supported_fleet_lifecycle")
    assert not hasattr(runner, "FLEET_LIFECYCLE_UNAVAILABLE")
    assert runner.ADMISSION_EXIT == 75
    # ... and supervision has its own, distinguishable codes.
    assert runner.SUPERVISION_EXIT == 76
