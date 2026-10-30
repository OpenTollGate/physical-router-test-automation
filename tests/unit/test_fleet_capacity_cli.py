"""Unit tests for scripts/fleet-capacity.py — the operator CLI.

The CLI is driven end to end with a scripted labgrid runner, so the assertions
cover the actual operator surface: exit codes, the four separated numbers, the
NOT_QUALIFIED marking, the text renderer, snapshot round-trip and refusal paths.
No coordinator is contacted and nothing is acquired.

Audit reference: reports/prta-fleet-capacity-audit.md gate 1 (no capacity is
reported without evidence) and the manager steer on t_3ad7fda2 (raw headroom
separate from admission; never claim parallel execution from a CLI alone).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "fleet-capacity.py"

_spec = importlib.util.spec_from_file_location("fleet_capacity_cli", SCRIPT)
_cli = importlib.util.module_from_spec(_spec)
sys.modules["fleet_capacity_cli"] = _cli  # dataclasses introspection requires registration
_spec.loader.exec_module(_cli)

from lib.fleet_observe import ADMISSION_STATUS, CommandResult, LabgridReadOnlyClient  # noqa: E402

pytestmark = [pytest.mark.story("P11")]

NOW = 1_000_000.0


# ---------------------------------------------------------------------------
# Manifest + runner fixtures
# ---------------------------------------------------------------------------
MANIFEST = """
schema_version: 1
fleets:
  - id: local-lab
    owner: it
    coordinator: {address: 127.0.0.1:20408}
    host_controller: {endpoint: 127.0.0.1:8099}
    allowed_places:
      - router-a1
      - {id: lane-a1, kind: guest}
    permitted_actions: [observe]
    capacity_limits: {max_concurrent_runs: 1, max_idle_guests: 1, max_new_vm_slots: 0}
    shared_conflict_ids: []
"""


@pytest.fixture()
def manifest_path(tmp_path: Path) -> Path:
    path = tmp_path / "fleets.yaml"
    path.write_text(MANIFEST)
    return path


def _show(name: str, *, acquired: str = "None") -> str:
    return (
        f"Place '{name}':\n"
        "  matches:\n"
        f"    probe/{name}/NetworkService\n"
        f"  acquired: {acquired}\n"
        "  acquired resources:\n"
        "Matching resource 'NetworkService' (probe/router-a1/NetworkService/NetworkService):\n"
        "  {'acquired': None,\n"
        "   'avail': True,\n"
        "   'cls': 'NetworkService'}\n"
    )


class ScriptedRunner:
    def __init__(self, responses: dict[tuple[str, ...], CommandResult]):
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> CommandResult:
        self.calls.append(list(argv))
        key = tuple(argv[1:])
        if key in self.responses:
            return self.responses[key]
        for scripted, result in self.responses.items():
            if key[-len(scripted) :] == scripted:
                return result
        return CommandResult(argv=(), returncode=1, stderr="not scripted")

    @property
    def subcommands(self) -> list[str]:
        return [argv[-1] for argv in self.calls]


def _runner(*, version_ok: bool = True) -> ScriptedRunner:
    return ScriptedRunner(
        {
            ("-x", "127.0.0.1:20408", "version"): (
                CommandResult(argv=(), returncode=0, stdout="26.0.1\n")
                if version_ok
                else CommandResult(argv=(), returncode=1, stderr="Could not connect")
            ),
            ("-x", "127.0.0.1:20408", "resources"): CommandResult(
                argv=(), returncode=0, stdout="probe/router-a1/NetworkService\n"
            ),
            ("-x", "127.0.0.1:20408", "-p", "router-a1", "show"): CommandResult(
                argv=(), returncode=0, stdout=_show("router-a1")
            ),
        }
    )


@pytest.fixture()
def fake_client(monkeypatch):
    """Replace the CLI's client factory with one over a scripted runner."""

    def install(runner: ScriptedRunner) -> ScriptedRunner:
        original = _cli.LabgridReadOnlyClient

        def factory(limits=None, **kwargs):
            return LabgridReadOnlyClient(limits, binary="/usr/bin/labgrid-client", runner=runner)

        monkeypatch.setattr(_cli, "LabgridReadOnlyClient", factory)
        assert original is not None
        return runner

    return install


@pytest.fixture(autouse=True)
def _local_host(monkeypatch, tmp_path):
    """Pin host metrics to a synthetic local host so CI has no /proc dependency."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16384000 kB\nMemAvailable: 8192000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    original = _cli.collect

    def wrapped(manifest, **kwargs):
        kwargs.setdefault("meminfo_path", meminfo)
        kwargs.setdefault("host_root", tmp_path)
        kwargs.setdefault("hostname", "this-host")
        return original(manifest, **kwargs)

    monkeypatch.setattr(_cli, "collect", wrapped)


# ---------------------------------------------------------------------------
# 1. Happy path: four separate numbers, admission marked
# ---------------------------------------------------------------------------
def test_text_report_prints_the_four_numbers_and_not_qualified(manifest_path, fake_client, capsys):
    runner = fake_client(_runner())
    rc = _cli.main(["--manifest", str(manifest_path), "--epoch", "1", "--disk-path", "/"])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"admission: {ADMISSION_STATUS}" in out
    assert "fleets reachable" in out
    assert "idle physical router(s)" in out
    assert "idle existing guest(s)" in out
    assert "new VM slots: 0 raw / NOT_QUALIFIED" in out
    assert "host metrics (this machine, measured now)" in out
    assert "host admission: unknown slots=0" in out
    # No mutating verb was ever issued.
    for verb in ("acquire", "release", "power", "create", "delete"):
        assert verb not in runner.subcommands


def test_json_report_is_machine_readable_and_marks_admission(manifest_path, fake_client, capsys):
    fake_client(_runner())
    rc = _cli.main(["--manifest", str(manifest_path), "--epoch", "1", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    report = json.loads(out)
    assert report["admission"]["status"] == ADMISSION_STATUS
    assert report["totals"]["reachable_fleets"] == 1
    assert report["totals"]["new_vm_slots_raw"] == 0
    fleet = report["fleets"][0]
    assert fleet["fleet_id"] == "local-lab"
    assert fleet["idle_physical_routers"] == 0
    assert fleet["exported_available"] == 1
    assert fleet["admission"] == ADMISSION_STATUS


def test_exported_place_is_never_counted_as_idle_router(manifest_path, fake_client, capsys):
    fake_client(_runner())
    _cli.main(["--manifest", str(manifest_path), "--epoch", "1", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert report["totals"]["idle_physical_routers"] == 0
    assert report["totals"]["exported_available"] == 1


# ---------------------------------------------------------------------------
# 2. Refusals and exit codes
# ---------------------------------------------------------------------------
def test_unreachable_coordinator_reports_zero_and_exit_zero(manifest_path, fake_client, capsys):
    fake_client(_runner(version_ok=False))
    rc = _cli.main(["--manifest", str(manifest_path), "--epoch", "1", "--json"])
    assert rc == 0, "a refusal is a result, not a CLI failure"
    report = json.loads(capsys.readouterr().out)
    assert report["totals"]["reachable_fleets"] == 0
    assert report["totals"]["idle_physical_routers"] == 0
    reasons = [item["reasons"] for fleet in report["fleets"] for item in fleet["verdicts"]]
    assert ["coordinator_unreachable"] in reasons


def test_missing_manifest_is_exit_2(tmp_path, capsys):
    rc = _cli.main(["--manifest", str(tmp_path / "absent.yaml")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "manifest refused" in err


def test_placeholder_manifest_is_refused(tmp_path, capsys):
    path = tmp_path / "example.yaml"
    path.write_text((REPO / "config" / "fleets.example.yaml").read_text())
    rc = _cli.main(["--manifest", str(path)])
    assert rc == 2
    assert "placeholder" in capsys.readouterr().err


def test_missing_epoch_without_authority_is_a_refusal(manifest_path, fake_client, capsys):
    fake_client(_runner())
    rc = _cli.main(["--manifest", str(manifest_path)])
    err = capsys.readouterr().err
    assert rc == 3
    assert "no authority epoch" in err


def test_cli_does_not_create_an_authority_store_it_only_reads(manifest_path, fake_client, tmp_path, capsys):
    """`--authority <absent path>` must not be turned into a store by looking at it."""
    fake_client(_runner())
    absent = tmp_path / "authority.sqlite3"
    assert not absent.exists()
    rc = _cli.main(["--manifest", str(manifest_path), "--authority", str(absent), "--epoch", "4"])
    capsys.readouterr()
    assert rc == 0
    assert not absent.exists(), (
        "the CLI created an authority store at an absent --authority path; "
        "an observation command must not be a writer on a live bench"
    )


def test_cli_reads_a_real_authority_epoch_instead_of_the_override(manifest_path, fake_client, tmp_path, capsys):
    """A configured store is authoritative: --epoch must not mask a different one."""
    from lib.fleet_authority import FleetAuthority

    fake_client(_runner())
    store = tmp_path / "authority.sqlite3"
    authority = FleetAuthority(store)
    authority.reconcile()
    rc = _cli.main(["--manifest", str(manifest_path), "--authority", str(store), "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["authority_epoch"] == authority.epoch


def test_bad_snapshot_schema_is_exit_3(manifest_path, tmp_path, capsys):
    snapshot = tmp_path / "ev.json"
    snapshot.write_text(json.dumps({"schema_version": 99}))
    rc = _cli.main(["--manifest", str(manifest_path), "--from-snapshot", str(snapshot)])
    err = capsys.readouterr().err
    assert rc == 3
    assert "schema_version" in err


def test_snapshot_epoch_mismatch_is_exit_3(manifest_path, fake_client, tmp_path, capsys):
    fake_client(_runner())
    evidence_path = tmp_path / "ev.json"
    assert _cli.main(["--manifest", str(manifest_path), "--epoch", "5", "--save-evidence", str(evidence_path)]) == 0
    capsys.readouterr()
    rc = _cli.main(
        ["--manifest", str(manifest_path), "--from-snapshot", str(evidence_path), "--epoch", "6"]
    )
    assert rc == 3
    assert "does not match" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 3. Snapshot round-trip: no probing at all
# ---------------------------------------------------------------------------
def test_snapshot_mode_contacts_nothing(manifest_path, fake_client, tmp_path, capsys):
    runner = fake_client(_runner())
    evidence_path = tmp_path / "ev.json"
    assert _cli.main(["--manifest", str(manifest_path), "--epoch", "5", "--save-evidence", str(evidence_path)]) == 0
    capsys.readouterr()
    calls_after_probe = len(runner.calls)
    assert calls_after_probe > 0

    rc = _cli.main(["--manifest", str(manifest_path), "--from-snapshot", str(evidence_path), "--json"])
    assert rc == 0
    assert len(runner.calls) == calls_after_probe, "a snapshot re-decision must not contact anything"
    report = json.loads(capsys.readouterr().out)
    assert report["totals"]["reachable_fleets"] == 1


def test_saved_evidence_is_redacted(manifest_path, fake_client, tmp_path, capsys):
    fake_client(_runner())
    evidence_path = tmp_path / "ev.json"
    assert _cli.main(["--manifest", str(manifest_path), "--epoch", "1", "--save-evidence", str(evidence_path)]) == 0
    payload = evidence_path.read_text()
    assert "password=" not in payload.lower()
    assert json.loads(payload)["schema_version"] == 1


# ---------------------------------------------------------------------------
# 4. Guest declaration parsing
# ---------------------------------------------------------------------------
def test_parse_guest_without_ticks():
    spec = _cli._parse_guest("lane-a=/tmp/lane.pid")
    assert spec.resource_id == "lane-a"
    assert spec.pidfile == Path("/tmp/lane.pid")
    assert spec.expected_pid_start_ticks is None


def test_parse_guest_with_ticks():
    spec = _cli._parse_guest("lane-a=/tmp/lane.pid:4242")
    assert spec.pidfile == Path("/tmp/lane.pid")
    assert spec.expected_pid_start_ticks == 4242


def test_parse_guest_requires_both_parts():
    with pytest.raises(argparse.ArgumentTypeError):
        _cli._parse_guest("lane-a")
    with pytest.raises(argparse.ArgumentTypeError):
        _cli._parse_guest("=/tmp/lane.pid")
    with pytest.raises(argparse.ArgumentTypeError):
        _cli._parse_guest("lane-a=")


def test_guest_flag_is_allowlisted_and_reported(manifest_path, fake_client, tmp_path, capsys):
    fake_client(_runner())
    pidfile = tmp_path / "lane.pid"
    pidfile.write_text("999999\n")  # dead pid: identification fails, capacity stays 0
    rc = _cli.main(
        ["--manifest", str(manifest_path), "--epoch", "1", "--json", "--guest", f"lane-a1={pidfile}"]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    lane = next(
        item for fleet in report["fleets"] for item in fleet["verdicts"] if item["resource_id"] == "lane-a1"
    )
    assert lane["capacity"] == 0
    assert "bridge_only" in lane["reasons"]


# ---------------------------------------------------------------------------
# 5. Argument surface
# ---------------------------------------------------------------------------
def test_parser_defaults():
    args = _cli.build_parser().parse_args(["--manifest", "x.yaml"])
    assert args.fleet is None
    assert args.timeout == 10.0
    assert args.ttl == 60.0
    assert args.demand_vcpus == 2
    assert args.admission if hasattr(args, "admission") else True


def test_render_text_shows_refusals():
    from lib.fleet_observe import Refusal

    report = {
        "generated_at": "now",
        "age_s": 0.0,
        "ttl_s": 60.0,
        "manifest": "m",
        "authority_epoch": 1,
        "admission": {"status": ADMISSION_STATUS, "detail": "d"},
        "declared_lane_demand": {"vcpus": 1, "ram_mib": 1, "disk_mib": 1, "disk_inodes": 1},
        "fleets": [],
        "totals": {
            "reachable_fleets": 0,
            "configured_fleets": 0,
            "idle_physical_routers": 0,
            "idle_existing_guests": 0,
            "exported_available": 0,
            "new_vm_slots_raw": 0,
        },
        "reservations": {"source": "", "detail": "none", "outstanding": []},
        "refusals": [Refusal(subject="f:host", reason="refused", detail="not this host").as_report()],
    }
    text = _cli._render_text(report)
    assert "refusals (1)" in text
    assert "f:host: refused — not this host" in text


def test_render_text_with_reservations_and_host():
    report = {
        "generated_at": "now",
        "age_s": 5.0,
        "ttl_s": 60.0,
        "manifest": "m",
        "authority_epoch": 1,
        "admission": {"status": ADMISSION_STATUS, "detail": "d"},
        "declared_lane_demand": {"vcpus": 1, "ram_mib": 1, "disk_mib": 1, "disk_inodes": 1},
        "fleets": [
            {
                "fleet_id": "f",
                "coordinator_reachable": True,
                "coordinator_status": ["f/coordinator: coordinator_reachable capacity=0 (control_plane_only)"],
                "configured_places": 1,
                "exported_available": 0,
                "idle_physical_routers": 0,
                "idle_existing_guests": 0,
                "new_vm_slots_raw": 0,
                "admission": ADMISSION_STATUS,
                "limits": {"max_concurrent_runs": 1, "max_idle_guests": 0, "max_new_vm_slots": 0},
                "published_resources": [],
                "host": {
                    "cpu_total": 4,
                    "load1": 0.5,
                    "ram_available_mib": 2048,
                    "ram_total_mib": 4096,
                    "swap_used_mib": 0,
                    "disk_free_mib": 1000,
                    "inode_free": 500,
                    "kvm_usable": True,
                    "architecture": "x86_64",
                    "detail": "",
                },
                "host_admission": [
                    {
                        "state": "unknown",
                        "slots": 0,
                        "reasons": ["unknown_dimension"],
                        "detail": "",
                        "raw_headroom": {"cpu": 4.0, "ram_mib": 1024.0},
                    }
                ],
                "excluded": [],
            }
        ],
        "totals": {
            "reachable_fleets": 1,
            "configured_fleets": 1,
            "idle_physical_routers": 0,
            "idle_existing_guests": 0,
            "exported_available": 0,
            "new_vm_slots_raw": 0,
        },
        "reservations": {
            "source": "/tmp/store.sqlite3",
            "detail": "",
            "outstanding": [
                {
                    "run_id": "run-1",
                    "started": False,
                    "expires_at": 1_000_060.0,
                    "demand": {"vcpus": 2, "ram_mib": 1024, "disk_mib": 4096, "disk_inodes": 1},
                }
            ],
        },
        "refusals": [],
    }
    text = _cli._render_text(report)
    assert "outstanding reservations (/tmp/store.sqlite3)" in text
    assert "run-1 pending 2vCPU/1024MiB/4096MiB" in text
    assert "raw headroom:" in text
    assert "kvm=True" in text


def test_fleet_selection_limits_the_report(manifest_path, fake_client, capsys):
    fake_client(_runner())
    rc = _cli.main(["--manifest", str(manifest_path), "--epoch", "1", "--fleet", "local-lab", "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert [f["fleet_id"] for f in report["fleets"]] == ["local-lab"]


def test_unknown_fleet_selection_is_a_refusal(manifest_path, fake_client, capsys):
    fake_client(_runner())
    rc = _cli.main(["--manifest", str(manifest_path), "--epoch", "1", "--fleet", "nope"])
    assert rc == 3
    assert "not configured" in capsys.readouterr().err
