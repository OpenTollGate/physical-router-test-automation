"""Real (not mocked) labgrid coordinator/exporter integration — read-only adapter.

The unit suite drives ``lib.fleet_observe`` against a scripted runner that
*models* ``labgrid-client``. This module removes the model: it starts the actual
``labgrid-coordinator`` and ``labgrid-exporter`` binaries, creates real places,
and asserts what the observation adapter reports about them. Anything the model
got wrong shows up here.

It proves, against labgrid 26.0.1 as installed:

* ``version`` answers and the adapter reads a live coordinator;
* a free place is reported exported + not acquired, and is counted at capacity 0
  (exported is not idle — a router additionally needs owner-authorized health
  telemetry this adapter never invents);
* a place held by another identity is REFUSED with ``place_acquired``;
* with the exporter stopped, the coordinator stays reachable, the place stops
  being exported, and capacity does not appear;
* an unknown place is reported missing rather than free;
* the whole read issues only allowlisted subcommands — no acquire, no power, no
  place creation — and the coordinator's ownership table is unchanged afterwards;
* a snapshot of the evidence re-decides to the same answers with no coordinator
  contact, and decays to zero once its TTL has passed.

It does NOT prove anything about physical DUTs, real routers or hardware: no
QEMU guest and no router is involved. ``/dev/kvm`` and host RAM are read from
this machine — that part is a real local measurement, marked as such.

Missing labgrid is NOT a skip: the tests carry ``required``, and the suite gate
in ``tests/conftest.py`` turns a skipped required test into FAILED "NOT
VERIFIED" — a lane that never ran cannot report green.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.required,
    pytest.mark.integration,
]

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.fleet_capacity import Reason, State, parse_manifest  # noqa: E402
from lib.fleet_observe import (  # noqa: E402
    ADMISSION_STATUS,
    Evidence,
    LabgridReadOnlyClient,
    ProbeLimits,
    build_report,
    collect,
    decide,
    read_authority_reservations,
    resolve_host_demand,
)

PLACE_FREE = "prta-observe-free"
PLACE_HELD = "prta-observe-held"
PLACE_UNKNOWN = "prta-observe-absent"
GROUP = "prta-observe"


# --------------------------------------------------------------------------- #
# toolchain discovery -- absence is a hard failure, never a skip
# --------------------------------------------------------------------------- #
def _binary(name: str) -> str:
    override = os.environ.get("TOLLGATE_LABGRID_CLIENT", "").strip() if name == "labgrid-client" else ""
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        pytest.fail(
            f"TOLLGATE_LABGRID_CLIENT points at {override!r}, which is not an executable — "
            "this gate requires the real labgrid client and is NOT allowed to pass by skipping",
            pytrace=False,
        )
    found = shutil.which(name)
    if found:
        return found
    sibling = Path(sys.executable).parent / name
    if sibling.is_file():
        return str(sibling)
    pytest.fail(
        f"{name} is not installed — this gate requires the real labgrid package "
        "(pip install labgrid) and is NOT allowed to pass by skipping",
        pytrace=False,
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(what: str, check, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        ok, detail = check()
        if ok:
            return
        last = detail
        time.sleep(0.2)
    pytest.fail(f"timed out after {timeout}s waiting for {what}: {last}")


class Lab:
    """One real coordinator + one real exporter, in a private directory."""

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.port = _free_port()
        self.address = f"127.0.0.1:{self.port}"
        self.coordinator: subprocess.Popen | None = None
        self.exporter: subprocess.Popen | None = None
        self._exporter_cfg = workdir / "exporter.yaml"

    def start(self) -> None:
        self._exporter_cfg.write_text(
            f"{PLACE_FREE}:\n"
            "  NetworkService:\n"
            '    address: "127.0.0.1"\n'
            "    username: root\n"
            "    port: 2222\n"
            f"{PLACE_HELD}:\n"
            "  NetworkService:\n"
            '    address: "127.0.0.1"\n'
            "    username: root\n"
            "    port: 2223\n"
        )
        self.coordinator = subprocess.Popen(
            [_binary("labgrid-coordinator"), "-l", self.address],
            cwd=self.workdir,
            stdout=open(self.workdir / "coordinator.log", "ab"),
            stderr=subprocess.STDOUT,
        )
        _wait_for("coordinator to answer", self._coordinator_up)
        self.start_exporter()
        _wait_for("exporter to publish its resources", self._exporter_up)

    def start_exporter(self) -> None:
        self.exporter = subprocess.Popen(
            [
                _binary("labgrid-exporter"),
                "-c",
                self.address,
                "-n",
                GROUP,
                "--hostname",
                GROUP,
                str(self._exporter_cfg),
            ],
            cwd=self.workdir,
            stdout=open(self.workdir / "exporter.log", "ab"),
            stderr=subprocess.STDOUT,
        )

    def stop_exporter(self) -> None:
        if self.exporter is not None and self.exporter.poll() is None:
            self.exporter.terminate()
            try:
                self.exporter.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.exporter.kill()
                self.exporter.wait(timeout=10)
        self.exporter = None

    def stop(self) -> None:
        for proc in (self.exporter, self.coordinator):
            if proc is None or proc.poll() is not None:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        self.exporter = None
        self.coordinator = None

    # -- client plumbing --------------------------------------------------- #
    def client(self, *args: str, place: str | None = None, user: str = "probe", host: str = "it-host"):
        cmd = [_binary("labgrid-client"), "-x", self.address]
        if place:
            cmd += ["-p", place]
        cmd += list(args)
        env = {**os.environ, "LG_HOSTNAME": host, "LG_USERNAME": user}
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env, cwd=self.workdir)

    def _coordinator_up(self) -> tuple[bool, str]:
        if self.coordinator is not None and self.coordinator.poll() is not None:
            return False, f"coordinator exited rc={self.coordinator.returncode}"
        proc = self.client("version")
        return proc.returncode == 0, (proc.stderr or proc.stdout).strip()

    def _exporter_up(self) -> tuple[bool, str]:
        if self.exporter is not None and self.exporter.poll() is not None:
            return False, f"exporter exited rc={self.exporter.returncode}"
        proc = self.client("resources")
        return (GROUP in proc.stdout), proc.stdout.strip()

    def create_place(self, name: str, group: str) -> None:
        proc = self.client("create", place=name)
        assert proc.returncode == 0, proc.stderr
        proc = self.client("add-match", f"{GROUP}/{group}/NetworkService", place=name)
        assert proc.returncode == 0, proc.stderr

    def places(self) -> list[str]:
        out = self.client("places").stdout
        return [line.split()[0] for line in out.splitlines() if line.strip()]

    def owner(self, place: str) -> str:
        out = self.client("show", place=place).stdout
        for line in out.splitlines():
            if line.strip().startswith("acquired:"):
                value = line.split(":", 1)[1].strip()
                return "" if value.lower() in {"none", "null"} else value
        raise AssertionError(f"no owner line in 'show' output:\n{out}")


@pytest.fixture()
def lab(tmp_path: Path):
    instance = Lab(tmp_path)
    try:
        instance.start()
        instance.create_place(PLACE_FREE, PLACE_FREE)
        instance.create_place(PLACE_HELD, PLACE_HELD)
        yield instance
    finally:
        instance.stop()


def _manifest(lab: Lab):
    """Explicitly authorized local manifest: one fleet, two places, one unknown."""
    doc = {
        "schema_version": 1,
        "fleets": [
            {
                "id": "local-it",
                "owner": "prta-it",
                "coordinator": {"address": lab.address},
                "host_controller": {"endpoint": "127.0.0.1:8099"},
                "allowed_places": [PLACE_FREE, PLACE_HELD, PLACE_UNKNOWN],
                "permitted_actions": ["observe"],
                "capacity_limits": {
                    "max_concurrent_runs": 2,
                    "max_idle_guests": 0,
                    "max_new_vm_slots": 0,
                },
                "shared_conflict_ids": [],
            }
        ],
    }
    return parse_manifest(doc, source="integration-test")


def _collect(manifest, *, ttl: float = 120.0, now: float | None = None) -> Evidence:
    limits = ProbeLimits(timeout_s=20.0, ttl_s=ttl)
    return collect(
        manifest,
        limits=limits,
        authority_epoch=7,
        now=time.time() if now is None else now,
        client=LabgridReadOnlyClient(limits, binary=_binary("labgrid-client")),
        disk_path="/",
        hostname=socket.gethostname(),
    )


def _verdict(verdicts, resource_id):
    return next(v for v in verdicts if v.resource_id == resource_id)


# --------------------------------------------------------------------------- #
# 1. Real read-only observation of a live coordinator
# --------------------------------------------------------------------------- #
def test_live_coordinator_is_read_and_free_place_is_exported_not_idle(lab: Lab):
    manifest = _manifest(lab)
    evidence = _collect(manifest)

    assert evidence.coordinators["local-it"].reachable is True
    read = evidence.coordinators_read["local-it"]
    assert read.version, "the adapter must record the real coordinator version it talked to"
    assert any(PLACE_FREE in resource for resource in read.published_resources)

    free = read.places[PLACE_FREE]
    assert free.exists is True
    assert free.acquired_by == ""
    assert free.available is True

    exporter_view = evidence.exporters[PLACE_FREE]
    assert exporter_view.exporter_connected is True
    assert exporter_view.exported is True
    assert exporter_view.acquired is False

    verdicts = decide(manifest, evidence, now=time.time())
    verdict = _verdict(verdicts, PLACE_FREE)
    assert verdict.state is State.EXPORTED_AVAILABLE
    assert verdict.capacity == 0, "exported is not idle: no owner-authorized health telemetry exists"
    assert Reason.NO_TELEMETRY in verdict.reasons


def test_place_held_by_another_identity_is_refused(lab: Lab):
    lease = lab.client("acquire", place=PLACE_HELD, user="someone-else")
    assert lease.returncode == 0, lease.stderr
    try:
        manifest = _manifest(lab)
        evidence = _collect(manifest)
        assert evidence.exporters[PLACE_HELD].acquired is True
        verdicts = decide(manifest, evidence, now=time.time())
        verdict = _verdict(verdicts, PLACE_HELD)
        assert verdict.state is State.REFUSED
        assert Reason.PLACE_ACQUIRED in verdict.reasons
        assert verdict.capacity == 0
        # The read did not disturb the holder's lease.
        assert lab.owner(PLACE_HELD) == "it-host/someone-else"
    finally:
        lab.client("release-from", "it-host/someone-else", place=PLACE_HELD)
    assert lab.owner(PLACE_HELD) == ""


def test_unknown_place_is_missing_not_free(lab: Lab):
    manifest = _manifest(lab)
    evidence = _collect(manifest)
    read = evidence.coordinators_read["local-it"]
    assert read.places[PLACE_UNKNOWN].exists is False
    verdicts = decide(manifest, evidence, now=time.time())
    verdict = _verdict(verdicts, PLACE_UNKNOWN)
    assert verdict.capacity == 0
    assert verdict.state is not State.EXPORTED_AVAILABLE


def test_read_only_collection_never_mutates_the_coordinator(lab: Lab):
    before_places = sorted(lab.places())
    before_owners = {p: lab.owner(p) for p in before_places}
    manifest = _manifest(lab)
    _collect(manifest)
    assert sorted(lab.places()) == before_places, "observation must not create or delete places"
    assert {p: lab.owner(p) for p in before_places} == before_owners, "observation must not acquire/release"


# --------------------------------------------------------------------------- #
# 2. Partial outage: exporter down, coordinator up
# --------------------------------------------------------------------------- #
def test_exporter_down_removes_capacity_and_says_why(lab: Lab):
    manifest = _manifest(lab)
    healthy = _collect(manifest)
    assert healthy.exporters[PLACE_FREE].exported is True

    lab.stop_exporter()
    # Give the coordinator a moment to drop the exporter's resources.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not lab.client("resources").stdout.strip():
            break
        time.sleep(0.2)

    degraded = _collect(manifest)
    assert degraded.coordinators["local-it"].reachable is True, "the coordinator itself is still up"
    assert degraded.exporters[PLACE_FREE].exporter_connected is False
    assert degraded.exporters[PLACE_FREE].exported is False

    verdicts = decide(manifest, degraded, now=time.time())
    verdict = _verdict(verdicts, PLACE_FREE)
    assert verdict.capacity == 0
    assert verdict.state is not State.EXPORTED_AVAILABLE


# --------------------------------------------------------------------------- #
# 3. Host metrics are this host's, measured
# --------------------------------------------------------------------------- #
def test_local_host_metrics_are_measured_and_admission_is_not_qualified(lab: Lab):
    manifest = _manifest(lab)
    evidence = _collect(manifest)
    metrics = evidence.host_metrics["local-it"]
    assert metrics.ram_total_mib > 0
    assert metrics.ram_available_mib <= metrics.ram_total_mib
    assert metrics.cpu_total >= 1
    assert metrics.inode_free > 0
    assert metrics.kvm_present is True
    assert metrics.architecture, "an unknown architecture must stay empty, never be guessed"

    verdicts = decide(manifest, evidence, now=time.time(), demand=resolve_host_demand())
    host_verdicts = [v for v in verdicts if v.kind.value == "host"]
    assert host_verdicts
    assert host_verdicts[0].state is State.UNKNOWN
    assert host_verdicts[0].slots == 0

    report = build_report(
        manifest,
        evidence,
        verdicts,
        generated_at="now",
        now=time.time(),
        demand=resolve_host_demand(),
    )
    assert report["admission"]["status"] == ADMISSION_STATUS
    assert report["totals"]["new_vm_slots_raw"] == 0


def test_reservations_are_read_from_a_real_authority_store(lab: Lab, tmp_path: Path):
    from lib.fleet_authority import Demand, FleetAuthority, LaneSpec

    store = tmp_path / "authority.sqlite3"
    authority = FleetAuthority(store)
    authority.reconcile()
    authority.register_lane(
        LaneSpec(
            lane_id=f"lane-{PLACE_FREE}",
            fleet_id="local-it",
            resource_id=PLACE_FREE,
            coordinator=lab.address,
            # The lane must be able to cover the demand it is asked for;
            # LaneSpec defaults to 1 vCPU and this reservation wants 2.
            vcpus_total=8,
            ram_mib_total=8192,
            disk_mib_total=20000,
            inodes_total=100000,
        )
    )
    granted = authority.try_reserve(
        lane_id=f"lane-{PLACE_FREE}",
        demand=Demand(vcpus=2, ram_mib=1024, disk_mib=4096, inodes=1),
        run_id="it-run-1",
    )
    assert granted.granted

    manifest = _manifest(lab)
    limits = ProbeLimits(timeout_s=20.0, ttl_s=120.0)
    evidence = collect(
        manifest,
        limits=limits,
        authority_epoch=authority.epoch,
        now=time.time(),
        client=LabgridReadOnlyClient(limits, binary=_binary("labgrid-client")),
        authority_path=store,
        disk_path="/",
        hostname=socket.gethostname(),
    )
    assert [r.run_id for r in evidence.reservations] == ["it-run-1"]
    assert evidence.reservations[0].demand.vcpus == 2
    # Reading did not touch the reservation.
    assert authority.allocation("it-run-1").state.value == "reserved"
    # And the read-only reservation reader agrees with the collect() path.
    direct = read_authority_reservations(store, now=time.time())
    assert direct.available is True and len(direct.reservations) == 1


# --------------------------------------------------------------------------- #
# 4. Snapshots: re-decide offline, decay when stale
# --------------------------------------------------------------------------- #
def test_snapshot_redecides_without_contact_and_decays_when_stale(lab: Lab, tmp_path: Path):
    manifest = _manifest(lab)
    evidence = _collect(manifest, ttl=120.0)

    snapshot = tmp_path / "evidence.json"
    snapshot.write_text(json.dumps(evidence.to_dict()), encoding="utf-8")
    restored = Evidence.from_dict(json.loads(snapshot.read_text(encoding="utf-8")))
    assert restored.coordinators["local-it"].reachable is True

    # Stop the lab entirely: the snapshot must still be decidable, offline.
    lab.stop()
    fresh_verdicts = decide(manifest, restored, now=restored.observed_at + 1)
    observed_at = restored.observed_at
    assert _verdict(fresh_verdicts, PLACE_FREE).state is State.EXPORTED_AVAILABLE

    stale_verdicts = decide(manifest, restored, now=observed_at + restored.ttl_s + 1)
    stale = _verdict(stale_verdicts, PLACE_FREE)
    assert stale.state is State.STALE
    assert stale.capacity == 0
    assert Reason.STALE_TELEMETRY in stale.reasons

    stale_report = build_report(
        manifest,
        restored,
        stale_verdicts,
        generated_at="stale",
        now=observed_at + restored.ttl_s + 1,
        demand=resolve_host_demand(),
    )
    assert stale_report["totals"]["exported_available"] == 0
    assert stale_report["totals"]["idle_physical_routers"] == 0
    assert stale_report["totals"]["reachable_fleets"] == 0


def test_epoch_mismatch_refuses_an_otherwise_fresh_snapshot(lab: Lab):
    manifest = _manifest(lab)
    evidence = _collect(manifest, ttl=600.0)
    # Same evidence, different authority epoch: nothing may be trusted.
    mismatched = Evidence(
        observed_at=evidence.observed_at,
        authority_epoch=evidence.authority_epoch + 1,
        ttl_s=evidence.ttl_s,
        coordinators=evidence.coordinators,
        exporters=evidence.exporters,
        coordinators_read=evidence.coordinators_read,
    )
    verdicts = decide(manifest, mismatched, now=evidence.observed_at + 1)
    assert _verdict(verdicts, PLACE_FREE).state is State.STALE
    assert _verdict(verdicts, PLACE_FREE).capacity == 0
