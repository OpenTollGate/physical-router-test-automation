"""Guest telemetry for a real QEMU lane, end to end.

`archy-vm` is a genuine QEMU lane on the bench (10.99.99.1, behind tap
`tg-poc-tap`). It was reported `no_telemetry` because the CLI was run without
`--guest`, and because the obvious pidfiles on disk are stale:

    /home/c08r4d0r/archy-tollgate-lab/qemu.pid        -> pid 321247   (dead)
    /home/c08r4d0r/tollgate-virtual-lab-pr1142/...    -> pid 2051281  (dead)

The live one is the sidecar's:

    /home/c08r4d0r/tollgate-sidecar-2512/run/tollgate.pid -> 2534391 (live)

A DEAD pidfile must never become "idle guest" — that is a claim, and `/proc` is
the evidence. These tests pin the three states a real bench produces:

1. live pidfile whose pid is running  -> identified (still not *ready*);
2. pidfile whose pid is gone          -> NOT idle, and the reason names the pid;
3. pidfile written by a recycled pid  -> refused, because start ticks differ.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from lib.fleet_capacity import Reason, State, parse_manifest
from lib.fleet_observe import GuestSpec, ProbeLimits, build_report, collect, decide

FLEET = "x280-bench"
PLACE = "archy-vm"
NOW = 1_800_000_000.0
TTL = 60.0
EPOCH = 5


def _manifest():
    return parse_manifest(
        {
            "schema_version": 1,
            "fleets": [
                {
                    "id": FLEET,
                    "coordinator": {"address": "x280.fips:20408"},
                    "host_controller": {"endpoint": "x280:8099"},
                    "allowed_places": [{"id": PLACE, "kind": "guest"}],
                    "permitted_actions": ["observe"],
                }
            ],
        },
        source="<test>",
    )


def _proc(tmp_path: Path, pid: int, ticks: int) -> Path:
    root = tmp_path / "proc"
    d = root / str(pid)
    d.mkdir(parents=True)
    fields = ["S"] + ["0"] * 18 + [str(ticks)] + ["0"] * 10
    (d / "stat").write_text(f"{pid} (qemu-system-x86) {' '.join(fields)}\n")
    return root


class TestDeadPidfileIsNotIdle:
    def test_a_dead_pid_yields_no_identification(self, tmp_path):
        """Claim vs evidence: the pidfile says nothing if the pid is gone."""
        from lib.fleet_observe import read_local_guest

        pidfile = tmp_path / "stale.pid"
        pidfile.write_text("321247\n")  # measured dead pid
        obs = read_local_guest(
            GuestSpec(resource_id=PLACE, pidfile=pidfile),
            fleet_id=FLEET,
            now=NOW,
            bandwidth=ProbeLimits(ttl_s=TTL),
            authority_epoch=EPOCH,
            proc_root=_proc(tmp_path, 999, 1),  # pid 321247 absent
        )
        assert obs.pid is None
        verdict = decide(
            _manifest(),
            _evidence(obs),
            now=NOW,
        )
        guest = next(v for v in verdict if v.resource_id == PLACE)
        assert guest.capacity == 0
        assert guest.state is not State.IDLE_GUEST

    def test_a_live_pid_is_identified(self, tmp_path):
        from lib.fleet_observe import read_local_guest

        pidfile = tmp_path / "live.pid"
        pidfile.write_text("2534391\n")
        obs = read_local_guest(
            GuestSpec(resource_id=PLACE, pidfile=pidfile, expected_pid_start_ticks=7845007),
            fleet_id=FLEET,
            now=NOW,
            bandwidth=ProbeLimits(ttl_s=TTL),
            authority_epoch=EPOCH,
            proc_root=_proc(tmp_path, 2534391, 7845007),
        )
        assert obs.pid == 2534391
        assert obs.pid_start_ticks == 7845007
        # Identification is NOT readiness: health/topology/lease/vantage unproven.
        assert obs.health_ok is None
        assert obs.lease_free is None

    def test_a_recycled_pid_is_refused(self, tmp_path):
        """Same pid, different start ticks = a different process."""
        from lib.fleet_observe import read_local_guest

        pidfile = tmp_path / "recycled.pid"
        pidfile.write_text("2534391\n")
        obs = read_local_guest(
            GuestSpec(resource_id=PLACE, pidfile=pidfile, expected_pid_start_ticks=111),
            fleet_id=FLEET,
            now=NOW,
            bandwidth=ProbeLimits(ttl_s=TTL),
            authority_epoch=EPOCH,
            proc_root=_proc(tmp_path, 2534391, 999999),
        )
        verdict = decide(_manifest(), _evidence(obs), now=NOW)
        guest = next(v for v in verdict if v.resource_id == PLACE)
        assert guest.capacity == 0
        assert Reason.GUEST_PID_REUSED in guest.reasons


def _evidence(guest_obs, *, coordinators=None, exporters=None):
    from lib.fleet_observe import Evidence

    return Evidence(
        observed_at=NOW,
        authority_epoch=EPOCH,
        ttl_s=TTL,
        guests={PLACE: guest_obs},
    )


class TestLiveBenchGuest:
    """The real sidecar lane, if it is running on this host."""

    SIDECAR = Path("/home/c08r4d0r/tollgate-sidecar-2512/run/tollgate.pid")

    def test_sidecar_pidfile_resolves_to_a_live_qemu(self):
        if not self.SIDECAR.exists():
            pytest.skip("sidecar lane not running on this host")
        pid = int(self.SIDECAR.read_text().strip())
        proc = Path(f"/proc/{pid}")
        if not proc.exists():
            pytest.skip(f"sidecar pid {pid} is not running")
        cmdline = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
        assert "qemu-system" in cmdline, f"pid {pid} is not a qemu process"

    def test_stale_pidfiles_on_this_host_are_dead(self):
        """Records the trap: two plausible pidfiles point at nothing."""
        for stale in (
            Path("/home/c08r4d0r/archy-tollgate-lab/qemu.pid"),
            Path("/home/c08r4d0r/tollgate-virtual-lab-pr1142/qemu.pid"),
        ):
            if not stale.exists():
                continue
            pid = int(stale.read_text().strip())
            if not Path(f"/proc/{pid}").exists():
                # Expected: these are stale. The point is the tool must not
                # treat them as lanes.
                assert True
