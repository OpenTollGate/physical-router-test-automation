"""Fleet lifecycle at the runner level: the four defects the audit measured.

Each test here mirrors one blocking finding from
``~/reports/reviews/prta-334-runner-safety.md`` and is written to be RED against
the shipped behaviour at base ``ed687b2``:

(a) an observe-only / undeclared manifest reached ACTIVE;
(b) a registered lane was activated with no coordinator-owned acquisition;
(c) a child outlived its TTL with ``RENEW CALLS 0``;
(d) a simulated subprocess/teardown failure released the lane **clean**.

Real SQLite stores and harmless Python children only. The coordinator is stubbed
at the boundary (``re_read_ownership`` / ``LabgridBench``), and the runner never
acquires a place, so nothing here contacts a coordinator, reads the real
inventory, or touches hardware.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from lib.fleet_authority import Demand, FleetAuthority, HostSpec, LaneSpec, State
from lib.fleet_binding import Binding, PlaceClaim
from lib.run_admission import AdmissionRefused

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lifecycle_runner", ROOT / "scripts/run-profile.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

#: The unstubbed re-read, for the two tests that exercise it directly. The
#: autouse fixture below replaces the module attribute, so keep a handle.
_REAL_RE_READ = getattr(runner, "re_read_ownership", None)

BUDGET = dict(
    vcpus_total=8, ram_mib_total=16384, disk_mib_total=200000, inodes_total=1000000
)
COORDINATOR = "coord.example:20408"
PLACE = "place-a1"
DUT = "10.0.0.11"
OWNER = "host/runner"


# --------------------------------------------------------------------------- #
# fixtures: a real store, a real manifest, a stubbed venue boundary
# --------------------------------------------------------------------------- #
def write_manifest(tmp_path: Path, *, actions=("observe", "acquire", "release")) -> Path:
    """A valid manifest with every placeholder filled in."""
    doc = yaml.safe_load(
        (ROOT / "config/fleets.example.yaml").read_text().replace("REPLACE_ME_", "test_")
    )
    doc["fleets"][0]["permitted_actions"] = list(actions)
    doc["fleets"][0]["coordinator"]["address"] = COORDINATOR
    doc["fleets"][0]["allowed_places"] = [PLACE]
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def write_inventory(tmp_path: Path, *, place: str = PLACE, coordinator: str = COORDINATOR) -> Path:
    """A lab inventory whose one dossier owns ``place`` at ``DUT``."""
    doc = {
        "switch": {"host": "switch.example"},
        "coordinator": {"address": coordinator},
        "routers": {
            "router-a": {
                "poe_port": "lan1",
                "address": DUT,
                "place": place,
            }
        },
    }
    path = tmp_path / "inventory.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def real_store(tmp_path: Path, *, fleet_id="test_FLEET_A", resource_id=PLACE) -> FleetAuthority:
    auth = FleetAuthority(tmp_path / "authority.db")
    auth.reconcile()
    auth.register_host(HostSpec(**BUDGET))
    auth.register_lane(
        LaneSpec(lane_id="lane-a", fleet_id=fleet_id, resource_id=resource_id, **BUDGET)
    )
    return auth


def reconcile_store(path: Path) -> FleetAuthority:
    """A store the runner's own ``FleetAuthority`` will accept as reconciled.

    Host budget plus lane ``lane-a`` registered to ``test_FLEET_A`` / ``PLACE``,
    which is the identity the manifest authorizes.
    """
    auth = FleetAuthority(path)
    auth.reconcile()
    auth.register_host(HostSpec(**BUDGET))
    auth.register_lane(
        LaneSpec(lane_id="lane-a", fleet_id="test_FLEET_A", resource_id=PLACE, **BUDGET)
    )
    return auth


def fleet_args(tmp_path: Path, manifest: Path, *, ttl: float = 900.0, **overrides) -> SimpleNamespace:
    values = dict(
        fleet_manifest=str(manifest),
        fleet="test_FLEET_A",
        lane="lane-a",
        resource=PLACE,
        domain=None,
        demand_vcpus=1,
        demand_ram_mib=128,
        demand_disk_mib=256,
        demand_inodes=1,
        authority_store=str(tmp_path / "authority.db"),
        lease_ttl=ttl,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def child_env(tmp_path: Path, inventory: Path) -> dict[str, str]:
    """The env a bound run would hand its child, minus anything from this host."""
    env = {
        "TOLLGATE_LAB_INVENTORY": str(inventory),
        "TOLLGATE_LAB_TYPE": "physical",
        "TOLLGATE_VENUE": "labgrid",
        "TOLLGATE_LABGRID_PLACE": PLACE,
        "TOLLGATE_SSH_HOST": DUT,
        "LG_HOSTNAME": "host",
        "LG_USERNAME": "runner",
        "PATH": "/usr/bin:/bin",
    }
    return env


PROFILE = {
    "name": "offline",
    "runner": "pytest",
    "matrix": {"tier": "unit", "lab": "physical", "scope": "test"},
    "env": {},
    "pytest": {"testpaths": "tests/e2e"},
}


@pytest.fixture(autouse=True)
def _stub_coordinator(monkeypatch):
    """Never contact a coordinator: ownership re-reads are answered locally.

    ``"free"`` means the place is unheld. Also pins the identity so the claim
    owner is deterministic instead of this machine's user.
    """
    monkeypatch.setattr(runner, "re_read_ownership", lambda plan, env: "", raising=False)
    from lib.labgrid_venue import ClientIdentity

    monkeypatch.setattr(
        ClientIdentity,
        "current",
        classmethod(lambda cls, environ=None: cls(host="host", user="runner")),
    )
    return ""


def prepare_main(monkeypatch, tmp_path, *extra, marker: Path | None = None):
    """Point ``main()`` at a harmless child and a throwaway results tree."""
    marker = marker or (tmp_path / "child-started")
    command = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).touch(); print('harmless output')",
    ]
    monkeypatch.setattr(runner, "load_profile", lambda _: dict(PROFILE))
    monkeypatch.setattr(runner, "build_pytest_command", lambda *a: command)
    monkeypatch.setattr(runner, "REPO_DIR", tmp_path)
    monkeypatch.setattr(runner, "TESTS_DIR", tmp_path)
    monkeypatch.setattr(runner, "git_short_sha", lambda: "offline")
    monkeypatch.setattr(runner, "git_full_sha", lambda: "offline")
    # The venue decision the child's fixture would make: forced + explicit, so
    # the run binds to PLACE instead of falling back to auto/legacy.
    monkeypatch.setenv("TOLLGATE_VENUE", "labgrid")
    monkeypatch.setenv("TOLLGATE_LABGRID_PLACE", "")
    monkeypatch.setenv("TOLLGATE_SSH_HOST", DUT)
    monkeypatch.setenv("TOLLGATE_LAB_TYPE", "physical")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run-profile.py", "--profile", "offline", "--run-dir", str(tmp_path / "results"), "--no-render", *extra],
    )
    return marker


# --------------------------------------------------------------------------- #
# (a) an observe-only / undeclared manifest is refused, nothing spawns
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("actions", [("observe",), ("observe", "acquire")])
def test_observe_only_manifest_is_refused_before_any_spawn(monkeypatch, tmp_path, capsys, actions):
    """The audit's first defect: an observe-only manifest reached ACTIVE.

    Authorization requires ``acquire`` AND ``release``; a manifest that grants
    only observation may describe capacity but never drive a DUT. Nothing may
    spawn, nothing may be reserved, and no store may be minted.
    """
    manifest = write_manifest(tmp_path, actions=actions)
    inventory = write_inventory(tmp_path)
    store = tmp_path / "authority.db"
    marker = prepare_main(
        monkeypatch, tmp_path, "--fleet-manifest", str(manifest), "--fleet", "test_FLEET_A",
        "--resource", PLACE, "--lane", "lane-a", "--authority-store", str(store),
        "--demand-vcpus", "1", "--demand-ram-mib", "128", "--demand-disk-mib", "256",
        "--demand-inodes", "1",
    )
    monkeypatch.setenv("TOLLGATE_LAB_INVENTORY", str(inventory))

    assert runner.main() == runner.ADMISSION_EXIT
    err = capsys.readouterr().err
    assert "action_not_permitted" in err
    assert not marker.exists(), "a refused run spawned a child"
    assert not store.exists(), "a refused run minted the authority store"


def test_undeclared_fleet_and_resource_are_refused(monkeypatch, tmp_path, capsys):
    """An existing registered lane cannot be taken with an undeclared identity."""
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path)
    real_store(tmp_path)  # lane-a exists and is registered to test_FLEET_A / PLACE
    store = tmp_path / "authority.db"
    marker = prepare_main(
        monkeypatch, tmp_path, "--fleet-manifest", str(manifest), "--fleet", "NOT-AUTHORIZED",
        "--resource", "OTHER-ROUTER", "--lane", "lane-a", "--authority-store", str(store),
        "--demand-vcpus", "1", "--demand-ram-mib", "128", "--demand-disk-mib", "256",
        "--demand-inodes", "1",
    )
    monkeypatch.setenv("TOLLGATE_LAB_INVENTORY", str(inventory))

    assert runner.main() == runner.ADMISSION_EXIT
    assert "not_authorized" in capsys.readouterr().err
    assert not marker.exists()
    assert FleetAuthority(tmp_path / "authority.db").allocations() == ()


# --------------------------------------------------------------------------- #
# (b) a coordinator/place disagreement is refused before the store exists
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"coordinator": "other.example:20408"}, "coordinator_address_mismatch"),
        ({"place": "place-b2"}, "place_mismatch"),
    ],
)
def test_coordinator_or_place_disagreement_is_refused(monkeypatch, tmp_path, capsys, overrides, code):
    """Requirement 1: the lane must BE the place the fixture will own."""
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path, **overrides)
    store = tmp_path / "authority.db"
    marker = prepare_main(
        monkeypatch, tmp_path, "--fleet-manifest", str(manifest), "--fleet", "test_FLEET_A",
        "--resource", PLACE, "--lane", "lane-a", "--authority-store", str(store),
        "--demand-vcpus", "1", "--demand-ram-mib", "128", "--demand-disk-mib", "256",
        "--demand-inodes", "1",
    )
    monkeypatch.setenv("TOLLGATE_LAB_INVENTORY", str(inventory))

    assert runner.main() == runner.ADMISSION_EXIT
    err = capsys.readouterr().err
    assert code in err, err
    assert not marker.exists(), "a mis-bound run spawned a child"
    assert not store.exists(), "a mis-bound run minted the authority store"


def test_a_venue_that_would_acquire_nothing_is_refused(monkeypatch, tmp_path, capsys):
    """The audit's second defect: host budget without a place owner.

    ``TOLLGATE_VENUE=direct`` makes the fixture drive the DUT over plain SSH and
    acquire nothing, so the reservation would mean nothing. Refused, and no
    store.
    """
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path)
    store = tmp_path / "authority.db"
    marker = prepare_main(
        monkeypatch, tmp_path, "--fleet-manifest", str(manifest), "--fleet", "test_FLEET_A",
        "--resource", PLACE, "--lane", "lane-a", "--authority-store", str(store),
        "--demand-vcpus", "1", "--demand-ram-mib", "128", "--demand-disk-mib", "256",
        "--demand-inodes", "1",
    )
    monkeypatch.setenv("TOLLGATE_LAB_INVENTORY", str(inventory))
    monkeypatch.setenv("TOLLGATE_VENUE", "direct")
    monkeypatch.setenv("TOLLGATE_SSH_HOST", DUT)

    assert runner.main() == runner.ADMISSION_EXIT
    assert "venue_takes_no_place" in capsys.readouterr().err
    assert not marker.exists()
    assert not store.exists()


def test_binding_is_checked_before_authorization_and_never_acquires(tmp_path, monkeypatch):
    """Order is a safety property: the pure check runs before the store is minted.

    Also proves the runner performs no place acquisition: ``check_binding`` is
    given no acquisition affordance and the returned binding is only read.
    """
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path, place="place-b2")
    args = fleet_args(tmp_path, manifest)
    env = child_env(tmp_path, inventory)
    store = Path(args.authority_store)
    # The inventory says the target dossier owns place-b2; the manifest lane and
    # the forced place both say place-a1, so the fixture would own a different
    # DUT than the lane reserved.
    env["TOLLGATE_SSH_HOST"] = DUT
    env["TOLLGATE_LABGRID_PLACE"] = PLACE

    with pytest.raises(AdmissionRefused) as excinfo:
        runner.plan_fleet_run(args, run_id="run-1", env=env, profile=dict(PROFILE))
    assert excinfo.value.reason in {"place_override_conflict", "venue_place_unresolvable"}
    assert not store.exists(), "the binding gate ran after the store was minted"


def test_a_bound_plan_exports_the_claim_without_acquiring(tmp_path, monkeypatch):
    """The plan carries the claim env the fixture verifies; it holds no bench."""
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path)
    args = fleet_args(tmp_path, manifest)
    env = child_env(tmp_path, inventory)
    reconcile_store(Path(args.authority_store))

    plan = runner.plan_fleet_run(args, run_id="run-1", env=env, profile=dict(PROFILE))
    plan.admission.acquire()

    claim = plan.claim_env()
    assert claim["TOLLGATE_FLEET_CLAIM_PLACE"] == PLACE
    assert claim["TOLLGATE_FLEET_CLAIM_OWNER"] == OWNER
    assert claim["TOLLGATE_FLEET_RUN_ID"] == "run-1"
    # venue forced + explicit: the fixture cannot drop to the legacy path
    assert claim["TOLLGATE_VENUE"] == "labgrid"
    assert claim["TOLLGATE_LABGRID_PLACE"] == PLACE
    # the runner never acquired anything: a reservation exists, no place owner
    allocation = plan.admission.authority.allocation("run-1")
    assert allocation.state is State.RESERVED
    assert not hasattr(plan, "bench")


# --------------------------------------------------------------------------- #
# (c) a child that outlives its TTL is renewed, not abandoned
# --------------------------------------------------------------------------- #
def test_child_outliving_ttl_is_renewed_and_stays_accounted(tmp_path, monkeypatch, capsys):
    """The audit's third defect: "CHILD FINISHED AFTER TTL / RENEW CALLS 0".

    A 0.9 s child under a 0.3 s TTL must be renewed while it runs, and the lane
    must still be ACTIVE (not expired) when it finishes.
    """
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path)
    args = fleet_args(tmp_path, manifest, ttl=0.3)
    reconcile_store(Path(args.authority_store))
    plan = runner.plan_fleet_run(
        args,
        run_id="run-1",
        env=child_env(tmp_path, inventory),
        profile=dict(PROFILE),
    )
    plan.admission.acquire()
    plan.admission.mark_started()

    renewals: list[float] = []
    authority = plan.admission.authority
    real_renew = authority.renew

    def counting_renew(run_id, fence, *, ttl_s):
        renewals.append(ttl_s)
        return real_renew(run_id, fence, ttl_s=ttl_s)

    monkeypatch.setattr(authority, "renew", counting_renew)

    child = [sys.executable, "-c", "import time; time.sleep(0.9); print('CHILD FINISHED AFTER TTL')"]
    rc = runner.supervised_tee_run(
        child,
        cwd=tmp_path,
        env=dict(child_env(tmp_path, inventory)),
        log_path=tmp_path / "out.log",
        dry_run=False,
        authority=authority,
        run_id="run-1",
        fence=plan.admission.allocation.fence,
        ttl_s=0.3,
    )

    assert rc == 0
    assert renewals, "the child ran past its TTL with zero renewals"
    assert len(renewals) >= 2
    allocation = authority.allocation("run-1")
    assert allocation.state is State.ACTIVE, "the lane expired under a running child"
    assert allocation.expires_at > time.time()


def test_a_renewal_failure_terminates_the_child(tmp_path, monkeypatch):
    """Requirement 4: a bad beat kills the group (76) instead of running on.

    The child would live 5 s; the store stops renewing after the first beat, so
    it must be dead long before that and the exit code must be supervision's own.
    """
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path)
    args = fleet_args(tmp_path, manifest, ttl=0.3)
    reconcile_store(Path(args.authority_store))
    plan = runner.plan_fleet_run(
        args,
        run_id="run-1",
        env=child_env(tmp_path, inventory),
        profile=dict(PROFILE),
    )
    plan.admission.acquire()
    plan.admission.mark_started()

    def broken_renew(*_args, **_kwargs):
        raise RuntimeError("authority store went away")

    monkeypatch.setattr(plan.admission.authority, "renew", broken_renew)
    marker = tmp_path / "still-running"

    child = [
        sys.executable,
        "-c",
        f"import time, pathlib; time.sleep(5); pathlib.Path({str(marker)!r}).touch()",
    ]
    started = time.monotonic()
    from lib.supervised_run import RenewalFailed

    with pytest.raises(RenewalFailed) as excinfo:
        runner.supervised_tee_run(
            child,
            cwd=tmp_path,
            env=dict(child_env(tmp_path, inventory)),
            log_path=tmp_path / "out.log",
            dry_run=False,
            authority=plan.admission.authority,
            run_id="run-1",
            fence=plan.admission.allocation.fence,
            ttl_s=0.3,
        )

    assert excinfo.value.exit_code == 76
    assert time.monotonic() - started < 4.0, "the runner waited for the child instead of killing it"
    time.sleep(0.2)
    assert not marker.exists(), "the child outlived a failed renewal"


def test_main_reports_a_supervision_failure_as_76_not_a_test_verdict(monkeypatch, tmp_path, capsys):
    """A supervision failure is distinguishable from a test failure, end to end."""
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path)
    real_store(tmp_path)
    marker = prepare_main(
        monkeypatch, tmp_path, "--fleet-manifest", str(manifest), "--fleet", "test_FLEET_A",
        "--resource", PLACE, "--lane", "lane-a", "--authority-store", str(tmp_path / "authority.db"),
        "--demand-vcpus", "1", "--demand-ram-mib", "128", "--demand-disk-mib", "256",
        "--demand-inodes", "1", "--lease-ttl", "0.3",
    )
    monkeypatch.setenv("TOLLGATE_LAB_INVENTORY", str(inventory))
    monkeypatch.setenv("TOLLGATE_VENUE", "labgrid")
    monkeypatch.setenv("TOLLGATE_SSH_HOST", DUT)

    # The child would pass; only supervision fails. The exit code must not be 0.
    monkeypatch.setattr(
        runner,
        "build_pytest_command",
        lambda *a: [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
    )

    from lib.fleet_authority import FleetAuthority as FA
    from lib.supervised_run import RenewalFailed

    authority = FA(tmp_path / "authority.db")

    def broken_renew(*_args, **_kwargs):
        raise RuntimeError("store gone")

    monkeypatch.setattr(authority, "renew", broken_renew)
    monkeypatch.setattr(
        runner, "supervised_tee_run", lambda *a, **kw: (_ for _ in ()).throw(
            RenewalFailed("could not renew", outcome=None)
        )
    )

    assert runner.main() == 76
    captured = capsys.readouterr()
    assert "SUPERVISION FAILED" in captured.err
    assert "not a test verdict" in captured.err
    assert "SUPERVISION" in captured.err and "76" in captured.err
    # Requirement 3: a supervision failure must not be dressed up as a finished
    # run. Nothing downstream may print a completion summary or claim a verdict.
    assert "PROFILE RUN SUMMARY" not in captured.out


# --------------------------------------------------------------------------- #
# (d) a failed run never yields a clean release
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "child_rc, ownership, expected_state, expected_reason",
    [
        # The child FAILED (rc=9) but the place came back free: a failing test
        # is not a dirty teardown, so this is the one clean case.
        ("fail", "", State.RELEASED, "released_confirmed"),
        # Ownership unreadable (coordinator down / no client binary): unproven.
        ("ok", None, State.CLEANUP_PENDING, "ownership_unreadable"),
        # The place is still ours after the run: the teardown did not happen.
        ("ok", OWNER, State.CLEANUP_PENDING, "lease_still_held"),
    ],
)
def test_release_state_is_derived_from_evidence(
    monkeypatch, tmp_path, capsys, child_rc, ownership, expected_state, expected_reason
):
    """Requirement 5: ``clean`` comes from the verdict, never a literal.

    The audit's fourth defect was an unconditional ``release(clean=True)`` that
    freed the lane after a simulated teardown failure.
    """
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path)
    real_store(tmp_path)
    marker = tmp_path / "child-started"
    if child_rc == "fail":
        command = [sys.executable, "-c", "raise SystemExit(9)"]
    else:
        command = [
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).touch()",
        ]
    prepare_main(
        monkeypatch, tmp_path, "--fleet-manifest", str(manifest), "--fleet", "test_FLEET_A",
        "--resource", PLACE, "--lane", "lane-a", "--authority-store", str(tmp_path / "authority.db"),
        "--demand-vcpus", "1", "--demand-ram-mib", "128", "--demand-disk-mib", "256",
        "--demand-inodes", "1",
    )
    monkeypatch.setattr(runner, "build_pytest_command", lambda *a: command)
    monkeypatch.setattr(runner, "re_read_ownership", lambda plan, env: ownership)
    monkeypatch.setenv("TOLLGATE_LAB_INVENTORY", str(inventory))
    monkeypatch.setenv("TOLLGATE_VENUE", "labgrid")
    monkeypatch.setenv("TOLLGATE_SSH_HOST", DUT)

    runner.main()

    allocation = FleetAuthority(tmp_path / "authority.db").allocation(
        json.loads((tmp_path / "results/raw/e2e/release.json").read_text())["run_id"]
    )
    assert allocation.state is expected_state, (
        f"release derived {allocation.state.value}; expected {expected_state.value}"
    )
    evidence = json.loads((tmp_path / "results/raw/e2e/release.json").read_text())
    assert evidence["reason"] == expected_reason
    assert evidence["clean"] is (expected_state is State.RELEASED)


def test_a_teardown_failure_never_frees_the_lane(monkeypatch, tmp_path, capsys):
    """The exact audit probe: a raising teardown must leave CLEANUP_PENDING."""
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path)
    real_store(tmp_path)
    prepare_main(
        monkeypatch, tmp_path, "--fleet-manifest", str(manifest), "--fleet", "test_FLEET_A",
        "--resource", PLACE, "--lane", "lane-a", "--authority-store", str(tmp_path / "authority.db"),
        "--demand-vcpus", "1", "--demand-ram-mib", "128", "--demand-disk-mib", "256",
        "--demand-inodes", "1",
    )
    monkeypatch.setenv("TOLLGATE_LAB_INVENTORY", str(inventory))
    monkeypatch.setenv("TOLLGATE_VENUE", "labgrid")
    monkeypatch.setenv("TOLLGATE_SSH_HOST", DUT)

    from lib.supervised_run import SupervisionError

    def boom(*_args, **_kwargs):
        raise SupervisionError("simulated subprocess/teardown failure")

    monkeypatch.setattr(runner, "supervised_tee_run", boom)

    assert runner.main() == 77
    err = capsys.readouterr().err
    assert "WARNING: lane not released cleanly" in err
    states = [a.state for a in FleetAuthority(tmp_path / "authority.db").allocations()]
    assert State.CLEANUP_PENDING in states, states
    assert State.RELEASED not in states, "a failed run freed its lane"


def test_unconfirmed_release_is_printed_and_keeps_the_lane(monkeypatch, tmp_path, capsys):
    """An unreadable coordinator is reported, not assumed free."""
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path)
    real_store(tmp_path)
    prepare_main(
        monkeypatch, tmp_path, "--fleet-manifest", str(manifest), "--fleet", "test_FLEET_A",
        "--resource", PLACE, "--lane", "lane-a", "--authority-store", str(tmp_path / "authority.db"),
        "--demand-vcpus", "1", "--demand-ram-mib", "128", "--demand-disk-mib", "256",
        "--demand-inodes", "1",
    )
    monkeypatch.setenv("TOLLGATE_LAB_INVENTORY", str(inventory))
    monkeypatch.setenv("TOLLGATE_VENUE", "labgrid")
    monkeypatch.setenv("TOLLGATE_SSH_HOST", DUT)
    monkeypatch.setattr(runner, "re_read_ownership", lambda plan, env: None)

    runner.main()

    err = capsys.readouterr().err
    assert "ownership_unreadable" in err
    assert "capacity remains consumed" in err


# --------------------------------------------------------------------------- #
# the non-fleet path is unchanged, and dry runs still debit nothing
# --------------------------------------------------------------------------- #
def test_nonfleet_run_still_uses_plain_tee_and_never_binds(monkeypatch, tmp_path, capsys):
    marker = prepare_main(monkeypatch, tmp_path)
    monkeypatch.setattr(
        runner, "plan_fleet_run", lambda *a, **kw: pytest.fail("a non-fleet run must not bind")
    )
    assert runner.main() == 0
    assert marker.exists()
    assert (tmp_path / "results/raw/e2e/output.log").read_text() == "harmless output\n"


def test_fleet_dry_run_creates_no_store_and_no_child(monkeypatch, tmp_path):
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path)
    store = tmp_path / "never.db"
    marker = prepare_main(
        monkeypatch, tmp_path, "--fleet-manifest", str(manifest), "--authority-store", str(store),
        "--dry-run",
    )
    monkeypatch.setenv("TOLLGATE_LAB_INVENTORY", str(inventory))
    assert runner.main() == 0
    assert not store.exists()
    assert not marker.exists()


def test_release_inputs_do_not_read_ownership_before_activation(tmp_path):
    """A reserved-but-never-activated lane owes no coordinator contact."""
    plan = SimpleNamespace(binding=SimpleNamespace(place=PLACE, owner=OWNER))
    inputs = runner.release_inputs(
        plan, error=None, ever_acquired=False, env={"TOLLGATE_LAB_INVENTORY": "unused"}
    )
    assert inputs["owner_after"] is None
    assert inputs["acquired"] is False

# --------------------------------------------------------------------------- #
# the read-only ownership re-read is the evidence source; it must never raise
# --------------------------------------------------------------------------- #
def test_re_read_ownership_reads_the_coordinator_without_acquiring(monkeypatch, tmp_path):
    """Requirement 4's evidence: a *read* of who holds the place, never a claim.

    ``re_read_ownership`` is the only input that can make a release clean, so it
    goes through ``LabgridBench.owner()`` and nothing else. The bench is stubbed
    at the boundary, and the test asserts that no acquisition method is touched.
    """
    from lib import labgrid_venue

    monkeypatch.setattr(runner, "re_read_ownership", _REAL_RE_READ)
    seen = {}

    class FakeBench:
        def __init__(self, coordinator, place, *, identity=None):
            seen["coordinator"] = coordinator
            seen["place"] = place
            seen["identity"] = identity

        def owner(self):
            seen["owner_calls"] = seen.get("owner_calls", 0) + 1
            return OWNER

        def acquire(self):  # pragma: no cover - must never be called
            raise AssertionError("the runner acquired the place")

        def release(self):  # pragma: no cover - must never be called
            raise AssertionError("the runner released the place")

    monkeypatch.setattr(labgrid_venue, "LabgridBench", FakeBench)
    plan = SimpleNamespace(
        binding=SimpleNamespace(coordinator=COORDINATOR, place=PLACE, owner=OWNER)
    )

    assert runner.re_read_ownership(plan, {"TOLLGATE_LAB_INVENTORY": "unused"}) == OWNER
    assert seen["coordinator"] == COORDINATOR
    assert seen["place"] == PLACE
    assert seen["owner_calls"] == 1
    # The identity the bench reads with is derived from the *child's* env, so the
    # runner and the fixture agree on who "we" are.
    assert seen["identity"].owner == OWNER


def test_re_read_ownership_failure_is_not_reported_as_free(monkeypatch, tmp_path):
    """A coordinator that cannot be read must not look like an empty place.

    Returning ``None`` (not ``""``) is what makes ``release_verdict`` choose
    ``ownership_unreadable`` instead of ``released_confirmed``.
    """
    from lib import labgrid_venue

    monkeypatch.setattr(runner, "re_read_ownership", _REAL_RE_READ)

    class BrokenBench:
        def __init__(self, *a, **kw):
            raise RuntimeError("no labgrid-client binary")

    monkeypatch.setattr(labgrid_venue, "LabgridBench", BrokenBench)
    plan = SimpleNamespace(
        binding=SimpleNamespace(coordinator=COORDINATOR, place=PLACE, owner=OWNER)
    )

    assert runner.re_read_ownership(plan, {}) is None
    # And no plan at all (non-fleet run) never contacts anything.
    assert runner.re_read_ownership(None, {}) is None


def test_release_that_the_store_refuses_keeps_the_lane(monkeypatch, tmp_path, capsys):
    """``complete(clean=True)`` can still be refused; that must not free the lane.

    Requirement 4 says the release path derives clean from evidence — but the
    authority can independently refuse a clean completion (fence lost, store
    disagreeing). The runner must then report the lane as still occupied rather
    than printing a clean release.
    """
    manifest = write_manifest(tmp_path)
    inventory = write_inventory(tmp_path)
    real_store(tmp_path)
    prepare_main(
        monkeypatch, tmp_path, "--fleet-manifest", str(manifest), "--fleet", "test_FLEET_A",
        "--resource", PLACE, "--lane", "lane-a", "--authority-store", str(tmp_path / "authority.db"),
        "--demand-vcpus", "1", "--demand-ram-mib", "128", "--demand-disk-mib", "256",
        "--demand-inodes", "1",
    )
    monkeypatch.setenv("TOLLGATE_LAB_INVENTORY", str(inventory))
    monkeypatch.setenv("TOLLGATE_VENUE", "labgrid")
    monkeypatch.setenv("TOLLGATE_SSH_HOST", DUT)

    from lib.run_admission import AdmissionRefused as AuthRefused

    real_release = runner.Admission.release

    def refuse_clean(self, *, clean=True, detail=""):
        if clean:
            raise AuthRefused("release_refused", "store says the fence is gone")
        return real_release(self, clean=clean, detail=detail)

    monkeypatch.setattr(runner.Admission, "release", refuse_clean)

    runner.main()

    err = capsys.readouterr().err
    assert "RELEASE UNCONFIRMED" in err
    assert "stays occupied" in err
    assert "lane not released cleanly" in err
    # The store refused the completion, so it never moved: the lane is still
    # consuming capacity. That is the point — an unconfirmed release must not
    # look free, and `release.json` must record it as pending.
    states = [a.state for a in FleetAuthority(tmp_path / "authority.db").allocations()]
    assert State.RELEASED not in states, "a refused release still reported the lane free"
    assert State.ACTIVE in states, states
    evidence = json.loads((tmp_path / "results/raw/e2e/release.json").read_text())
    assert evidence["clean"] is False
    assert evidence["state"] == "cleanup_pending"


def test_unwritable_release_evidence_does_not_mask_the_run(monkeypatch, tmp_path, capsys):
    """Evidence is best-effort; the lane state is the authority."""
    from lib.claim_handoff import ReleaseReason, ReleaseState, ReleaseVerdict

    verdict = ReleaseVerdict(
        state=ReleaseState.CLEAN_RELEASED,
        reason=ReleaseReason.RELEASED_CONFIRMED,
        detail="place is free",
    )
    target = tmp_path / "nope" / "release.json"
    monkeypatch.setattr(Path, "mkdir", lambda *a, **kw: (_ for _ in ()).throw(OSError("read-only fs")))

    runner.write_release_evidence(target, run_id="r1", verdict=verdict)

    assert "could not write release evidence" in capsys.readouterr().err
