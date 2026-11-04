"""Multi-contributor fleet detection, driven through the real CLI.

The goal is not "detect a fleet" but "detect WHICH fleet is physically available"
across contributors who each run their own coordinator, host controller and
hardware. The failure modes that matter are all cross-fleet:

* an unreachable contributor must read as ``down`` — never as busy, and never as
  a zero that looks like a measurement;
* a reachable fleet's lanes must never be attributed to an unreachable one;
* the totals must separate "how many fleets answered" from "how much capacity
  exists", because those disagree constantly.

These tests run the actual ``scripts/fleet-capacity.py`` as a subprocess against
a two-fleet manifest: the real bench (reachable) plus a deliberately unreachable
contributor on TEST-NET-1 (``192.0.2.0/24``, reserved for documentation and never
routable). The unreachable side is not a mock — it is a real connection attempt
that really fails.

Driving the CLI rather than re-implementing its wiring is deliberate: the wiring
is what a contributor's operator actually runs, and a test that assembles the
report itself would pass while `main()` was broken.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "config" / "fleets.x280-bench.yaml"
CLI = ROOT / "scripts" / "fleet-capacity.py"

pytestmark = pytest.mark.required

#: TEST-NET-1 (RFC 5737): reserved for documentation, must never route.
UNREACHABLE = "192.0.2.77"


@pytest.fixture(scope="module")
def two_fleet_manifest(tmp_path_factory) -> Path:
    if not BENCH.is_file():
        pytest.fail(f"bench manifest missing at {BENCH}; cannot measure fleet detection")
    bench = BENCH.read_text()
    second = f"""
  - id: contributor-b
    owner: contributor-b
    coordinator:
      address: "{UNREACHABLE}:20408"
      credential_ref:
        source: file
        name: fleet-b/coordinator
    host_controller:
      endpoint: "{UNREACHABLE}:8099"
      credential_ref:
        source: file
        name: fleet-b/host-controller
    allowed_places:
      - id: b-router-1
        kind: router
      - id: b-qemu-1
        kind: guest
    permitted_actions: [observe, acquire, release]
    capacity_limits:
      max_concurrent_runs: 2
      max_idle_guests: 1
      max_new_vm_slots: 0
    shared_conflict_ids:
      - fleet-b-host
"""
    target = tmp_path_factory.mktemp("fleets") / "two-fleets.yaml"
    target.write_text(bench.rstrip() + "\n" + second)
    return target


@pytest.fixture(scope="module")
def report(two_fleet_manifest) -> str:
    import os

    env = {k: v for k, v in os.environ.items() if k != "LG_PROXY"}
    env["LG_PROXY"] = ""
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, str(CLI), "--manifest", str(two_fleet_manifest), "--epoch", "1", "--timeout", "2"],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    if proc.returncode != 0:
        pytest.fail(f"fleet-capacity exited {proc.returncode}: {proc.stderr[-600:]}")
    return proc.stdout


def test_both_fleets_appear_in_the_report(report):
    assert "x280-bench" in report
    assert "contributor-b" in report


def test_the_reachable_fleet_is_reported_up(report):
    line = next(ln for ln in report.splitlines() if ln.startswith("x280-bench"))
    assert " up " in f" {line} ", f"bench must be up: {line!r}"


def test_the_unreachable_contributor_is_reported_down(report):
    line = next(ln for ln in report.splitlines() if ln.startswith("contributor-b"))
    assert " down " in f" {line} ", f"unreachable contributor must read down: {line!r}"


def test_totals_separate_fleets_accounted_for_from_capacity(report):
    """'1 of 2 fleets' and 'N lanes' answer different questions."""
    totals = next(ln for ln in report.splitlines() if ln.startswith("totals:"))
    assert "1/2 fleets reachable" in totals, totals
    # Zero across two fleets must not be presented as a capacity measurement of
    # the fleet that never answered.
    assert "0 idle physical router" in totals, totals


def test_an_unreachable_contributor_contributes_no_unclaimed_lanes(report):
    """The bench's three lanes are the only ones counted."""
    totals = next(ln for ln in report.splitlines() if ln.startswith("totals:"))
    assert "3 exported/unclaimed; eligibility unproven" in totals, totals


def test_multi_fleet_detection_does_not_become_multi_fleet_admission(report):
    assert "admission: NOT_QUALIFIED" in report
