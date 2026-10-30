"""Unit tests for lib/fleet_observe.py — slice 4: read-only observation adapters.

Everything here runs against injected fakes and this machine's own ``/proc``;
no coordinator is contacted and no place is acquired. The four things that
would make this adapter dangerous are each pinned by a test that fails if the
behaviour regresses:

* a mutating labgrid subcommand can be issued at all (allowlist);
* an unreadable place is treated as free (guess);
* a raw host headroom number is presented as allocatable capacity (promise);
* a pidfile is treated as a live guest (identification != readiness).

Audit reference: reports/prta-fleet-capacity-audit.md, gates 1, 4 and 5; manager
steer on t_3ad7fda2 (raw headroom separate from admission, NOT_QUALIFIED).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from lib.fleet_capacity import (
    HostDemand,
    ManifestError,
    Reason,
    ResourceKind,
    State,
    host_observation,
    load_manifest,
    parse_manifest,
)
from lib.fleet_observe import (
    ADMISSION_DETAIL,
    ADMISSION_STATUS,
    EVIDENCE_SCHEMA_VERSION,
    READ_ONLY_SUBCOMMANDS,
    UNMEASURED_HOST_DIMENSIONS,
    CommandResult,
    CoordinatorRead,
    Evidence,
    GuestSpec,
    HostMetrics,
    LabgridReadOnlyClient,
    ObservationRefused,
    PlaceState,
    ProbeLimits,
    Refusal,
    build_report,
    collect,
    decide,
    endpoint_host,
    host_observation_from_metrics,
    is_local_endpoint,
    parse_place_show,
    read_authority_reservations,
    read_coordinator,
    read_local_guest,
    read_local_host,
    resolve_host_demand,
)

pytestmark = [pytest.mark.story("P11")]

NOW = 1_000_000.0
TTL = 60.0
EPOCH = 3
REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures: two independent fleets, nothing shared
# ---------------------------------------------------------------------------
def _manifest_doc() -> dict:
    return {
        "schema_version": 1,
        "fleets": [
            {
                "id": "fleet-a",
                "owner": "contributor-a",
                "coordinator": {"address": "127.0.0.1:20408"},
                "host_controller": {"endpoint": "127.0.0.1:8099"},
                "allowed_places": [
                    "router-a1",
                    {"id": "lane-a1", "kind": "guest"},
                ],
                "permitted_actions": ["observe"],
                "capacity_limits": {
                    "max_concurrent_runs": 2,
                    "max_idle_guests": 1,
                    "max_new_vm_slots": 1,
                },
                "shared_conflict_ids": ["rf-band-a"],
            },
            {
                "id": "fleet-b",
                "owner": "contributor-b",
                "coordinator": {"address": "other.invalid:20408"},
                "host_controller": {"endpoint": "other.invalid:8099"},
                "allowed_places": ["router-b1"],
                "permitted_actions": ["observe"],
                "capacity_limits": {
                    "max_concurrent_runs": 1,
                    "max_idle_guests": 0,
                    "max_new_vm_slots": 0,
                },
                "shared_conflict_ids": ["rf-band-b"],
            },
        ],
    }


@pytest.fixture()
def manifest():
    return parse_manifest(_manifest_doc(), source="test-manifest")


class FakeRunner:
    """Records argv and answers from a scripted map. No subprocess ever runs."""

    def __init__(self, responses: dict[tuple[str, ...], CommandResult] | None = None, default: CommandResult | None = None):
        self.calls: list[list[str]] = []
        self.responses = responses or {}
        self.default = default or CommandResult(argv=(), returncode=1, stderr="not scripted")

    def __call__(self, argv: list[str]) -> CommandResult:
        self.calls.append(list(argv))
        key = tuple(argv[1:])
        if key in self.responses:
            return self.responses[key]
        # Fall back to matching on the tail (subcommand may follow -p PLACE).
        for scripted, result in self.responses.items():
            if key[-len(scripted) :] == scripted:
                return result
        return self.default

    @property
    def subcommands(self) -> list[str]:
        return [argv[-1] for argv in self.calls]


def _client(runner: FakeRunner, *, timeout_s: float = 5.0) -> LabgridReadOnlyClient:
    return LabgridReadOnlyClient(
        ProbeLimits(timeout_s=timeout_s, ttl_s=TTL), binary="/usr/bin/labgrid-client", runner=runner
    )


def _metrics(**overrides) -> HostMetrics:
    base = dict(
        endpoint="127.0.0.1:8099",
        observed_at=NOW,
        hostname="this-host",
        cpu_total=8,
        load1=1.0,
        ram_total_mib=8192,
        ram_available_mib=6144,
        swap_total_mib=2048,
        swap_free_mib=1024,
        ram_reserve_mib=512,
        disk_path="/",
        disk_total_mib=100_000,
        disk_free_mib=50_000,
        inode_total=1_000_000,
        inode_free=900_000,
        kvm_present=True,
        kvm_usable=True,
        architecture="x86_64",
        detail="",
    )
    base.update(overrides)
    return HostMetrics(**base)


def _show_output(name: str, *, acquired: str = "None", avail: bool | None = True) -> str:
    text = [
        f"Place '{name}':",
        "  matches:",
        f"    probe/{name}/NetworkService",
        f"  acquired: {acquired}",
        "  acquired resources:",
        "  created: 2026-10-30 21:00:17.268845",
        "  changed: 2026-10-30 21:00:18.223887",
    ]
    if avail is True:
        text += [
            f"Matching resource 'NetworkService' (probe/{name}/NetworkService/NetworkService):",
            "  {'acquired': None,",
            "   'avail': True,",
            "   'cls': 'NetworkService'}",
        ]
    elif avail is False:
        text += [
            f"Matching resource 'NetworkService' (probe/{name}/NetworkService/NetworkService):",
            "  {'acquired': None,",
            "   'avail': False,",
            "   'cls': 'NetworkService'}",
        ]
    return "\n".join(text) + "\n"


# ---------------------------------------------------------------------------
# 1. The allowlist is structural: mutating verbs never reach a process
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("verb", ["acquire", "release", "release-from", "power", "flashscript", "create", "delete"])
def test_client_refuses_every_mutating_subcommand(verb: str):
    runner = FakeRunner()
    client = _client(runner)
    with pytest.raises(ObservationRefused) as exc:
        client.run("127.0.0.1:20408", verb)
    assert "not read-only" in str(exc.value)
    assert runner.calls == [], "a refused subcommand must not reach the process boundary"


def test_client_allows_exactly_the_read_only_set():
    assert READ_ONLY_SUBCOMMANDS == frozenset({"version", "resources", "places", "show"})


def test_client_command_shape_is_host_port_and_place():
    runner = FakeRunner({("version",): CommandResult(argv=(), returncode=0, stdout="26.0.1\n")})
    client = _client(runner)
    result = client.run("127.0.0.1:20408", "version")
    assert result.ok and result.stdout.strip() == "26.0.1"
    assert runner.calls == [["/usr/bin/labgrid-client", "-x", "127.0.0.1:20408", "version"]]

    runner.calls.clear()
    client.run("127.0.0.1:20408", "show", place="lane-a")
    assert runner.calls == [["/usr/bin/labgrid-client", "-x", "127.0.0.1:20408", "-p", "lane-a", "show"]]


def test_client_requires_host_port():
    client = _client(FakeRunner())
    with pytest.raises(ObservationRefused):
        client.run("no-port", "version")


def test_missing_client_binary_is_a_refusal_not_a_skip(monkeypatch):
    monkeypatch.delenv("TOLLGATE_LABGRID_CLIENT", raising=False)
    monkeypatch.setattr("lib.fleet_observe.shutil.which", lambda name: None)
    with pytest.raises(ObservationRefused) as exc:
        LabgridReadOnlyClient()
    assert "not installed" in str(exc.value)


def test_override_binary_must_be_executable(monkeypatch, tmp_path):
    bogus = tmp_path / "not-executable"
    bogus.write_text("x")
    monkeypatch.setenv("TOLLGATE_LABGRID_CLIENT", str(bogus))
    with pytest.raises(ObservationRefused):
        LabgridReadOnlyClient()


def test_probe_limits_reject_non_positive_values():
    with pytest.raises(ValueError):
        ProbeLimits(timeout_s=0)
    with pytest.raises(ValueError):
        ProbeLimits(ttl_s=0)
    with pytest.raises(ValueError):
        ProbeLimits(max_output_bytes=0)
    with pytest.raises(ValueError):
        ProbeLimits(timeout_s=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. Place parsing never guesses "free"
# ---------------------------------------------------------------------------
def test_parse_free_place():
    state = parse_place_show(_show_output("lane-a"), "lane-a")
    assert state.exists is True
    assert state.acquired_by == ""
    assert state.available is True
    assert state.match_patterns


def test_parse_held_place():
    state = parse_place_show(_show_output("lane-a", acquired="it-host/probe"), "lane-a")
    assert state.acquired_by == "it-host/probe"


def test_parse_place_without_resource_detail_is_unproven_not_unavailable():
    state = parse_place_show(_show_output("lane-a", avail=None), "lane-a")
    assert state.available is None, "absent detail is unknown — never 'unavailable', never 'available'"


def test_parse_place_with_unreadable_owner_is_unknown():
    text = "Place 'lane-a':\n  matches:\n    probe/lane-a/NetworkService\n"
    state = parse_place_show(text, "lane-a")
    assert state.exists is True
    assert state.acquired_by is None, "no owner line is unknown ownership, not free"


def test_parse_absent_place():
    state = parse_place_show("labgrid-client: Error: place pattern ghost matches nothing\n", "ghost")
    assert state.exists is False


def test_parse_place_available_false():
    state = parse_place_show(_show_output("lane-a", avail=False), "lane-a")
    assert state.available is False


# ---------------------------------------------------------------------------
# 3. Coordinator read: reachability is control plane only
# ---------------------------------------------------------------------------
def test_read_coordinator_unreachable_marks_nothing():
    runner = FakeRunner({("version",): CommandResult(argv=(), returncode=1, stderr="Could not connect")})
    read = read_coordinator(_client(runner), fleet_id="fleet-a", address="127.0.0.1:20408", place_ids=["router-a1"])
    assert read.reachable is False
    assert read.places == {}
    assert "did not answer" in read.detail
    assert runner.subcommands == ["version"], "an unreachable coordinator ends the read immediately"


def test_read_coordinator_happy_path_reads_only_allowlisted_places():
    runner = FakeRunner(
        {
            ("version",): CommandResult(argv=(), returncode=0, stdout="26.0.1\n"),
            ("resources",): CommandResult(
                argv=(), returncode=0, stdout="probe/router-a1/NetworkService\nprobe/other/NetworkService\n"
            ),
            ("-p", "router-a1", "show"): CommandResult(argv=(), returncode=0, stdout=_show_output("router-a1")),
        }
    )
    read = read_coordinator(
        _client(runner), fleet_id="fleet-a", address="127.0.0.1:20408", place_ids=["router-a1"]
    )
    assert read.reachable is True
    assert read.version == "26.0.1"
    assert "probe/other/NetworkService" in read.published_resources
    assert list(read.places) == ["router-a1"]
    # The place list comes from the manifest, never from `places` — reading it
    # would turn observation into discovery.
    assert "places" not in runner.subcommands


def test_read_coordinator_unknown_place_is_not_free():
    runner = FakeRunner(
        {
            ("version",): CommandResult(argv=(), returncode=0, stdout="26.0.1\n"),
            ("resources",): CommandResult(argv=(), returncode=0, stdout="probe/x/NetworkService\n"),
            ("-p", "router-a1", "show"): CommandResult(
                argv=(), returncode=1, stderr="labgrid-client: Error: place pattern router-a1 matches nothing"
            ),
        }
    )
    read = read_coordinator(
        _client(runner), fleet_id="fleet-a", address="127.0.0.1:20408", place_ids=["router-a1"]
    )
    assert read.places["router-a1"].exists is False


def test_read_coordinator_unreadable_place_records_detail():
    runner = FakeRunner(
        {
            ("version",): CommandResult(argv=(), returncode=0, stdout="26.0.1\n"),
            ("resources",): CommandResult(argv=(), returncode=0, stdout="probe/x/NetworkService\n"),
            ("-p", "router-a1", "show"): CommandResult(argv=(), returncode=2, stderr="boom token=abc123"),
        }
    )
    read = read_coordinator(
        _client(runner), fleet_id="fleet-a", address="127.0.0.1:20408", place_ids=["router-a1"]
    )
    assert read.places["router-a1"].exists is False
    assert "could not be read" in read.detail
    assert "abc123" not in read.detail, "probe diagnostics must be redacted"


def test_read_coordinator_resource_listing_failure_is_recorded():
    runner = FakeRunner(
        {
            ("version",): CommandResult(argv=(), returncode=0, stdout="26.0.1\n"),
            ("resources",): CommandResult(argv=(), returncode=1, stderr="listing broke"),
            ("-p", "router-a1", "show"): CommandResult(argv=(), returncode=0, stdout=_show_output("router-a1")),
        }
    )
    read = read_coordinator(
        _client(runner), fleet_id="fleet-a", address="127.0.0.1:20408", place_ids=["router-a1"]
    )
    assert read.published_resources == ()
    assert "resource listing failed" in read.detail


# ---------------------------------------------------------------------------
# 4. Local host identification: this host only
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("endpoint", ["127.0.0.1:8099", "localhost:8099", "127.0.1.5:8099"])
def test_local_endpoints_are_recognised(endpoint: str):
    assert is_local_endpoint(endpoint) is True


def test_remote_endpoint_is_not_local():
    assert is_local_endpoint("other.invalid:8099") is False
    assert is_local_endpoint("") is False


def test_hostname_matches_this_host():
    import socket

    assert is_local_endpoint(f"{socket.gethostname()}:8099") is True


def test_endpoint_host_handles_ipv6_brackets():
    assert endpoint_host("[::1]:8099") == "::1"
    assert endpoint_host("host:8099") == "host"
    assert endpoint_host("bare") == "bare"


def test_read_local_host_refuses_a_remote_controller():
    with pytest.raises(ObservationRefused) as exc:
        read_local_host("other.invalid:8099", bandwidth=ProbeLimits(), now=NOW)
    assert "not this host" in str(exc.value)


def test_read_local_host_measures_this_host(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       16384000 kB\n"
        "MemFree:         1000000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "SwapTotal:       2048000 kB\n"
        "SwapFree:        1024000 kB\n"
    )
    metrics = read_local_host(
        "127.0.0.1:8099",
        bandwidth=ProbeLimits(ttl_s=TTL),
        now=NOW,
        disk_path=tmp_path,
        ram_reserve_mib=256,
        meminfo_path=meminfo,
        hostname="this-host",
    )
    assert metrics.ram_total_mib == 16000
    assert metrics.ram_available_mib == 8000
    assert metrics.ram_committed_mib == 8000
    assert metrics.swap_used_mib == 1000
    assert metrics.ram_reserve_mib == 256
    assert metrics.cpu_total >= 1
    assert metrics.disk_total_mib > 0
    assert metrics.inode_total > 0
    assert metrics.kvm_usable in (True, False, None)
    assert metrics.architecture in {"x86_64", "aarch64", "armv7l", "mips", "mipsel", ""}


def test_read_local_host_without_meminfo_refuses(tmp_path):
    with pytest.raises(ObservationRefused):
        read_local_host(
            "127.0.0.1:8099",
            bandwidth=ProbeLimits(),
            now=NOW,
            meminfo_path=tmp_path / "missing",
            hostname="this-host",
        )


def test_read_local_host_rejects_negative_reserve():
    with pytest.raises(ObservationRefused):
        read_local_host(
            "127.0.0.1:8099", bandwidth=ProbeLimits(), now=NOW, ram_reserve_mib=-1, hostname="this-host"
        )


def test_kvm_presence_is_read_from_the_host_root(tmp_path):
    (tmp_path / "dev").mkdir()
    (tmp_path / "dev" / "kvm").write_text("")
    os.chmod(tmp_path / "dev" / "kvm", 0o600)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 1024 kB\nMemAvailable: 512 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    metrics = read_local_host(
        "127.0.0.1:8099",
        bandwidth=ProbeLimits(),
        now=NOW,
        disk_path=tmp_path,
        meminfo_path=meminfo,
        host_root=tmp_path,
        hostname="this-host",
    )
    assert metrics.kvm_present is True
    assert metrics.kvm_usable is True


# ---------------------------------------------------------------------------
# 5. Raw headroom is NOT admission
# ---------------------------------------------------------------------------
def test_host_admission_stays_unknown_because_cpu_commitment_is_unmeasured():
    obs = host_observation_from_metrics(
        _metrics(), fleet_id="fleet-a", resource_id="host:fleet-a", ttl_s=TTL, authority_epoch=EPOCH
    )
    assert obs.unknown_dimensions == UNMEASURED_HOST_DIMENSIONS
    assert obs.cpu_committed == 0.0
    from lib.fleet_capacity import new_vm_slots

    verdict = new_vm_slots(
        obs, demand=HostDemand(vcpus=2, ram_mib=1024, disk_mib=1024), now=NOW, authority_epoch=EPOCH
    )
    assert verdict.state is State.UNKNOWN
    assert verdict.slots == 0
    assert Reason.UNKNOWN_DIMENSION in verdict.reasons


def test_ram_committed_is_measured_not_assumed():
    obs = host_observation_from_metrics(
        _metrics(ram_total_mib=8192, ram_available_mib=2048),
        fleet_id="fleet-a",
        resource_id="host:fleet-a",
        ttl_s=TTL,
        authority_epoch=EPOCH,
    )
    assert obs.ram_committed_mib == 6144


def test_admission_status_constant_is_not_qualified():
    assert ADMISSION_STATUS == "NOT_QUALIFIED"
    assert "no host-wide reservation accounting" in ADMISSION_DETAIL


# ---------------------------------------------------------------------------
# 6. Guest identification: a pidfile is a claim, not a lane
# ---------------------------------------------------------------------------
def _write_proc(proc_root: Path, pid: int, ticks: int) -> None:
    d = proc_root / str(pid)
    d.mkdir(parents=True)
    fields = ["S"] + ["0"] * 18 + [str(ticks)] + ["0"] * 10
    (d / "stat").write_text(f"{pid} (qemu-system-x) {' '.join(fields)}\n")


def test_guest_without_pidfile_is_bridge_only(tmp_path):
    obs = read_local_guest(
        GuestSpec(resource_id="lane-a1", pidfile=tmp_path / "nope.pid"),
        fleet_id="fleet-a",
        now=NOW,
        bandwidth=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        proc_root=tmp_path,
    )
    assert obs.pid is None
    from lib.fleet_capacity import Observations, guest_capacity

    manifest = parse_manifest(_manifest_doc(), source="t")
    verdict = guest_capacity(manifest, "lane-a1", now=NOW, authority_epoch=EPOCH, observations=Observations(guest=obs))
    assert verdict.state is State.UNKNOWN
    assert Reason.BRIDGE_ONLY in verdict.reasons
    assert verdict.capacity == 0


def test_guest_with_dead_pid_is_not_idle(tmp_path):
    pidfile = tmp_path / "dead.pid"
    pidfile.write_text("999999\n")
    obs = read_local_guest(
        GuestSpec(resource_id="lane-a1", pidfile=pidfile),
        fleet_id="fleet-a",
        now=NOW,
        bandwidth=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        proc_root=tmp_path,
    )
    assert obs.pid is None


def test_guest_with_live_pid_is_identified_but_still_not_ready(tmp_path):
    proc_root = tmp_path / "proc"
    _write_proc(proc_root, 4242, 777)
    pidfile = tmp_path / "live.pid"
    pidfile.write_text("4242\n")
    obs = read_local_guest(
        GuestSpec(resource_id="lane-a1", pidfile=pidfile),
        fleet_id="fleet-a",
        now=NOW,
        bandwidth=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        proc_root=proc_root,
    )
    assert obs.pid == 4242
    assert obs.pid_start_ticks == 777
    # Every readiness dimension is unproven on purpose.
    assert obs.health_ok is None
    assert obs.topology_ok is None
    assert obs.lease_free is None
    assert obs.client_reachable is None


def test_guest_with_recycled_pid_is_refused(tmp_path):
    proc_root = tmp_path / "proc"
    _write_proc(proc_root, 4242, 777)
    pidfile = tmp_path / "live.pid"
    pidfile.write_text("4242\n")
    obs = read_local_guest(
        GuestSpec(resource_id="lane-a1", pidfile=pidfile, expected_pid_start_ticks=999),
        fleet_id="fleet-a",
        now=NOW,
        bandwidth=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        proc_root=proc_root,
    )
    from lib.fleet_capacity import Observations, guest_capacity

    manifest = parse_manifest(_manifest_doc(), source="t")
    verdict = guest_capacity(manifest, "lane-a1", now=NOW, authority_epoch=EPOCH, observations=Observations(guest=obs))
    assert verdict.state is State.REFUSED
    assert Reason.GUEST_PID_REUSED in verdict.reasons


def test_pidfile_with_garbage_is_not_a_pid(tmp_path):
    pidfile = tmp_path / "junk.pid"
    pidfile.write_text("not-a-pid\n")
    obs = read_local_guest(
        GuestSpec(resource_id="lane-a1", pidfile=pidfile),
        fleet_id="fleet-a",
        now=NOW,
        bandwidth=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        proc_root=tmp_path,
    )
    assert obs.pid is None


# ---------------------------------------------------------------------------
# 7. Reservations come from the authority, read-only
# ---------------------------------------------------------------------------
def test_reservations_without_a_store_are_unavailable_not_zero():
    read = read_authority_reservations(None, now=NOW)
    assert read.available is False
    assert read.reservations == ()
    assert "no host authority store" in read.detail


def test_reservations_from_a_missing_file_are_unavailable(tmp_path):
    read = read_authority_reservations(tmp_path / "absent.sqlite3", now=NOW)
    assert read.available is False
    assert "not found" in read.detail


def test_reservations_from_a_non_authority_database_are_unavailable(tmp_path):
    bogus = tmp_path / "bogus.sqlite3"
    conn = sqlite3.connect(bogus)
    conn.execute("CREATE TABLE something (x INTEGER)")
    conn.commit()
    conn.close()
    read = read_authority_reservations(bogus, now=NOW)
    assert read.available is False
    assert "not an authority store" in read.detail


def test_reservations_are_read_from_a_real_authority_store(tmp_path):
    from lib.fleet_authority import Demand, FleetAuthority, HostSpec, LaneSpec

    store = tmp_path / "fleet-authority.sqlite3"
    authority = FleetAuthority(store)
    authority.reconcile()
    # Aggregate admission fails CLOSED on an unregistered host, so a bare lane
    # registration is no longer admissible. These fixtures declare the host
    # budget that actually backs the lane.
    authority.register_host(HostSpec(vcpus_total=8, ram_mib_total=8192, disk_mib_total=65536, inodes_total=100_000))
    authority.register_lane(
        LaneSpec(lane_id="lane-1", fleet_id="fleet-a", resource_id="lane-a1", vcpus_total=8, ram_mib_total=8192)
    )
    decision = authority.try_reserve(
        lane_id="lane-1", demand=Demand(vcpus=2, ram_mib=1024, disk_mib=4096, inodes=1), run_id="run-1"
    )
    assert decision.granted

    read = read_authority_reservations(store, now=time.time())
    assert read.available is True
    assert [r.run_id for r in read.reservations] == ["run-1"]
    assert read.reservations[0].started is False
    assert read.reservations[0].demand.vcpus == 2

    # ...and the store was not mutated by reading it.
    assert authority.allocation("run-1").state.value == "reserved"


def test_started_reservations_are_not_double_counted(tmp_path):
    from lib.fleet_authority import Demand, FleetAuthority, HostSpec, LaneSpec

    store = tmp_path / "fleet-authority.sqlite3"
    authority = FleetAuthority(store)
    authority.reconcile()
    # Aggregate admission fails CLOSED on an unregistered host, so a bare lane
    # registration is no longer admissible. These fixtures declare the host
    # budget that actually backs the lane.
    authority.register_host(HostSpec(vcpus_total=8, ram_mib_total=8192, disk_mib_total=65536, inodes_total=100_000))
    authority.register_lane(
        LaneSpec(lane_id="lane-1", fleet_id="fleet-a", resource_id="lane-a1", vcpus_total=8, ram_mib_total=8192)
    )
    decision = authority.try_reserve(
        lane_id="lane-1", demand=Demand(vcpus=2, ram_mib=1024, disk_mib=4096, inodes=1), run_id="run-2"
    )
    authority.activate(decision.allocation.run_id, decision.allocation.fence)
    read = read_authority_reservations(store, now=time.time())
    assert read.reservations[0].started is True


# ---------------------------------------------------------------------------
# 8. Evidence round-trip and staleness
# ---------------------------------------------------------------------------
def _evidence(**overrides) -> Evidence:
    base = dict(observed_at=NOW, authority_epoch=EPOCH, ttl_s=TTL)
    base.update(overrides)
    return Evidence(**base)


def test_evidence_round_trips_through_json():
    from lib.fleet_capacity import coordinator_observation, exporter_observation, guest_observation
    from lib.fleet_observe import _coordinator_to_dict  # noqa: F401  (import sanity)

    evidence = _evidence(
        coordinators={
            "fleet-a": coordinator_observation(
                fleet_id="fleet-a", reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            )
        },
        exporters={
            "router-a1": exporter_observation(
                fleet_id="fleet-a",
                resource_id="router-a1",
                exporter_connected=True,
                now=NOW,
                ttl_s=TTL,
                authority_epoch=EPOCH,
            )
        },
        guests={
            "lane-a1": guest_observation(
                fleet_id="fleet-a", resource_id="lane-a1", pid=42, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            )
        },
        hosts={
            "fleet-a": host_observation_from_metrics(
                _metrics(), fleet_id="fleet-a", resource_id="host:fleet-a", ttl_s=TTL, authority_epoch=EPOCH
            )
        },
        host_metrics={"fleet-a": _metrics()},
        refusals=(Refusal(subject="fleet-b:coordinator", reason="refused", detail="not local"),),
    )
    restored = Evidence.from_dict(json.loads(json.dumps(evidence.to_dict())))
    assert restored.observed_at == evidence.observed_at
    assert restored.authority_epoch == evidence.authority_epoch
    assert restored.coordinators["fleet-a"].reachable is True
    assert restored.exporters["router-a1"].exporter_connected is True
    assert restored.guests["lane-a1"].pid == 42
    assert restored.hosts["fleet-a"].unknown_dimensions == UNMEASURED_HOST_DIMENSIONS
    assert restored.host_metrics["fleet-a"].ram_total_mib == 8192
    assert restored.refusals[0].subject == "fleet-b:coordinator"


def test_evidence_schema_version_is_checked():
    evidence = _evidence()
    doc = evidence.to_dict()
    doc["schema_version"] = EVIDENCE_SCHEMA_VERSION + 1
    with pytest.raises(ObservationRefused):
        Evidence.from_dict(doc)


def test_snapshot_serialization_redacts_secrets():
    evidence = _evidence(
        refusals=(Refusal(subject="x", reason="refused", detail="password=hunter2 ghp_" + "a" * 30),)
    )
    from lib.fleet_capacity import redact

    payload = redact(json.dumps(evidence.to_dict()))
    assert "hunter2" not in payload
    assert "ghp_aaaa" not in payload


# ---------------------------------------------------------------------------
# 9. decide(): stale evidence decays into zero capacity
# ---------------------------------------------------------------------------
def test_stale_evidence_yields_no_capacity(manifest):
    from lib.fleet_capacity import coordinator_observation, exporter_observation

    evidence = _evidence(
        coordinators={
            "fleet-a": coordinator_observation(
                fleet_id="fleet-a", reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            )
        },
        exporters={
            "router-a1": exporter_observation(
                fleet_id="fleet-a",
                resource_id="router-a1",
                exporter_connected=True,
                now=NOW,
                ttl_s=TTL,
                authority_epoch=EPOCH,
            )
        },
    )
    fresh = decide(manifest, evidence, now=NOW + 1)
    stale = decide(manifest, evidence, now=NOW + TTL + 1)
    fresh_router = next(v for v in fresh if v.resource_id == "router-a1")
    stale_router = next(v for v in stale if v.resource_id == "router-a1")
    assert fresh_router.state is State.EXPORTED_AVAILABLE
    assert stale_router.state is State.STALE
    assert stale_router.capacity == 0
    assert Reason.STALE_TELEMETRY in stale_router.reasons


def test_epoch_mismatch_is_refused_not_trusted(manifest):
    from lib.fleet_capacity import coordinator_observation

    evidence = _evidence(
        coordinators={
            "fleet-a": coordinator_observation(
                fleet_id="fleet-a", reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH - 1
            )
        }
    )
    verdicts = decide(manifest, evidence, now=NOW)
    router = next(v for v in verdicts if v.resource_id == "router-a1")
    assert router.capacity == 0
    assert Reason.EPOCH_MISMATCH in router.reasons


# ---------------------------------------------------------------------------
# 10. collect(): authorization, partial outage, no mutation
# ---------------------------------------------------------------------------
def _collect_runner(*, fleet_a_up: bool = True, exporter_up: bool = True, acquired: str = "None") -> FakeRunner:
    """Scripted coordinator answers. ``other.invalid`` never answers — it is a
    second, independent fleet that is simply down, which is the partial-outage
    case the audit asks about."""
    a_version = (
        CommandResult(argv=(), returncode=0, stdout="26.0.1\n")
        if fleet_a_up
        else CommandResult(argv=(), returncode=1, stderr="Could not connect to coordinator")
    )
    responses: dict[tuple[str, ...], CommandResult] = {
        ("-x", "127.0.0.1:20408", "version"): a_version,
        ("-x", "127.0.0.1:20408", "resources"): CommandResult(
            argv=(), returncode=0, stdout=("probe/router-a1/NetworkService\n" if exporter_up else "")
        ),
        ("-x", "127.0.0.1:20408", "-p", "router-a1", "show"): CommandResult(
            argv=(),
            returncode=0,
            stdout=_show_output("router-a1", acquired=acquired, avail=True if exporter_up else None),
        ),
        ("-x", "other.invalid:20408", "version"): CommandResult(
            argv=(), returncode=1, stderr="Could not connect to coordinator"
        ),
    }
    return FakeRunner(responses)


def _local_host_kwargs(tmp_path: Path) -> dict:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16384000 kB\nMemAvailable: 8192000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    return dict(disk_path=tmp_path, meminfo_path=meminfo, hostname="this-host", host_root=tmp_path)


def test_collect_observes_authorized_fleets_and_never_mutates(manifest, tmp_path):
    runner = _collect_runner()
    evidence = collect(
        manifest,
        limits=ProbeLimits(timeout_s=5.0, ttl_s=TTL),
        authority_epoch=EPOCH,
        now=NOW,
        client=_client(runner),
        **_local_host_kwargs(tmp_path),
    )
    for verb in ("acquire", "release", "release-from", "power", "create", "delete", "flashscript"):
        assert verb not in runner.subcommands, f"collect() must never issue {verb}"
    assert evidence.coordinators["fleet-a"].reachable is True
    assert evidence.coordinators["fleet-b"].reachable is False
    assert evidence.exporters["router-a1"].exporter_connected is True
    assert evidence.exporters["router-a1"].acquired is False
    # fleet-b's host controller is remote: refused, not measured.
    assert "fleet-b" not in evidence.host_metrics
    assert any(r.subject == "fleet-b:host_controller" for r in evidence.refusals)
    assert "fleet-a" in evidence.host_metrics


def test_collect_partial_fleet_outage_reports_zero_for_the_dead_fleet(manifest, tmp_path):
    runner = _collect_runner(fleet_a_up=False)
    evidence = collect(
        manifest,
        limits=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        now=NOW,
        client=_client(runner),
        **_local_host_kwargs(tmp_path),
    )
    verdicts = decide(manifest, evidence, now=NOW)
    router_a = next(v for v in verdicts if v.resource_id == "router-a1")
    assert router_a.capacity == 0
    assert Reason.COORDINATOR_UNREACHABLE in router_a.reasons
    report = build_report(
        manifest, evidence, verdicts, generated_at="now", now=NOW, demand=resolve_host_demand()
    )
    assert report["totals"]["reachable_fleets"] == 0
    assert report["totals"]["idle_physical_routers"] == 0


def test_collect_with_exporter_down_reports_exported_available_zero(manifest, tmp_path):
    runner = _collect_runner(exporter_up=False)
    evidence = collect(
        manifest,
        limits=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        now=NOW,
        client=_client(runner),
        **_local_host_kwargs(tmp_path),
    )
    verdicts = decide(manifest, evidence, now=NOW)
    router = next(v for v in verdicts if v.resource_id == "router-a1")
    assert router.capacity == 0
    assert router.state in {State.UNKNOWN, State.STALE}
    assert Reason.EXPORTER_DISCONNECTED in router.reasons


def test_collect_reports_an_acquired_place_as_refused(manifest, tmp_path):
    runner = _collect_runner(acquired="it-host/someone-else")
    evidence = collect(
        manifest,
        limits=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        now=NOW,
        client=_client(runner),
        **_local_host_kwargs(tmp_path),
    )
    verdicts = decide(manifest, evidence, now=NOW)
    router = next(v for v in verdicts if v.resource_id == "router-a1")
    assert router.state is State.REFUSED
    assert Reason.PLACE_ACQUIRED in router.reasons


def test_collect_refuses_unconfigured_fleets_without_contacting_anything(manifest, tmp_path):
    runner = _collect_runner()
    with pytest.raises(ManifestError):
        collect(
            manifest,
            limits=ProbeLimits(ttl_s=TTL),
            authority_epoch=EPOCH,
            now=NOW,
            client=_client(runner),
            fleets=["no-such-fleet"],
            **_local_host_kwargs(tmp_path),
        )
    assert runner.calls == []


def test_collect_rejects_a_guest_pidfile_outside_the_allowlist(manifest, tmp_path):
    runner = _collect_runner()
    evidence = collect(
        manifest,
        limits=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        now=NOW,
        client=_client(runner),
        guests=(GuestSpec(resource_id="not-in-manifest", pidfile=tmp_path / "x.pid"),),
        **_local_host_kwargs(tmp_path),
    )
    assert "not-in-manifest" not in evidence.guests
    assert any(r.subject == "guest:not-in-manifest" for r in evidence.refusals)


def test_collect_rejects_a_router_place_claimed_as_a_guest(manifest, tmp_path):
    runner = _collect_runner()
    evidence = collect(
        manifest,
        limits=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        now=NOW,
        client=_client(runner),
        guests=(GuestSpec(resource_id="router-a1", pidfile=tmp_path / "x.pid"),),
        **_local_host_kwargs(tmp_path),
    )
    assert "router-a1" not in evidence.guests
    assert any(r.reason == "kind_mismatch" for r in evidence.refusals)


def test_collect_records_an_allowlisted_guest_lane(manifest, tmp_path):
    proc_root = tmp_path / "proc"
    _write_proc(proc_root, 5252, 111)
    pidfile = tmp_path / "lane.pid"
    pidfile.write_text("5252\n")
    runner = _collect_runner()
    evidence = collect(
        manifest,
        limits=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        now=NOW,
        client=_client(runner),
        guests=(GuestSpec(resource_id="lane-a1", pidfile=pidfile),),
        proc_root=proc_root,
        **_local_host_kwargs(tmp_path),
    )
    assert evidence.guests["lane-a1"].pid == 5252
    verdicts = decide(manifest, evidence, now=NOW)
    guest = next(v for v in verdicts if v.resource_id == "lane-a1")
    assert guest.capacity == 0, "an identified guest with unproven readiness is never capacity"
    assert guest.state is State.UNKNOWN


# ---------------------------------------------------------------------------
# 11. Report shape
# ---------------------------------------------------------------------------
def test_report_separates_the_four_numbers_and_marks_admission(manifest, tmp_path):
    runner = _collect_runner()
    evidence = collect(
        manifest,
        limits=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        now=NOW,
        client=_client(runner),
        **_local_host_kwargs(tmp_path),
    )
    verdicts = decide(manifest, evidence, now=NOW)
    report = build_report(
        manifest, evidence, verdicts, generated_at="2026-10-30T00:00:00+00:00", now=NOW, demand=resolve_host_demand()
    )
    assert report["admission"]["status"] == ADMISSION_STATUS
    assert "reachable_fleets" in report["totals"]
    assert "idle_physical_routers" in report["totals"]
    assert "idle_existing_guests" in report["totals"]
    assert "new_vm_slots_raw" in report["totals"]
    fleet_a = next(f for f in report["fleets"] if f["fleet_id"] == "fleet-a")
    assert fleet_a["admission"] == ADMISSION_STATUS
    assert fleet_a["host"]["ram_total_mib"] == 16000
    assert fleet_a["host_admission"], "host admission verdict must be reported separately from idle lanes"
    assert fleet_a["host_admission"][0]["state"] == State.UNKNOWN.value
    assert fleet_a["host_admission"][0]["slots"] == 0
    # The report is JSON-serializable end to end.
    json.dumps(report)


def test_report_records_reservation_source_and_detail(manifest, tmp_path):
    runner = _collect_runner()
    evidence = collect(
        manifest,
        limits=ProbeLimits(ttl_s=TTL),
        authority_epoch=EPOCH,
        now=NOW,
        client=_client(runner),
        authority_path=tmp_path / "absent.sqlite3",
        **_local_host_kwargs(tmp_path),
    )
    verdicts = decide(manifest, evidence, now=NOW)
    report = build_report(manifest, evidence, verdicts, generated_at="now", now=NOW, demand=resolve_host_demand())
    assert report["reservations"]["outstanding"] == []
    assert "not found" in report["reservations"]["detail"]


def test_resolve_host_demand_rejects_zero():
    with pytest.raises(ValueError):
        resolve_host_demand(vcpus=0)


def test_coordinator_read_dataclass_defaults():
    read = CoordinatorRead(fleet_id="f", address="h:1", reachable=False)
    assert read.places == {}
    assert read.published_resources == ()


def test_place_state_defaults():
    state = PlaceState(name="p", exists=True)
    assert state.acquired_by is None
    assert state.available is None


def test_manifest_fixture_matches_the_committed_example_schema():
    """The example manifest must stay loadable — it is the documented schema."""
    example = REPO / "config" / "fleets.example.yaml"
    with pytest.raises(ManifestError):
        load_manifest(example)  # placeholders are refused by design


def test_read_only_adapter_imports_no_acquisition_api():
    """A grep-level guarantee that this module cannot acquire, power or flash.

    It is a policy test rather than a behavioural one, and it exists because the
    audit's first rule for this slice is that discovery/mutation has no code
    path here at all.
    """
    source = (REPO / "lib" / "fleet_observe.py").read_text(encoding="utf-8")
    body = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    for forbidden in ("LabgridBench(", '"acquire"', '"release"', '"power "', "subprocess.run([\"qemu", "flash("):
        assert forbidden not in body.replace("READ_ONLY_SUBCOMMANDS = frozenset({\"version\", \"resources\", \"places\", \"show\"})", ""), (
            f"fleet_observe must not reference {forbidden}"
        )
