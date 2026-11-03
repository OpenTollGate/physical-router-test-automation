"""The committed bench manifest must parse, and must not name production kit.

``config/fleets.x280-bench.yaml`` is the first REAL fleet manifest in the tree:
three bench routers enrolled as labgrid infrastructure. It is the file a test
run will point at, so two properties matter and both are checked here rather
than trusted:

1. **It parses under the real schema.** A manifest that only looks right is a
   run that refuses at the worst moment.
2. **It never names a protected device.** The protected list already guards
   consoles by cable serial, but a manifest naming the production gateway would
   be a reservation against production hardware — the failure this whole thread
   started with. The bench and the protected list must be disjoint.

The distinction being enforced: three units are BENCH (owner-confirmed
2026-11-03), two are PRODUCTION, and both are cabled to the same host.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.fleet_capacity import load_manifest
from lib.protected_devices import load_protected, match_protected

ROOT = Path(__file__).resolve().parents[2]
BENCH_MANIFEST = ROOT / "config" / "fleets.x280-bench.yaml"

BENCH_PLACES = {
    "bench-comfast-wr632ax",
    "bench-mt3000-tollgate",
    "bench-covr-x1860-tollgate",
}
#: Cables measured on this host 2026-11-03, with what they serve.
BENCH_CABLES = {"BG046H14", "BG046H5F", "BH00D6DL"}
PRODUCTION_CABLES = {"BG02QAPG", "BH00DL3M"}


def test_the_bench_manifest_exists_and_parses():
    assert BENCH_MANIFEST.is_file(), (
        "no committed bench manifest — a test run has nothing real to point at"
    )
    manifest = load_manifest(BENCH_MANIFEST)
    assert "x280-bench" in manifest.fleets


def test_it_declares_exactly_the_three_bench_units():
    manifest = load_manifest(BENCH_MANIFEST)
    declared = set(manifest.resources)
    assert declared == BENCH_PLACES, (
        f"bench manifest declares {sorted(declared)}; expected the three "
        "owner-confirmed bench units"
    )


def test_no_protected_device_is_reachable_through_this_manifest():
    """Every declared place must be free of any protected identity."""
    manifest = load_manifest(BENCH_MANIFEST)
    for resource_id in manifest.resources:
        row = match_protected(board_name=resource_id, hostname=resource_id)
        # Place names are bench prefixes; a protected match here would mean the
        # manifest reached production kit.
        assert row is None, (
            f"bench manifest declares {resource_id!r}, which matches protected "
            f"device {row.get('id')!r}"
        )


def test_the_fleet_permits_the_actions_a_runner_requires():
    """lib/run_admission refuses a fleet without acquire+release."""
    manifest = load_manifest(BENCH_MANIFEST)
    fleet = manifest.fleet("x280-bench")
    assert "observe" in fleet.permitted_actions
    assert "acquire" in fleet.permitted_actions
    assert "release" in fleet.permitted_actions


def test_bench_and_production_cables_are_disjoint():
    """The two sets are physically different hardware on one host."""
    assert BENCH_CABLES.isdisjoint(PRODUCTION_CABLES)
    assert len(BENCH_CABLES) == 3
    assert len(PRODUCTION_CABLES) == 2


def test_production_cables_are_all_protected_and_bench_cables_are_not():
    protected_adapters: set[str] = set()
    for row in load_protected():
        protected_adapters |= {str(s).upper() for s in (row.get("serial_adapters") or [])}
    assert PRODUCTION_CABLES <= protected_adapters, (
        "a production cable is not in the protected list"
    )
    assert BENCH_CABLES.isdisjoint(protected_adapters), (
        "a bench cable is protected — the bench would be un-runnable"
    )


def test_the_manifest_shares_a_conflict_domain_across_the_bench_host():
    """No power control: one host-level wedge affects every unit."""
    manifest = load_manifest(BENCH_MANIFEST)
    fleet = manifest.fleet("x280-bench")
    assert fleet.shared_conflict_ids, (
        "bench units share one host and one USB hub; declaring no conflict "
        "domain would let parallel runs fight over the same hub"
    )
