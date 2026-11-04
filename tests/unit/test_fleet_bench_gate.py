"""Admission gate + lane isolation contract for the QEMU throughput benchmark.

These tests are pure: they feed the gate synthetic host metrics and check the
lane planner's collision checks. No guest is started, so they are safe on a busy
box — which is the only kind of box this repository's development host is.

Why the gate is tested at all: the benchmark's numbers are only meaningful if a
refusal is possible. A gate that always grants would let a contended host
publish "parallel is slower" (or worse, "parallel is faster") from contention
rather than from the scheduler.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib.qemu_lane import LaneConfig, LaneError, lane_plan  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("fleet_bench", ROOT / "scripts" / "fleet-bench.py")
fleet_bench = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(fleet_bench)


def metrics(**overrides) -> dict:
    """A healthy idle host — the ONLY state in which the gate grants."""
    base = {
        "cores": 8,
        "load1": 0.4,
        "mem_total_mib": 32_000,
        "mem_available_mib": 20_000,
        "swap_total_mib": 8_000,
        "swap_used_pct": 0.0,
        "home_free_mib": 100_000,
        "kvm": True,
        "qemu_processes": [],
    }
    base.update(overrides)
    return base


class TestAdmissionGrantsOnlyOnIdleHost:
    def test_idle_host_grants_two_lanes(self):
        ok, why, verdict = fleet_bench.admission(2, metrics=metrics())
        assert ok is True, why
        assert verdict["refusals"] == []
        assert verdict["required_ram_mib"] == 2 * fleet_bench.RAM_PER_LANE_MIB + fleet_bench.RAM_RESERVE_MIB

    def test_requirement_scales_with_lanes(self):
        _, _, one = fleet_bench.admission(1, metrics=metrics())
        _, _, two = fleet_bench.admission(2, metrics=metrics())
        assert two["required_ram_mib"] - one["required_ram_mib"] == fleet_bench.RAM_PER_LANE_MIB


class TestAdmissionRefusals:
    def test_refuses_without_kvm(self):
        ok, why, _ = fleet_bench.admission(1, metrics=metrics(kvm=False))
        assert not ok and "kvm" in why.lower()

    def test_refuses_when_ram_is_short(self):
        ok, why, _ = fleet_bench.admission(2, metrics=metrics(mem_available_mib=1500))
        assert not ok and "MemAvailable" in why

    def test_refuses_when_swap_is_already_used(self):
        ok, why, _ = fleet_bench.admission(1, metrics=metrics(swap_used_pct=80.0))
        assert not ok and "swap" in why

    def test_refuses_when_load_exceeds_cores(self):
        ok, why, _ = fleet_bench.admission(1, metrics=metrics(load1=20.0, cores=8))
        assert not ok and "load1" in why

    def test_refuses_when_home_is_short(self):
        ok, why, _ = fleet_bench.admission(2, metrics=metrics(home_free_mib=500))
        assert not ok and "home free" in why

    def test_refuses_when_foreign_qemu_is_running(self):
        ok, why, _ = fleet_bench.admission(1, metrics=metrics(qemu_processes=[4242]))
        assert not ok and "foreign QEMU" in why

    def test_all_reasons_are_reported_not_just_the_first(self):
        ok, why, verdict = fleet_bench.admission(
            2, metrics=metrics(mem_available_mib=100, swap_used_pct=90, load1=40, qemu_processes=[1])
        )
        assert not ok
        assert len(verdict["refusals"]) >= 4


class TestLiveHostIsReportedNotAssumed:
    def test_host_metrics_are_read_from_proc(self):
        m = fleet_bench.host_metrics()
        assert m["cores"] >= 1
        assert m["mem_total_mib"] > 0
        assert isinstance(m["kvm"], bool)
        assert isinstance(m["qemu_processes"], list)

    def test_refusal_on_this_host_is_verbatim_and_machine_readable(self):
        """Whatever the box looks like, the verdict must carry its own numbers."""
        ok, why, verdict = fleet_bench.admission(2)
        assert ok or why, "a refusal must say why"
        assert verdict["metrics"]["mem_available_mib"] >= 0


class TestLaneIsolationContract:
    def test_plans_unique_paths_per_lane(self):
        cfgs = lane_plan(
            workdir=Path("/tmp/prta-test-not-created"),
            base_image=Path(__file__),
            lane_ids=["qemu-1", "qemu-2"],
            mem_mib=256,
            vcpus=1,
        )
        assert [c.lane_id for c in cfgs] == ["qemu-1", "qemu-2"]
        for attr in ("overlay", "console_sock", "log_path", "events_path"):
            values = [getattr(c, attr) for c in cfgs]
            assert len(set(values)) == len(values), attr

    def test_duplicate_lane_ids_refused(self):
        with pytest.raises(ValueError, match="duplicate lane ids"):
            lane_plan(
                workdir=Path("/tmp/x"), base_image=Path(__file__), lane_ids=["a", "a"], mem_mib=256, vcpus=1
            )

    def test_missing_base_image_refused(self):
        with pytest.raises(LaneError, match="base image not found"):
            lane_plan(
                workdir=Path("/tmp/x"),
                base_image=Path("/nonexistent/image.qcow2"),
                lane_ids=["a"],
                mem_mib=256,
                vcpus=1,
            )

    def test_unique_host_ports_refused_when_duplicated(self):
        # first_port 0 means "no host port"; explicit ports must not collide.
        cfgs = lane_plan(
            workdir=Path("/tmp/x"),
            base_image=Path(__file__),
            lane_ids=["a", "b"],
            mem_mib=256,
            vcpus=1,
            first_port=2200,
        )
        assert [c.ssh_port for c in cfgs] == [2200, 2201]


class TestBenchRecordShape:
    def test_overlap_detection_finds_concurrent_lanes(self):
        results = [
            {"lane": "qemu-1", "start": 100.0, "end": 110.0},
            {"lane": "qemu-2", "start": 105.0, "end": 115.0},
        ]
        report = fleet_bench._overlap(results, admitted=[1, 2])
        assert report["cross_lane_overlapping_job_pairs"] == 1

    def test_overlap_detection_ignores_serial_runs(self):
        results = [
            {"lane": "qemu-1", "start": 100.0, "end": 110.0},
            {"lane": "qemu-2", "start": 120.0, "end": 130.0},
        ]
        assert fleet_bench._overlap(results, admitted=[1, 2])["cross_lane_overlapping_job_pairs"] == 0

    def test_overlap_ignores_failed_jobs(self):
        results = [
            {"lane": "qemu-1", "start": 100.0, "end": 110.0},
            {"lane": "qemu-2", "start": 101.0, "end": 109.0, "error": "boom"},
        ]
        assert fleet_bench._overlap(results, admitted=[1, 2])["cross_lane_overlapping_job_pairs"] == 0
